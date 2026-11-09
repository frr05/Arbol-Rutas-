"""Métricas de calidad del OCR por página y regla para marcar las páginas mal leídas.

La confianza media de Tesseract por sí sola engaña (una página con pocas palabras muy seguras y mucha basura
puede salir alta), así que se combinan varias señales y basta con que una falle para marcar la página.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from . import config

_LETRAS = "a-záéíóúüñ"
_SOLO_LETRAS = re.compile(rf"^[{_LETRAS}]+$")
_VOCAL = re.compile(r"[aeiouyáéíóúü]")
_CONSONANTES_SEGUIDAS = re.compile(rf"[b-df-hj-np-tv-xzñ]{{5,}}")
_PUNTUACION = "\"'«»“”‘’.,;:()[]{}¿?¡!-–—_/*°º"


def palabra_rara(palabra: str) -> bool | None:
    """True si la palabra no parece español; None si no se evalúa (números, siglas cortas, símbolos)."""
    p = palabra.strip(_PUNTUACION).lower()
    if len(p) < 3 or any(c.isdigit() for c in p):
        return None
    if not _SOLO_LETRAS.match(p):
        return True                       # letras mezcladas con símbolos raros: "ﬁn", "ca|le", "m0p"
    return not _VOCAL.search(p) or bool(_CONSONANTES_SEGUIDAS.search(p))


def metricas(palabras: list[tuple[str, float]], tinta: float) -> dict:
    """`palabras`: (texto, conf 0–1) de cada palabra reconocida; `tinta`: fracción de píxeles oscuros."""
    n = len(palabras)
    total_chars = sum(len(t) for t, _ in palabras)
    conf_media = sum(len(t) * c for t, c in palabras) / total_chars if total_chars else 0.0
    evaluables = [r for r in (palabra_rara(t) for t, _ in palabras) if r is not None]
    return {
        "n_palabras": n,
        "conf_media": round(conf_media, 3),
        "frac_baja": round(sum(c < config.CONF_PALABRA_BAJA for _, c in palabras) / n, 3) if n else 1.0,
        "frac_rara": round(sum(evaluables) / len(evaluables), 3) if evaluables else 0.0,
        "tinta": round(tinta, 4),
    }


@dataclass
class Umbrales:
    conf_media: float = config.UMBRAL_CONF_MEDIA
    frac_baja: float = config.UMBRAL_FRAC_BAJA
    frac_rara: float = config.UMBRAL_FRAC_RARA
    min_palabras: int = config.UMBRAL_MIN_PALABRAS
    tinta_min: float = config.TINTA_MIN

    def como_dict(self) -> dict:
        return asdict(self)


def motivos(calidad: dict, u: Umbrales) -> list[str]:
    """Razones por las que la página está mal leída (lista vacía = página aceptable o en blanco)."""
    if calidad["tinta"] < u.tinta_min:
        return []                         # página en blanco: no hay nada que rescatar
    salida = []
    if calidad["n_palabras"] < u.min_palabras:
        salida.append("pocas_palabras")
    if calidad["conf_media"] < u.conf_media:
        salida.append("conf_media")
    if calidad["frac_baja"] > u.frac_baja:
        salida.append("palabras_baja_conf")
    if calidad["frac_rara"] > u.frac_rara:
        salida.append("texto_raro")
    return salida
