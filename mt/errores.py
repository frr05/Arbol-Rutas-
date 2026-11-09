"""Dónde falla el pipeline: cada pregunta mal respondida por el RAG se asigna a una etapa, y se lleva la cuenta.

Para cada pregunta que no salió correcta en `evaluar`, la columna `fuente` del CSV de preguntas (artículos "1.9.5",
ids de nodo "(BL:1.3.1-i15)" o actos "DS MOP N°136") dice dónde está la respuesta. Con eso, reglas sin LLM:
  estructura    el artículo de la fuente no existe en el árbol (bases.py no lo detectó)
  ocr           las cifras de la referencia no están en el texto literal ni en las tablas de visión de la fuente
  recuperacion  la fuente está bien, pero ningún chunk suyo quedó entre los encontrados (chunks.py / vectores.py)
  generacion    un chunk de la fuente estuvo en el contexto y el modelo respondió mal o incompleto (prompt / modelo)
  sin_fuente    la pregunta no tiene una fuente que se pueda ubicar en el árbol
La revisión a mano (`revision/errores_revision.csv`: set;n;etapa;detalle) manda sobre las reglas; ahí también van
las etapas que las reglas no ven: `cambios` (relaciones/vincular no aplicaron un acto), `referencia` (la referencia o
la pregunta estaban mal o incompletas) y `juez` (la respuesta es correcta y el juez la castiga por datos extra
verdaderos o por no repetir algo que la pregunta no pedía).
Cada corrida agrega sus filas a `revision/errores_pipeline.csv` (así se ve si una mejora baja los errores) y rehace
`revision/errores_pipeline.md` con los conteos.
"""
from __future__ import annotations

import csv
import hashlib
import re
import time
from collections import Counter, defaultdict

from .actos import actos_nombrados
from .arbol import leer_arbol
from .indice import Proyecto
from .numerales import clave_numero

CAMPOS = ["fecha", "set", "n", "tema", "veredicto", "etapa", "origen", "detalle", "fuente", "fuente_en_contexto",
          "chunks_hallados"]
MODULO = {"estructura": "bases.py (títulos y numerales del cuerpo)", "ocr": "ocr.py / texto.py / tablas.py",
          "cambios": "relaciones.py / vincular.py", "recuperacion": "chunks.py / vectores.py (búsqueda)",
          "generacion": "vectores.responder (prompt, contexto, modelo)", "referencia": "preguntas de evaluación (referencia incompleta o equivocada)",
          "ficha": "arbol.ficha (conteos de actos)",
          "tablas": "tablas.py (detección de páginas con tabla)",
          "juez": "evaluar._juzgar (castiga datos extra verdaderos o lo que la pregunta no pedía)",
          "sin_fuente": "preguntas de evaluación (fuente)"}
_RE_NUMERAL = re.compile(r"(?<![\d.,])(\d{1,2}(?:\.\d{1,2}){1,5})(?!\d|[.,]\d)")
_RE_CIFRA = re.compile(r"\d+(?:[.,]\d+)*")


def _plano(t: str) -> str:
    return re.sub(r"\s+", " ", t)


