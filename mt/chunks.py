"""Chunks para vectorizar, recorridos desde el árbol (`nodos_versionados.json`), no desde el Markdown.

- Un chunk = un nodo: un artículo con su texto vigente (todos sus incisos) si es corto; si supera MAX_ARTICULO
  caracteres, grupos de incisos consecutivos de hasta ~GRUPO_INCISOS caracteres (un inciso solo si es largo), con
  traslape (la última oración del grupo anterior). Un inciso más largo que MAX_INCISO se parte por oraciones.
- Cada chunk lleva delante su ruta jerárquica ("Bases de Licitación › 1. BASES ADMINISTRATIVAS › … › inciso 2:")
  en `texto_embedding`; `texto` es el literal y `metadatos` va aparte (para filtrar en la consulta).
- Versiones no vigentes: un chunk cada una, con `vigente: false` (el filtro por defecto en la consulta es
  vigente = true).
- Tablas (JSON de visión): un chunk resumen (título, dónde aparece, columnas, n.º de filas) y un chunk por fila
  ("Encabezado: valor · …"); las celdas también están en TABLAS_<ID>.sqlite para consultas exactas.
- Formalidades e instrucciones: `embeber: false`.
→ `<concesión>/CHUNKS_<ID>.jsonl`
"""
from __future__ import annotations

import json
import re

from . import config
from .arbol import leer_arbol
from .indice import Proyecto

MAX_ARTICULO = 2000
MAX_INCISO = 2500
OBJETIVO = 1500
GRUPO_INCISOS = config.env_int("CHUNK_GRUPO", 700)   # incisos cortos consecutivos se juntan hasta este largo
_FIN_ORACION = re.compile(r"(?<=[.;:])\s+(?=[A-ZÁÉÍÓÚÑ0-9«“\"(])")


def _plano(t: str) -> str:
    return re.sub(r"\s*\n\s*", " ", t).strip()


def _oraciones(t: str) -> list[str]:
    """Oraciones del texto; una "oración" más larga que OBJETIVO (tablas, cronogramas sin puntos) se corta por
    palabras."""
    salida = []
    for o in _FIN_ORACION.split(_plano(t)):
        while len(o) > OBJETIVO:
            corte = o.rfind(" ", 0, OBJETIVO)
            corte = corte if corte > OBJETIVO // 2 else OBJETIVO
            salida.append(o[:corte].strip())
            o = o[corte:].strip()
        if o:
            salida.append(o)
    return salida


def _partir(texto: str) -> list[tuple[str, str]]:
    """(traslape, parte) de un texto largo, cortado por oraciones a ~OBJETIVO caracteres."""
    partes, actual = [], []
    for o in _oraciones(texto):
        if actual and sum(len(x) for x in actual) + len(o) > OBJETIVO:
            partes.append(actual)
            actual = []
        actual.append(o)
    if actual:
        partes.append(actual)
    salida = []
    for k, ps in enumerate(partes):
        salida.append((partes[k - 1][-1] if k else "", " ".join(ps)))
    return salida


