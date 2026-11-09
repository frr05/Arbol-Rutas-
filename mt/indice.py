"""Lectura de los INDICE.md de cada concesión.

Mismas convenciones que doc-unico-v2 (`superdoc/indice.py`): una carpeta por concesión en la raíz, un código
corto por concesión (ETPM, RITP…) y documentos numerados en el orden del INDICE (`ETPM.D01`, `ETPM.D02`…).
Todo lo que genera este proyecto se guarda dentro de la carpeta de la concesión.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .config import RAIZ

# Códigos cortos de cada concesión (palabra clave en el nombre de la carpeta → código).
CODIGOS = {
    "temuco": "TERB",
    "interportuaria": "RITP",
    "tepual": "ETPM",
    "nogales": "PCNP",
}

ETAPAS = {
    "1_licitacion": (1, "licitacion"),
    "2_adjudicacion": (2, "adjudicacion"),
    "3_ejecucion": (3, "ejecucion"),
    "4_modificacion_obras_convenios": (4, "modificacion"),
    "5_otros_documentos": (5, "otros"),
}


@dataclass
class Documento:
    id: str                      # ETPM.D01
    proyecto: str                # ETPM
    titulo: str                  # columna "Documento" del INDICE
    subtipo: str                 # bases licitacion, decreto adjudicacion, ...
    archivo: str                 # ruta relativa a la carpeta de la concesión
    ruta: Path                   # ruta absoluta al PDF
    etapa: str
    alias: list[str] = field(default_factory=list)


@dataclass
class Proyecto:
    id: str
    nombre: str
    carpeta: Path
    documentos: list[Documento]

    # Salidas, todas dentro de la carpeta de la concesión
    @property
    def dir_ocr(self) -> Path:
        return self.carpeta / "cache" / "ocr"

    @property
    def dir_vlm(self) -> Path:
        return self.carpeta / "cache" / "vlm"

    @property
    def dir_revision(self) -> Path:
        return self.carpeta / "revision"

    def doc(self, doc_id: str) -> Documento:
        for d in self.documentos:
            if d.id == doc_id:
                return d
        raise KeyError(doc_id)


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def codigo_proyecto(nombre_carpeta: str) -> str:
    base = _sin_tildes(nombre_carpeta).lower()
    for clave, codigo in CODIGOS.items():
        if clave in base:
            return codigo
    palabras = [p for p in re.findall(r"[A-Za-z]+", _sin_tildes(nombre_carpeta)) if len(p) > 3]
    return "".join(p[0] for p in palabras).upper()[:5] or "PROY"


_FILA = re.compile(r"^\|\s*(?P<titulo>[^|]+?)\s*\|\s*(?P<subtipo>[^|]+?)\s*\|\s*\[[^\]]*\]\(<(?P<archivo>[^>]+)>\)\s*\|")


def leer_proyecto(carpeta: Path) -> Proyecto:
    lineas = (carpeta / "INDICE.md").read_text(encoding="utf-8").splitlines()
    codigo = codigo_proyecto(carpeta.name)
    por_archivo: dict[str, Documento] = {}
    for linea in lineas:
        m = _FILA.match(linea)
        if not m:
            continue
        archivo, titulo = m["archivo"], m["titulo"].strip()
        if archivo in por_archivo:
            # El mismo PDF aparece con otro nombre en la ficha del MOP: se guarda como alias.
            por_archivo[archivo].alias.append(titulo)
            continue
        por_archivo[archivo] = Documento(
            id=f"{codigo}.D{len(por_archivo) + 1:02d}",
            proyecto=codigo,
            titulo=re.sub(r"\s+—\s+idéntico a .*$", "", titulo),
            subtipo=m["subtipo"].strip(),
            archivo=archivo,
            ruta=carpeta / archivo,
            etapa=ETAPAS.get(archivo.split("/")[0], (9, archivo.split("/")[0]))[1],
        )
    return Proyecto(id=codigo, nombre=lineas[0].lstrip("# ").strip(), carpeta=carpeta,
                    documentos=list(por_archivo.values()))


def proyectos(filtro: str | None = None) -> list[Proyecto]:
    """Concesiones de la raíz (carpetas con INDICE.md). `filtro`: código (ETPM) o None/"TODOS"."""
    salida = []
    for carpeta in sorted(p for p in RAIZ.iterdir() if (p / "INDICE.md").is_file()):
        if filtro and filtro.upper() != "TODOS" and codigo_proyecto(carpeta.name) != filtro.upper():
            continue
        salida.append(leer_proyecto(carpeta))
    return salida


def buscar_documento(doc_id: str) -> tuple[Proyecto, Documento]:
    for p in proyectos(doc_id.split(".")[0]):
        return p, p.doc(doc_id)
    raise KeyError(doc_id)
