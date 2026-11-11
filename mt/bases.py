"""Etapa 4: estructura de las Bases de Licitación (el esqueleto del documento único).

1. El índice de las propias Bases (primeras páginas) da la lista de numerales y títulos esperados.
2. En el cuerpo se buscan líneas que empiezan con un numeral: se aceptan las que están en el índice (o tienen
   título en mayúsculas) y se queda la subsecuencia creciente más larga (descarta filas de tablas, citas, etc.).
3. Las entradas del índice que no aparecen se buscan por título con rapidfuzz; si aún faltan, el LLM elige la
   línea del encabezado entre las candidatas (nunca escribe texto). `revision/estructura_correcciones.csv`
   (numero;linea;titulo) agrega o corrige encabezados a mano.
4. Cada sección guarda sus párrafos como listas de índices de línea → `cache/estructura/bases.json`.
"""
from __future__ import annotations

import csv
import json
import re

from pydantic import BaseModel
from rapidfuzz import fuzz

from . import llm
from .indice import Documento, Proyecto
from .numerales import RE_NUMERAL, clave_numero, creciente_mas_larga, orden_numero
from .texto import agrupar_parrafos, leer_lineas

# Partes de primer nivel: "1. BASES ADMINISTRATIVAS", "4. ANEXO N°1" (el OCR a veces lee "5." como "S.")
_RE_PARTE = re.compile(r"^\s*(\d{1,2}|S)\s*[.)]?\s+((?:BASES\s+[A-ZÁÉÍÓÚ]+|ANEXO\b).*)$")
_RE_ANEXO = re.compile(r"^\s*ANEXO\s*N?\s*[°º*o9]?\s*(\d{1,2})\b(.*)$", re.I)
_RE_RELLENO = re.compile(r"\s*(\.{2,}|…).*$")
# numeral solo en su línea ("1.5.8"): en Bases antiguas el título del artículo está únicamente en el índice
MIN_SOLOS = 0.25
_RE_NUMERAL_SOLO = re.compile(r"^\s*((?:\d{1,2}|[IL]{1,3})(?:\s?[.,]\s?\d{1,2}){1,5})[.\-)]?\s*$")


def ruta_estructura(p: Proyecto):
    return p.carpeta / "cache" / "estructura" / "bases.json"


def leer_estructura(p: Proyecto) -> dict:
    return json.loads(ruta_estructura(p).read_text(encoding="utf-8"))


def documento_bases(p: Proyecto) -> Documento:
    for d in p.documentos:
        if d.subtipo.lower().startswith("bases licitacion"):
            return d
    raise ValueError(f"{p.id}: el INDICE no tiene un documento 'bases licitacion'")


def _limpiar_titulo(t: str) -> str:
    """Quita los puntos de relleno y el número de página de una entrada del índice."""
    t = _RE_RELLENO.sub("", t)
    t = re.sub(r"\s+\d{1,3}\s*$", "", t)
    return t.strip(" .-")


def _es_titulo(t: str) -> bool:
    letras = [c for c in t if c.isalpha()]
    return bool(letras) and len(t) <= 160 and sum(c.isupper() for c in letras) / len(letras) >= 0.6


