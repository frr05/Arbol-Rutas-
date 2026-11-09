"""Prueba: chunks cortados del documento único (`DOCUMENTO_UNICO_<ID>.md`, ya limpio) en vez del árbol.

Se usa con `RAG_FUENTE=documento` (archivos `CHUNKS_<ID>_DOC.jsonl` y `EMBEDDINGS_<ID>_DOC.npz`), para comparar
con `evaluar` contra el RAG de siempre sin pisarlo. Cada párrafo del `.md` termina con su marca
`<!-- id · estado · acto · pág. -->`; con el id se recuperan del árbol los metadatos (artículo, acto, páginas).
- Texto vigente de un artículo: párrafos consecutivos agrupados hasta ~GRUPO_INCISOS caracteres (como `chunks.py`).
- Citas de otros actos (líneas "> "): un chunk por cita, partido por oraciones si es larga.
- "Cambios en este artículo": un chunk por artículo (qué acto cambió qué párrafo, con el texto anterior).
- Lo plegado (formalidades, lectura OCR de tablas) no entra; la portada sí, como en `chunks.py`. Tablas, filas de tabla, la ficha de actos y
  las versiones anteriores se copian de `CHUNKS_<ID>.jsonl` (no son párrafos marcados del documento).
"""
from __future__ import annotations

import json
import re

from .chunks import GRUPO_INCISOS, MAX_INCISO, Chunker
from .indice import Proyecto

_RE_MARCA = re.compile(r"<!-- (\S+) · .*?-->")
_RE_TITULO = re.compile(r"^(#{2,6}) ")
COPIAR = ("tabla", "fila_tabla", "ficha")


def _limpio(linea: str) -> str:
    t = _RE_MARCA.sub("", linea)
    t = re.sub(r"^\s*>\s?", "", t)
    t = re.sub(r"\*\*\[c\d+\]\*\*\s*", "", t)                 # llamadas [c1] del texto vigente
    t = t.replace("**", "")
    t = re.sub(r"\\([\\*_\[\]()#>+\-.|!`])", r"\1", t)
    return t.replace("&lt;", "<").strip()


def chunks_documento(p: Proyecto) -> list[dict]:
    ch = Chunker(p)
    previos = [json.loads(l) for l in (p.carpeta / f"CHUNKS_{p.id}.jsonl").read_text(encoding="utf-8").splitlines()]
    copiados = [c for c in previos if c["metadatos"]["tipo_nodo"] in COPIAR or not c["metadatos"]["vigente"]]
    ch.ids.update(c["id"] for c in copiados)
    md = (p.carpeta / f"DOCUMENTO_UNICO_{p.id}.md").read_text(encoding="utf-8").splitlines()

    grupo: list[tuple[dict, str]] = []                          # párrafos vigentes por agrupar
    pendiente: list[str] = []                                   # bloques de un párrafo que aún no llega a su marca
    cambios: list[str] = []
    articulo: dict | None = None                                # último artículo con párrafos vistos
    modo, plegado = "texto", 0

    def ruta(nodo: dict) -> str:
        padre = ch.nodos.get(nodo["padre"]) if nodo["tipo"] == "inciso" and nodo["padre"] else None
        return (padre or nodo)["ruta"]

    def cerrar_grupo():
        if not grupo:
            return
        nodos = [n for n, _ in grupo]
        texto = "\n".join(t for _, t in grupo)
        ch.agregar(nodos[0], [n["versiones"][0] for n in nodos], texto, ruta(nodos[0]), parte=f"d{len(ch.chunks)}")
        grupo.clear()

    def cerrar_cambios():
        if cambios and articulo:
            v = {"texto": "", "vigente": True, "fecha_desde": None, "fecha_hasta": None, "acto": None,
                 "documento": None, "paginas": [], "lineas": [], "fuente": ["documento único"],
                 "relacion": "cambios", "operacion": None}
            ch.texto_largo(articulo, [v], "\n".join(cambios), f"{articulo['ruta']} › cambios en este artículo",
                           tipo="cambios")
        cambios.clear()

    for linea in md:
        if re.sub(r"^\s*>\s?", "", linea).startswith("<details"):
            if "Portada e índice" in linea:                     # la portada sí entra (región, nombre, fecha)
                continue
            plegado += 1
        if linea.strip() == "</details>" and not plegado:
            continue
        if plegado:
            plegado -= linea.count("</details>")
            continue
        if _RE_TITULO.match(linea) or linea.startswith("**Otros actos sobre este artículo**"):
            cerrar_grupo(), cerrar_cambios()
            pendiente.clear()
            modo = "texto"
            continue
        if linea.startswith("**Cambios en este artículo**"):
            cerrar_grupo()
            modo = "cambios"
            continue
        if modo == "cambios":
            if linea.strip():
                cambios.append(_limpio(linea))
            continue
        cuerpo = re.sub(r"^\s*>\s?", "", linea).strip()
        if not cuerpo or cuerpo.startswith("|") or (cuerpo.startswith("*") and cuerpo.endswith("*")):
            continue                                            # vacía, fila de tabla o nota en cursiva
        ids = [i for i in _RE_MARCA.findall(linea) if i in ch.nodos]
        if not ids:
            pendiente.append(_limpio(linea))
            continue
        texto = "\n".join(pendiente + [_limpio(linea)]).strip()
        pendiente.clear()
        nodo = ch.nodos[ids[0]]
        if not texto or not nodo.get("versiones") or nodo["tipo"] == "tabla":   # la tabla se copia del árbol
            continue
        if nodo["tipo"] == "inciso":
            articulo = ch.nodos.get(nodo["padre"]) or articulo
        if linea.lstrip().startswith(">") or len(texto) > MAX_INCISO:
            cerrar_grupo()                                      # cita de otro acto o párrafo largo: chunk propio
            ch.texto_largo(nodo, [ch.nodos[i]["versiones"][0] for i in ids], texto, nodo["ruta"])
            continue
        if grupo and (ruta(grupo[0][0]) != ruta(nodo) or sum(len(t) for _, t in grupo) + len(texto) > GRUPO_INCISOS):
            cerrar_grupo()
        grupo.append((nodo, texto))
    cerrar_grupo(), cerrar_cambios()

    todos = ch.chunks + copiados
    ruta_salida = p.carpeta / f"CHUNKS_{p.id}_DOC.jsonl"
    with ruta_salida.open("w", encoding="utf-8") as fh:
        for c in todos:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    from collections import Counter
    tipos = Counter(c["metadatos"]["tipo_nodo"] for c in todos)
    print(f"  {ruta_salida.name}: {len(todos)} chunks ({len(ch.chunks)} del documento, {len(copiados)} copiados del "
          f"árbol) · " + " · ".join(f"{k} {v}" for k, v in tipos.most_common()))
    return todos
