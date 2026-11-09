"""Rutas y parámetros. Todo se puede cambiar con variables de entorno (o un archivo .env en la raíz)."""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_env():
    ruta = RAIZ / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env()


def env(clave: str, defecto: str) -> str:
    return os.environ.get(clave) or defecto


def env_float(clave: str, defecto: float) -> float:
    return float(env(clave, str(defecto)).replace(",", "."))


def env_int(clave: str, defecto: int) -> int:
    return int(env(clave, str(defecto)))


# --- Paso 1: OCR ---------------------------------------------------------------------------
OCR_DPI = env_int("OCR_DPI", 300)
OCR_IDIOMA = env("OCR_IDIOMA", "spa")
OCR_PSM = env_int("OCR_PSM", 3)                 # 3 = segmentación automática (detecta columnas)
OCR_HILOS = env_int("OCR_HILOS", max(1, (os.cpu_count() or 2) - 1))
OCR_MIN_CHARS_TEXTO = env_int("OCR_MIN_CHARS_TEXTO", 200)   # capa de texto del PDF que se usa sin OCR
TESSERACT_CMD = os.environ.get("TESSERACT_CMD")             # en Windows: C:\Program Files\Tesseract-OCR\tesseract.exe

# --- Paso 2: marcar páginas mal leídas -----------------------------------------------------
# Una página se marca si cumple CUALQUIERA de estas condiciones (y tiene tinta: no está en blanco).
UMBRAL_CONF_MEDIA = env_float("UMBRAL_CONF_MEDIA", 0.90)       # confianza media de las palabras (0–1)
UMBRAL_FRAC_BAJA = env_float("UMBRAL_FRAC_BAJA", 0.30)         # fracción de palabras con conf < CONF_PALABRA_BAJA
UMBRAL_FRAC_RARA = env_float("UMBRAL_FRAC_RARA", 0.25)         # fracción de palabras que no parecen español
UMBRAL_MIN_PALABRAS = env_int("UMBRAL_MIN_PALABRAS", 15)       # menos palabras que esto en una página con tinta
CONF_PALABRA_BAJA = env_float("CONF_PALABRA_BAJA", 0.60)
TINTA_MIN = env_float("TINTA_MIN", 0.005)                      # fracción de píxeles oscuros: debajo = página en blanco

# --- Paso 3: rescate con modelo de visión ----------------------------------------------------
VLM_MODELO = env("VLM_MODELO", "Qwen/Qwen2-VL-2B-Instruct")
VLM_DPI = env_int("VLM_DPI", 200)
VLM_MAX_PIXELS = env_int("VLM_MAX_PIXELS", 1280 * 28 * 28)     # el procesador de Qwen reduce la imagen a esto
VLM_MAX_TOKENS = env_int("VLM_MAX_TOKENS", 2048)