class Clasificador:
    def __init__(self, p: Proyecto):
        self.p = p
        arbol = leer_arbol(p)
        self.nodos = {n["id"]: n for n in arbol["nodos"]}
        self.actos = arbol["actos"]
        self.articulos: dict[str, str] = {}                     # clave de numeral → id del nodo
        for n in arbol["nodos"]:
            if n["tipo"] in ("articulo", "capitulo", "parte") and n.get("numero") and n["id"].startswith("BL:"):
                self.articulos.setdefault(clave_numero(n["numero"]), n["id"])

    # --- fuente de la referencia ----------------------------------------------------------------------
    def actos_citados(self, fuente: str) -> set[str]:
        return actos_nombrados(fuente, self.actos)

    def fuente(self, texto: str) -> tuple[set[str], set[str], list[str]]:
        """(nodos de artículo o fragmento, actos, numerales que no existen en el árbol)."""
        nodos, faltan = set(), []
        for i in re.findall(rf"\(((?:BL|{re.escape(self.p.id)})[:.][^)\s·]+)", texto):
            i = i.split("#")[0].split("@")[0]
            if i in self.nodos:
                nodos.add(i)
        sin_ids = re.sub(r"\([^)]*\)", " ", texto)
        for num in _RE_NUMERAL.findall(re.sub(r"págs?\.\s*[\d–-]+", " ", sin_ids)):
            nid = self.articulos.get(clave_numero(num))
            if nid:
                nodos.add(nid)
            else:
                faltan.append(num)
        return nodos, self.actos_citados(texto), faltan

    def descendientes(self, nid: str) -> list[str]:
        salida, pila = [], [nid]
        while pila:
            x = pila.pop()
            salida.append(x)
            pila.extend(self.nodos[x].get("hijos", []))
        return salida

    def literal(self, nodos: set[str], actos: set[str]) -> str:
        textos = []
        for nid in nodos:
            for x in self.descendientes(nid):
                n = self.nodos[x]
                textos += [v["texto"] for v in n.get("versiones", [])[:1]]
                if n["tipo"] == "tabla":
                    textos += [" ".join(f) for f in n["tabla"]["filas"]]
        for n in self.nodos.values():
            v = (n.get("versiones") or [{}])[0]
            if (v.get("acto") or {}).get("id") in actos:
                textos.append(v["texto"])
                if n["tipo"] == "tabla":
                    textos += [" ".join(f) for f in n["tabla"]["filas"]]
        return _plano(" ".join(textos))

    def en_fuente(self, chunk: dict, nodos: set[str], actos: set[str]) -> bool:
        m = chunk["metadatos"]
        arts = {nid for nid in nodos if nid.startswith("BL:")}
        art = (m.get("articulo") or {}).get("id") or ""
        if any(art == a or art.startswith(a + ".") for a in arts):
            return True
        if m.get("id_nodo") in nodos or any(m.get("id_nodo", "").startswith(n + "-") for n in nodos):
            return True
        return bool(actos & ({(m.get("acto") or {}).get("id")} | set(m.get("actos") or [])))

    # --- clasificación ---------------------------------------------------------------------------------
    def clasificar(self, referencia: str, fuente: str, hallados: list[dict]) -> tuple[str, str, bool]:
        nodos, actos, faltan = self.fuente(fuente)
        en_contexto = any(self.en_fuente(x, nodos, actos) for c in hallados for x in [c] + c.get("incluidos", []))
        if faltan and not nodos and not actos:
            return "estructura", f"el árbol no tiene el artículo {', '.join(faltan)}", en_contexto
        if not nodos and not actos:
            return "sin_fuente", "la fuente no se pudo ubicar en el árbol", en_contexto
        literal = self.literal(nodos, actos)
        cifras = [c for c in _RE_CIFRA.findall(referencia) if len(c.replace(".", "").replace(",", "")) >= 2
                  and not _RE_NUMERAL.fullmatch(c)]
        if cifras:
            faltantes = [c for c in cifras if c not in literal]
            if len(faltantes) > len(cifras) / 2:
                return "ocr", f"cifras de la referencia que no están en el texto de la fuente: {', '.join(faltantes[:6])}", en_contexto
        if faltan:
            detalle = f"; además el árbol no tiene {', '.join(faltan)}"
        else:
            detalle = ""
        if not en_contexto:
            return "recuperacion", "ningún chunk de la fuente quedó entre los encontrados" + detalle, en_contexto
        return "generacion", "la fuente estuvo en el contexto y la respuesta quedó mal o incompleta" + detalle, en_contexto


def huella(respuesta: str) -> str:
    """Identifica la respuesta revisada: una revisión manual solo vale para esa misma respuesta."""
    return hashlib.sha1(respuesta.strip().encode("utf-8")).hexdigest()[:10]


def _revision(p: Proyecto) -> dict[tuple[str, str], dict]:
    ruta = p.dir_revision / "errores_revision.csv"
    if not ruta.exists():
        return {}
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        filas = list(csv.DictReader(fh, delimiter=";", restkey="resto"))
    for f in filas:                                          # un ";" dentro del detalle no lo corta
        f["detalle"] = ";".join([f.get("detalle") or ""] + f.pop("resto", []))
    return {(f["set"], f["n"]): f for f in filas}


def registrar(p: Proyecto, nombre_set: str, filas: list[dict], hallados: dict[int, list[dict]]) -> list[dict]:
    """Clasifica las preguntas no correctas de una corrida y las agrega a `errores_pipeline.csv`."""
    cl = Clasificador(p)
    manual = _revision(p)
    fecha = time.strftime("%Y-%m-%d %H:%M")
    nuevas = []
    for f in filas:
        if f["veredicto"] == "correcta":
            continue
        etapa, detalle, en_ctx = cl.clasificar(f["referencia"], f.get("fuente_ref", ""), hallados.get(f["n"], []))
        origen = "reglas"
        m = manual.get((nombre_set, str(f["n"])))
        if m and m.get("etapa") and m.get("huella") in (None, "", huella(f.get("respuesta", ""))):
            etapa, detalle, origen = m["etapa"], m.get("detalle") or detalle, "revisión"
        nuevas.append({"fecha": fecha, "set": nombre_set, "n": f["n"], "tema": f.get("tema", ""),
                       "veredicto": f["veredicto"], "etapa": etapa, "origen": origen, "detalle": detalle,
                       "fuente": f.get("fuente_ref", ""), "fuente_en_contexto": "sí" if en_ctx else "no",
                       "chunks_hallados": " · ".join(c["id"] for c in hallados.get(f["n"], []))})
    ruta = p.dir_revision / "errores_pipeline.csv"
    nuevo = not ruta.exists()
    with ruta.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS, delimiter=";", lineterminator="\n")
        if nuevo:
            w.writeheader()
        w.writerows(nuevas)
    resumen(p)
    c = Counter(x["etapa"] for x in nuevas)
    print(f"  errores por etapa ({nombre_set}): " + (" · ".join(f"{k} {v}" for k, v in c.most_common()) or "ninguno"))
    return nuevas


