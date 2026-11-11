"""Compara Muse Spark (Meta Model API) con la caché de visión del pipeline (Qwen3-VL) en las mismas páginas.

Dos tareas, con la MISMA imagen y el MISMO prompt que generaron la caché:
  - texto:  transcripción literal de páginas mal leídas (mt/vlm.py)   ↔ cache/vlm/<doc>/pNNNN.json (texto_vlm)
  - tablas: tablas a JSON validado (mt/tablas.py)                      ↔ cache/tablas/<doc>/pNNNN.json

No modifica el pipeline ni su caché. Escribe:
  <concesión>/cache/spark/<doc>/pNNNN_<tarea>.json   respuestas de Spark (repetir no vuelve a pagar)
  <concesión>/revision/spark/muestra.csv             páginas evaluadas (editable)
  <concesión>/revision/spark/comparacion.csv / .md / .html

  python comparar_spark.py --modelos                         # comprueba la clave y lista los modelos
  python comparar_spark.py --proyecto RITP --sin-api         # solo prepara: muestra, imágenes y chequeo de caché
  python comparar_spark.py --proyecto RITP --limite 1        # prueba con una página por tarea
  python comparar_spark.py --proyecto RITP                   # 10 páginas de texto + 10 de tablas
  python comparar_spark.py --proyecto RITP --paginas texto:RITP.D01:5,tablas:RITP.D10:3

Razonamiento: SPARK_REASONING (minimal por defecto; low, medium, high…). Cada nivel tiene su propia caché.

Clave en .env: MODEL_API_KEY=...  (modelo: SPARK_MODELO, por defecto muse-spark-1.3-contributor)
"""
from __future__ import annotations

import argparse
import base64
import csv
import difflib
import hashlib
import html
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from rapidfuzz import fuzz

from mt import config, indice, tablas, vlm
from mt.calidad import metricas
from mt.config import RAIZ
from mt.ocr import renderizar, ruta_pagina

URL = config.env("SPARK_URL", "https://api.meta.ai/v1")
MODELO = config.env("SPARK_MODELO", "muse-spark-1.3-contributor")
PRECIO_IN = config.env_float("SPARK_USD_1M_IN", 0.10)       # US$ por 1M tokens (Contributor)
PRECIO_OUT = config.env_float("SPARK_USD_1M_OUT", 0.20)
SISTEMA = "Eres un transcriptor literal de documentos escaneados."   # el mismo de llm.pedir_vision
PROMPTS = {"texto": vlm.PROMPT, "tablas": tablas.PROMPT_JSON}
# Spark siempre razona (no acepta 'none'): minimal | low | medium | high | xhigh | max. "minimal" es lo más parecido
# a Qwen3-VL-Instruct, que no razona. Los tokens de razonamiento cuentan en max_tokens, así que se da margen sobre
# los 4096 / 8000 que tuvo Qwen para la respuesta visible.
ESFUERZO = config.env("SPARK_REASONING", "minimal")
MAX_TOKENS = {"texto": config.env_int("SPARK_MAX_TOKENS_TEXTO", 4096 + 4096),
              "tablas": config.env_int("SPARK_MAX_TOKENS_TABLAS", 8000 + 4096)}


def _clave_api() -> str:
    clave = config.env("MODEL_API_KEY", "") or config.env("META_API_KEY", "")
    if not clave:
        sys.exit("Falta MODEL_API_KEY en .env (clave de la Meta Model API).")
    return clave


# --- muestra -------------------------------------------------------------------------------------
def _repartir(filas: list[dict], n: int) -> list[dict]:
    """Toma n filas alternando entre documentos (round-robin), en el orden dado."""
    por_doc: dict[str, list[dict]] = {}
    for f in filas:
        por_doc.setdefault(f["documento"], []).append(f)
    salida = []
    while len(salida) < n and any(por_doc.values()):
        for d in list(por_doc):
            if por_doc[d] and len(salida) < n:
                salida.append(por_doc[d].pop(0))
    return salida


