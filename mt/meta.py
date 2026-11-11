"""Visión con la API de Meta (Muse Spark), separada de OpenRouter. El resto del LLM sigue en `llm.py`.

- `VISION_PROVEEDOR=meta` hace que `vlm` y `tablas` usen este cliente; `VISION_META_MODELO` elige el modelo
  (por defecto `muse-spark-1.3-contributor`) y `MODEL_API_KEY` es la clave.
- Spark siempre razona: `SPARK_REASONING` (minimal por defecto) y se suman 4096 tokens de margen a `max_tokens`,
  porque el razonamiento cuenta dentro de ese tope. Si aun así sale truncada, se reintenta una vez con el doble.
- Caché propia en `cache/llm/vision_meta/` por hash de (modelo, esfuerzo, prompt, imagen): repetir no vuelve a pagar.
  Una respuesta que no pasa `validar` se guarda en `*.rechazada.json` y no se reintenta con otro modelo.
- El costo se calcula desde `usage` (US$ por 1M tokens: `SPARK_USD_1M_IN` / `SPARK_USD_1M_OUT`) y suma al mismo
  tope `LLM_TOPE_USD` y al registro de uso de `llm.py`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable

import requests

from . import config, llm

URL = config.env("META_URL", "https://api.meta.ai/v1") + "/chat/completions"
MODELO = config.env("VISION_META_MODELO", "muse-spark-1.3-contributor")
ESFUERZO = config.env("SPARK_REASONING", "minimal")
MARGEN_RAZONAMIENTO = config.env_int("SPARK_MARGEN_TOKENS", 4096)
PRECIO_IN = config.env_float("SPARK_USD_1M_IN", 0.10)
PRECIO_OUT = config.env_float("SPARK_USD_1M_OUT", 0.20)
RPM = config.env_int("META_RPM", 120)
SISTEMA = "Eres un transcriptor literal de documentos escaneados."      # el mismo de llm.pedir_vision

_lock = threading.Lock()
_proximo = 0.0


def activo() -> bool:
    return config.env("VISION_PROVEEDOR", "openrouter").lower() == "meta"


def nombre() -> str:
    return f"meta:{MODELO}"


def _esperar_turno():
    global _proximo
    with _lock:
        ahora = time.time()
        espera = _proximo - ahora
        _proximo = max(ahora, _proximo) + 60 / RPM
    if espera > 0:
        time.sleep(espera)


def _llamar(prompt: str, png: bytes, max_tokens: int) -> dict:
    clave = config.env("MODEL_API_KEY", "") or config.env("META_API_KEY", "")
    if not clave:
        raise llm.SinModelos("falta MODEL_API_KEY (Meta) en .env")
    cuerpo = {"model": MODELO, "temperature": 0, "max_tokens": max_tokens, "reasoning_effort": ESFUERZO,
              "messages": [{"role": "system", "content": SISTEMA},
                           {"role": "user", "content": [
                               {"type": "text", "text": prompt},
                               {"type": "image_url",
                                "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}
    error = ""
    for intento in range(6):
        _esperar_turno()
        inicio = time.time()
        try:
            r = requests.post(URL, json=cuerpo, timeout=(15, 600), headers={"Authorization": f"Bearer {clave}"})
        except requests.RequestException as e:
            error = str(e)[:200]
            time.sleep(5 * (intento + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            error = f"HTTP {r.status_code}: {r.text[:200]}"
            try:
                pausa = float(r.headers.get("retry-after") or 0)
            except ValueError:
                pausa = 0
            time.sleep(max(pausa, 5 * (intento + 1)))
            continue
        if r.status_code != 200:
            raise llm.SinModelos(f"Meta HTTP {r.status_code}: {r.text[:300]}")
        datos = r.json()
        eleccion = (datos.get("choices") or [{}])[0]
        contenido = (eleccion.get("message") or {}).get("content") or ""
        if isinstance(contenido, list):
            contenido = "".join(c.get("text", "") for c in contenido if isinstance(c, dict))
        uso = datos.get("usage") or {}
        costo = (uso.get("prompt_tokens", 0) * PRECIO_IN + uso.get("completion_tokens", 0) * PRECIO_OUT) / 1e6
        return {"texto": contenido, "finish_reason": eleccion.get("finish_reason"), "usage": uso,
                "costo": round(costo, 6), "segundos": round(time.time() - inicio, 1)}
    raise llm.SinModelos(f"Meta: sin respuesta tras 6 intentos: {error}")


def pedir(dir_cache: Path, prompt: str, png: bytes, max_tokens: int = 4096,
          validar: Callable[[str], str | None] | None = None) -> dict:
    """Transcripción de una imagen con Spark. Devuelve {texto, modelo, truncado, usage, costo, segundos, de_cache}."""
    clave = hashlib.sha256("\x00".join([MODELO, ESFUERZO, prompt]).encode("utf-8")
                           + hashlib.sha256(png).digest()).hexdigest()[:24]
    ruta = dir_cache / "vision_meta" / f"{clave}.json"
    if ruta.exists():
        guardado = json.loads(ruta.read_text(encoding="utf-8"))
        if not validar or not validar(guardado["texto"]):
            return {**guardado, "de_cache": True}
    rechazada = ruta.with_suffix(".rechazada.json")

    r = llm._router(dir_cache)
    if r.estado["gasto_usd"] >= llm.TOPE_USD:
        raise llm.SinModelos(f"tope de gasto alcanzado (US$ {llm.TOPE_USD})")
    tope = max_tokens + MARGEN_RAZONAMIENTO
    resp = None
    for _ in range(2):                                  # una vez más con el doble si sale truncada
        resp = _llamar(prompt, png, tope)
        r.sumar(nombre(), resp["costo"])
        llm.registrar_uso(nombre(), resp["usage"], resp["costo"])
        if resp["finish_reason"] != "length":
            break
        tope *= 2
    texto = resp["texto"].strip()
    guardado = {"modelo": nombre(), "reasoning_effort": ESFUERZO, "fecha": time.strftime("%Y-%m-%d %H:%M"),
                "costo": resp["costo"], "usage": resp["usage"], "finish_reason": resp["finish_reason"],
                "segundos": resp["segundos"], "truncado": resp["finish_reason"] == "length", "texto": texto}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if validar and (problema := validar(texto)):
        previas = json.loads(rechazada.read_text(encoding="utf-8")) if rechazada.exists() else []
        rechazada.write_text(json.dumps(previas + [{**guardado, "error": problema}], ensure_ascii=False, indent=1),
                             encoding="utf-8")
        raise llm.SinModelos(f"Meta: respuesta inválida: {problema}")
    ruta.write_text(json.dumps(guardado, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**guardado, "de_cache": False}


def pedir_vision(dir_cache: Path, prompt: str, png: bytes, max_tokens: int = 4096,
                 validar: Callable[[str], str | None] | None = None) -> tuple[str, str]:
    """Misma firma de retorno que `llm.pedir_vision`: (texto, modelo)."""
    r = pedir(dir_cache, prompt, png, max_tokens, validar)
    return r["texto"], r["modelo"]