def _paginas_indice(lineas: list[dict]) -> set[int]:
    """Páginas del índice: al comienzo, con muchas líneas que empiezan con numeral."""
    por_pag: dict[int, list[dict]] = {}
    for ln in lineas:
        por_pag.setdefault(ln["pag"], []).append(ln)
    total = max(por_pag) if por_pag else 0
    salida = set()
    for pag, ls in por_pag.items():
        if pag > max(12, total // 8):
            continue
        con_numeral = sum(1 for ln in ls if RE_NUMERAL.match(ln["texto"]) or _RE_PARTE.match(ln["texto"]))
        if con_numeral >= 8 and con_numeral >= 0.4 * len(ls):
            salida.add(pag)
    return salida


def _entradas_indice(lineas: list[dict], paginas: set[int]) -> list[dict]:
    entradas, vistas = [], set()
    for ln in lineas:
        if ln["pag"] not in paginas:
            continue
        m = RE_NUMERAL.match(ln["texto"])
        if not m:
            continue
        clave = clave_numero(m[1])
        if clave in vistas:
            continue
        vistas.add(clave)
        entradas.append({"clave": clave, "titulo": _limpiar_titulo(m[2]), "linea": ln["i"]})
    # El índice también numera en orden creciente: una entrada fuera de orden es un error de OCR ("12.1" por 1.2.1)
    validas = creciente_mas_larga([orden_numero(e["clave"]) for e in entradas])
    return [e for k, e in enumerate(entradas) if k in validas]


class _Eleccion(BaseModel):
    linea: int | None


def _buscar_con_llm(p: Proyecto, entrada: dict, candidatas: list[dict]) -> int | None:
    if not candidatas:
        return None
    listado = "\n".join(f"[{ln['i']}] {ln['texto'][:150]}" for ln in candidatas)
    prompt = (f"En estas líneas OCR de unas Bases de Licitación debería estar el encabezado del numeral "
              f"{entrada['clave']} «{entrada['titulo']}» (el OCR puede haber leído mal el número o el título).\n"
              f"Indica el número entre corchetes de la línea que es ese encabezado, o null si no está.\n\n{listado}")
    ids = {ln["i"] for ln in candidatas}
    r = llm.pedir_json(p.carpeta / "cache" / "llm", "bases_encabezado", prompt, _Eleccion,
                       validar=lambda o: None if o.linea is None or o.linea in ids else "línea fuera de la lista")
    return r.linea


def _correcciones(p: Proyecto) -> list[dict]:
    ruta = p.dir_revision / "estructura_correcciones.csv"
    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        return [f for f in csv.DictReader(fh, delimiter=";") if (f.get("linea") or "").strip()]


def estructurar(p: Proyecto, usar_llm: bool = True) -> dict:
    doc = documento_bases(p)
    lineas = leer_lineas(p, doc.id)
    utiles = [ln for ln in lineas if not ln["marginal"]]
    pags_indice = _paginas_indice(utiles)
    indice = _entradas_indice(utiles, pags_indice)
    titulo_indice = {e["clave"]: e["titulo"] for e in indice}

    # 1) candidatos a encabezado en el cuerpo
    # numerales solos como encabezado: solo si el documento usa ese estilo (≥ MIN_SOLOS de las entradas del índice);
    # si no, un numeral suelto suele ser una celda de tabla
    solos = {clave_numero(m[1]) for ln in utiles if ln["pag"] not in pags_indice
             and (m := _RE_NUMERAL_SOLO.match(ln["texto"])) and clave_numero(m[1]) in titulo_indice}
    usar_solos = bool(indice) and len(solos) >= MIN_SOLOS * len(indice)
    cand = []
    for ln in utiles:
        if ln["pag"] in pags_indice:
            continue
        t = ln["texto"]
        if m := _RE_PARTE.match(t):
            cand.append({"clave": "5" if m[1] == "S" else m[1], "titulo": m[2].strip(), "linea": ln["i"], "origen": "regla"})
        elif m := _RE_ANEXO.match(t):
            if len(t) <= 120:
                cand.append({"clave": f"A{int(m[1])}", "titulo": t.strip(), "linea": ln["i"], "origen": "regla"})
        elif usar_solos and (m := _RE_NUMERAL_SOLO.match(t)) and (
                clave_numero(m[1]) in titulo_indice or clave_numero(m[1]).count(".") >= 2):
            # también los que faltan en el índice (página del índice ilegible): el orden creciente filtra
            clave = clave_numero(m[1])
            cand.append({"clave": clave, "titulo": t.strip(), "linea": ln["i"],
                         "origen": "indice" if clave in titulo_indice else "regla",
                         "titulo_indice": titulo_indice.get(clave)})
        elif m := RE_NUMERAL.match(t):
            clave, titulo = clave_numero(m[1]), m[2].strip()
            esperado = titulo_indice.get(clave)
            parecido = fuzz.partial_ratio(esperado.lower(), titulo.lower()) if esperado else 0
            if (esperado and (parecido >= 60 or _es_titulo(titulo))) or (not esperado and _es_titulo(titulo)):
                cand.append({"clave": clave, "titulo": titulo, "linea": ln["i"],
                             "origen": "indice" if esperado else "regla"})

    # 2) orden creciente (los anexos van después de todo el articulado)
    def orden(c):
        return (1000 + int(c["clave"][1:]),) if c["clave"].startswith("A") else orden_numero(c["clave"])
    elegidos = creciente_mas_larga([orden(c) for c in cand])
    heads = [c for k, c in enumerate(cand) if k in elegidos]

    # 3) entradas del índice que faltan: rapidfuzz por título y luego LLM, entre los vecinos encontrados
    encontrados = {h["clave"] for h in heads}
    for e in indice:
        if e["clave"] in encontrados:
            continue
        previos = [h for h in heads if orden(h) < orden_numero(e["clave"])]
        siguientes = [h for h in heads if orden(h) > orden_numero(e["clave"])]
        a = max(previos, key=orden)["linea"] if previos else 0
        b = min(siguientes, key=orden)["linea"] if siguientes else len(lineas)
        rango = [ln for ln in utiles if a < ln["i"] < b and ln["pag"] not in pags_indice and len(ln["texto"]) <= 160]
        mejor = max(rango, key=lambda ln: fuzz.partial_ratio(e["titulo"].lower(), ln["texto"].lower()), default=None)
        linea, origen = None, None
        if (mejor and len(e["titulo"]) >= 6 and len(mejor["texto"]) >= 0.6 * len(e["titulo"])
                and fuzz.partial_ratio(e["titulo"].lower(), mejor["texto"].lower()) >= 85):
            linea, origen = mejor["i"], "titulo"
        elif usar_llm and rango:
            try:
                linea, origen = _buscar_con_llm(p, e, [ln for ln in rango if _es_titulo(ln["texto"]) or RE_NUMERAL.match(ln["texto"])][:120]), "llm"
            except llm.SinModelos as err:
                print(f"    {e['clave']}: sin LLM disponible ({str(err)[:80]})")
        if linea is not None and linea not in {h["linea"] for h in heads}:
            heads.append({"clave": e["clave"], "titulo": lineas[linea]["texto"], "linea": linea, "origen": origen})
            encontrados.add(e["clave"])

    for c in _correcciones(p):
        heads = [h for h in heads if h["clave"] != clave_numero(c["numero"])]
        heads.append({"clave": clave_numero(c["numero"]), "titulo": c.get("titulo") or lineas[int(c["linea"])]["texto"],
                      "linea": int(c["linea"]), "origen": "manual"})
    heads.sort(key=lambda h: h["linea"])

    # 4) secciones con sus párrafos
    secciones, usados = [], set()
    inicio_cuerpo = heads[0]["linea"] if heads else len(lineas)
    previas = [ln for ln in utiles if ln["i"] < inicio_cuerpo]
    secciones.append(_seccion(p, "0", "[Portada e índice de las Bases]", [], previas, "sintetica", usados))
    for k, h in enumerate(heads):
        fin = heads[k + 1]["linea"] if k + 1 < len(heads) else len(lineas)
        cuerpo = [ln for ln in utiles if h["linea"] < ln["i"] < fin]
        titulo_lineas = [h["linea"]]
        # título que sigue en la línea siguiente, del mismo párrafo y en mayúsculas ("…DENOMINADO / OFERTA ECONÓMICA")
        if cuerpo and cuerpo[0]["parrafo"] == lineas[h["linea"]]["parrafo"] and _es_titulo(cuerpo[0]["texto"]) \
                and len(cuerpo[0]["texto"]) <= 80:
            titulo_lineas.append(cuerpo.pop(0)["i"])
        titulo = " ".join(lineas[i]["texto"] for i in titulo_lineas)
        if h.get("titulo_indice"):                              # texto del índice, no del cuerpo: entre corchetes
            titulo += f" [{h['titulo_indice']}]"
        secciones.append(_seccion(p, h["clave"], titulo, titulo_lineas, cuerpo, h["origen"], usados))

    faltan = [e for e in indice if e["clave"] not in {h["clave"] for h in heads}]
    datos = {"documento": doc.id, "paginas_indice": sorted(pags_indice), "indice": indice,
             "faltan": faltan, "secciones": secciones}
    ruta = ruta_estructura(p)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    _exportar(p, datos, lineas)
    cubiertas = len(indice) - len(faltan)
    print(f"  {doc.id}: {len(secciones) - 1} secciones · índice con {len(indice)} entradas, "
          f"{cubiertas} encontradas ({100 * cubiertas / max(1, len(indice)):.0f} %)"
          + (f" · faltan: {', '.join(e['clave'] for e in faltan[:15])}" if faltan else ""))
    return datos


def _seccion(p: Proyecto, clave: str, titulo: str, titulo_lineas: list[int], cuerpo: list[dict], origen: str,
             usados: set) -> dict:
    sid = f"{p.id}.S{clave}"
    k = 2
    while sid in usados:
        sid, k = f"{p.id}.S{clave}-{k}", k + 1
    usados.add(sid)
    parrafos = [{"id": f"P{n}", "lineas": [ln["i"] for ln in g]} for n, g in enumerate(agrupar_parrafos(cuerpo), 1)]
    nivel = 1 if clave.startswith("A") or clave in ("0",) else clave.count(".") + 1
    pags = sorted({ln["pag"] for ln in cuerpo})
    return {"id": sid, "numero": clave, "titulo": titulo, "nivel": nivel, "titulo_lineas": titulo_lineas,
            "parrafos": parrafos, "pags": [pags[0], pags[-1]] if pags else [], "origen": origen}


def _exportar(p: Proyecto, datos: dict, lineas: list[dict]):
    p.dir_revision.mkdir(parents=True, exist_ok=True)
    with (p.dir_revision / "estructura_bases.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\n")
        w.writerow(["numero", "titulo", "pagina", "linea", "parrafos", "origen"])
        for s in datos["secciones"]:
            linea = s["titulo_lineas"][0] if s["titulo_lineas"] else ""
            pag = lineas[linea]["pag"] if linea != "" else ""
            w.writerow([s["numero"], s["titulo"], pag, linea, len(s["parrafos"]), s["origen"]])
        for e in datos["faltan"]:
            w.writerow([e["clave"], e["titulo"], "", "", "", "FALTA (agregar en estructura_correcciones.csv)"])


def mapa_secciones(estructura: dict) -> dict[str, dict]:
    """clave de numeral → sección (la primera si se repite)."""
    salida: dict[str, dict] = {}
    for s in estructura["secciones"]:
        salida.setdefault(s["numero"], s)
    return salida