def aplicar_revision(p: Proyecto):
    """Aplica `errores_revision.csv` a la última corrida de cada set (sin volver a evaluar) y rehace el resumen."""
    ruta = p.dir_revision / "errores_pipeline.csv"
    with ruta.open(encoding="utf-8", newline="") as fh:
        filas = list(csv.DictReader(fh, delimiter=";"))
    ultima = {}
    for f in filas:
        ultima[f["set"]] = max(ultima.get(f["set"], ""), f["fecha"])
    manual = _revision(p)
    for f in filas:
        m = manual.get((f["set"], f["n"]))
        if f["fecha"] == ultima[f["set"]] and m and m.get("etapa"):
            f["etapa"], f["detalle"], f["origen"] = m["etapa"], m.get("detalle") or f["detalle"], "revisión"
    with ruta.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS, delimiter=";", lineterminator="\n")
        w.writeheader()
        w.writerows(filas)
    resumen(p)


def resumen(p: Proyecto):
    """`errores_pipeline.md`: última corrida de cada set por etapa y tema, e historial de corridas."""
    with (p.dir_revision / "errores_pipeline.csv").open(encoding="utf-8", newline="") as fh:
        filas = list(csv.DictReader(fh, delimiter=";"))
    corridas: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for f in filas:
        corridas[f["set"]][f["fecha"]].append(f)
    ultimas = {s: fs[max(fs)] for s, fs in corridas.items()}
    md = [f"# Errores del pipeline · {p.id}", "",
          "Cada pregunta mal respondida por el RAG (`evaluar`) se asigna a la etapa donde se pierde la respuesta "
          "(reglas de `mt/errores.py`, corregidas a mano en `revision/errores_revision.csv`).", "",
          "## Última corrida, por etapa", "", "| Etapa | Qué mejorar | " + " | ".join(ultimas) + " | Total |",
          "|---|---|" + "---:|" * (len(ultimas) + 1)]
    etapas = Counter(f["etapa"] for fs in ultimas.values() for f in fs)
    for e, total in etapas.most_common():
        md.append(f"| {e} | {MODULO.get(e, '')} | " + " | ".join(str(sum(f['etapa'] == e for f in fs))
                                                            for fs in ultimas.values()) + f" | {total} |")
    for s, fs in ultimas.items():
        temas = sorted({f["tema"] for f in fs if f["tema"]})
        if temas:
            md += ["", f"## {s}: errores por tema y etapa", "", "| Tema | " + " | ".join(sorted(etapas)) + " |",
                   "|---|" + "---:|" * len(etapas)]
            for t in temas:
                md.append(f"| {t} | " + " | ".join(str(sum(f['tema'] == t and f['etapa'] == e for f in fs))
                                                  for e in sorted(etapas)) + " |")
        md += ["", f"## {s}: detalle", ""] + [f"- **{f['n']}** ({f['veredicto']}, {f['etapa']}, {f['origen']}): "
                                               f"{f['detalle']} — fuente: {f['fuente'][:90]}" for f in fs]
    ruta_h = p.dir_revision / "hallazgos_pipeline.csv"
    if ruta_h.exists():                                       # errores vistos al revisar, sin pregunta asociada
        with ruta_h.open(encoding="utf-8-sig", newline="") as fh:
            hallazgos = list(csv.DictReader(fh, delimiter=";"))
        md += ["", "## Otros errores encontrados al revisar (sin pregunta asociada)", "",
               "| Etapa | Dónde | Detalle |", "|---|---|---|"] + [f"| {h['etapa']} | {h['donde']} | {h['detalle']} |"
                                                               for h in hallazgos]
    md += ["", "## Historial", "", "| Fecha | Set | Errores | " + " | ".join(sorted(etapas)) + " |",
           "|---|---|---:|" + "---:|" * len(etapas)]
    for s, fs in corridas.items():
        for fecha in sorted(fs):
            md.append(f"| {fecha} | {s} | {len(fs[fecha])} | " + " | ".join(str(sum(f['etapa'] == e for f in fs[fecha]))
                                                                      for e in sorted(etapas)) + " |")
    (p.dir_revision / "errores_pipeline.md").write_text("\n".join(md) + "\n", encoding="utf-8")
