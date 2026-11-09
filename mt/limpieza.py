"""Limpieza de presentación del documento único (sin LLM): reglas deterministas sobre el texto OCR.

Solo se aplica al escribir `DOCUMENTO_UNICO_<ID>.md`; el árbol y los chunks siguen literales. Cada regla es un
patrón sin ambigüedad del OCR y cuenta sus cambios para `revision/limpieza.md`:
  guion_corte    "Mini-\\nmo" → "Minimo", solo si la palabra unida existe (diccionario o vocabulario de la concesión)
  numero         "N*19.300", "N%30", "N”1" → "N°…" (solo antes de un dígito)
  grado          "artículo 45%", "artículo 2”", "16*" → "artículo 45°", "16°"
  barras         "|" sueltos que el OCR deja de los bordes de tabla dentro de un texto corrido
  puntos_indice  "1.15 SISTEMA TARIFARIO ......... 87" → "1.15 SISTEMA TARIFARIO … 87"
  espacios       espacio antes de , ; : . y dentro de comillas
  vocabulario    palabra rara que no está en el diccionario → la única palabra frecuente de la concesión a una letra
                 de distancia ("Inereso" → "Ingreso", "lluminación" → "iluminación")
El diccionario es la lista de palabras del modelo `spa` de Tesseract (ya instalado); el vocabulario sale de los PDF
de la propia concesión, así que no hay nada específico de una concesión en el código. `LIMPIEZA=no` la desactiva.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from . import config
from .indice import Proyecto
from .texto import leer_lineas

ACTIVA = config.env("LIMPIEZA", "si").lower() not in ("no", "0", "false")
MIN_LARGO = 5            # palabras más cortas no se corrigen por vocabulario
MAX_RARA = 2             # una palabra se corrige solo si aparece a lo más estas veces en la concesión
MIN_FRECUENTE = 3        # el reemplazo debe aparecer al menos estas veces y estar en el diccionario
EJEMPLOS = 6
SUFIJOS = ("s", "n", "es", "er", "rán", "rá", "ren", "re", "ndo", "se", "le", "la", "lo", "las", "los", "nos", "mos",
           "ase", "ese", "iese", "ara", "iera", "ada", "ado", "adas", "ados", "idos", "idas")

LETRA = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
_RE_PALABRA = re.compile(rf"[{LETRA}]+")
_RE_CORTE = re.compile(rf"([{LETRA}]{{2,}})-[ \t]*(\n[ \t]*| )([a-záéíóúüñ]{{2,}})")
_RE_NUMERO = re.compile(r"\bN\s?[*%\"”“'º°?]{1,2}\s?(?=\d)")
_RE_GRADO = re.compile(r"\b([Aa]rt[íi]culos?|ART[ÍI]CULOS?|[Aa]rt\.)\s+(\d{1,3})\s?[%”\"*º°?](?=[\s,;.)]|$)")
_RE_ASTERISCO = re.compile(r"(?<=\d)\*(?=[\s,;.)]|$)")       # "16*" → "16°" (ordinal)
_RE_BARRA = re.compile(r"(?<!\S)[|¦](?!\S)|^[|¦]\s*|[|¦](?=[.,;:])")
_RE_PUNTOS = re.compile(r"\s*(?:[.…·]\s?){4,}\s*")
_RE_ESPACIO_PUNT = re.compile(r"(?<=[^\s.])\s+([,;:]|\.(?![\d.]))")
_RE_ABRE = re.compile(r"([“«])\s+")
_RE_CIERRA = re.compile(r"\s+([”»])")


@lru_cache(maxsize=1)
def diccionario() -> frozenset[str]:
    """Palabras del modelo `spa` de Tesseract (combine_tessdata + dawg2wordlist). Vacío si no hay herramientas."""
    if not (shutil.which("combine_tessdata") and shutil.which("dawg2wordlist")):
        return frozenset()
    rutas = [*Path("/usr/share/tesseract-ocr").glob("*/tessdata/spa.traineddata"),
             Path("/usr/share/tessdata/spa.traineddata"), Path("/usr/local/share/tessdata/spa.traineddata")]
    datos = next((r for r in rutas if r.exists()), None)
    if datos is None:
        return frozenset()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "spa."
        try:
            subprocess.run(["combine_tessdata", "-u", str(datos), str(base)], check=True, capture_output=True)
            salida = Path(tmp) / "palabras.txt"
            subprocess.run(["dawg2wordlist", f"{base}lstm-unicharset", f"{base}lstm-word-dawg", str(salida)],
                           check=True, capture_output=True)
            return frozenset(w.strip().lower() for w in salida.read_text(encoding="utf-8").splitlines() if w.strip())
        except (subprocess.CalledProcessError, OSError):
            return frozenset()


def _edicion_segura(rara: str, candidata: str) -> bool:
    """La única edición no está en las dos últimas letras (género, número, tiempo verbal: "señalizada"/"señalizado")
    ni agrega una letra al inicio ("acontinuación" → "continuación" perdería la "a")."""
    op = Levenshtein.editops(rara, candidata)[0]
    if op.src_pos >= len(rara) - 2 or op.dest_pos >= len(candidata) - 2:
        return False
    return not (op.tag == "delete" and op.src_pos == 0)


def _con_mayusculas(modelo: str, palabra: str) -> str:
    if modelo.isupper():
        return palabra.upper()
    return palabra[0].upper() + palabra[1:] if modelo[0].isupper() else palabra


class Limpiador:
    def __init__(self, p: Proyecto):
        self.frecuencia: Counter = Counter()
        for d in p.documentos:
            for ln in leer_lineas(p, d.id):
                if not ln["marginal"]:
                    self.frecuencia.update(w.lower() for w in _RE_PALABRA.findall(ln["texto"]))
        self.dic = diccionario()
        self.frecuentes = sorted(w for w, c in self.frecuencia.items()
                                 if c >= MIN_FRECUENTE and len(w) >= MIN_LARGO - 1 and w in self.dic)
        self.cambios: Counter = Counter()
        self.ejemplos: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.correcciones: Counter = Counter()             # (rara, reemplazo) → veces
        self._cache: dict[str, str | None] = {}

    # --- reglas ---------------------------------------------------------------------------------------
    def existe(self, palabra: str) -> bool:
        w = palabra.lower()
        return w in self.dic or self.frecuencia[w] >= MIN_FRECUENTE

    def flexion(self, w: str) -> bool:
        """Forma verbal o plural de una palabra del diccionario (que no trae todas las conjugaciones):
        "implicarán" (implicar), "estuvieren" (estuviere), "cronogramas" (cronograma)."""
        for suf in SUFIJOS:
            raiz = w[:-len(suf)]
            if w.endswith(suf) and len(raiz) >= 3 and any(raiz + x in self.dic for x in ("", "r", "ar", "er", "ir")):
                return True
        return False

    def _anotar(self, regla: str, antes: str, despues: str):
        self.cambios[regla] += 1
        if len(self.ejemplos[regla]) < EJEMPLOS and (antes, despues) not in self.ejemplos[regla]:
            self.ejemplos[regla].append((antes, despues))

    def reemplazo(self, palabra: str) -> str | None:
        """La única palabra frecuente (y del diccionario) a una letra de distancia de una palabra rara."""
        w = palabra.lower()
        if w in self._cache:
            return self._cache[w]
        r = None
        if (len(w) >= MIN_LARGO and not palabra.isupper() and w not in self.dic
                and self.frecuencia[w] <= MAX_RARA and self.frecuentes):
            cand = process.extract(w, self.frecuentes, scorer=Levenshtein.distance, score_cutoff=1, limit=3)
            cand = [c for c, dist, _ in cand if dist == 1]
            if len(cand) == 1 and not self.flexion(w) and _edicion_segura(w, cand[0]):
                r = cand[0]
        self._cache[w] = r
        return r

    def limpiar(self, texto: str) -> str:
        """Texto OCR (con saltos de línea) → una línea limpia. Sin `ACTIVA`, solo une las líneas."""
        if not ACTIVA:
            return re.sub(r"\s*\n\s*", " ", texto).strip()

        def corte(m: re.Match) -> str:
            unida = m[1] + m[3]
            if self.existe(unida):
                self._anotar("guion_corte", m[0].replace("\n", " "), unida)
                return unida
            return m[0]

        t = _RE_CORTE.sub(corte, texto)
        t = re.sub(r"\s*\n\s*", " ", t).strip()
        for regla, patron, nuevo in (("numero", _RE_NUMERO, "N°"), ("grado", _RE_GRADO, r"\1 \2°"), ("grado", _RE_ASTERISCO, "°"),
                                     ("barras", _RE_BARRA, " "), ("puntos_indice", _RE_PUNTOS, " … ")):
            t = patron.sub(lambda m: self._regla(regla, m, m.expand(nuevo)), t)
        antes = t
        t = _RE_ESPACIO_PUNT.sub(r"\1", t)
        t = _RE_CIERRA.sub(r"\1", _RE_ABRE.sub(r"\1", t))
        t = re.sub(r"[ \t]{2,}", " ", t).strip()
        if t != antes:
            self._anotar("espacios", antes[:80], t[:80])

        def palabra(m: re.Match) -> str:
            if t[max(0, m.start() - 2):m.start()].endswith(("-", "- ")) or t[m.end():m.end() + 1] == "-":
                return m[0]                                    # trozo de una palabra cortada con guion
            r = self.reemplazo(m[0])
            if not r:
                return m[0]
            nuevo = _con_mayusculas(m[0], r)
            self.correcciones[(m[0], nuevo)] += 1
            self._anotar("vocabulario", m[0], nuevo)
            return nuevo

        return _RE_PALABRA.sub(palabra, t)

    def _regla(self, regla: str, m: re.Match, nuevo: str) -> str:
        if nuevo != m[0]:
            self._anotar(regla, m[0], nuevo)
        return nuevo

    # --- informe --------------------------------------------------------------------------------------
    def informe(self, p: Proyecto) -> Path:
        md = [f"# Limpieza del documento único · {p.id}", "",
              "Reglas deterministas (sin LLM) aplicadas solo al Markdown; el árbol y los chunks siguen literales.", "",
              f"- Diccionario: {len(self.dic):,} palabras (modelo `spa` de Tesseract)"
              f"{'' if self.dic else ' — no disponible: la regla de vocabulario no corrió'}.",
              f"- Vocabulario de la concesión: {len(self.frecuencia):,} palabras; {len(self.frecuentes):,} frecuentes "
              f"(≥ {MIN_FRECUENTE} y en el diccionario).", "", "| Regla | Cambios |", "|---|---:|"]
        md += [f"| {r} | {c} |" for r, c in self.cambios.most_common()] + [""]
        for r, ejs in self.ejemplos.items():
            md += [f"## {r}", ""] + [f"- «{a}» → «{b}»" for a, b in ejs] + [""]
        if self.correcciones:
            md += ["## Todas las correcciones por vocabulario", "", "| Palabra OCR | Reemplazo | Veces |", "|---|---|---:|"]
            md += [f"| {a} | {b} | {c} |" for (a, b), c in sorted(self.correcciones.items())] + [""]
        ruta = p.dir_revision / "limpieza.md"
        ruta.write_text("\n".join(md) + "\n", encoding="utf-8")
        return ruta


def palabras_ajenas(p: Proyecto, nodos: dict[str, dict], md: str) -> list[tuple[str, str]]:
    """Control de `verificar`: palabras de los párrafos marcados del documento que no están en el texto literal de
    sus nodos ni existen en el diccionario o el vocabulario de la concesión (una limpieza que inventara texto).
    Se ignoran los rótulos en negrita/cursiva que escribe el código. [(id, palabra)]"""
    limpiador = Limpiador(p)
    salida = []
    for linea in md.splitlines():
        ids = re.findall(r"<!-- (\S+) · ", linea)
        if not ids or not all(i in nodos for i in ids):
            continue
        texto = re.sub(r"<!--.*?-->", " ", linea)
        texto = re.sub(r"(?<!\\)\*\*.*?(?<!\\)\*\*|(?<!\\)\*[^*]+?(?<!\\)\*", " ", texto)
        literal = {w.lower() for i in ids for v in nodos[i].get("versiones", []) for w in _RE_PALABRA.findall(v["texto"])}
        literal |= {w.lower() for i in ids for w in _RE_PALABRA.findall(nodos[i].get("titulo") or "")}
        salida += [(ids[0], w) for w in _RE_PALABRA.findall(texto)
                   if w.lower() not in literal and not limpiador.existe(w)]
    return salida
