"""Etapa 5: corta cada acto (salvo las Bases) en fragmentos → `cache/fragmentos/<doc>.json`.

Un fragmento empieza en un párrafo que abre con un numeral de punto ("1.", "2.-", "a)"), VISTOS, CONSIDERANDO,
DECRETO:, RESUELVO:, ARTÍCULO, CLÁUSULA o ANEXO (regex de doc-unico-v2), y no supera ~MAX_LINEAS líneas.
Cada fragmento lleva:
  refs   numerales de las Bases que cita (solo los que existen en la estructura)
  verbo  si contiene una instrucción ("se reemplaza", "sustitúyese", "agrégase", "elimínase", "donde dice"…)
  ajeno  si está en una página del Diario Oficial dentro de una publicación que no trata de la concesión
"""
from __future__ import annotations

import json
import re

from .actos import acto_de_linea
from .bases import documento_bases, leer_estructura
from .indice import Documento, Proyecto
from .numerales import citas
from .texto import leer_lineas

MAX_LINEAS = 60
# Encabezados que siempre abren un fragmento
RE_BLOQUE = re.compile(
    r"^\s*(VISTOS?|CONSIDERANDO|TENIENDO PRESENTE|DECRETO\s*:|RESUELVO\s*:|D\s*E\s*C\s*R\s*E\s*T\s*O|"
    r"R\s*E\s*S\s*U\s*E\s*L\s*V\s*O|ART[IÍ]CULO\s+\S+|CL[AÁ]USULA|ANEXO|PREGUNTA|[IVX]+\s?[.)]-?\s)", re.I)
# Puntos numerados ("1. En 1.4.1 …", "2.- Se reemplaza …"): abren fragmento solo si siguen la secuencia del acto
# (1, 2, 3…); así las definiciones o ítems numerados dentro de un texto citado ("3.- Área de Concesión…") no cortan.
RE_PUNTO = re.compile(r"^\s*(\d{1,2})\s?(?:[.)°º]-?|-)\s*\S")
# Punto de instrucción de una circular ("9. En 1.11.5 Aspectos…"): corta aunque el OCR haya leído mal el número
RE_PUNTO_EN = re.compile(r"^\s*\S{1,3}\s?[.)°º,-]+\s*En[\s,]+\S{0,2}\d{1,2}[.,]\d")
RE_INSTRUCCION = re.compile(
    r"\b(reempl[aá]z|sustit[uú]y|modif[ií]c|agr[eé]g|incorp[oó]r|elim[ií]n|supr[ií]m|der[oó]g|intercal|"
    r"donde\s+dice|debe\s+decir|rectific)\w*", re.I)
_RE_PUBLICACION = re.compile(r"^\s*N[uú]m[.,]?\s*\d[\d.]*\s*[.\-]", re.I)
_GENERICAS = {"concesion", "concesión", "primera", "segunda", "tercera", "tramo", "ruta", "camino", "internacional",
              "aeropuerto", "sistema", "acceso", "programa", "mejoramiento"}