def elegir_muestra(p: indice.Proyecto, n_texto: int, n_tablas: int) -> list[dict]:
    with (p.dir_revision / "comparacion_vlm.csv").open(encoding="utf-8") as fh:
        filas = [{"documento": f["documento"], "pagina": int(f["pagina"]), "conf": float(f["conf_ocr"])}
                 for f in csv.DictReader(fh, delimiter=";") if f["truncado"] == "False"]
    filas = [f for f in filas if vlm.ruta_vlm(p, f["documento"], f["pagina"]).exists()]
    baja = [f for f in filas if f["conf"] < 0.6]
    alta = [f for f in filas if f["conf"] >= 0.85]
    media = [f for f in filas if 0.6 <= f["conf"] < 0.85]
    k_baja, k_alta = round(n_texto * 0.3), round(n_texto * 0.3)
    texto = _repartir(baja, k_baja) + _repartir(media, n_texto - k_baja - k_alta) + _repartir(alta, k_alta)

    con_tablas = []
    for ruta in sorted((p.carpeta / "cache" / "tablas").glob("*/p*.json")):
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("tablas"):
            con_tablas.append({"documento": datos["documento"], "pagina": datos["pagina"]})
    tabs = _repartir(con_tablas, n_tablas)
    return ([{"tarea": "texto", "documento": f["documento"], "pagina": f["pagina"]} for f in texto] +
            [{"tarea": "tablas", "documento": f["documento"], "pagina": f["pagina"]} for f in tabs])


def leer_o_crear_muestra(p, args) -> list[dict]:
    ruta = p.dir_revision / "spark" / "muestra.csv"
    if args.paginas:
        muestra = []
        for parte in args.paginas.split(","):
            tarea, doc, pag = parte.strip().split(":")
            muestra.append({"tarea": tarea, "documento": doc, "pagina": int(pag)})
    elif ruta.exists() and not args.nueva_muestra:
        with ruta.open(encoding="utf-8") as fh:
            muestra = [{**f, "pagina": int(f["pagina"])} for f in csv.DictReader(fh, delimiter=";")]
    else:
        muestra = elegir_muestra(p, args.n_texto, args.n_tablas)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["tarea", "documento", "pagina"], delimiter=";", lineterminator="\n")
            w.writeheader()
            w.writerows(muestra)
    return muestra


# --- imagen idéntica a la de la caché ------------------------------------------------------------
def ruta_pdf(p: indice.Proyecto, doc: indice.Documento, dir_pdfs: Path | None) -> Path:
    if doc.ruta.exists():
        return doc.ruta
    base = dir_pdfs or (RAIZ.parent.parent / "Superdocumento" / p.carpeta.name)
    ruta = base / doc.archivo
    if not ruta.exists():
        raise FileNotFoundError(f"no encuentro el PDF {doc.archivo} (usa --pdfs)")
    return ruta


