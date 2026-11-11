"""Paso 3: rescate de las páginas marcadas con un modelo de visión (Qwen2-VL por defecto). Pensado para Colab con GPU.

Lee `revision/paginas_marcadas.csv`, renderiza cada página del PDF y le pide al modelo la transcripción literal.
Guarda `cache/vlm/<doc>/pNNNN.json` con el texto del modelo JUNTO al texto del OCR original (no reemplaza nada)
y `comparar()` arma `revision/comparacion_vlm.md` / `.csv` para revisarlos lado a lado.
"""
from __future__ import annotations

import csv
import difflib
import html
import json
import re
import time
from pathlib import Path

from . import config
from .calidad import metricas
from .indice import Proyecto
from .marcar import leer_marcadas
from .ocr import renderizar, ruta_pagina

PROMPT = (
    "Transcribe literalmente todo el texto de esta página escaneada de un documento legal chileno "
    "(bases de licitación, circulares o decretos de una concesión de obras públicas), en orden de lectura. "
    "No resumas, no traduzcas, no corrijas la redacción ni agregues comentarios. Conserva los saltos de línea, "
    "la numeración de artículos y numerales (por ejemplo 1.6.42.1) y los montos tal como aparecen. "
    "Si hay una tabla, escribe cada fila en una línea separando las celdas con ' | '. "
    "Si una palabra es ilegible escribe [ilegible]. Responde solo con el texto de la página."
)
VERSION_PROMPT = 1


def ruta_vlm(p: Proyecto, doc_id: str, n: int) -> Path:
    return p.dir_vlm / doc_id / f"p{n:04d}.json"


class ModeloVLM:
    """Carga perezosa del modelo con transformers. GPU con bf16 (Ampere+), fp16 (T4) o CPU fp32."""

    def __init__(self, nombre: str):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self.torch = torch
        if torch.cuda.is_available():
            # La T4 de Colab (capacidad 7.5) no tiene bf16 nativo: ahí se usa fp16.
            dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
            mapa = "auto"
        else:
            dtype, mapa = torch.float32, "cpu"
            print("  ⚠ sin GPU: el modelo corre en CPU (muy lento, solo sirve para probar)")
        print(f"  cargando {nombre} ({str(dtype).removeprefix('torch.')}, {mapa})…")
        self.nombre = nombre
        self.modelo = AutoModelForImageTextToText.from_pretrained(nombre, torch_dtype=dtype, device_map=mapa)
        self.procesador = AutoProcessor.from_pretrained(nombre, min_pixels=256 * 28 * 28,
                                                        max_pixels=config.VLM_MAX_PIXELS)

    def transcribir(self, imagen) -> tuple[str, int]:
        mensajes = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": PROMPT}]}]
        texto = self.procesador.apply_chat_template(mensajes, tokenize=False, add_generation_prompt=True)
        entradas = self.procesador(text=[texto], images=[imagen], return_tensors="pt").to(self.modelo.device)
        with self.torch.inference_mode():
            salida = self.modelo.generate(**entradas, max_new_tokens=config.VLM_MAX_TOKENS, do_sample=False,
                                          repetition_penalty=1.05)
        nuevos = salida[:, entradas["input_ids"].shape[1]:]
        return self.procesador.batch_decode(nuevos, skip_special_tokens=True)[0].strip(), int(nuevos.shape[1])


class ModeloOpenRouter:
    """Misma interfaz que ModeloVLM, pero con modelos de visión de OpenRouter (sin GPU local). Usa la caché y el
    tope de gasto de llm.py. Modelos en VLM_OPENROUTER (el primero que responda)."""

    def __init__(self, dir_cache: Path):
        self.dir_cache = dir_cache
        self.modelos = [m.strip() for m in config.env(
            "VLM_OPENROUTER", "qwen/qwen3-vl-30b-a3b-instruct,google/gemini-2.5-flash-lite").split(",") if m.strip()]
        self.nombre = "openrouter:" + self.modelos[0]
        self.ultimo_modelo = None

    def transcribir_con_modelo(self, imagen) -> tuple[str, int, str]:
        import io

        from . import llm

        buf = io.BytesIO()
        imagen.convert("L").save(buf, format="PNG", optimize=True)
        texto, modelo = llm.pedir_vision(self.dir_cache, PROMPT, buf.getvalue(), self.modelos)
        return texto.strip(), len(texto) // 4, modelo

    def transcribir(self, imagen) -> tuple[str, int]:
        texto, tokens, self.ultimo_modelo = self.transcribir_con_modelo(imagen)
        return texto, tokens


