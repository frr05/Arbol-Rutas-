"""Embeddings de los chunks y consulta RAG (recuperación + respuesta citada).

1. `vectorizar()`: vectoriza el `texto_embedding` de cada chunk con `embeber: true` (modelo de OpenRouter,
   por defecto `baai/bge-m3`, multilingüe) → `EMBEDDINGS_<ID>.npz` con `ids`, `hashes`, `vectores` (float16,
   normalizados) y `modelo`. Los vectores cuyo texto no cambió se reutilizan: repetir la etapa no gasta.
2. `buscar()`: búsqueda híbrida — similitud coseno y BM25 por palabras exactas para la pregunta y sus
   reformulaciones (`reformular()`, LLM barato con el vocabulario de los contratos), fusionados con Reciprocal Rank
   Fusion —, con filtro por metadatos (por defecto `vigente = true`). Si la pregunta cita un numeral
   ("artículo 1.6.3"), los chunks de ese artículo entran primero.
3. `responder()`: el LLM responde SOLO con los chunks recuperados y cita cada uno con [n]. Si un chunk es el
   resumen de una tabla, se le agregan las filas de `TABLAS_<ID>.json`.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

import numpy as np
from pydantic import BaseModel

from . import config, llm
from .indice import Proyecto
from .actos import actos_nombrados
from .numerales import citas, clave_numero

MODELO = config.env("EMBEDDINGS_MODELO", "baai/bge-m3")
LOTE = 64
MAX_CARACTERES = 8000                     # bge-m3 acepta ~8 000 tokens; los chunks miden ≤ ~2 600 caracteres
MODELOS_RAG = [m.strip() for m in config.env(
    "LLM_MODELOS_RAG", "qwen/qwen3.7-flash,deepseek/deepseek-chat-v3.1").split(",") if m.strip()]


# prueba: `RAG_FUENTE=documento` usa los chunks cortados del documento único (`chunks_doc.py`), en archivos aparte
SUFIJO = "_DOC" if config.env("RAG_FUENTE", "arbol").lower() == "documento" else ""


def _ruta(p: Proyecto):
    return p.carpeta / f"EMBEDDINGS_{p.id}{SUFIJO}.npz"


def _hash(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:16]


def leer_chunks(p: Proyecto) -> list[dict]:
    with (p.carpeta / f"CHUNKS_{p.id}{SUFIJO}.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(l) for l in fh]


def leer_vectores(p: Proyecto) -> dict | None:
    ruta = _ruta(p)
    if not ruta.exists():
        return None
    z = np.load(ruta, allow_pickle=False)
    return {"ids": list(z["ids"]), "hashes": list(z["hashes"]), "vectores": z["vectores"], "modelo": str(z["modelo"])}


# --- 1. vectorizar ------------------------------------------------------------------------------
def vectorizar(p: Proyecto, usar_llm: bool = True) -> int:
    chunks = [c for c in leer_chunks(p) if c["embeber"]]
    previos = leer_vectores(p)
    guardados = {}
    if previos and previos["modelo"] == MODELO:
        guardados = {(i, h): v for i, h, v in zip(previos["ids"], previos["hashes"], previos["vectores"])}
    textos = [c["texto_embedding"][:MAX_CARACTERES] for c in chunks]
    hashes = [_hash(t) for t in textos]
    faltan = [k for k, (c, h) in enumerate(zip(chunks, hashes)) if (c["id"], h) not in guardados]
    nuevos: dict[int, np.ndarray] = {}
    if faltan and usar_llm:
        print(f"  {len(faltan)} chunks por vectorizar con {MODELO} (lotes de {LOTE})")
        dir_llm = p.carpeta / "cache" / "llm"
        for a in range(0, len(faltan), LOTE):
            lote = faltan[a:a + LOTE]
            try:
                vecs = llm.pedir_embeddings(dir_llm, [textos[k] for k in lote], MODELO)
            except llm.SinModelos as e:
                print(f"    se detiene: {str(e)[:200]}")
                break
            for k, v in zip(lote, vecs):
                v = np.asarray(v, dtype=np.float32)
                nuevos[k] = v / (np.linalg.norm(v) or 1.0)
    ids, hs, vs = [], [], []
    for k, (c, h) in enumerate(zip(chunks, hashes)):
        v = guardados.get((c["id"], h))
        v = nuevos.get(k) if v is None else v
        if v is not None:
            ids.append(c["id"]), hs.append(h), vs.append(np.asarray(v, dtype=np.float16))
    if vs:
        np.savez_compressed(_ruta(p), ids=np.array(ids), hashes=np.array(hs), vectores=np.stack(vs),
                            modelo=np.array(MODELO))
    print(f"  {_ruta(p).name}: {len(vs)} de {len(chunks)} chunks vectorizados · {len(nuevos)} nuevos · "
          f"dimensión {vs[0].shape[0] if vs else 0} · modelo {MODELO}")
    return len(chunks) - len(vs)


# --- 2. buscar ------------------------------------------------------------------------------------
_SIN_TILDES = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")
_RE_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-zñ]+")
_VACIAS = set("a al con de del el en es la las lo los o para por que se su sus un una y cual cuales cuanto cuantos "
              "cuantas como donde debe deben esta este esto son fue".split())
RRF_K = 60                  # constante de Reciprocal Rank Fusion
RRF_TOPE = 100              # posiciones de cada ranking que entran a la fusión
N_REFORMULACIONES = config.env_int("RAG_REFORMULACIONES", 0)   # consultas extra; 0: en RITP y CM no mejoró
MAX_FILAS_ADJUNTAS = 40     # tablas más largas no se adjuntan al texto (tienen sus propios chunks por fila)
# contexto "articulo": cada párrafo encontrado se amplía con sus vecinos del mismo artículo (o del mismo acto) hasta
# MAX_BLOQUE caracteres, y dos hallazgos del mismo artículo van en un solo bloque; "chunk": un chunk por bloque
CONTEXTO = config.env("RAG_CONTEXTO", "chunk")
MAX_BLOQUE = config.env_int("RAG_ARTICULO", 3000)
MAX_FILAS_TABLA = 3         # filas de una misma tabla entre los bloques (el resto queda en el resumen de la tabla)


def _raiz(t: str) -> str:
    """Plural → singular, aproximado ("comunas" → "comuna", "multas" → "multa"); los números quedan igual."""
    if t[0].isdigit() or len(t) <= 4:
        return t
    return t[:-2] if t.endswith(("ces", "nes", "les", "res", "des")) else t[:-1] if t.endswith("s") else t


def _tokens(texto: str) -> list[str]:
    return [_raiz(t) for t in _RE_TOKEN.findall(texto.lower().translate(_SIN_TILDES)) if t not in _VACIAS]


class BM25:
    """BM25 sobre los textos de los chunks (minúsculas, sin tildes; números y numerales como un token)."""

    def __init__(self, textos: list[str], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [_tokens(t) for t in textos]
        self.largo = np.array([len(d) for d in self.docs], dtype=np.float32)
        self.promedio = float(self.largo.mean()) if len(self.docs) else 1.0
        self.indice: dict[str, list[tuple[int, int]]] = {}
        for i, d in enumerate(self.docs):
            frec: dict[str, int] = {}
            for t in d:
                frec[t] = frec.get(t, 0) + 1
            for t, f in frec.items():
                self.indice.setdefault(t, []).append((i, f))

    def ranking(self, consulta: str) -> list[int]:
        n = len(self.docs)
        puntaje = np.zeros(n, dtype=np.float32)
        for t in set(_tokens(consulta)):
            post = self.indice.get(t)
            if not post:
                continue
            idf = np.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5))
            ids = np.array([i for i, _ in post])
            tf = np.array([f for _, f in post], dtype=np.float32)
            norma = self.k1 * (1 - self.b + self.b * self.largo[ids] / self.promedio)
            puntaje[ids] += idf * tf * (self.k1 + 1) / (tf + norma)
        return [int(i) for i in np.argsort(-puntaje) if puntaje[i] > 0]


class Indice:
    """Chunks + vectores de una concesión cargados en memoria (búsqueda híbrida: vectores + BM25)."""

    def __init__(self, p: Proyecto):
        self.p = p
        datos = leer_vectores(p)
        if datos is None:
            raise FileNotFoundError(f"falta {_ruta(p).name}: corre `python main.py embeddings --proyecto {p.id}`")
        self.modelo = datos["modelo"]
        self.matriz = datos["vectores"].astype(np.float32)
        por_id = {c["id"]: c for c in leer_chunks(p)}
        self.chunks = [por_id[i] for i in datos["ids"]]
        tablas = p.carpeta / f"TABLAS_{p.id}.json"
        self.tablas = {t["id"]: t for t in json.loads(tablas.read_text(encoding="utf-8"))} if tablas.exists() else {}
        self.bm25 = BM25([c["texto_embedding"] for c in self.chunks])
        self.tablas_de: dict[str, list[dict]] = {}              # nodo → tablas JSON transcritas de sus páginas
        for t in self.tablas.values():
            self.tablas_de.setdefault(t["nodo"], []).append(t)
        self.ficha = por_id.get("FICHA")
        self.pos = {c["id"]: i for i, c in enumerate(self.chunks)}
        self.secuencias: dict[str, list[int]] = {}            # artículo o documento → sus chunks vigentes en orden
        for i, c in enumerate(self.chunks):
            clave = self.secuencia(c)
            if clave:
                self.secuencias.setdefault(clave, []).append(i)
        for clave, lista in self.secuencias.items():
            if clave.startswith("D:"):                         # fragmentos de un acto: orden del documento
                lista.sort(key=lambda i: self.chunks[i]["id"])
        self.actos = list({m["acto"]["id"]: m["acto"] for c in self.chunks
                           if (m := c["metadatos"]).get("acto") and m["acto"].get("numero")}.values())

    @staticmethod
    def secuencia(c: dict) -> str | None:
        """Grupo de lectura de un chunk vigente: el artículo de sus incisos o el documento de sus fragmentos."""
        m = c["metadatos"]
        if not m["vigente"]:
            return None
        if m["tipo_nodo"] in ("inciso", "incisos", "articulo", "capitulo", "parte") and m.get("articulo"):
            return "A:" + m["articulo"]["id"]
        if m["tipo_nodo"] in ("acto_posterior", "aclaracion") and m.get("documento"):
            return "D:" + m["documento"]["id"]
        return None

    def buscar(self, pregunta: str, k: int = 10, solo_vigentes: bool = True,
               filtro: dict | None = None, extra: list[str] | None = None,
               cupo: int | None = None) -> list[tuple[float, dict]]:
        """`extra`: reformulaciones de la pregunta; cada consulta aporta su ranking vectorial y BM25 a la fusión."""
        consultas = [pregunta] + [q for q in (extra or []) if q.strip()]
        vecs = llm.pedir_embeddings(self.p.carpeta / "cache" / "llm", consultas, self.modelo)
        matriz_q = np.asarray(vecs, dtype=np.float32)
        matriz_q /= np.linalg.norm(matriz_q, axis=1, keepdims=True).clip(1e-9)
        todos = self.matriz @ matriz_q.T                       # chunks × consultas
        puntajes = todos[:, 0]                                 # el puntaje mostrado es el de la pregunta original

        def pasa(c: dict) -> bool:
            m = c["metadatos"]
            if solo_vigentes and not m["vigente"]:
                return False
            return all(m.get(campo) == valor for campo, valor in (filtro or {}).items())

        # búsqueda híbrida: ranking vectorial + BM25 (palabras exactas: "comuna", "UTM", "1.9.5"), fusionados por RRF
        listas = []
        for j, q in enumerate(consultas):
            listas.append([i for i in np.argsort(-todos[:, j]) if pasa(self.chunks[i])][:RRF_TOPE])
            listas.append([i for i in self.bm25.ranking(q) if pasa(self.chunks[i])][:RRF_TOPE])
        fusion: dict[int, float] = {}
        for lista in listas:
            for rango, i in enumerate(lista):
                fusion[i] = fusion.get(i, 0.0) + 1 / (RRF_K + rango + 1)
        orden = sorted(fusion, key=lambda i: -fusion[i])
        # numeral citado en la pregunta → primero los chunks de ese artículo
        citados = {clave_numero(n) for n in citas(pregunta)}
        cupo = cupo or max(1, k // 2)
        primero = [i for i in orden if (self.chunks[i]["metadatos"]["articulo"] or {}).get("numero")
                   and clave_numero(self.chunks[i]["metadatos"]["articulo"]["numero"]) in citados][:cupo]
        # acto nombrado en la pregunta ("Convenio Complementario N°1", "DS 136") → también primero sus fragmentos
        nombrados = actos_nombrados(pregunta, self.actos) if CONTEXTO == "articulo" else set()
        if nombrados:
            del_acto = [i for i in orden if i not in primero and
                        ((self.chunks[i]["metadatos"].get("acto") or {}).get("id") in nombrados)]
            # el texto del acto antes que sus tablas (que ya traen todas sus filas en el resumen)
            primero += sorted(del_acto, key=lambda i: bool(self.chunks[i]["metadatos"].get("id_tabla")))[:cupo]
        elegidos = primero + [i for i in orden if i not in primero]
        return [(float(puntajes[i]), self.chunks[i]) for i in elegidos[:k]]

    def bloques(self, hallados: list[tuple[float, dict]], k: int) -> list[dict]:
        """Agrupa los hallazgos en k bloques: los del mismo artículo (o acto) van juntos y una tabla aporta a lo más
        MAX_FILAS_TABLA filas. Cada bloque: {puntaje, chunk (el mejor), anclas (índices encontrados), clave}."""
        salida, por_clave, filas, resumenes = [], {}, Counter(), set()
        for puntaje, c in hallados:
            m, i = c["metadatos"], self.pos.get(c["id"])
            if m.get("fila") is not None:                     # filas sueltas: pocas, y ninguna si ya está el resumen
                if filas[m.get("id_tabla")] >= MAX_FILAS_TABLA or m.get("id_tabla") in resumenes:
                    continue
                filas[m.get("id_tabla")] += 1
            elif m.get("id_tabla"):
                resumenes.add(m["id_tabla"])
            clave = self.secuencia(c) if i is not None else None
            if clave in por_clave:
                por_clave[clave]["anclas"].append(i)
                continue
            if len(salida) < k:
                b = {"puntaje": puntaje, "chunk": c, "anclas": [i] if i is not None else [], "clave": clave}
                salida.append(b)
                if clave:
                    por_clave[clave] = b
        return salida

    def ampliar(self, b: dict) -> tuple[str, list[dict]]:
        """Texto de un bloque: sus chunks encontrados más los vecinos del mismo artículo o acto, hasta MAX_BLOQUE
        caracteres, en orden de lectura ("[…]" donde se salta). Devuelve (texto para el prompt, chunks incluidos)."""
        seq = self.secuencias.get(b["clave"] or "", [])
        if len(seq) < 2:
            return self.contexto(b["chunk"]), [b["chunk"]]
        lugar = {i: q for q, i in enumerate(seq)}
        anclas = sorted({lugar[i] for i in b["anclas"] if i in lugar})
        elegidos = set(anclas)
        total = sum(len(self.chunks[seq[q]]["texto"]) for q in elegidos)
        for r in range(1, len(seq)):
            for a in anclas:
                for q in (a - r, a + r):
                    if 0 <= q < len(seq) and q not in elegidos and total < MAX_BLOQUE:
                        elegidos.add(q)
                        total += len(self.chunks[seq[q]]["texto"])
            if total >= MAX_BLOQUE:
                break
        partes, previo, incluidos = [], None, []
        for q in sorted(elegidos):
            if previo is not None and q != previo + 1:
                partes.append("[…]")
            c = self.chunks[seq[q]]
            partes.append(c["texto"])
            incluidos.append(c)
            previo = q
        primero = incluidos[0]
        m = primero["metadatos"]
        if b["clave"].startswith("A:"):
            cabecera = re.sub(r"( › (incisos? [^›]*|cambios[^›]*))+$|\s*\(parte \d+\)$", "", m["ruta"])
        else:
            cabecera = f"{(m['acto'] or {}).get('nombre') or m['documento']['titulo']} (fragmentos en orden del documento)"
        texto = f"{cabecera}:\n" + "\n".join(partes)
        nodos = {b["clave"][2:]} | {c["metadatos"]["id_nodo"] for c in incluidos}
        for t in [t for n in nodos for t in self.tablas_de.get(n, [])]:
            if len(t["filas"]) <= MAX_FILAS_ADJUNTAS:
                texto += (f"\nTabla transcrita con visión ({t['titulo'] or 'sin título'}):\n"
                          + _tabla_md(t, MAX_FILAS_ADJUNTAS))
        pags = sorted({pg for c in incluidos for pg in c["metadatos"]["paginas"]})
        fuente = ((m["acto"] or {}).get("nombre") or (m["documento"] or {}).get("titulo") or "")
        fuente += f", pág. {pags[0]}" if len(pags) == 1 else f", págs. {pags[0]}–{pags[-1]}" if pags else ""
        return f"{texto}\n(Fuente: {fuente})", incluidos

    def contexto(self, c: dict) -> str:
        m = c["metadatos"]
        pags = m["paginas"]
        fuente = (f"{(m['acto'] or {}).get('nombre') or (m['documento'] or {}).get('titulo') or 'ficha de actos'}"
                  + (f", pág. {pags[0]}" if len(pags) == 1 else f", págs. {pags[0]}–{pags[-1]}" if pags else ""))
        texto = c["texto_embedding"]
        if m.get("id_tabla") in self.tablas and m.get("fila") is None:          # resumen de tabla → sus filas
            texto += "\nFilas de la tabla (transcrita con visión):\n" + _tabla_md(self.tablas[m["id_tabla"]], 80)
        elif not m.get("id_tabla"):
            # texto OCR de un nodo que tiene tablas transcritas con visión: se agregan para contrastar cifras
            for t in self.tablas_de.get(m["id_nodo"], []):
                if len(t["filas"]) <= MAX_FILAS_ADJUNTAS:
                    texto += (f"\nTabla transcrita con visión de la misma página ({t['titulo'] or 'sin título'}):\n"
                              + _tabla_md(t, MAX_FILAS_ADJUNTAS))
        return f"{texto}\n(Fuente: {fuente}{'' if m['vigente'] else '; versión NO vigente'})"


# --- 3. responder ---------------------------------------------------------------------------------
def _tabla_md(t: dict, maximo: int) -> str:
    return "\n".join(["| " + " | ".join(t["encabezados"]) + " |"] + ["| " + " | ".join(f) + " |" for f in t["filas"][:maximo]])


class Respuesta(BaseModel):
    respuesta: str
    fuentes: list[int]


SISTEMA_RAG = ("Eres un asistente experto en contratos de concesión de obras públicas de Chile. Respondes en "
               "español, solo con la información de los FRAGMENTOS entregados, citando cada afirmación con su "
               "número entre corchetes, p. ej. [2]. Si los fragmentos no contienen la respuesta, dilo. El texto de "
               "los fragmentos viene de OCR y puede tener errores en cifras: si una tabla transcrita con visión de la "
               "misma página dice otra cifra, prefiere la de la tabla. Para contar actos o saber cuál es el último, "
               "usa la ficha de actos. Responde SOLO con un objeto JSON válido que cumple el esquema pedido.")


class _Reformulacion(BaseModel):
    consultas: list[str]


def reformular(p: Proyecto, pregunta: str) -> list[str]:
    """Hasta N_REFORMULACIONES consultas con el vocabulario de los documentos (LLM barato, en caché): una pregunta
    por el "factor de licitación (ITC, VPI…)" debe encontrar la oferta que habla de "subsidio del Estado"."""
    prompt = ("Reescribe la PREGUNTA como consultas de búsqueda para encontrar la respuesta en los documentos de un "
              "contrato de concesión de obras públicas de Chile (bases de licitación, circulares aclaratorias, oferta "
              "económica, decreto de adjudicación, convenios, resoluciones). Usa las palabras que tendrían esos "
              "documentos: por ejemplo, para el factor o la variable de licitación, «oferta económica, subsidio del "
              "Estado, peaje o tarifa máxima, plazo de concesión, ingresos mínimos garantizados». No respondas la "
              f"pregunta. Devuelve {N_REFORMULACIONES} consultas distintas y breves.\n\nPREGUNTA: {pregunta}")
    if N_REFORMULACIONES <= 0:
        return []
    try:
        r = llm.pedir_json(p.carpeta / "cache" / "llm", "reformular", prompt, _Reformulacion, modelos=MODELOS_RAG,
                           razonar=False, validar=lambda r: None if r.consultas else "sin consultas")
        return [q for q in r.consultas if q.strip()][:N_REFORMULACIONES]
    except llm.SinModelos:
        return []


def responder(p: Proyecto, pregunta: str, k: int = 10, solo_vigentes: bool = True,
              indice: Indice | None = None) -> tuple[Respuesta | None, list[tuple[float, dict]]]:
    indice = indice or Indice(p)
    if CONTEXTO == "articulo":
        candidatos = indice.buscar(pregunta, k=3 * k, solo_vigentes=solo_vigentes, extra=reformular(p, pregunta),
                                   cupo=max(1, k // 2))
        textos, hallados = [], []
        for b in indice.bloques(candidatos, k):
            texto, incluidos = indice.ampliar(b)
            textos.append(texto)
            hallados.append((b["puntaje"], b["chunk"] | {"incluidos": incluidos}))
    else:
        hallados = indice.buscar(pregunta, k=k, solo_vigentes=solo_vigentes, extra=reformular(p, pregunta))
        textos = [indice.contexto(c) for _, c in hallados]
    if indice.ficha and all(c["id"] != "FICHA" for _, c in hallados):     # siempre a mano para contar actos
        hallados.append((0.0, indice.ficha))
        textos.append(indice.contexto(indice.ficha))
    bloques = "\n\n".join(f"[{n}] {t}" for n, t in enumerate(textos, 1))
    prompt = (f"CONCESIÓN: {p.nombre}\n\nFRAGMENTOS:\n\n{bloques}\n\nPREGUNTA: {pregunta}\n\n"
              "En `respuesta` escribe la respuesta citando [n]; en `fuentes`, los números de los fragmentos usados.")
    try:
        r = llm.pedir_json(p.carpeta / "cache" / "llm", "rag", prompt, Respuesta, sistema=SISTEMA_RAG,
                           modelos=MODELOS_RAG, razonar=False)
    except llm.SinModelos as e:
        print(f"  sin respuesta del LLM: {str(e)[:200]}")
        r = None
    return r, hallados


def imprimir(p: Proyecto, pregunta: str, k: int = 10, solo_vigentes: bool = True, solo_buscar: bool = False):
    indice = Indice(p)
    if solo_buscar and CONTEXTO == "articulo":               # muestra los bloques tal como llegan al modelo
        candidatos = indice.buscar(pregunta, k=3 * k, solo_vigentes=solo_vigentes, cupo=max(1, k // 2))
        for n, b in enumerate(indice.bloques(candidatos, k), 1):
            texto, incluidos = indice.ampliar(b)
            print(f"\n[{n}] {b['puntaje']:.3f} · {len(incluidos)} chunks · {len(texto)} caracteres\n{texto[:900]}"
                  + (" […]" if len(texto) > 900 else ""))
        return
    if solo_buscar:
        r, hallados = None, indice.buscar(pregunta, k=k, solo_vigentes=solo_vigentes, extra=reformular(p, pregunta))
    else:
        r, hallados = responder(p, pregunta, k=k, solo_vigentes=solo_vigentes, indice=indice)
    print(f"\nPREGUNTA: {pregunta}\n")
    if r:
        print(f"RESPUESTA:\n{r.respuesta}\n")
    print("FRAGMENTOS RECUPERADOS:")
    for n, (puntaje, c) in enumerate(hallados, 1):
        m = c["metadatos"]
        usado = "*" if r and n in r.fuentes else " "
        print(f" {usado}[{n}] {puntaje:.3f} · {c['id']} · {m['ruta'][-110:]}"
              f"{'' if m['vigente'] else ' · NO VIGENTE'}\n      {re.sub(r'\s+', ' ', c['texto'])[:160]}")
