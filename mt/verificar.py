"""Verificación del árbol, el documento y los chunks.

- Literalidad: cada línea no marginal de cada documento aparece exactamente UNA vez entre los títulos y las
  versiones (con `cuenta`) de los nodos de `nodos_versionados.json`, y el texto de cada versión es la unión literal
  de sus líneas. (Las versiones armadas por "donde dice / debe decir" no cuentan: sus líneas completas están en la
  versión anterior y en la instrucción.)
- Chunks: cada artículo con incisos y cada inciso, fragmento, versión anterior, tabla y fila de tabla tienen chunk.
- Tablas: JSON con filas del mismo largo que los encabezados.
- Documento: la limpieza de presentación (`limpieza.py`) no agrega palabras: cada palabra de un párrafo marcado está
  en el texto literal de su nodo o existe en el diccionario / vocabulario de la concesión.
Escribe `revision/verificacion.md`.
"""
from __future__ import annotations

import json
from collections import Counter

from .arbol import leer_arbol
from .indice import Proyecto
from .texto import leer_lineas


def verificar(p: Proyecto) -> bool:
    arbol = leer_arbol(p)
    nodos = {n["id"]: n for n in arbol["nodos"]}
    lineas = {d.id: leer_lineas(p, d.id) for d in p.documentos}
    esperadas = {(d, ln["i"]) for d, ls in lineas.items() for ln in ls if not ln["marginal"]}

    cuenta: Counter = Counter()
    distinto = []
    for n in arbol["nodos"]:
        t = n.get("titulo_lineas")
        if t and t["lineas"]:
            cuenta.update((t["doc"], i) for i in t["lineas"])
        for v in n.get("versiones", []):
            if not v["cuenta"]:
                continue
            for seg in v["lineas"]:
                cuenta.update((seg["doc"], i) for i in seg["lineas"])
            if len(v["lineas"]) == 1:
                seg = v["lineas"][0]
                if "\n".join(lineas[seg["doc"]][i]["texto"] for i in seg["lineas"]) != v["texto"]:
                    distinto.append(n["id"])
    faltan = sorted(esperadas - set(cuenta))
    repetidas = sorted(k for k, c in cuenta.items() if c > 1)
    sobran = sorted(set(cuenta) - esperadas)

    chunks = [json.loads(l) for l in (p.carpeta / f"CHUNKS_{p.id}.jsonl").read_text(encoding="utf-8").splitlines()]
    con_chunk = {c["metadatos"]["id_nodo"] for c in chunks}
    sin_chunk = []
    for n in arbol["nodos"]:
        if n["tipo"] in ("acto_posterior", "aclaracion", "instruccion", "formalidad", "tabla", "inciso_eliminado"):
            if n["id"] not in con_chunk and any(v["texto"].strip() for v in n["versiones"]) | (n["tipo"] == "tabla"):
                sin_chunk.append(n["id"])
        elif n["tipo"] == "inciso":
            padre = n["padre"]
            if n["id"] not in con_chunk and padre not in con_chunk and n["versiones"][0]["texto"].strip():
                sin_chunk.append(n["id"])
    tablas = json.loads((p.carpeta / f"TABLAS_{p.id}.json").read_text(encoding="utf-8"))
    tablas_malas = [t["id"] for t in tablas if any(len(f) != len(t["encabezados"]) for f in t["filas"])]
    filas_con_chunk = {(c["metadatos"]["id_tabla"], c["metadatos"]["fila"]) for c in chunks if c["metadatos"].get("fila")}
    sin_chunk += [f"{t['id']}#f{i}" for t in tablas for i, f in enumerate(t["filas"], 1)
                  if any(x.strip() for x in f) and (t["id"], i) not in filas_con_chunk]
    from .vectores import leer_vectores
    vec = leer_vectores(p)
    sin_vector = []
    if vec is not None:                                      # la etapa `embeddings` es opcional
        hechos = set(vec["ids"])
        sin_vector = [c["id"] for c in chunks if c["embeber"] and c["id"] not in hechos]
    from .limpieza import palabras_ajenas
    ruta_md = p.carpeta / f"DOCUMENTO_UNICO_{p.id}.md"
    ajenas = palabras_ajenas(p, nodos, ruta_md.read_text(encoding="utf-8")) if ruta_md.exists() else []
    ok = not (faltan or repetidas or sobran or distinto or sin_chunk or tablas_malas or sin_vector or ajenas)

    tipos = Counter(n["tipo"] for n in arbol["nodos"])
    md = [f"# Verificación · {p.id}", "",
          f"**{'OK' if ok else 'CON PROBLEMAS'}** · {len(esperadas)} líneas no marginales en {len(lineas)} documentos", "",
          f"- Líneas sin aparecer: {len(faltan)}", f"- Líneas repetidas: {len(repetidas)}",
          f"- Líneas que no existen en el texto: {len(sobran)}", f"- Versiones con texto no literal: {len(distinto)}",
          f"- Nodos sin chunk: {len(sin_chunk)}", f"- Tablas con filas de largo distinto: {len(tablas_malas)}",
          f"- Palabras del documento que no están en su texto literal ni en el diccionario: {len(ajenas)}",
          f"- Chunks a embeber sin vector: {len(sin_vector) if vec is not None else 'sin EMBEDDINGS (etapa no corrida)'}", "",
          "## Nodos", "", "| Tipo | Nodos |", "|---|---:|"] + [f"| {t} | {c} |" for t, c in tipos.most_common()]
    md += ["", "## Chunks", "", f"- Total: {len(chunks)} · vigentes: {sum(c['metadatos']['vigente'] for c in chunks)}"
           f" · a embeber: {sum(c['embeber'] for c in chunks)} · tablas: {len(tablas)}"]
    if faltan[:15] or repetidas[:15] or sin_chunk[:15] or distinto[:15]:
        md += ["", "## Ejemplos", ""] + [f"- falta {d}:{i} «{lineas[d][i]['texto'][:80]}»" for d, i in faltan[:15]] \
              + [f"- repetida {d}:{i} «{lineas[d][i]['texto'][:80]}»" for d, i in repetidas[:15]] \
              + [f"- sin chunk {x}" for x in sin_chunk[:15]] + [f"- no literal {x}" for x in distinto[:15]]
    if ajenas:
        md += ["", "## Palabras ajenas al literal", ""] + [f"- {i}: «{w}»" for i, w in ajenas[:30]]
    p.dir_revision.mkdir(parents=True, exist_ok=True)
    (p.dir_revision / "verificacion.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"  verificación {'OK' if ok else 'CON PROBLEMAS'} · {len(esperadas)} líneas · faltan {len(faltan)} · "
          f"repetidas {len(repetidas)} · no literales {len(distinto)} · nodos sin chunk {len(sin_chunk)} · "
          f"tablas mal formadas {len(tablas_malas)} · palabras ajenas en el documento {len(ajenas)}"
          + (f" · chunks sin vector {len(sin_vector)}" if vec is not None else " · sin embeddings"))
    return ok
