"""Tablas: detección de páginas con tablas, transcripción con visión directamente a JSON y guardado en SQLite.

1. `detectar()` (sin LLM): páginas con filas de tabla Markdown, muchas líneas numéricas, bordes de tabla que el
   OCR lee como "|", encabezados típicos ("Monto", "UTM", "Año"…) o un título "Tabla"/"Cuadro"; además todas
   las páginas de la oferta económica (un formulario cuya cifra decide el contrato)
   → `revision/paginas_tabla.csv` (editable: columna `incluir` = si/no).
2. `transcribir_json()`: TODA página con tabla va al modelo de visión (Qwen3-VL), que devuelve las tablas en JSON
   (`{"tablas": [{"titulo", "encabezados", "filas", "notas"}]}`), validado con pydantic: cada fila con tantas
   celdas como encabezados; si no, se prueba el modelo de respaldo. → `cache/tablas/<doc>/pNNNN.json`.
   El texto de la página NO cambia: los índices de línea, las relaciones y la literalidad del árbol no se tocan.
3. `tablas_json()`: lee esa caché, une las tablas que siguen en la página siguiente (mismos encabezados) y
   conserva el título solo si aparece en el OCR de la página. `guardar()` escribe `TABLAS_<ID>.json` y `TABLAS_<ID>.sqlite` (una fila por celda, con el valor
   numérico normalizado) para consultar cifras exactas sin pasar por embeddings.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import time

from pydantic import BaseModel, ValidationError

from . import config
from .indice import Proyecto
from .texto import leer_lineas

_RE_NUMEROS = re.compile(r"\d[\d.,]*")
_RE_TITULO = re.compile(r"^\s*(TABLA|CUADRO|Tabla|Cuadro)\b")
_RE_ENCABEZADO = re.compile(r"\b(Monto|Multa|UTM|U\.?F\.?|Art[ií]culo|Año|Total|[IÍ]tem|Unidad|Valor|Cantidad|"
                            r"Tramo|Plazo|Porcentaje|Precio)\b", re.I)
MIN_BORDES = 4          # líneas con "|" del OCR (bordes verticales de una tabla)
UMBRAL_TITULO = 85     # parecido mínimo del título con una línea del OCR de la página
MIN_ENCABEZADOS = 4     # líneas cortas con palabras típicas de encabezado (3 si además hay líneas numéricas)

PROMPT_JSON = (
    "Transcribe TODAS las tablas de esta página de un documento legal chileno (concesiones de obras públicas) "
    "y responde SOLO con un objeto JSON con esta forma:\n"
    '{"tablas": [{"titulo": "texto del título o null", "encabezados": ["col1", "col2", ...], '
    '"filas": [["celda", "celda", ...], ...], "notas": "notas al pie de la tabla o null"}]}\n'
    "Reglas:\n"
    "- Copia cada celda tal como aparece (números, puntos, comas, unidades, mayúsculas). No calcules, no "
    "resumas, no completes ni corrijas nada.\n"
    "- Cada fila debe tener exactamente tantas celdas como encabezados. Celda vacía = \"\".\n"
    "- Celda combinada que abarca varias filas o columnas: repite su valor en cada celda que cubre.\n"
    "- Encabezados de varios niveles: únelos con \" / \" (p. ej. \"Ingresos Mínimos (UF) / Con Av. X\").\n"
    "- Un formulario con campos y valores (p. ej. \"Monto del Subsidio ... : 287.000 UF\") es una tabla de dos "
    "columnas: [\"Campo\", \"Valor\"]. Si un valor está en blanco, déjalo \"\".\n"
    "- \"titulo\": solo el título propio de la tabla (p. ej. \"Tabla N° 3: …\"); null si no tiene. El encabezado o "
    "pie de página del documento NO es título. Una tabla que continúa de la página anterior va sin título.\n"
    "- Texto corrido que no es tabla: no lo incluyas.\n"
    "- Si la página no tiene tablas: {\"tablas\": []}."
)
VERSION_PROMPT_JSON = 2


class Tabla(BaseModel):
    titulo: str | None = None
    encabezados: list[str]
    filas: list[list[str]]
    notas: str | None = None


class _Pagina(BaseModel):
    tablas: list[Tabla]


def es_fila(linea: str) -> bool:
    return linea.lstrip().startswith("|")


def numero(valor: str) -> float | None:
    """'5.955.000' → 5955000 · '0,4313%' → 0.4313 · '718,30' → 718.3 · 'UF 4.900' → 4900. None si no es un número."""
    v = valor.replace("UF", "").replace("%", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d[\d.,]*", v):
        return None
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    elif v.count(".") >= 1 and all(len(g) == 3 for g in v.split(".")[1:]):
        v = v.replace(".", "")
    try:
        return float(v)
    except ValueError:
        return None


def _ruta_json(p: Proyecto, doc_id: str, pagina: int):
    return p.carpeta / "cache" / "tablas" / doc_id / f"p{pagina:04d}.json"


# --- 1. detección -------------------------------------------------------------------------------
def detectar(p: Proyecto) -> list[dict]:
    filas = []
    for d in p.documentos:
        por_pag: dict[int, list[dict]] = {}
        for ln in leer_lineas(p, d.id):
            if not ln["marginal"]:
                por_pag.setdefault(ln["pag"], []).append(ln)
        for pag, ls in sorted(por_pag.items()):
            textos = [ln["texto"] for ln in ls]
            md = sum(es_fila(t) for t in textos)
            num = sum(1 for t in textos if len(_RE_NUMEROS.findall(t)) >= 3 and len(t) < 160)
            bordes = sum(1 for t in textos if "|" in t and not es_fila(t))
            encabezados = sum(1 for t in textos if len(t) < 45 and _RE_ENCABEZADO.search(t))
            titulo = any(_RE_TITULO.match(t) for t in textos)
            motivo = ("oferta_economica" if d.subtipo.startswith("oferta economica") else
                      "tabla_markdown" if md >= 3 else
                      "lineas_numericas" if len(ls) >= 8 and num >= 0.35 * len(ls) else
                      "bordes_tabla" if bordes >= MIN_BORDES else
                      "encabezados" if encabezados >= MIN_ENCABEZADOS or (encabezados >= 3 and num >= 2) else
                      "titulo_tabla" if titulo and num >= 3 else None)
            if motivo:
                filas.append({"documento": d.id, "pagina": pag, "motivos": motivo,
                              "json": "si" if _ruta_json(p, d.id, pag).exists() else "no", "incluir": "si"})
    ruta = p.dir_revision / "paginas_tabla.csv"
    manual = {}
    if ruta.exists():
        with ruta.open(encoding="utf-8-sig", newline="") as fh:
            manual = {(f["documento"], int(f["pagina"])): f.get("incluir", "si") for f in csv.DictReader(fh, delimiter=";")}
    for f in filas:
        f["incluir"] = manual.get((f["documento"], f["pagina"]), "si")
    p.dir_revision.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["documento", "pagina", "motivos", "json", "incluir"], delimiter=";",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(filas)
    pendientes = sum(f["json"] == "no" and f["incluir"] == "si" for f in filas)
    print(f"  {p.id}: {len(filas)} páginas con tabla · {pendientes} por transcribir a JSON")
    return filas


# --- 2. transcripción a JSON ---------------------------------------------------------------------
def _leer_respuesta(texto: str) -> _Pagina:
    """JSON de la respuesta. Tolera bloques ```json``` y varios objetos seguidos (una tabla por objeto), que los
    modelos de visión a veces devuelven en lugar de una sola lista."""
    t = re.sub(r"```(?:json)?", "", texto)
    objetos, decodificador, k = [], json.JSONDecoder(), 0
    while (k := t.find("{", k)) >= 0:
        try:
            obj, fin = decodificador.raw_decode(t, k)
        except ValueError:
            if not objetos:
                raise
            break
        objetos.append(obj)
        k = fin
    if not objetos:
        raise ValueError("la respuesta no contiene un objeto JSON")
    tablas = []
    for obj in objetos:
        tablas += obj.get("tablas", []) if "tablas" in obj else ([obj] if "encabezados" in obj else [])
    return _Pagina.model_validate({"tablas": tablas})


def _validar(texto: str) -> str | None:
    try:
        pagina = _leer_respuesta(texto)
    except (ValueError, ValidationError) as e:
        return f"JSON inválido: {str(e)[:120]}"
    for t in pagina.tablas:
        if not t.encabezados:
            return "tabla sin encabezados"
        malas = [k for k, f in enumerate(t.filas) if len(f) != len(t.encabezados)]
        if malas:
            return f"filas {malas[:5]} con largo distinto a los {len(t.encabezados)} encabezados"
    return None


def transcribir_json(p: Proyecto, limite: int | None = None) -> int:
    import io
    from concurrent.futures import ThreadPoolExecutor

    from . import llm, meta
    from .ocr import renderizar, ruta_pagina

    paginas = [f for f in detectar(p) if f["incluir"] == "si" and f["json"] == "no"][:limite]
    if not paginas:
        return 0
    modelos = [m.strip() for m in config.env(
        "TABLAS_VLM", "qwen/qwen3-vl-30b-a3b-instruct,google/gemini-2.5-flash-lite").split(",") if m.strip()]
    dir_llm = p.carpeta / "cache" / "llm"
    trabajos = []                       # pdfium no es seguro entre hilos: se renderiza aquí
    for f in paginas:
        doc = p.doc(f["documento"])
        ocr = json.loads(ruta_pagina(p, doc.id, f["pagina"]).read_text(encoding="utf-8"))
        imagen = renderizar(doc.ruta, f["pagina"], config.VLM_DPI)
        if ocr.get("rotacion"):
            imagen = imagen.rotate(-ocr["rotacion"], expand=True)
        buf = io.BytesIO()
        imagen.convert("L").save(buf, format="PNG", optimize=True)
        trabajos.append((f, buf.getvalue()))

    def una(trabajo):
        f, png = trabajo
        inicio = time.time()
        if meta.activo():                               # VISION_PROVEEDOR=meta: Muse Spark (API de Meta)
            texto, modelo = meta.pedir_vision(dir_llm, PROMPT_JSON, png, max_tokens=8000, validar=_validar)
        else:
            texto, modelo = llm.pedir_vision(dir_llm, PROMPT_JSON, png, modelos, max_tokens=8000, validar=_validar)
        return f, _leer_respuesta(texto), modelo, round(time.time() - inicio, 1)

    hechas = 0
    with ThreadPoolExecutor(config.env_int("VLM_HILOS", 6)) as pool:
        for i, fut in enumerate([pool.submit(una, t) for t in trabajos], 1):
            f = trabajos[i - 1][0]
            try:
                f, pagina, modelo, segundos = fut.result()
            except Exception as e:                      # sin modelos, tope de gasto, JSON inválido en todos
                print(f"  [{i}/{len(trabajos)}] {f['documento']} p{f['pagina']:04d} · sin tablas JSON: {str(e)[:140]}")
                continue
            ruta = _ruta_json(p, f["documento"], f["pagina"])
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps({"documento": f["documento"], "pagina": f["pagina"], "modelo": modelo,
                                        "version_prompt": VERSION_PROMPT_JSON, "fecha": time.strftime("%Y-%m-%d %H:%M"),
                                        "tablas": [t.model_dump() for t in pagina.tablas]},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
            hechas += 1
            print(f"  [{i}/{len(trabajos)}] {f['documento']} p{f['pagina']:04d} · {len(pagina.tablas)} tablas · "
                  f"{sum(len(t.filas) for t in pagina.tablas)} filas · {segundos}s · {modelo}")
    return hechas


# --- 3. lectura y guardado ----------------------------------------------------------------------------
def _titulo_literal(titulo: str | None, textos: list[str]) -> str | None:
    """El título solo se conserva si aparece en el OCR de la página (una línea no marginal, o dos seguidas): los
    modelos de visión a veces inventan uno ("Tabla N° 3: …") en las páginas que continúan una tabla."""
    from rapidfuzz import fuzz

    if not titulo or not titulo.strip():
        return None
    t = re.sub(r"\s+", " ", titulo).strip().lower()
    candidatos = textos + [f"{a} {b}" for a, b in zip(textos, textos[1:])]
    return titulo.strip() if any(fuzz.ratio(t, x.lower()) >= UMBRAL_TITULO for x in candidatos) else None


def _limpiar(t: dict) -> dict:
    """Espacios y saltos de línea dentro de encabezados y celdas → un espacio (para comparar y unir tablas)."""
    plano = lambda x: re.sub(r"\s+", " ", x or "").strip()
    return {**t, "encabezados": [plano(h) for h in t["encabezados"]], "filas": [[plano(c) for c in f] for f in t["filas"]]}


def tablas_json(p: Proyecto) -> list[dict]:
    """Tablas de la caché JSON en orden de documento y página: [{documento, paginas, titulo, encabezados, filas,
    notas}]. Una tabla con los mismos encabezados que la última de la página anterior la continúa (se unen)."""
    incluidas = None
    ruta_csv = p.dir_revision / "paginas_tabla.csv"
    if ruta_csv.exists():
        with ruta_csv.open(encoding="utf-8-sig", newline="") as fh:
            incluidas = {(f["documento"], int(f["pagina"])) for f in csv.DictReader(fh, delimiter=";")
                         if f.get("incluir", "si") == "si"}
    salida: list[dict] = []
    for d in p.documentos:
        carpeta = p.carpeta / "cache" / "tablas" / d.id
        if not carpeta.exists():
            continue
        textos: dict[int, list[str]] = {}
        for ln in leer_lineas(p, d.id):
            if not ln["marginal"]:
                textos.setdefault(ln["pag"], []).append(ln["texto"])
        for ruta in sorted(carpeta.glob("p*.json")):
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            pag = datos["pagina"]
            if incluidas is not None and (d.id, pag) not in incluidas:
                continue
            for k, t in enumerate(_formulario(_limpiar(t)) for t in datos["tablas"]):
                titulo = _titulo_literal(t.get("titulo"), textos.get(pag, []))
                previa = salida[-1] if salida else None
                if (k == 0 and previa and previa["documento"] == d.id and previa["paginas"][-1] == pag - 1
                        and t["encabezados"] == previa["encabezados"]):
                    previa["filas"] += t["filas"]
                    previa["paginas"].append(pag)
                    continue
                salida.append({"documento": d.id, "paginas": [pag], "titulo": titulo,
                               "encabezados": t["encabezados"], "filas": t["filas"], "notas": t.get("notas")})
    return salida


def _formulario(t: dict) -> dict:
    """Formulario que el modelo devolvió con los pares campo/valor como encabezados y sin filas con datos
    (p. ej. la oferta económica) → tabla Campo/Valor, un par por fila."""
    enc = t["encabezados"]
    if any(any(c.strip() for c in f) for f in t["filas"]) or len(enc) < 2 or len(enc) % 2:
        return t
    return {**t, "encabezados": ["Campo", "Valor"], "filas": [[enc[k], enc[k + 1]] for k in range(0, len(enc), 2)]}


def guardar(p: Proyecto, tablas: list[dict]):
    """`tablas`: [{id, titulo, encabezados, filas, notas, documento, paginas, nodo, ruta}] → JSON y SQLite."""
    (p.carpeta / f"TABLAS_{p.id}.json").write_text(json.dumps(tablas, ensure_ascii=False, indent=1), encoding="utf-8")
    ruta = p.carpeta / f"TABLAS_{p.id}.sqlite"
    ruta.unlink(missing_ok=True)
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE tablas (id TEXT PRIMARY KEY, titulo TEXT, nodo TEXT, ruta TEXT, documento TEXT, pagina INTEGER,
                             paginas TEXT, columnas TEXT, n_filas INTEGER, notas TEXT);
        CREATE TABLE celdas (id_tabla TEXT, fila INTEGER, columna TEXT, n_columna INTEGER, valor_texto TEXT,
                             valor_numerico REAL);
        CREATE INDEX celdas_tabla ON celdas (id_tabla);
        CREATE INDEX celdas_valor ON celdas (valor_numerico);
    """)
    for t in tablas:
        con.execute("INSERT INTO tablas VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (t["id"], t["titulo"], t["nodo"], t["ruta"], t["documento"], t["paginas"][0],
                     json.dumps(t["paginas"]), json.dumps(t["encabezados"], ensure_ascii=False), len(t["filas"]),
                     t.get("notas")))
        con.executemany("INSERT INTO celdas VALUES (?,?,?,?,?,?)",
                        [(t["id"], i, t["encabezados"][j] if j < len(t["encabezados"]) else str(j), j, v, numero(v))
                         for i, fila in enumerate(t["filas"], 1) for j, v in enumerate(fila)])
    con.commit()
    con.close()
