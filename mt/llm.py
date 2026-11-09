"""Cliente mínimo de OpenRouter que devuelve JSON validado con pydantic.

- Modelos: primero `LLM_MODELOS_GRATIS` (`:free`) y, si se agotan o fallan, `LLM_MODELOS_PAGO`, con un tope de gasto
  `LLM_TOPE_USD` (el costo sale de `usage.cost` de cada respuesta).
- Errores (idea del router de doc-unico): cuota diaria → el modelo descansa hasta el día siguiente (UTC);
  429/5xx → pausa corta; modelo inexistente o que no acepta la petición → se aparta 6 h; contexto muy largo →
  se salta solo en esa llamada; sin crédito (402) → se apartan los modelos de pago en esta corrida.
- Respuesta que no es JSON válido para el esquema (o que no pasa `validar`) → se prueba el siguiente modelo.
- Caché por hash de (tarea, esquema, prompt), sin el modelo: repetir una corrida no gasta cuota.
  Estado de cuotas y gasto en `cache/llm/_router.json` (fuera de git).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from . import config

URL = "https://openrouter.ai/api/v1/chat/completions"
T = TypeVar("T", bound=BaseModel)

MODELOS_GRATIS = [m.strip() for m in config.env(
    "LLM_MODELOS_GRATIS", "poolside/laguna-s-2.1:free,nvidia/nemotron-3-ultra-550b-a55b:free,"
    "dots-studio/dots-3-note-preview:free,google/gemma-4-31b-it:free").split(",") if m.strip()]
MODELOS_PAGO = [m.strip() for m in config.env(
    "LLM_MODELOS_PAGO", "google/gemini-2.5-flash-lite,deepseek/deepseek-chat-v3.1").split(",") if m.strip()]
TOPE_USD = config.env_float("LLM_TOPE_USD", 2.0)
# "si": con crédito disponible se usan primero los modelos de pago (más rápidos y estables) y los gratuitos de respaldo
PAGO_PRIMERO = config.env("LLM_PAGO_PRIMERO", "no").lower() in ("si", "sí", "1", "true")
RPM = config.env_int("LLM_RPM", 20)
# saldo real de la clave (GET /api/v1/key): por debajo de esto no se usan modelos de pago
SALDO_MIN = config.env_float("LLM_SALDO_MIN", 0.05)
TIMEOUT = config.env_int("LLM_TIMEOUT", 120)

SISTEMA = ("Eres un asistente que analiza documentos legales chilenos de concesiones de obras públicas. "
           "Respondes SOLO con un objeto JSON válido que cumple el esquema pedido, sin texto adicional.")


class SinModelos(RuntimeError):
    """Ningún modelo disponible pudo responder (cuotas agotadas, tope de gasto o formato inválido en todos)."""


class _Router:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.lock = threading.Lock()
        self.estado = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
        self.estado.setdefault("apartados", {})
        self.estado.setdefault("gasto_usd", 0.0)
        self.estado.setdefault("sin_json_mode", [])
        self.ultima: dict[str, float] = {}
        self.sin_credito = False
        self._consultas, self._ultima_consulta = 0, 0.0
        self.llamadas: dict[str, int] = {}

    def guardar(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta.write_text(json.dumps(self.estado, ensure_ascii=False, indent=1), encoding="utf-8")

    def saldo_ok(self) -> bool:
        """Consulta el saldo real de la clave cada ~20 llamadas o 2 minutos (el costo informado por llamada
        subestima el gasto: no incluye llamadas cortadas o fallidas que igual se cobran)."""
        with self.lock:
            if self.sin_credito:
                return False
            if self._consultas < 20 and time.time() - self._ultima_consulta < 120:
                return True
            self._consultas, self._ultima_consulta = 0, time.time()
        restante = saldo_restante()
        if restante is not None and restante < SALDO_MIN:
            self.sin_credito = True
            print(f"      · saldo de la clave US$ {restante:.3f} < {SALDO_MIN}: no se usan más modelos de pago")
        return not self.sin_credito

    def disponibles(self, modelos: list[str] | None = None) -> list[str]:
        ahora = time.time()
        if modelos is None:
            pago_ok = self.estado["gasto_usd"] < TOPE_USD and self.saldo_ok()
            pago = MODELOS_PAGO if pago_ok else []
            modelos = pago + MODELOS_GRATIS if PAGO_PRIMERO else MODELOS_GRATIS + pago
        else:
            pago_ok = self.estado["gasto_usd"] < TOPE_USD and self.saldo_ok()
            modelos = [m for m in modelos if m.endswith(":free") or pago_ok]
        return [m for m in modelos if self.estado["apartados"].get(m, {}).get("hasta", 0) <= ahora]

    def apartar(self, modelo: str, segundos: float, motivo: str):
        with self.lock:
            self.estado["apartados"][modelo] = {"hasta": time.time() + segundos, "motivo": motivo[:200]}
            self.guardar()
        print(f"      · {modelo} apartado {segundos / 3600:.1f} h: {motivo[:120]}")

    def esperar_turno(self, modelo: str):
        with self.lock:
            espera = self.ultima.get(modelo, 0) + 60 / RPM - time.time()
            self.ultima[modelo] = max(time.time(), self.ultima.get(modelo, 0) + 60 / RPM)
        if espera > 0:
            time.sleep(espera)

    def sumar(self, modelo: str, costo: float):
        with self.lock:
            self._consultas += 1
            self.estado["gasto_usd"] = round(self.estado["gasto_usd"] + (costo or 0), 6)
            self.llamadas[modelo] = self.llamadas.get(modelo, 0) + 1
            self.guardar()


_routers: dict[Path, _Router] = {}


def _router(dir_cache: Path) -> _Router:
    ruta = dir_cache / "_router.json"
    if ruta not in _routers:
        _routers[ruta] = _Router(ruta)
    return _routers[ruta]


def saldo_restante() -> float | None:
    """Saldo disponible de la clave según OpenRouter (None si no se puede consultar o no tiene límite)."""
    try:
        datos = requests.get("https://openrouter.ai/api/v1/key", timeout=20, headers={
            "Authorization": f"Bearer {config.env('OPENROUTER_API_KEY', '')}"}).json().get("data", {})
        return datos.get("limit_remaining")
    except (requests.RequestException, ValueError):
        return None


def resumen(dir_cache: Path) -> str:
    r = _router(dir_cache)
    llamadas = ", ".join(f"{m}: {n}" for m, n in r.llamadas.items()) or "ninguna (todo desde caché)"
    return f"llamadas LLM: {llamadas} · gasto acumulado US$ {r.estado['gasto_usd']:.4f} (tope {TOPE_USD})"


def _extraer_json(texto: str) -> dict:
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    a, b = texto.find("{"), texto.rfind("}")
    if a < 0 or b < a:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(texto[a:b + 1])


def _hasta_manana() -> float:
    ahora = time.time()
    return 86400 - ahora % 86400 + 60


def _llamar(r: _Router, modelo: str, sistema: str, prompt: str | list, json_mode: bool = True,
            max_tokens: int = 6000, plazo: int | None = None, razonar: bool = True) -> tuple[str, float]:
    """Una llamada HTTP. Devuelve (contenido, costo) o lanza `_Falla` con la acción a tomar. `prompt` puede ser
    una lista de partes (texto e imagen) para los modelos de visión."""
    cuerpo = {"model": modelo, "temperature": 0, "max_tokens": max_tokens, "usage": {"include": True},
              "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": prompt}]}
    if json_mode and modelo not in r.estado["sin_json_mode"]:
        cuerpo["response_format"] = {"type": "json_object"}
    if not razonar:          # los modelos híbridos (Qwen3.x) razonan por defecto: gastan tokens y pueden no responder
        cuerpo["reasoning"] = {"enabled": False}
    r.esperar_turno(modelo)
    # Plazo TOTAL por llamada: mientras el proveedor encola, OpenRouter mantiene la conexión abierta mandando
    # espacios de "procesando", que reinician el timeout de lectura de requests (la llamada podría no terminar nunca).
    inicio = time.time()
    try:
        resp = requests.post(URL, json=cuerpo, timeout=(15, 60), stream=True, headers={
            "Authorization": f"Bearer {config.env('OPENROUTER_API_KEY', '')}",
            "HTTP-Referer": "https://github.com/frr05/MT-v3", "X-Title": "MT-v3 documento unico"})
        partes = []
        for trozo in resp.iter_content(4096):
            partes.append(trozo)
            if time.time() - inicio > (plazo or TIMEOUT):
                resp.close()
                raise _Falla("lento", f"sin respuesta completa en {plazo or TIMEOUT} s")
        cuerpo_resp = b"".join(partes).decode("utf-8", "replace")
    except requests.RequestException as e:
        raise _Falla("temporal", str(e))
    try:
        datos = json.loads(cuerpo_resp)
    except ValueError:
        datos = {"error": {"message": cuerpo_resp.strip()[:300] or f"respuesta vacía (HTTP {resp.status_code})"}}
    error = datos.get("error") if isinstance(datos, dict) else None
    if resp.status_code != 200 or error:
        codigo = (error or {}).get("code", resp.status_code)
        mensaje = json.dumps(error or datos, ensure_ascii=False)[:400]
        texto = mensaje.lower()
        if codigo == 402 or resp.status_code == 402 or "key limit exceeded" in texto or "insufficient credits" in texto:
            raise _Falla("credito", mensaje)
        if "response_format" in texto or "json mode" in texto or "json_object" in texto:
            raise _Falla("sin_json_mode", mensaje)
        if "context" in texto and ("length" in texto or "too long" in texto or "maximum" in texto):
            raise _Falla("contexto", mensaje)
        if resp.status_code == 429 or codigo == 429:
            raise _Falla("dia" if ("per-day" in texto or "per day" in texto or "daily" in texto) else "temporal", mensaje)
        if resp.status_code >= 500 or codigo in (502, 503, 504, 408):
            raise _Falla("temporal", mensaje)
        raise _Falla("modelo", mensaje)
    eleccion = (datos.get("choices") or [{}])[0]
    contenido = (eleccion.get("message") or {}).get("content") or ""
    costo = float((datos.get("usage") or {}).get("cost") or 0)
    if not contenido.strip():
        raise _Falla("formato", f"respuesta vacía (finish_reason={eleccion.get('finish_reason')})", costo)
    return contenido, costo


class _Falla(Exception):
    def __init__(self, tipo: str, mensaje: str, costo: float = 0.0):
        super().__init__(mensaje)
        self.tipo, self.costo = tipo, costo


def pedir_json(dir_cache: Path, tarea: str, prompt: str, esquema: type[T],
               validar: Callable[[T], str | None] | None = None, sistema: str = SISTEMA,
               solo_cache: bool = False, modelos: list[str] | None = None, razonar: bool = True,
               alias: list[str] | None = None) -> T:
    """Pide al LLM un objeto JSON con la forma de `esquema`. `validar(obj)` devuelve un mensaje de error o None.
    Con `solo_cache` no llama a la red: si la respuesta no está guardada lanza SinModelos. `razonar=False` apaga
    el razonamiento de los modelos híbridos (tareas de clasificación). `alias`: versiones anteriores del prompt cuya
    respuesta guardada sirve igual (se copia a la clave nueva)."""
    firma = json.dumps(esquema.model_json_schema(), sort_keys=True)

    def ruta_de(texto: str) -> Path:
        clave = hashlib.sha256("\x00".join([tarea, firma, sistema, texto]).encode("utf-8")).hexdigest()[:24]
        return dir_cache / tarea / f"{clave}.json"

    ruta = ruta_de(prompt)
    for previa in [ruta] + [ruta_de(a) for a in alias or []]:
        if not previa.exists():
            continue
        guardado = json.loads(previa.read_text(encoding="utf-8"))
        try:
            obj = esquema.model_validate(guardado["respuesta"])
        except ValidationError:
            continue
        if validar and validar(obj):
            continue
        if previa != ruta:                                   # respuesta guardada con una versión anterior del prompt
            ruta.write_text(json.dumps(guardado, ensure_ascii=False, indent=1), encoding="utf-8")
        return obj

    if solo_cache:
        raise SinModelos(f"{tarea}: la respuesta no está en caché")
    r = _router(dir_cache)
    instruccion = (f"{prompt}\n\nResponde SOLO con un objeto JSON que cumpla este esquema JSON:\n"
                   f"{json.dumps(esquema.model_json_schema(), ensure_ascii=False)}")
    errores = []
    for intento in range(2):
        candidatos = r.disponibles(modelos)
        if not candidatos:
            break
        for modelo in candidatos:
            if r.sin_credito and not modelo.endswith(":free"):
                continue
            try:
                contenido, costo = _llamar(r, modelo, sistema, instruccion, razonar=razonar)
                r.sumar(modelo, costo)
                obj = esquema.model_validate(_extraer_json(contenido))
                if validar and (problema := validar(obj)):
                    raise _Falla("formato", f"no pasa la validación: {problema}")
            except _Falla as f:
                errores.append(f"{modelo}: {f.tipo} {str(f)[:160]}")
                if f.tipo == "credito":
                    r.sin_credito = True
                    print(f"      · sin crédito para modelos de pago: {str(f)[:120]}")
                elif f.tipo == "sin_json_mode":
                    with r.lock:
                        r.estado["sin_json_mode"].append(modelo)
                        r.guardar()
                elif f.tipo == "dia":
                    r.apartar(modelo, _hasta_manana(), str(f))
                elif f.tipo == "modelo":
                    r.apartar(modelo, 6 * 3600, str(f))
                elif f.tipo == "lento":
                    r.apartar(modelo, 15 * 60, str(f))
                elif f.tipo == "temporal":
                    time.sleep(min(30, 5 * (intento + 1)))
                continue
            except (ValueError, ValidationError) as e:
                errores.append(f"{modelo}: formato {str(e)[:160]}")
                continue
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps({"tarea": tarea, "modelo": modelo, "fecha": time.strftime("%Y-%m-%d %H:%M"),
                                        "respuesta": obj.model_dump()}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
            return obj
        if not any(" temporal " in e for e in errores):
            break
    raise SinModelos(f"{tarea}: ningún modelo respondió bien. Últimos errores: " + " | ".join(errores[-4:]))


URL_EMBEDDINGS = "https://openrouter.ai/api/v1/embeddings"


def pedir_embeddings(dir_cache: Path, textos: list[str], modelo: str) -> list[list[float]]:
    """Vectores de `textos` con un modelo de embeddings de OpenRouter (una llamada; el gasto cuenta para
    LLM_TOPE_USD y se respeta el saldo mínimo de la clave). Sin caché propia: la lleva `vectores.py`."""
    r = _router(dir_cache)
    gratis = modelo.endswith(":free")
    if not gratis and (r.estado["gasto_usd"] >= TOPE_USD or not r.saldo_ok()):
        raise SinModelos(f"embeddings: tope de gasto (US$ {TOPE_USD}) o saldo mínimo de la clave alcanzado")
    errores = []
    for intento in range(4):
        r.esperar_turno(modelo)
        try:
            resp = requests.post(URL_EMBEDDINGS, timeout=(15, 180), json={"model": modelo, "input": textos},
                                 headers={"Authorization": f"Bearer {config.env('OPENROUTER_API_KEY', '')}",
                                          "HTTP-Referer": "https://github.com/frr05/MT-v3",
                                          "X-Title": "MT-v3 documento unico"})
            datos = resp.json()
        except (requests.RequestException, ValueError) as e:
            errores.append(str(e)[:160])
            time.sleep(5 * (intento + 1))
            continue
        if resp.status_code != 200 or "error" in datos:
            errores.append(json.dumps(datos.get("error", datos), ensure_ascii=False)[:200])
            if resp.status_code in (402, 401, 400, 404):
                break
            time.sleep(5 * (intento + 1))
            continue
        r.sumar(modelo, float((datos.get("usage") or {}).get("cost") or 0))
        filas = sorted(datos["data"], key=lambda d: d["index"])
        if len(filas) != len(textos):
            errores.append(f"{len(filas)} vectores para {len(textos)} textos")
            continue
        return [d["embedding"] for d in filas]
    raise SinModelos(f"embeddings ({modelo}): " + " | ".join(errores[-3:]))


def pedir_vision(dir_cache: Path, prompt: str, png: bytes, modelos: list[str], max_tokens: int = 4096,
                 validar: Callable[[str], str | None] | None = None) -> tuple[str, str]:
    """Transcripción de una imagen con un modelo de visión (primero el de la lista que responda). Devuelve
    (texto, modelo). Caché por hash de (prompt, imagen); el gasto cuenta para el mismo tope LLM_TOPE_USD.
    `validar(texto)` devuelve un error o None: una respuesta inválida se guarda aparte (`*.rechazada.json`, por si
    una versión posterior del lector la acepta: no se vuelve a pagar) y se prueba el siguiente modelo."""
    import base64

    clave = hashlib.sha256(prompt.encode("utf-8") + hashlib.sha256(png).digest()).hexdigest()[:24]
    ruta = dir_cache / "vision" / f"{clave}.json"
    if ruta.exists():
        guardado = json.loads(ruta.read_text(encoding="utf-8"))
        if not validar or not validar(guardado["texto"]):
            return guardado["texto"], guardado["modelo"]
    rechazada = ruta.with_suffix(".rechazada.json")
    if validar and rechazada.exists():
        for previa in json.loads(rechazada.read_text(encoding="utf-8")):
            if not validar(previa["texto"]):
                ruta.write_text(json.dumps(previa, ensure_ascii=False, indent=1), encoding="utf-8")
                return previa["texto"], previa["modelo"]
    r = _router(dir_cache)
    partes = [{"type": "text", "text": prompt},
              {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]
    errores = []
    for modelo in modelos:
        gratis = modelo.endswith(":free")
        if not gratis and (r.estado["gasto_usd"] >= TOPE_USD or not r.saldo_ok()):
            errores.append(f"{modelo}: tope de gasto o saldo de la clave alcanzado")
            continue
        if r.estado["apartados"].get(modelo, {}).get("hasta", 0) > time.time():
            continue
        try:
            texto, costo = _llamar(r, modelo, "Eres un transcriptor literal de documentos escaneados.", partes,
                                   json_mode=False, max_tokens=max_tokens, plazo=300)
            r.sumar(modelo, costo)
            if validar and (problema := validar(texto)):
                previas = json.loads(rechazada.read_text(encoding="utf-8")) if rechazada.exists() else []
                rechazada.parent.mkdir(parents=True, exist_ok=True)
                rechazada.write_text(json.dumps(previas + [{"modelo": modelo, "costo": costo, "error": problema,
                                                            "texto": texto}], ensure_ascii=False, indent=1),
                                     encoding="utf-8")
                raise _Falla("formato", problema)
        except _Falla as f:
            errores.append(f"{modelo}: {f.tipo} {str(f)[:160]}")
            if f.tipo == "credito":
                r.sin_credito = True
            elif f.tipo in ("modelo", "dia"):
                r.apartar(modelo, 6 * 3600, str(f))     # una página lenta no aparta al modelo para las demás
            continue
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({"modelo": modelo, "fecha": time.strftime("%Y-%m-%d %H:%M"), "costo": costo,
                                    "texto": texto}, ensure_ascii=False, indent=1), encoding="utf-8")
        return texto, modelo
    raise SinModelos("visión: ningún modelo respondió. " + " | ".join(errores[-3:]))