def patron_propio(nombre: str) -> re.Pattern:
    """Palabras distintivas del nombre de la concesión ("Talcahuano", "Penco", "Interportuaria"…) para reconocer
    las publicaciones del Diario Oficial que tratan de ella."""
    palabras = [w for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{4,}", nombre) if w.lower() not in _GENERICAS]
    sin_tilde = lambda w: re.sub("[áa]", "[aá]", re.sub("[ée]", "[eé]", re.sub("[íi]", "[ií]", re.sub(
        "[óo]", "[oó]", re.sub("[úu]", "[uú]", w.lower())))))
    return re.compile("|".join(sin_tilde(w) for w in palabras) or r"$^", re.I)


def ruta_fragmentos(p: Proyecto, doc_id: str):
    return p.carpeta / "cache" / "fragmentos" / f"{doc_id}.json"


def leer_fragmentos(p: Proyecto, doc_id: str) -> list[dict]:
    return json.loads(ruta_fragmentos(p, doc_id).read_text(encoding="utf-8"))["fragmentos"]


def _ajenas(lineas: list[dict], propio: re.Pattern) -> set[int]:
    """Líneas de publicaciones del Diario Oficial que no tratan de la concesión (otras concesiones, decretos
    de otros ministerios en la misma página). Una publicación empieza en la línea "Núm. 123.-"."""
    paginas_do = {ln["pag"] for ln in lineas if "DIARIO OFICIAL" in ln["texto"].upper()}
    if not paginas_do:
        return set()
    grupos, actual = [], []
    for ln in lineas:
        if ln["pag"] in paginas_do and _RE_PUBLICACION.match(ln["texto"]) and actual:
            grupos.append(actual)
            actual = []
        actual.append(ln)
    grupos.append(actual)
    salida = set()
    for g in grupos:
        en_do = [ln for ln in g if ln["pag"] in paginas_do]
        if en_do and not any(propio.search(ln["texto"]) for ln in g):
            salida.update(ln["i"] for ln in en_do)
    return salida


def fragmentar(p: Proyecto, doc: Documento, actos: list[dict], claves_bases: set[str]) -> list[dict]:
    lineas = leer_lineas(p, doc.id)
    utiles = [ln for ln in lineas if not ln["marginal"]]
    ajenas = _ajenas(utiles, patron_propio(p.nombre))
    fragmentos: list[dict] = []

    def cerrar(grupo: list[dict]):
        if not grupo:
            return
        texto = "\n".join(ln["texto"] for ln in grupo)
        acto = acto_de_linea(actos, doc.id, grupo[0]["pag"])
        fragmentos.append({
            "id": f"{doc.id}.F{len(fragmentos) + 1:04d}", "acto": acto["acto"],
            "lineas": [ln["i"] for ln in grupo], "pags": sorted({ln["pag"] for ln in grupo}),
            "refs": [c for c in citas(texto) if c in claves_bases],
            "verbo": bool(RE_INSTRUCCION.search(texto)),
            "ajeno": sum(ln["i"] in ajenas for ln in grupo) > len(grupo) / 2,
        })

    actual: list[dict] = []
    acto_actual, ultimo_punto = None, 0
    for ln in utiles:
        acto = acto_de_linea(actos, doc.id, ln["pag"])["acto"]
        corta, inicio_parrafo = acto != acto_actual, not actual or ln["parrafo"] != actual[-1]["parrafo"]
        if acto != acto_actual:
            ultimo_punto = 0
        if RE_BLOQUE.match(ln["texto"]) and inicio_parrafo:
            corta = True
            ultimo_punto = 0 if re.match(r"^\s*[IVX]+\s?[.)]", ln["texto"]) else ultimo_punto
        elif RE_PUNTO_EN.match(ln["texto"]):
            corta = True
            if m := RE_PUNTO.match(ln["texto"]):
                ultimo_punto = int(m[1])
        elif m := RE_PUNTO.match(ln["texto"]):
            n = int(m[1])
            if n == ultimo_punto + 1 or (n == 1 and inicio_parrafo):
                corta, ultimo_punto = True, n
        if actual and (corta or (len(actual) >= MAX_LINEAS and inicio_parrafo)):
            cerrar(actual)
            actual = []
        actual.append(ln)
        acto_actual = acto
    cerrar(actual)
    return fragmentos


def fragmentos_proyecto(p: Proyecto, actos: list[dict], solo: set[str] | None = None) -> dict[str, list[dict]]:
    estructura = leer_estructura(p)
    claves = {s["numero"] for s in estructura["secciones"]}
    bases = documento_bases(p)
    salida = {}
    for doc in p.documentos:
        if doc.id == bases.id or (solo and doc.id not in solo):
            continue
        frs = fragmentar(p, doc, actos, claves)
        ruta = ruta_fragmentos(p, doc.id)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({"documento": doc.id, "fragmentos": frs}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        candidatos = sum(1 for f in frs if f["refs"] and not f["ajeno"])
        print(f"  {doc.id}: {len(frs)} fragmentos · {candidatos} citan artículos de las Bases · "
              f"{sum(f['refs'] != [] and f['verbo'] and not f['ajeno'] for f in frs)} con instrucción · "
              f"{sum(f['ajeno'] for f in frs)} ajenos")
        salida[doc.id] = frs
    return salida
