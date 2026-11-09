"""Paso 1: OCR con Tesseract de los PDF escaneados. Una página = un archivo `cache/ocr/<doc>/pNNNN.json`.

Mismo formato de página que doc-unico-v2 (items `linea` con bbox en píxeles a OCR_DPI y conf 0–1), más:
  "texto":   texto completo de la página (líneas unidas con \n, línea en blanco entre párrafos)
  "calidad": métricas para detectar páginas mal leídas (ver calidad.py)
`_ocr.json` registra sha256 del PDF y parámetros: un documento ya hecho no se repite salvo --forzar.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from . import config
from .calidad import metricas
from .indice import Documento, Proyecto

os.environ.setdefault("OMP_THREAD_LIMIT", "1")   # un hilo por proceso de Tesseract; el paralelismo es por página
if config.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

PARAMETROS_OCR = {"motor": "tesseract", "idioma": config.OCR_IDIOMA, "psm": config.OCR_PSM, "dpi": config.OCR_DPI}
VERSION_CODIGO = 1


def ruta_pagina(p: Proyecto, doc_id: str, n: int) -> Path:
    return p.dir_ocr / doc_id / f"p{n:04d}.json"


def ruta_registro(p: Proyecto, doc_id: str) -> Path:
    return p.dir_ocr / doc_id / "_ocr.json"


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def leer_registro(p: Proyecto, doc: Documento) -> dict | None:
    ruta = ruta_registro(p, doc.id)
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def leer_paginas(p: Proyecto, doc: Documento) -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted((p.dir_ocr / doc.id).glob("p*.json"))]


def ocr_completo(p: Proyecto, doc: Documento) -> bool:
    reg = leer_registro(p, doc)
    return bool(reg) and all(ruta_pagina(p, doc.id, n).exists() for n in range(1, reg["paginas"] + 1))


def renderizar(ruta_pdf: Path, n: int, dpi: int) -> Image.Image:
    """Página n (desde 1) del PDF como imagen en escala de grises."""
    pdf = pdfium.PdfDocument(ruta_pdf)
    try:
        return pdf[n - 1].render(scale=dpi / 72, grayscale=True).to_pil().convert("L")
    finally:
        pdf.close()


def _tinta(img: Image.Image) -> float:
    hist = img.histogram()
    return sum(hist[:128]) / max(1, sum(hist))


def _tesseract(img: Image.Image) -> dict:
    """Líneas (items), texto y palabras con su confianza, agrupando las palabras de Tesseract por línea."""
    datos = pytesseract.image_to_data(img, lang=config.OCR_IDIOMA, config=f"--psm {config.OCR_PSM}",
                                      output_type=pytesseract.Output.DICT)
    lineas: dict[tuple, dict] = {}
    for i, palabra in enumerate(datos["text"]):
        palabra = (palabra or "").strip()
        conf = float(datos["conf"][i])
        if not palabra or conf < 0:
            continue
        clave = (datos["block_num"][i], datos["par_num"][i], datos["line_num"][i])
        x0, y0 = datos["left"][i], datos["top"][i]
        x1, y1 = x0 + datos["width"][i], y0 + datos["height"][i]
        ln = lineas.setdefault(clave, {"palabras": [], "bbox": [x0, y0, x1, y1]})
        ln["palabras"].append((palabra, conf / 100))
        b = ln["bbox"]
        ln["bbox"] = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]

    items, partes, palabras, parrafo_anterior = [], [], [], None
    for (bloque, parrafo, _), ln in lineas.items():          # el dict conserva el orden de lectura de Tesseract
        texto = " ".join(t for t, _ in ln["palabras"])
        conf = sum(c for _, c in ln["palabras"]) / len(ln["palabras"])
        items.append({"tipo": "linea", "texto": texto, "bbox": [float(v) for v in ln["bbox"]], "conf": round(conf, 3)})
        if parrafo_anterior is not None and (bloque, parrafo) != parrafo_anterior:
            partes.append("")
        partes.append(texto)
        parrafo_anterior = (bloque, parrafo)
        palabras.extend(ln["palabras"])
    return {"items": items, "texto": "\n".join(partes), "palabras": palabras}


def _rotacion_sugerida(img: Image.Image) -> int:
    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return 0                                  # muy poco texto para detectar la orientación
    return int(osd.get("rotate", 0)) if float(osd.get("orientation_conf", 0)) > 2 else 0


def ocr_imagen(img: Image.Image) -> dict:
    """OCR de una página. Si sale mal, prueba la orientación que sugiere Tesseract y se queda con la mejor."""
    inicio = time.time()
    tinta = _tinta(img)
    res = _tesseract(img)
    cal = metricas(res["palabras"], tinta)
    rotacion = 0
    if tinta >= config.TINTA_MIN and cal["conf_media"] < config.UMBRAL_CONF_MEDIA:
        giro = _rotacion_sugerida(img)
        if giro:
            res_g = _tesseract(img.rotate(-giro, expand=True))
            cal_g = metricas(res_g["palabras"], tinta)
            if cal_g["conf_media"] > cal["conf_media"]:
                res, cal, rotacion = res_g, cal_g, giro
    return {"fuente": "ocr", "motor": "tesseract", "ancho": img.width, "alto": img.height, "rotacion": rotacion,
            "items": res["items"], "texto": res["texto"], "calidad": cal, "segundos": round(time.time() - inicio, 1)}


def _pagina_con_texto(pdf: pdfium.PdfDocument, n: int) -> dict | None:
    """Si la página ya trae capa de texto (PDF digital), se usa tal cual con confianza 1."""
    texto = pdf[n - 1].get_textpage().get_text_range().replace("\r\n", "\n").strip()
    if len(texto) < config.OCR_MIN_CHARS_TEXTO:
        return None
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    palabras = [(t, 1.0) for l in lineas for t in l.split()]
    return {"fuente": "texto_pdf", "motor": "pdfium", "rotacion": 0,
            "items": [{"tipo": "linea", "texto": l, "bbox": None, "conf": 1.0} for l in lineas],
            "texto": "\n".join(lineas), "calidad": metricas(palabras, 1.0), "segundos": 0.0}


def ocr_documento(p: Proyecto, doc: Documento, forzar: bool = False, paginas: list[int] | None = None) -> int:
    """OCR de un documento (o de algunas páginas). Devuelve cuántas páginas se procesaron."""
    carpeta = p.dir_ocr / doc.id
    sha = _sha256(doc.ruta)
    reg = leer_registro(p, doc)
    if reg and not forzar:
        if reg.get("sha256") != sha:
            print(f"  {doc.id}: el PDF cambió desde el último OCR; usa --forzar para rehacerlo")
            return 0
        distintos = {k: (reg.get(k), v) for k, v in PARAMETROS_OCR.items() if reg.get(k) != v}
        if distintos:
            print(f"  {doc.id}: parámetros distintos al OCR guardado {distintos}; usa --forzar para rehacerlo")
        if ocr_completo(p, doc) and not paginas:
            print(f"  {doc.id}: OCR ya hecho ({reg['paginas']} págs.), se omite")
            return 0
    if forzar and carpeta.exists() and not paginas:
        for f in carpeta.glob("p*.json"):
            f.unlink()
    carpeta.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(doc.ruta)
    total = len(pdf)
    pendientes = [n for n in (paginas or range(1, total + 1))
                  if (forzar and paginas) or not ruta_pagina(p, doc.id, n).exists()]
    print(f"  {doc.id}: {len(pendientes)}/{total} págs. pendientes · {doc.archivo}")

    def guardar(n: int, pagina: dict):
        pagina = {"documento": doc.id, "pagina": n, "dpi": config.OCR_DPI, **pagina}
        tmp = ruta_pagina(p, doc.id, n).with_suffix(".tmp")
        tmp.write_text(json.dumps(pagina, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ruta_pagina(p, doc.id, n))
        c = pagina["calidad"]
        print(f"    p{n:04d} {pagina['fuente']:<9} {len(pagina['items']):>3} líneas · conf {c['conf_media']:.2f}"
              f" · baja {c['frac_baja']:.2f} · rara {c['frac_rara']:.2f}"
              f"{' · rot ' + str(pagina['rotacion']) if pagina['rotacion'] else ''} · {pagina['segundos']}s")

    # pdfium no es seguro entre hilos: se renderiza aquí, en orden, y solo Tesseract corre en paralelo
    # (con un máximo de imágenes en memoria).
    with ThreadPoolExecutor(config.OCR_HILOS) as pool:
        en_curso: list = []
        for n in pendientes:
            if (pagina := _pagina_con_texto(pdf, n)) is not None:
                guardar(n, pagina)
                continue
            img = pdf[n - 1].render(scale=config.OCR_DPI / 72, grayscale=True).to_pil().convert("L")
            en_curso.append((n, pool.submit(ocr_imagen, img)))
            if len(en_curso) >= 2 * config.OCR_HILOS:
                n0, fut = en_curso.pop(0)
                guardar(n0, fut.result())
        for n0, fut in en_curso:
            guardar(n0, fut.result())
    pdf.close()

    registro = {"documento": doc.id, "archivo": doc.archivo, "sha256": sha, "paginas": total, **PARAMETROS_OCR,
                "version_tesseract": str(pytesseract.get_tesseract_version()), "version_codigo": VERSION_CODIGO,
                "fecha": time.strftime("%Y-%m-%d %H:%M")}
    ruta_registro(p, doc.id).write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(pendientes)