def png_pagina(p, doc, n: int, dir_pdfs) -> bytes:
    """Igual que vlm.rescatar / tablas.transcribir_json: VLM_DPI, giro del OCR, gris, PNG optimizado."""
    ocr = json.loads(ruta_pagina(p, doc.id, n).read_text(encoding="utf-8"))
    imagen = renderizar(ruta_pdf(p, doc, dir_pdfs), n, config.VLM_DPI)
    if ocr.get("rotacion"):
        imagen = imagen.rotate(-ocr["rotacion"], expand=True)
    buf = io.BytesIO()
    imagen.convert("L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def clave_cache_qwen(prompt: str, png: bytes) -> str:
    return hashlib.sha256(prompt.encode("utf-8") + hashlib.sha256(png).digest()).hexdigest()[:24]


# --- Spark -----------------------------------------------------------------------------------------
def listar_modelos() -> list[str]:
    r = requests.get(f"{URL}/models", timeout=30, headers={"Authorization": f"Bearer {_clave_api()}"})
    if r.status_code != 200:
        sys.exit(f"Error {r.status_code} al listar modelos: {r.text[:300]}")
    return sorted(m.get("id", "?") for m in r.json().get("data", []))


def pedir_spark(tarea: str, png: bytes) -> dict:
    cuerpo = {"model": MODELO, "temperature": 0, "max_tokens": MAX_TOKENS[tarea], "reasoning_effort": ESFUERZO,
              "messages": [{"role": "system", "content": SISTEMA},
                           {"role": "user", "content": [
                               {"type": "text", "text": PROMPTS[tarea]},
                               {"type": "image_url",
                                "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}
    error = ""
    for intento in range(5):
        inicio = time.time()
        try:
            r = requests.post(f"{URL}/chat/completions", json=cuerpo, timeout=(15, 600),
                              headers={"Authorization": f"Bearer {_clave_api()}"})
        except requests.RequestException as e:
            error = str(e)
            time.sleep(5 * (intento + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            error = f"HTTP {r.status_code}: {r.text[:200]}"
            time.sleep(float(r.headers.get("retry-after") or 5 * (intento + 1)))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
        datos = r.json()
        eleccion = (datos.get("choices") or [{}])[0]
        contenido = (eleccion.get("message") or {}).get("content") or ""
        if isinstance(contenido, list):          # por si llega como lista de partes
            contenido = "".join(c.get("text", "") for c in contenido if isinstance(c, dict))
        uso = datos.get("usage") or {}
        costo = (uso.get("prompt_tokens", 0) * PRECIO_IN + uso.get("completion_tokens", 0) * PRECIO_OUT) / 1e6
        return {"modelo": datos.get("model", MODELO), "reasoning_effort": ESFUERZO, "texto": contenido.strip(),
                "finish_reason": eleccion.get("finish_reason"), "usage": uso, "costo_usd": round(costo, 6),
                "segundos": round(time.time() - inicio, 1), "fecha": time.strftime("%Y-%m-%d %H:%M")}
    raise RuntimeError(f"sin respuesta tras 5 intentos: {error}")


def ruta_spark(p, tarea: str, doc_id: str, n: int) -> Path:
    return p.carpeta / "cache" / "spark" / doc_id / f"p{n:04d}_{tarea}_{ESFUERZO}.json"


# --- comparación -------------------------------------------------------------------------------------
_RE_NUM = re.compile(r"\d[\d.,]*\d|\d")


def _numeros(texto: str) -> set[str]:
    return {m.rstrip(".,") for m in _RE_NUM.findall(texto)}


def _frac_rara(texto: str) -> float:
    return metricas([(t, 1.0) for t in texto.split()], 1.0)["frac_rara"]


def comparar_texto(qwen: str, spark: str) -> dict:
    nq, ns = _numeros(qwen), _numeros(spark)
    return {"similitud": vlm.similitud(qwen, spark),
            "palabras_qwen": len(qwen.split()), "palabras_spark": len(spark.split()),
            "frac_rara_qwen": _frac_rara(qwen), "frac_rara_spark": _frac_rara(spark),
            "numeros_comunes": len(nq & ns), "numeros_solo_qwen": len(nq - ns), "numeros_solo_spark": len(ns - nq),
            "ilegible_qwen": qwen.count("[ilegible]"), "ilegible_spark": spark.count("[ilegible]")}


def _plano(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def comparar_tablas(qwen: list[dict], spark: list[dict]) -> dict:
    enc_iguales = enc_total = celdas_iguales = celdas_total = num_iguales = num_total = 0
    for k in range(max(len(qwen), len(spark))):
        tq = qwen[k] if k < len(qwen) else {"encabezados": [], "filas": []}
        ts = spark[k] if k < len(spark) else {"encabezados": [], "filas": []}
        eq, es = [_plano(h).lower() for h in tq["encabezados"]], [_plano(h).lower() for h in ts["encabezados"]]
        enc_total += max(len(eq), len(es))
        enc_iguales += sum(1 for a, b in zip(eq, es) if fuzz.ratio(a, b) >= 90)
        fq, fs = tq["filas"], ts["filas"]
        for i in range(max(len(fq), len(fs))):
            rq = fq[i] if i < len(fq) else []
            rs = fs[i] if i < len(fs) else []
            for j in range(max(len(rq), len(rs))):
                cq = _plano(rq[j]) if j < len(rq) else None
                cs = _plano(rs[j]) if j < len(rs) else None
                celdas_total += 1
                if cq is not None and cs is not None and (cq == cs or fuzz.ratio(cq, cs) >= 90):
                    celdas_iguales += 1
                vq = tablas.numero(cq) if cq else None
                if vq is not None:
                    num_total += 1
                    num_iguales += cs is not None and tablas.numero(cs) == vq
    return {"tablas_qwen": len(qwen), "tablas_spark": len(spark),
            "filas_qwen": sum(len(t["filas"]) for t in qwen), "filas_spark": sum(len(t["filas"]) for t in spark),
            "encabezados_iguales": f"{enc_iguales}/{enc_total}",
            "celdas_iguales": f"{celdas_iguales}/{celdas_total}",
            "acuerdo_celdas": round(celdas_iguales / celdas_total, 3) if celdas_total else 1.0,
            "numeros_iguales": f"{num_iguales}/{num_total}",
            "acuerdo_numeros": round(num_iguales / num_total, 3) if num_total else 1.0}


# --- informe -------------------------------------------------------------------------------------------
def _jpeg_b64(png: bytes) -> str:
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    img.thumbnail((1000, 1400))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


def _diff_html(a: str, b: str) -> tuple[str, str]:
    """Diferencias por palabra; conserva los saltos de línea. Devuelve (html_a, html_b)."""
    ta, tb = re.findall(r"\S+|\n", a), re.findall(r"\S+|\n", b)
    sa, sb = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        for lado, toks, clase in ((sa, ta[i1:i2], "del"), (sb, tb[j1:j2], "ins")):
            trozo = " ".join(html.escape(t) for t in toks).replace(" \n ", "\n").replace("\n ", "\n").replace(" \n", "\n")
            lado.append(trozo if op == "equal" or not trozo.strip() else f'<span class="{clase}">{trozo}</span>')
    unir = lambda partes: re.sub(r"(?<!\n) (?=\n)", "", " ".join(partes))
    return unir(sa), unir(sb)


def _tablas_html(propias: list[dict], otras: list[dict]) -> str:
    salida = []
    for k, t in enumerate(propias):
        o = otras[k] if k < len(otras) else {"encabezados": [], "filas": []}
        fila_h = "".join(f"<th>{html.escape(_plano(h))}</th>" for h in t["encabezados"])
        cuerpo = []
        for i, fila in enumerate(t["filas"]):
            celdas = []
            for j, c in enumerate(fila):
                otra = o["filas"][i][j] if i < len(o["filas"]) and j < len(o["filas"][i]) else None
                igual = otra is not None and fuzz.ratio(_plano(c), _plano(otra)) >= 90
                celdas.append(f'<td{"" if igual else " class=dif"}>{html.escape(_plano(c))}</td>')
            cuerpo.append("<tr>" + "".join(celdas) + "</tr>")
        titulo = f"<caption>{html.escape(t.get('titulo') or '')}</caption>" if t.get("titulo") else ""
        salida.append(f"<table>{titulo}<tr>{fila_h}</tr>{''.join(cuerpo)}</table>")
    return "".join(salida) or "<p><em>(sin tablas)</em></p>"


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6b6b70;--bd:#d9d9de;--card:#f6f6f8;--del:#ffd9d6;--ins:#d4f5dc;--dif:#fff1b8}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--fg:#e8e8ea;--mut:#9a9aa2;--bd:#34343a;--card:#1c1c20;
--del:#5a2724;--ins:#1f4a2c;--dif:#4d4318}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1500px;padding:16px}
h1{font-size:22px} h2{font-size:17px;margin-top:40px;border-top:1px solid var(--bd);padding-top:16px}
.met{color:var(--mut);font-size:13px} .grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
.col{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:10px;overflow:auto;max-height:900px}
.col h3{margin:0 0 6px;font-size:14px} pre{white-space:pre-wrap;font:13px/1.45 ui-monospace,monospace;margin:0}
img{width:100%;height:auto} .del{background:var(--del)} .ins{background:var(--ins)}
table{border-collapse:collapse;font-size:12px;margin-bottom:10px;width:100%} caption{text-align:left;font-weight:600}
th,td{border:1px solid var(--bd);padding:3px 5px;vertical-align:top} td.dif{background:var(--dif)}
table.res td,table.res th{font-size:13px}
"""


def informe(p, filas: list[dict], detalle: list[dict]) -> None:
    dir_rev = p.dir_revision / "spark"
    columnas = list(dict.fromkeys(k for f in filas for k in f))
    with (dir_rev / "comparacion.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columnas, delimiter=";", lineterminator="\n")
        w.writeheader()
        w.writerows(filas)

    tx = [f for f in filas if f["tarea"] == "texto" and "similitud" in f]
    tb = [f for f in filas if f["tarea"] == "tablas" and "acuerdo_celdas" in f]
    ok = [f for f in filas if f.get("costo_usd") is not None]
    media = lambda xs: round(sum(xs) / len(xs), 3) if xs else float("nan")
    resumen = [
        f"| Modelo comparado | `{MODELO}` contra la caché (`qwen/qwen3-vl-30b-a3b-instruct`) |",
        f"| Páginas de texto | {len(tx)} · similitud media {media([f['similitud'] for f in tx])} · "
        f"números distintos: solo Qwen {sum(f['numeros_solo_qwen'] for f in tx)}, solo Spark "
        f"{sum(f['numeros_solo_spark'] for f in tx)}, comunes {sum(f['numeros_comunes'] for f in tx)} |",
        f"| Páginas de tablas | {len(tb)} · acuerdo de celdas medio {media([f['acuerdo_celdas'] for f in tb])} · "
        f"acuerdo numérico medio {media([f['acuerdo_numeros'] for f in tb])} · JSON inválido de Spark: "
        f"{sum(1 for f in filas if f['tarea'] == 'tablas' and f.get('json_valido_spark') == 'no')} |",
        f"| Spark | costo US$ {sum(f['costo_usd'] for f in ok):.4f} · {media([f['segundos'] for f in ok])} s por página · "
        f"truncadas {sum(1 for f in ok if f.get('finish_reason') == 'length')} · errores "
        f"{sum(1 for f in filas if f.get('error'))} |",
    ]
    md = [f"# Muse Spark vs caché de visión · {p.nombre} ({p.id})", "", "| | |", "|---|---|", *resumen, "",
          "*Similitud* = parecido por palabras (0–1). *Acuerdo* = celdas en la misma posición con parecido ≥ 90. "
          "Un desacuerdo no dice quién acierta: hay que mirar la imagen (ver `comparacion.html`).", "",
          "## Por página", ""]
    for f in filas:
        if f["tarea"] == "texto" and "similitud" in f:
            md.append(f"- **texto {f['documento']} p{f['pagina']}** (conf. OCR {f.get('conf_ocr')}): similitud "
                      f"{f['similitud']} · palabras Qwen/Spark {f['palabras_qwen']}/{f['palabras_spark']} · números "
                      f"solo Qwen {f['numeros_solo_qwen']}, solo Spark {f['numeros_solo_spark']} · "
                      f"{f['segundos']} s · {f.get('finish_reason')}")
        elif f["tarea"] == "tablas" and "acuerdo_celdas" in f:
            md.append(f"- **tablas {f['documento']} p{f['pagina']}**: tablas {f['tablas_qwen']}/{f['tablas_spark']} · "
                      f"filas {f['filas_qwen']}/{f['filas_spark']} · celdas iguales {f['celdas_iguales']} · "
                      f"números iguales {f['numeros_iguales']} · {f['segundos']} s")
        else:
            md.append(f"- **{f['tarea']} {f['documento']} p{f['pagina']}**: {f.get('error') or 'sin comparar'}")
    (dir_rev / "comparacion.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    cuerpo = [f"<h1>Muse Spark vs caché de visión · {html.escape(p.nombre)} ({p.id})</h1>",
              "<table class=res>" + "".join(
                  "<tr>" + "".join(f"<td>{html.escape(c.strip().strip('`'))}</td>" for c in r.strip('|').split(' | ', 1))
                  + "</tr>" for r in resumen) + "</table>",
              "<p class=met>Rojo: solo en la caché (Qwen). Verde: solo en Spark. Amarillo: celda distinta. "
              "Un desacuerdo no dice quién acierta: compara con la imagen.</p>"]
    for f, d in zip(filas, detalle):
        met = " · ".join(f"{k} {v}" for k, v in f.items() if k not in ("tarea", "documento", "pagina", "error"))
        cuerpo.append(f"<h2>{f['tarea']} · {f['documento']} · página {f['pagina']}</h2><p class=met>"
                      f"{html.escape(f.get('error') or met)}</p>")
        if not d:
            continue
        if f["tarea"] == "texto":
            a, b = _diff_html(d["qwen"], d["spark"])
        else:
            a, b = _tablas_html(d["qwen"], d["spark"]), _tablas_html(d["spark"], d["qwen"])
            if d.get("spark_crudo"):
                b += f"<pre>{html.escape(d['spark_crudo'][:4000])}</pre>"
        envoltura = (lambda x: f"<pre>{x}</pre>") if f["tarea"] == "texto" else (lambda x: x)
        cuerpo.append(f"<div class=grid><div class=col><h3>Página</h3><img alt='página' "
                      f"src='data:image/jpeg;base64,{d['jpeg']}'></div>"
                      f"<div class=col><h3>Caché · Qwen3-VL</h3>{envoltura(a)}</div>"
                      f"<div class=col><h3>Spark · {html.escape(MODELO)}</h3>{envoltura(b)}</div></div>")
    (dir_rev / "comparacion.html").write_text(
        f"<!doctype html><html lang=es><meta charset=utf-8><meta name=viewport content='width=device-width,"
        f"initial-scale=1'><title>Spark vs caché {p.id}</title><style>{CSS}</style><body>{''.join(cuerpo)}</body></html>",
        encoding="utf-8")


# --- principal ------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proyecto", default="RITP")
    ap.add_argument("--modelos", action="store_true", help="comprueba la clave y lista los modelos disponibles")
    ap.add_argument("--sin-api", action="store_true", help="solo muestra, imágenes y chequeo contra la caché")
    ap.add_argument("--limite", type=int, help="a lo más N páginas por tarea")
    ap.add_argument("--n-texto", type=int, default=10)
    ap.add_argument("--n-tablas", type=int, default=10)
    ap.add_argument("--nueva-muestra", action="store_true", help="rehace revision/spark/muestra.csv")
    ap.add_argument("--paginas", help="tarea:DOC:pág,… p. ej. texto:RITP.D01:5,tablas:RITP.D10:3")
    ap.add_argument("--pdfs", type=Path, help="carpeta con los PDF de la concesión (por defecto ../Superdocumento/…)")
    ap.add_argument("--hilos", type=int, default=4)
    ap.add_argument("--forzar", action="store_true", help="vuelve a pedir a Spark aunque esté en caché")
    args = ap.parse_args(argv)

    if args.modelos:
        ids = listar_modelos()
        print("\n".join(ids))
        print(f"\n{MODELO}: {'disponible' if MODELO in ids else 'NO aparece en la lista'}")
        return

    p = indice.proyectos(args.proyecto)[0]
    muestra = leer_o_crear_muestra(p, args)
    if args.limite:
        muestra = [f for t in ("texto", "tablas") for f in [m for m in muestra if m["tarea"] == t][:args.limite]]
    print(f"{p.id}: {sum(m['tarea'] == 'texto' for m in muestra)} páginas de texto · "
          f"{sum(m['tarea'] == 'tablas' for m in muestra)} de tablas · modelo {MODELO}")

    # Render en el hilo principal (pdfium no es seguro entre hilos) y chequeo contra la caché de Qwen
    trabajos = []
    for m in muestra:
        doc = p.doc(m["documento"])
        png = png_pagina(p, doc, m["pagina"], args.pdfs)
        # Bytes PNG idénticos → misma clave que la caché de Qwen. Otra versión de pdfium/Pillow/zlib puede
        # comprimir distinto la misma imagen, así que además se comparan los parámetros guardados en la caché.
        clave = clave_cache_qwen(PROMPTS[m["tarea"]], png)
        png_identico = (p.carpeta / "cache" / "llm" / "vision" / f"{clave}.json").exists()
        if m["tarea"] == "texto":
            qwen = json.loads(vlm.ruta_vlm(p, doc.id, m["pagina"]).read_text(encoding="utf-8"))
            conf = qwen["calidad_ocr"]["conf_media"]
            params = qwen.get("dpi") == config.VLM_DPI and qwen.get("version_prompt") == vlm.VERSION_PROMPT
            qwen = qwen["texto_vlm"]
        else:
            qwen = json.loads(tablas._ruta_json(p, doc.id, m["pagina"]).read_text(encoding="utf-8"))
            params = qwen.get("version_prompt") == tablas.VERSION_PROMPT_JSON
            qwen = qwen["tablas"]
            conf = None
        trabajos.append({**m, "png": png, "png_identico": "si" if png_identico else "no",
                         "mismos_parametros": "si" if params else "no", "qwen": qwen, "conf_ocr": conf})
        print(f"  {m['tarea']:<6} {doc.id} p{m['pagina']:04d} · DPI/prompt iguales a la caché: "
              f"{'sí' if params else 'NO'} · PNG idéntico byte a byte: {'sí' if png_identico else 'no'}")
    if args.sin_api:
        return

    _clave_api()

    def una(t):
        ruta = ruta_spark(p, t["tarea"], t["documento"], t["pagina"])
        if ruta.exists() and not args.forzar:
            return json.loads(ruta.read_text(encoding="utf-8")), True
        r = pedir_spark(t["tarea"], t["png"])
        r.update({"tarea": t["tarea"], "documento": t["documento"], "pagina": t["pagina"]})
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        return r, False

    filas, detalle = [], []
    with ThreadPoolExecutor(args.hilos) as pool:
        futuros = [pool.submit(una, t) for t in trabajos]
        for i, (t, fut) in enumerate(zip(trabajos, futuros), 1):
            base = {"tarea": t["tarea"], "documento": t["documento"], "pagina": t["pagina"],
                    "conf_ocr": t["conf_ocr"], "mismos_parametros": t["mismos_parametros"],
                    "png_identico": t["png_identico"]}
            try:
                r, de_cache = fut.result()
            except Exception as e:
                print(f"  [{i}/{len(trabajos)}] {t['tarea']} {t['documento']} p{t['pagina']:04d} · error: {str(e)[:200]}")
                filas.append({**base, "error": str(e)[:300]})
                detalle.append(None)
                continue
            uso = r.get("usage") or {}
            info = {"segundos": r["segundos"], "tokens_entrada": uso.get("prompt_tokens"),
                    "tokens_salida": uso.get("completion_tokens"),
                    "tokens_razonamiento": (uso.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                    "costo_usd": r["costo_usd"],
                    "finish_reason": r.get("finish_reason")}
            d = {"jpeg": _jpeg_b64(t["png"]), "qwen": t["qwen"]}
            if t["tarea"] == "texto":
                comp = comparar_texto(t["qwen"], r["texto"])
                d["spark"] = r["texto"]
                resumen = f"similitud {comp['similitud']:.2f}"
            else:
                problema = tablas._validar(r["texto"]) if r["texto"] else "respuesta vacía"
                if problema:
                    comp = {"json_valido_spark": "no", "problema_json": problema}
                    d["spark"], d["spark_crudo"] = [], r["texto"]
                    try:                                # un JSON con filas desparejas igual se puede comparar
                        d["spark"] = [t_.model_dump() for t_ in tablas._leer_respuesta(r["texto"]).tablas]
                        comp.update(comparar_tablas(t["qwen"], d["spark"]))
                    except Exception:
                        pass
                else:
                    d["spark"] = [t_.model_dump() for t_ in tablas._leer_respuesta(r["texto"]).tablas]
                    comp = {"json_valido_spark": "si", **comparar_tablas(t["qwen"], d["spark"])}
                resumen = (f"celdas iguales {comp.get('celdas_iguales', '?')}"
                           + (f" · JSON inválido: {comp['problema_json']}" if comp.get("problema_json") else ""))
            filas.append({**base, **comp, **info})
            detalle.append(d)
            print(f"  [{i}/{len(trabajos)}] {t['tarea']:<6} {t['documento']} p{t['pagina']:04d} · {resumen} · "
                  f"{r['segundos']} s · US$ {r['costo_usd']:.5f}{' (caché)' if de_cache else ''}"
                  f"{' · ⚠ truncada' if r.get('finish_reason') == 'length' else ''}", flush=True)

    informe(p, filas, detalle)
    print(f"\n→ {p.dir_revision / 'spark' / 'comparacion.md'}\n→ {p.dir_revision / 'spark' / 'comparacion.html'}")


if __name__ == "__main__":
    sys.exit(main())
