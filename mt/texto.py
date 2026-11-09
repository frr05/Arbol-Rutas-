"""Etapa 2: texto final de cada documento como una lista de líneas numeradas → `cache/texto/<doc>.json`.

Cada línea: {"i": índice en el documento, "pag", "texto", "conf", "fuente": ocr|vlm|texto_pdf, "parrafo", "marginal"}.
Los índices `i` son la clave de todo lo que sigue (fragmentos, relaciones, verificación): el LLM nunca escribe
texto, solo devuelve rangos de estas líneas.

- Fuente por página: el texto del modelo de visión si la página fue rescatada (cache/vlm) y no salió truncado;
  si no, el OCR. `revision/texto_fuente.csv` (documento;pagina;fuente) fuerza la fuente a mano.
- Párrafos: las líneas en blanco del texto de Tesseract (par_num); en texto sin esa información, una línea que
  termina en punto o dos puntos cierra el párrafo.
- Marginales (encabezados y pies repetidos, números de página, ruido): se marcan y no entran al cuerpo.
"""
from __future__ import annotations

import csv
import json
import re

from rapidfuzz import fuzz, process

from .indice import Documento, Proyecto
from .ocr import leer_paginas

_NUMERAL_INICIAL = re.compile(r"^\s*(?:\d{1,2}|[IL]{1,3})(?:\s?[.,]\s?\d{1,2})+[.)]?\s+\S")
_ANEXO = re.compile(r"^\s*(\S{1,2}\s*[.)]\s*)?(ANEXO|AP[EÉ]NDICE)\b", re.I)
_NUM_PAGINA = re.compile(r"\W*(p[aá]g(ina)?\.?\s*)?\d{1,3}(\s*(de|/)\s*\d{1,3})?\W*", re.I)
_FIN_PARRAFO = re.compile(r"[.:;]\s*[\"”»)]?\s*$")
_INICIO_ITEM = re.compile(r"^\s*([a-zA-Z]\)|[ivx]+\)|\d{1,3}[.)\-]|[•■▪●\-–])\s")


def ruta_texto(p: Proyecto, doc_id: str):
    return p.carpeta / "cache" / "texto" / f"{doc_id}.json"


def leer_lineas(p: Proyecto, doc_id: str) -> list[dict]:
    return json.loads(ruta_texto(p, doc_id).read_text(encoding="utf-8"))["lineas"]


def _fuentes_manuales(p: Proyecto) -> dict[tuple[str, int], str]:
    ruta = p.dir_revision / "texto_fuente.csv"
    if not ruta.exists():
        return {}
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        return {(f["documento"].strip(), int(f["pagina"])): f["fuente"].strip()
                for f in csv.DictReader(fh, delimiter=";") if f.get("pagina")}


def _forma(texto: str) -> str:
    return re.sub(r"[^a-záéíóúñ]", "", texto.lower())[:25]


def variedad(texto: str) -> float:
    """Fracción de secuencias de 6 palabras distintas (≈1 en texto normal, ≈0 en una salida en bucle)."""
    w = re.findall(r"\w+", texto.lower())
    grupos = [tuple(w[i:i + 6]) for i in range(max(0, len(w) - 5))]
    return len(set(grupos)) / len(grupos) if grupos else 1.0


def _lineas_pagina(pagina: dict, vlm: dict | None, fuente_manual: str | None) -> tuple[str, list[tuple[str, float | None, bool]]]:
    """(fuente, [(texto, conf, nuevo_parrafo)]) de una página."""
    # el texto del modelo de visión reemplaza al OCR si no salió truncado y no perdió más de la mitad de las
    # palabras (salvo que el OCR fuera basura: confianza media < 0,5)
    # (y si no entró en bucle repitiendo lo mismo, como pasa con tablas giradas)
    vlm_ok = vlm is not None and not vlm.get("truncado") and variedad(vlm["texto_vlm"]) >= 0.5 and (
        vlm.get("n_palabras_vlm", 0) >= 0.5 * pagina["calidad"]["n_palabras"] or pagina["calidad"]["conf_media"] < 0.5)
    usar_vlm = vlm is not None and (fuente_manual == "vlm" or (fuente_manual is None and vlm_ok))
    if usar_vlm:
        fuente, texto, confs = "vlm", vlm["texto_vlm"], None
    else:
        fuente, texto = pagina["fuente"], pagina["texto"]
        confs = [it["conf"] for it in pagina["items"]]
    salida, nuevo, k = [], True, 0
    hay_blancos = "\n\n" in texto
    for cruda in texto.split("\n"):
        linea = cruda.strip()
        if not linea:
            nuevo = True
            continue
        conf = confs[k] if confs and k < len(confs) else None
        k += 1
        if not hay_blancos and salida and not nuevo:
            # sin información de párrafos: corta tras punto final o antes de un ítem o numeral
            nuevo = bool(_FIN_PARRAFO.search(salida[-1][0]) or _INICIO_ITEM.match(linea) or _NUMERAL_INICIAL.match(linea))
        salida.append((linea, conf, nuevo))
        nuevo = False
    return fuente, salida