class ModeloMeta:
    """Visión con la API de Meta (Muse Spark, `mt/meta.py`). Misma interfaz que ModeloOpenRouter; además informa
    si la salida quedó truncada según `finish_reason` (no por el largo del texto)."""

    def __init__(self, dir_cache: Path):
        from . import meta

        self.dir_cache = dir_cache
        self.nombre = meta.nombre()

    def transcribir_meta(self, imagen) -> tuple[str, int, str, bool]:
        import io

        from . import meta

        buf = io.BytesIO()
        imagen.convert("L").save(buf, format="PNG", optimize=True)
        r = meta.pedir(self.dir_cache, PROMPT, buf.getvalue())
        uso = r.get("usage") or {}
        visibles = (uso.get("completion_tokens") or 0) - ((uso.get("completion_tokens_details") or {})
                                                          .get("reasoning_tokens") or 0)
        return r["texto"], visibles or len(r["texto"]) // 4, r["modelo"], bool(r.get("truncado"))

    def transcribir_con_modelo(self, imagen) -> tuple[str, int, str]:
        texto, tokens, modelo, _ = self.transcribir_meta(imagen)
        return texto, tokens, modelo

    def transcribir(self, imagen) -> tuple[str, int]:
        texto, tokens, _, _ = self.transcribir_meta(imagen)
        return texto, tokens


def _palabras(texto: str) -> list[str]:
    return re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", texto.lower())


def similitud(a: str, b: str) -> float:
    """Parecido entre dos textos por palabras (0–1), ignorando mayúsculas, puntuación y saltos de línea."""
    return round(difflib.SequenceMatcher(None, _palabras(a), _palabras(b), autojunk=False).ratio(), 3)


def rescatar(p: Proyecto, doc_ids: set[str] | None = None, modelo: str | None = None, forzar: bool = False,
             limite: int | None = None, vlm: ModeloVLM | None = None, paginas: list[dict] | None = None) -> int:
    """Rescata las páginas marcadas pendientes (o las de `paginas`: [{documento, pagina, motivos}], p. ej. las
    páginas con tablas). `vlm` permite reutilizar un modelo ya cargado (Colab) o usar OpenRouter."""
    nombre = vlm.nombre if vlm else (modelo or config.VLM_MODELO)
    marcadas = [f for f in (paginas if paginas is not None else leer_marcadas(p))
                if not doc_ids or f["documento"] in doc_ids]
    if not marcadas:
        print(f"{p.id}: no hay páginas marcadas (corre primero `python main.py marcar`)")
        return 0

    def hecha(f) -> bool:
        ruta = ruta_vlm(p, f["documento"], f["pagina"])
        return ruta.exists() and json.loads(ruta.read_text(encoding="utf-8")).get("modelo") == nombre

    pendientes = [f for f in marcadas if forzar or not hecha(f)][:limite]
    print(f"{p.id}: {len(marcadas)} páginas marcadas · {len(pendientes)} pendientes con {nombre}")
    if not pendientes:
        return 0

    vlm = vlm or ModeloVLM(nombre)
    inicio_total = time.time()
    # pdfium no es seguro entre hilos: se renderiza todo aquí; con OpenRouter las llamadas van en paralelo
    trabajos = []
    for f in pendientes:
        doc = p.doc(f["documento"])
        n = f["pagina"]
        ocr = json.loads(ruta_pagina(p, doc.id, n).read_text(encoding="utf-8"))
        imagen = renderizar(doc.ruta, n, config.VLM_DPI)
        if ocr.get("rotacion"):
            imagen = imagen.rotate(-ocr["rotacion"], expand=True)
        trabajos.append((f, doc, n, ocr, imagen))

    remoto = isinstance(vlm, (ModeloOpenRouter, ModeloMeta))

    def transcribir(trabajo):
        inicio = time.time()
        imagen = trabajo[4] if remoto else trabajo[4].convert("RGB")
        truncado = None
        if isinstance(vlm, ModeloMeta):
            texto, tokens, usado, truncado = vlm.transcribir_meta(imagen)
        elif isinstance(vlm, ModeloOpenRouter):
            texto, tokens, usado = vlm.transcribir_con_modelo(imagen)
        else:
            (texto, tokens), usado = vlm.transcribir(imagen), nombre
        if truncado is None:
            truncado = tokens >= config.VLM_MAX_TOKENS
        return texto, tokens, usado, round(time.time() - inicio, 1), truncado

    hilos = config.env_int("VLM_HILOS", 6) if remoto else 1
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(hilos) as pool:
        futuros = [pool.submit(transcribir, t) for t in trabajos]
        for i, (trabajo, fut) in enumerate(zip(trabajos, futuros), 1):
            f, doc, n, ocr, _ = trabajo
            try:
                texto, tokens, usado, segundos, truncado = fut.result()
            except Exception as e:                      # sin modelos disponibles, tope de gasto, etc.
                print(f"  [{i}/{len(pendientes)}] {doc.id} p{n:04d} · sin transcripción: {str(e)[:120]}")
                continue
            cal_vlm = metricas([(t, 1.0) for t in texto.split()], 1.0)
            resultado = {
                "documento": doc.id, "pagina": n, "archivo": doc.archivo,
                "modelo": nombre, "modelo_usado": usado,
                "version_prompt": VERSION_PROMPT, "dpi": config.VLM_DPI,
                "max_pixels": config.VLM_MAX_PIXELS, "tokens": tokens, "segundos": segundos,
                "fecha": time.strftime("%Y-%m-%d %H:%M"),
                "motivos": f["motivos"], "calidad_ocr": ocr["calidad"],
                "n_palabras_vlm": cal_vlm["n_palabras"], "frac_rara_vlm": cal_vlm["frac_rara"],
                "similitud": similitud(ocr["texto"], texto),
                "truncado": truncado,
                "texto_ocr": ocr["texto"],
                "texto_vlm": texto,
            }
            ruta = ruta_vlm(p, doc.id, n)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  [{i}/{len(pendientes)}] {doc.id} p{n:04d} · {tokens} tokens · {segundos}s · {usado} · "
                  f"similitud con OCR {resultado['similitud']:.2f}{' · ⚠ truncado' if resultado['truncado'] else ''}",
                  flush=True)
    print(f"⏱ {(time.time() - inicio_total) / 60:.1f} min")
    comparar(p)
    return len(pendientes)