class Chunker:
    def __init__(self, p: Proyecto):
        self.p = p
        self.arbol = leer_arbol(p)
        self.nodos = {n["id"]: n for n in self.arbol["nodos"]}
        self.chunks: list[dict] = []
        self.ids: set[str] = set()

    def tema(self, nodo: dict) -> str | None:
        while nodo:
            if nodo["id"].startswith("TEMA:"):
                return nodo["id"][5:]
            nodo = self.nodos.get(nodo["padre"]) if nodo["padre"] else None
        return None

    def articulo(self, nodo: dict) -> dict | None:
        while nodo:
            if nodo["id"].startswith("BL:") and nodo["tipo"] in ("articulo", "capitulo", "parte", "portada"):
                return {"id": nodo["id"], "numero": nodo.get("numero"), "titulo": nodo["titulo"]}
            nodo = self.nodos.get(nodo["padre"]) if nodo["padre"] else None
        return None

    def agregar(self, nodo: dict, versiones: list[dict], texto: str, ruta: str, parte: str | None = None,
                traslape: str = "", embeber: bool = True, tipo: str | None = None, extra: dict | None = None):
        v0 = versiones[0]
        cid = nodo["id"] + (f"#{parte}" if parte else "") + ("" if v0["vigente"] else f"@{v0['fecha_hasta'] or 'anterior'}")
        sufijo = cid
        k = 2
        while sufijo in self.ids:
            sufijo, k = f"{cid}~{k}", k + 1
        self.ids.add(sufijo)
        contexto = f"{ruta}" + ("" if v0["vigente"] else f" (versión anterior, vigente hasta {v0['fecha_hasta'] or '?'})")
        cuerpo = (f"[…] {traslape} " if traslape else "") + texto
        self.chunks.append({
            "id": sufijo,
            "texto_embedding": f"{contexto}:\n{cuerpo}",
            "texto": texto,
            "metadatos": {
                "id_nodo": nodo["id"], "tipo_nodo": tipo or nodo["tipo"], "concesion": self.arbol["concesion"],
                "articulo": self.articulo(nodo), "tema": self.tema(nodo), "ruta": ruta,
                "documento": v0["documento"], "acto": v0["acto"],
                "documentos": sorted({v["documento"]["id"] for v in versiones if v["documento"]}),
                "actos": sorted({v["acto"]["id"] for v in versiones if v["acto"]}),
                "paginas": sorted({pg for v in versiones for pg in v["paginas"]}),
                "fecha_vigencia_desde": v0["fecha_desde"], "fecha_vigencia_hasta": v0["fecha_hasta"],
                "vigente": v0["vigente"], "relacion": v0["relacion"], "operacion": v0.get("operacion"),
                "fuente": sorted({f for v in versiones for f in v["fuente"]}),
                "lineas": [l for v in versiones for l in v["lineas"]],
                "parte": parte, "traslape": bool(traslape), **(extra or {}),
            },
            "embeber": embeber,
        })

    def texto_largo(self, nodo, versiones, texto, ruta, embeber=True, tipo=None):
        if len(texto) <= MAX_INCISO:
            self.agregar(nodo, versiones, texto, ruta, embeber=embeber, tipo=tipo)
            return
        for k, (traslape, parte) in enumerate(_partir(texto), 1):
            self.agregar(nodo, versiones, parte, f"{ruta} (parte {k})", parte=f"p{k}", traslape=traslape,
                         embeber=embeber, tipo=tipo)

    def generar(self):
        for nodo in self.arbol["nodos"]:
            tipo = nodo["tipo"]
            if tipo in ("parte", "capitulo", "articulo", "portada"):
                self.articulo_chunks(nodo)
            elif tipo == "inciso":
                for v in nodo["versiones"][1:]:                       # versiones anteriores
                    self.texto_largo(nodo, [v], _plano(v["texto"]), nodo["ruta"])
            elif tipo == "inciso_eliminado":
                v = nodo["versiones"][0]
                self.texto_largo(nodo, [v], _plano(v["texto"]), nodo["ruta"])
            elif tipo in ("aclaracion", "acto_posterior"):
                v = nodo["versiones"][0]
                self.texto_largo(nodo, [v], _plano(v["texto"]), nodo["ruta"])
            elif tipo == "ficha":
                self.agregar(nodo, nodo["versiones"], nodo["versiones"][0]["texto"], nodo["ruta"])
            elif tipo in ("instruccion", "formalidad"):
                v = nodo["versiones"][0]
                self.texto_largo(nodo, [v], _plano(v["texto"]), nodo["ruta"], embeber=False)
            elif tipo == "tabla":
                t, v = nodo["tabla"], nodo["versiones"][0]
                resumen = (f"Tabla{' «' + nodo['titulo'] + '»' if nodo.get('titulo') else ''} en "
                           f"{self.nodos[nodo['padre']]['ruta']}"
                           f"{', página ' + str(v['paginas'][0]) if v['paginas'] else ''}"
                           f"{' de ' + v['documento']['titulo'] if v['documento'] else ''}. "
                           f"Columnas: {', '.join(c for c in t['encabezados'] if c)}. {len(t['filas'])} filas. "
                           f"Primera columna: {', '.join(f[0] for f in t['filas'][:6] if f and f[0])}"
                           f"{'…' if len(t['filas']) > 6 else ''}.")
                self.agregar(nodo, [v], resumen, nodo["ruta"],
                             extra={"id_tabla": nodo["id"], "columnas": t["encabezados"], "n_filas": len(t["filas"])})
                self.filas_tabla(nodo, v)

    def filas_tabla(self, nodo: dict, v: dict):
        """Un chunk por fila: "Encabezado: valor · …" con los valores literales del JSON de visión, para que una
        pregunta por una cifra ("multa por atraso…") encuentre su fila."""
        t = nodo["tabla"]
        titulo = f"Tabla «{nodo['titulo']}»"
        for i, fila in enumerate(t["filas"], 1):
            texto = " · ".join(f"{h or f'columna {j}'}: {c}" for j, (h, c) in enumerate(zip(t["encabezados"], fila), 1)
                               if c.strip())
            if not texto:
                continue
            self.agregar(nodo, [v], f"{titulo} · {texto}", f"{nodo['ruta']} › fila {i}", parte=f"f{i}",
                         tipo="fila_tabla", extra={"id_tabla": nodo["id"], "fila": i})

    def articulo_chunks(self, nodo: dict):
        """Texto vigente del artículo: un chunk si es corto; si no, uno por inciso con traslape."""
        incisos = [self.nodos[h] for h in nodo["hijos"] if self.nodos[h]["tipo"] == "inciso"]
        if not incisos:
            return
        vigentes = [i["versiones"][0] for i in incisos]
        textos = [_plano(v["texto"]) for v in vigentes]
        total = sum(len(t) for t in textos)
        if total <= MAX_ARTICULO:
            self.agregar(nodo, vigentes, "\n".join(textos), nodo["ruta"], tipo=nodo["tipo"])
            return
        # incisos consecutivos se agrupan hasta ~GRUPO_INCISOS caracteres: el OCR corta muchos párrafos en trozos de una
        # línea, y un chunk por trozo ("(5) Accesos a Predios") no se puede recuperar solo
        previo, grupo = "", []

        def cerrar():
            nonlocal previo, grupo
            if not grupo:
                return
            if len(grupo) == 1:
                inc, v, t = grupo[0]
                self.agregar(inc, [v], t, inc["ruta"], traslape=previo)
            else:
                a, b = grupo[0][0]["numero"], grupo[-1][0]["numero"]
                self.agregar(nodo, [g[1] for g in grupo], "\n".join(g[2] for g in grupo),
                             f"{nodo['ruta']} › incisos {a}–{b}", parte=f"i{a}-{b}", traslape=previo, tipo="incisos",
                             extra={"incisos": [g[0]["id"] for g in grupo]})
            oraciones = _oraciones(grupo[-1][2])
            previo, grupo = (oraciones[-1] if oraciones else ""), []

        for inc, v, t in zip(incisos, vigentes, textos):
            if len(t) > MAX_INCISO:
                cerrar()
                for k, (traslape, parte) in enumerate(_partir(t), 1):
                    self.agregar(inc, [v], parte, f"{inc['ruta']} (parte {k})", parte=f"p{k}",
                                 traslape=traslape or previo)
                oraciones = _oraciones(t)
                previo = oraciones[-1] if oraciones else ""
                continue
            if grupo and sum(len(g[2]) for g in grupo) + len(t) > GRUPO_INCISOS:
                cerrar()
            grupo.append((inc, v, t))
        cerrar()


def chunks(p: Proyecto) -> list[dict]:
    c = Chunker(p)
    c.generar()
    ruta = p.carpeta / f"CHUNKS_{p.id}.jsonl"
    with ruta.open("w", encoding="utf-8") as fh:
        for ch in c.chunks:
            fh.write(json.dumps(ch, ensure_ascii=False) + "\n")
    from collections import Counter
    tipos = Counter(ch["metadatos"]["tipo_nodo"] for ch in c.chunks)
    print(f"  {ruta.name}: {len(c.chunks)} chunks · vigentes {sum(ch['metadatos']['vigente'] for ch in c.chunks)} · "
          f"a embeber {sum(ch['embeber'] for ch in c.chunks)} · " + " · ".join(f"{k} {v}" for k, v in tipos.most_common()))
    return c.chunks