def construir(p: Proyecto, doc: Documento, manual: dict | None = None) -> dict:
    manual = manual if manual is not None else _fuentes_manuales(p)
    lineas: list[dict] = []
    parrafo = -1
    for pagina in leer_paginas(p, doc):
        n = pagina["pagina"]
        ruta_v = p.dir_vlm / doc.id / f"p{n:04d}.json"
        vlm = json.loads(ruta_v.read_text(encoding="utf-8")) if ruta_v.exists() else None
        fuente, filas = _lineas_pagina(pagina, vlm, manual.get((doc.id, n)))
        for j, (texto, conf, nuevo) in enumerate(filas):
            if nuevo or j == 0:
                parrafo += 1
            lineas.append({"i": len(lineas), "pag": n, "texto": texto, "conf": conf, "fuente": fuente,
                           "parrafo": parrafo, "marginal": False})
    _marcar_marginales(lineas)
    return {"documento": doc.id, "archivo": doc.archivo, "lineas": lineas}


def _marcar_marginales(lineas: list[dict]):
    # Un encabezado o pie se repite en buena parte del documento; un título que aparece en unas pocas páginas
    # (p. ej. "FORMULARIO DOCUMENTO N°5" en cada anexo) no es marginal.
    min_paginas = max(3, 0.2 * len({ln["pag"] for ln in lineas}))
    paginas_por_forma: dict[str, set[int]] = {}
    for ln in lineas:
        f = _forma(ln["texto"])
        if len(f) >= 3:
            paginas_por_forma.setdefault(f, set()).add(ln["pag"])
    def ruido(ln) -> bool:
        if ln["fuente"] == "vlm" and ln["texto"].lstrip().startswith("|"):
            return False                       # filas de tablas Markdown del modelo de visión (incluida |---|)
        alfanum = re.sub(r"[\W_]", "", ln["texto"])
        return len(alfanum) <= 2 and not _INICIO_ITEM.match(ln["texto"] + " ")

    # posición de cada línea en su página contando solo las que no son ruido (las manchas al pie de página
    # no deben impedir reconocer el pie)
    por_pag: dict[int, list[dict]] = {}
    for ln in lineas:
        if ruido(ln):
            ln["marginal"] = True
        else:
            por_pag.setdefault(ln["pag"], []).append(ln)
    # líneas de borde que se parecen (no solo son iguales) a líneas de borde de muchas otras páginas: el OCR lee
    # distinto el mismo timbre o encabezado en cada página ("CIRCULAR ACLARATORIA N°2" / "CIRCULAR MULARATORÍA N*2")
    borde = [ln for ls in por_pag.values() for j, ln in enumerate(ls) if j < 3 or j >= len(ls) - 3]
    formas = [_forma(ln["texto"]) for ln in borde]
    parecidas: dict[int, int] = {}
    if borde:
        matriz = process.cdist(formas, formas, scorer=fuzz.ratio, score_cutoff=75, workers=-1)
        for k, ln in enumerate(borde):
            if len(formas[k]) >= 8:
                parecidas[ln["i"]] = len({borde[m]["pag"] for m in matriz[k].nonzero()[0]})
    for ls in por_pag.values():
        for j, ln in enumerate(ls):
            t = ln["texto"]
            en_borde = j < 3 or j >= len(ls) - 3
            repetida = ((len(paginas_por_forma.get(_forma(t), ())) >= min_paginas
                         or parecidas.get(ln["i"], 0) >= min_paginas)
                        and not _NUMERAL_INICIAL.match(t) and not _ANEXO.match(t))
            mancha = len(t) <= 20 and ln["conf"] is not None and ln["conf"] < 0.5   # timbres y firmas
            if en_borde and (repetida or _NUM_PAGINA.fullmatch(t) or mancha):
                ln["marginal"] = True


def agrupar_parrafos(lineas: list[dict], unir_sin_punto: bool = False) -> list[list[dict]]:
    """Agrupa líneas (no marginales, en orden) en párrafos. Un párrafo cortado por un salto de página se une
    con el siguiente si no terminaba en punto y el siguiente empieza en minúscula. Con `unir_sin_punto` (texto
    nuevo citado en una circular) basta con que el anterior no termine en punto."""
    grupos: list[list[dict]] = []
    for ln in lineas:
        if grupos and ln["parrafo"] == grupos[-1][-1]["parrafo"]:
            grupos[-1].append(ln)
            continue
        previo = grupos[-1][-1] if grupos else None
        if previo and not _FIN_PARRAFO.search(previo["texto"]) and (
                unir_sin_punto or (ln["pag"] != previo["pag"] and ln["texto"][:1].islower())):
            grupos[-1].append(ln)
            continue
        grupos.append([ln])
    return grupos


def texto_documento(p: Proyecto, doc: Documento) -> dict:
    datos = construir(p, doc)
    ruta = ruta_texto(p, doc.id)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    ls = datos["lineas"]
    print(f"  {doc.id}: {len(ls)} líneas · {sum(l['marginal'] for l in ls)} marginales · "
          f"{len({l['parrafo'] for l in ls})} párrafos · vlm en {len({l['pag'] for l in ls if l['fuente'] == 'vlm'})} págs.")
    return datos
