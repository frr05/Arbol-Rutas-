"""Numerales de artículos (1.6.42.1) tolerantes a errores de OCR. Portado de doc-unico-v2 (segmentar.py)."""
from __future__ import annotations

import bisect
import re

_ROMANOS = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9", "X": "10"}
_ROMANOS_OCR = {"LL": "II", "IL": "II", "LI": "II", "1I": "II", "I1": "II", "LLL": "III", "ILL": "III",
                "LLI": "III", "IIL": "III", "L": "I"}

# Numeral al inicio de una línea: "1.2.1 Definiciones", "1.6.1.2. Garantías", "L6.32 Multas", "2.4,6 Niveles"
RE_NUMERAL = re.compile(r"^\s*((?:\d{1,2}|[IL]{1,3})(?:\s?[.,]\s?\d{1,2}){1,5})[.\-)]?\s+(\S.*)$")
# Numerales citados dentro de un texto ("En 1.4.1 Apertura…", "artículo 1.6.5.2 de las Bases")
RE_CITA = re.compile(r"(?<![\d.])(\d{1,2}(?:\s?[.,]\s?\d{1,2}){1,5})(?![\d])")


def clave_numero(numero: str) -> str:
    """Clave para comparar numerales: 'I.6.32', '1.6.32' y 'L6.32' (error de OCR) son el mismo artículo."""
    n = re.sub(r"\s", "", numero.upper()).replace(",", ".").rstrip(".")
    partes = n.split(".")
    if len(partes) > 1 and partes[0] in _ROMANOS_OCR:
        partes[0] = _ROMANOS_OCR[partes[0]]
    if len(partes) > 1 and partes[0] in _ROMANOS:
        partes[0] = _ROMANOS[partes[0]]
    elif partes and re.fullmatch(r"L\d+", partes[0]):
        partes = ["1", partes[0][1:]] + partes[1:]
    return ".".join(partes)


def orden_numero(numero: str) -> tuple:
    """Tupla comparable para ordenar numerales (1.6.9 < 1.6.10 < 1.6.64)."""
    return tuple(int(p) if p.isdigit() else 10_000 for p in clave_numero(numero).split("."))


def creciente_mas_larga(claves: list[tuple]) -> set[int]:
    """Índices de la subsecuencia estrictamente creciente más larga (O(n log n))."""
    colas: list[tuple] = []
    idx_colas: list[int] = []
    previo = [-1] * len(claves)
    for i, k in enumerate(claves):
        j = bisect.bisect_left(colas, k)
        if j == len(colas):
            colas.append(k)
            idx_colas.append(i)
        else:
            colas[j], idx_colas[j] = k, i
        previo[i] = idx_colas[j - 1] if j else -1
    salida, i = set(), idx_colas[-1] if idx_colas else -1
    while i >= 0:
        salida.add(i)
        i = previo[i]
    return salida


def citas(texto: str) -> list[str]:
    """Claves de los numerales citados en un texto, sin repetir y en orden de aparición."""
    vistas: list[str] = []
    for m in RE_CITA.finditer(texto):
        clave = clave_numero(m[1])
        if clave.count(".") >= 1 and clave not in vistas and not re.fullmatch(r"\d{1,2}\.\d{3,}", clave):
            vistas.append(clave)
    return vistas