def comparar(p: Proyecto) -> Path:
    """Informe lado a lado de todas las páginas rescatadas: revision/comparacion_vlm.md y .csv."""
    resultados = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(p.dir_vlm.glob("*/p*.json"))]
    p.dir_revision.mkdir(parents=True, exist_ok=True)
    columnas = ["documento", "pagina", "motivos", "conf_ocr", "n_palabras_ocr", "n_palabras_vlm", "frac_rara_ocr",
                "frac_rara_vlm", "similitud", "truncado", "modelo", "segundos"]
    with (p.dir_revision / "comparacion_vlm.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columnas, delimiter=";", lineterminator="\n")
        w.writeheader()
        for r in resultados:
            c = r["calidad_ocr"]
            w.writerow({**{k: r[k] for k in ("documento", "pagina", "motivos", "n_palabras_vlm", "frac_rara_vlm",
                                             "similitud", "truncado", "modelo", "segundos")},
                        "conf_ocr": c["conf_media"], "n_palabras_ocr": c["n_palabras"], "frac_rara_ocr": c["frac_rara"]})

    md = [f"# OCR vs modelo de visión · {p.nombre} ({p.id})", "",
          f"{len(resultados)} páginas rescatadas. *Similitud* = parecido por palabras entre ambos textos (0–1): "
          "baja similitud con conf. OCR baja suele indicar que el modelo recuperó texto que el OCR no leyó; "
          "revisar contra el PDF antes de confiar en cualquiera de los dos.", ""]
    for r in resultados:
        c = r["calidad_ocr"]
        md += [f"## {r['documento']} · página {r['pagina']}", "",
               f"`{r['archivo']}` · motivos: {r['motivos']} · conf. OCR {c['conf_media']:.2f} · "
               f"palabras OCR/VLM {c['n_palabras']}/{r['n_palabras_vlm']} · "
               f"rara OCR/VLM {c['frac_rara']:.2f}/{r['frac_rara_vlm']:.2f} · similitud {r['similitud']:.2f} · "
               f"{r['modelo']}{' · ⚠ salida truncada' if r['truncado'] else ''}", "",
               "<table><tr><th>OCR (Tesseract)</th><th>Modelo de visión</th></tr><tr>",
               f"<td valign=\"top\"><pre>{html.escape(r['texto_ocr'])}</pre></td>",
               f"<td valign=\"top\"><pre>{html.escape(r['texto_vlm'])}</pre></td>",
               "</tr></table>", ""]
    ruta = p.dir_revision / "comparacion_vlm.md"
    ruta.write_text("\n".join(md) + "\n", encoding="utf-8")
    return ruta
