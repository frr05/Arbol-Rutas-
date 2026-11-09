"""Paso 2: marca las páginas mal leídas a partir de las métricas del OCR (no vuelve a leer los PDF).

Salidas en `<concesión>/revision/`:
  paginas_marcadas.csv  (separador `;`)  entrada del paso 3
  calidad_ocr.md        resumen por documento, distribución de la confianza y cuántas páginas se marcarían
                        con otros umbrales (para calibrar)
"""
from __future__ import annotations

import csv
import time

from .calidad import Umbrales, motivos
from .indice import Proyecto
from .ocr import leer_paginas, ocr_completo

COLUMNAS = ["documento", "pagina", "archivo", "motivos", "conf_media", "frac_baja", "frac_rara", "n_palabras",
            "tinta", "rotacion"]


def filas_calidad(p: Proyecto) -> list[dict]:
    filas = []
    for doc in p.documentos:
        if not ocr_completo(p, doc):
            print(f"  {doc.id}: OCR incompleto, se omite (corre primero `python main.py ocr`)")
            continue
        for pag in leer_paginas(p, doc):
            filas.append({"documento": doc.id, "pagina": pag["pagina"], "archivo": doc.archivo,
                          "rotacion": pag.get("rotacion", 0), **pag["calidad"]})
    return filas


def marcar(p: Proyecto, u: Umbrales) -> list[dict]:
    filas = filas_calidad(p)
    for f in filas:
        f["motivos"] = ",".join(motivos(f, u))
    marcadas = [f for f in filas if f["motivos"]]
    p.dir_revision.mkdir(parents=True, exist_ok=True)
    with (p.dir_revision / "paginas_marcadas.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS, delimiter=";", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(marcadas)
    (p.dir_revision / "calidad_ocr.md").write_text(_informe(p, filas, marcadas, u), encoding="utf-8")
    return marcadas


def leer_marcadas(p: Proyecto) -> list[dict]:
    ruta = p.dir_revision / "paginas_marcadas.csv"
    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        return [{**f, "pagina": int(f["pagina"])} for f in csv.DictReader(fh, delimiter=";")]


def _informe(p: Proyecto, filas: list[dict], marcadas: list[dict], u: Umbrales) -> str:
    md = [f"# Calidad del OCR · {p.nombre} ({p.id})", "",
          f"Generado: {time.strftime('%Y-%m-%d %H:%M')} · {len(filas)} páginas · **{len(marcadas)} marcadas**", "",
          "Umbrales usados (una página con tinta se marca si cumple cualquiera):", "",
          f"- confianza media < {u.conf_media}",
          f"- fracción de palabras con conf baja > {u.frac_baja}",
          f"- fracción de palabras que no parecen español > {u.frac_rara}",
          f"- menos de {u.min_palabras} palabras",
          f"- páginas con tinta < {u.tinta_min} se consideran en blanco y no se marcan", "",
          "## Por documento", "",
          "| Documento | Archivo | Págs. | Conf. media | Marcadas |", "|---|---|---:|---:|---:|"]
    por_doc: dict[str, list[dict]] = {}
    for f in filas:
        por_doc.setdefault(f["documento"], []).append(f)
    for doc_id, fs in por_doc.items():
        conf = sum(f["conf_media"] for f in fs) / len(fs)
        n_marc = sum(1 for f in fs if f["motivos"])
        md.append(f"| {doc_id} | {fs[0]['archivo']} | {len(fs)} | {conf:.2f} | {n_marc} |")

    md += ["", "## Distribución de la confianza media", "", "| Rango | Páginas | |", "|---|---:|---|"]
    tramos = [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.01]
    for a, b in zip(tramos, tramos[1:]):
        n = sum(1 for f in filas if a <= f["conf_media"] < b)
        md.append(f"| {a:.2f}–{min(b, 1):.2f} | {n} | {'█' * n} |")

    md += ["", "## Calibración: páginas marcadas según el umbral de confianza media", "",
           "(manteniendo fijos los demás umbrales)", "", "| conf_media < | Marcadas |", "|---:|---:|"]
    for umbral in (0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9):
        alt = Umbrales(**{**u.como_dict(), "conf_media": umbral})
        md.append(f"| {umbral:.2f} | {sum(1 for f in filas if motivos(f, alt))} |")

    md += ["", "## Páginas marcadas", "", "| Documento | Pág. | Motivos | Conf. | Baja | Rara | Palabras |",
           "|---|---:|---|---:|---:|---:|---:|"]
    for f in marcadas:
        md.append(f"| {f['documento']} | {f['pagina']} | {f['motivos']} | {f['conf_media']:.2f} | "
                  f"{f['frac_baja']:.2f} | {f['frac_rara']:.2f} | {f['n_palabras']} |")
    return "\n".join(md) + "\n"
