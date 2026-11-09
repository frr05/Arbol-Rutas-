"""Línea de comandos del MVP.

  python main.py estado  [--proyecto TERB]
  python main.py ocr     [--proyecto TERB | --doc TERB.D01] [--paginas 3,7-9] [--forzar]     # paso 1
  python main.py marcar  [--proyecto TERB] [--umbral 0.70] [--frac-baja 0.30] ...          # paso 2
  python main.py vlm     [--proyecto TERB | --doc TERB.D01] [--modelo ...] [--forzar]       # paso 3 (GPU)
  python main.py comparar [--proyecto TERB]                                                  # informe OCR vs VLM

Documento único (cada etapa lee la anterior; todo queda en la carpeta de la concesión):
  python main.py texto       [--proyecto X]   # texto final por página (OCR o VLM), párrafos y marginales
  python main.py tablas      [--proyecto X]   # páginas con tablas → visión (Qwen-VL) → JSON validado (cache/tablas)
  python main.py actos       [--proyecto X]   # actos, fechas y orden → revision/actos.csv
  python main.py bases       [--proyecto X]   # estructura de las Bases → revision/estructura_bases.csv
  python main.py fragmentos  [--proyecto X]   # cortes de cada acto
  python main.py relaciones  [--proyecto X]   # LLM: cambios de las circulares sobre las Bases → revision/relaciones.csv
  python main.py vincular    [--proyecto X]   # LLM barato: artículo/tema de cada fragmento → revision/vinculos.csv
  python main.py arbol       [--proyecto X]   # nodos_versionados.json + TABLAS_<ID>.json/.sqlite
  python main.py ensamblar   [--proyecto X]   # DOCUMENTO_UNICO_<ID>.md (lectura) desde el árbol
  python main.py chunks      [--proyecto X]   # CHUNKS_<ID>.jsonl (para vectorizar) desde el árbol
  python main.py embeddings  [--proyecto X]   # EMBEDDINGS_<ID>.npz (bge-m3 vía OpenRouter; reutiliza lo ya hecho)
  python main.py verificar   [--proyecto X]   # literalidad, chunks y tablas → revision/verificacion.md
  python main.py todo        [--proyecto X]   # todas las etapas en orden
  (--reglas: sin LLM; las etapas con LLM solo usan respuestas ya guardadas)

RAG sobre los chunks:
  python main.py consultar "¿Cuál es el plazo de la concesión?" --proyecto RITP [--k 10] [--anteriores] [--solo-buscar]
  python main.py evaluar --proyecto RITP       # 50 preguntas de revision/preguntas_rag.csv → revision/evaluacion_rag.csv
"""
from __future__ import annotations

import argparse
import sys
import time

from mt import indice


def _paginas(texto: str | None) -> list[int] | None:
    if not texto:
        return None
    salida = []
    for parte in texto.split(","):
        a, _, b = parte.partition("-")
        salida.extend(range(int(a), int(b or a) + 1))
    return salida


def _objetivos(args) -> list[tuple[indice.Proyecto, list[indice.Documento]]]:
    if args.doc:
        p, d = indice.buscar_documento(args.doc)
        return [(p, [d])]
    return [(p, p.documentos) for p in indice.proyectos(args.proyecto)]


def cmd_estado(args):
    from mt.ocr import leer_registro, ocr_completo
    from mt.marcar import leer_marcadas
    from mt.vlm import ruta_vlm

    for p in indice.proyectos(args.proyecto):
        marcadas = leer_marcadas(p)
        print(f"\n{p.id} · {p.nombre}")
        print(f"  {'doc':<9} {'págs':>5} {'OCR':<9} {'marcadas':>8} {'VLM':>5}  archivo")
        for d in p.documentos:
            reg = leer_registro(p, d)
            ocr = "listo" if ocr_completo(p, d) else ("parcial" if reg or (p.dir_ocr / d.id).exists() else "-")
            m = [f for f in marcadas if f["documento"] == d.id]
            vlm = sum(1 for f in m if ruta_vlm(p, d.id, f["pagina"]).exists())
            print(f"  {d.id:<9} {reg['paginas'] if reg else '?':>5} {ocr:<9} {len(m):>8} {vlm:>5}  {d.archivo}")


def cmd_ocr(args):
    from mt.ocr import ocr_documento

    inicio = time.time()
    for p, docs in _objetivos(args):
        print(f"\n{p.id} · {p.nombre}")
        for d in docs:
            ocr_documento(p, d, forzar=args.forzar, paginas=_paginas(args.paginas))
    print(f"\n⏱ {time.time() - inicio:.0f} s")


def cmd_marcar(args):
    from mt.calidad import Umbrales
    from mt.marcar import marcar

    u = Umbrales()
    for campo, valor in (("conf_media", args.umbral), ("frac_baja", args.frac_baja),
                         ("frac_rara", args.frac_rara), ("min_palabras", args.min_palabras)):
        if valor is not None:
            setattr(u, campo, valor)
    for p in indice.proyectos(args.proyecto):
        marcadas = marcar(p, u)
        print(f"{p.id}: {len(marcadas)} páginas marcadas → {p.dir_revision / 'paginas_marcadas.csv'}")
        print(f"      resumen y calibración → {p.dir_revision / 'calidad_ocr.md'}")


def cmd_vlm(args):
    from mt.vlm import rescatar

    from mt.vlm import ModeloOpenRouter

    for p, docs in _objetivos(args):
        vlm = ModeloOpenRouter(p.carpeta / "cache" / "llm") if args.openrouter else None
        rescatar(p, {d.id for d in docs}, modelo=args.modelo, forzar=args.forzar, limite=args.limite, vlm=vlm)


def cmd_comparar(args):
    from mt.vlm import comparar

    for p in indice.proyectos(args.proyecto):
        print(f"{p.id}: comparación → {comparar(p)}")


def _etapa(nombre: str, p, args):
    from mt import actos, arbol, bases, chunks, ensamblar, fragmentos, llm, relaciones, tablas, texto, verificar
    from mt import vectores, vincular

    inicio = time.time()
    print(f"\n[{nombre}] {p.id} · {p.nombre}")
    usar_llm = not args.reglas
    if nombre == "texto":
        for d in p.documentos:
            texto.texto_documento(p, d)
    elif nombre == "actos":
        actos.actos_proyecto(p, usar_llm)
    elif nombre == "bases":
        bases.estructurar(p, usar_llm)
    elif nombre == "fragmentos":
        fragmentos.fragmentos_proyecto(p, actos.leer_actos(p))
    elif nombre == "relaciones":
        pr = relaciones.Procesador(p, solo_cache=not usar_llm)
        pr.procesar()
        pr.exportar()
        print(f"  {len(pr.filas)} operaciones · {len(pr.estado.pendientes)} pendientes · "
              f"{len(pr.sin_respuesta)} fragmentos sin respuesta → revision/relaciones.csv, revision/pendientes.csv")
    elif nombre == "tablas":
        if usar_llm:
            tablas.transcribir_json(p)                   # visión → JSON; el texto de las páginas no cambia
        else:
            tablas.detectar(p)
    elif nombre == "vincular":
        vincular.vincular(p, usar_llm)
    elif nombre == "arbol":
        arbol.construir(p)
    elif nombre == "chunks":
        chunks.chunks(p)
    elif nombre == "chunks_doc":                        # prueba: chunks desde el documento único (RAG_FUENTE=documento)
        from mt import chunks_doc
        chunks_doc.chunks_documento(p)
    elif nombre == "ensamblar":
        ensamblar.ensamblar(p)
    elif nombre == "embeddings":
        vectores.vectorizar(p, usar_llm)
    elif nombre == "verificar":
        verificar.verificar(p)
    if nombre in ("actos", "bases", "relaciones", "tablas", "vincular", "embeddings"):
        saldo = llm.saldo_restante()
        print(f"  {llm.resumen(p.carpeta / 'cache' / 'llm')}"
              + (f" · saldo de la clave US$ {saldo:.3f}" if saldo is not None else ""))
    print(f"  ⏱ {time.time() - inicio:.0f} s")


ETAPAS = ["texto", "tablas", "actos", "bases", "fragmentos", "relaciones", "vincular", "arbol", "ensamblar", "chunks",
          "embeddings", "verificar"]


def cmd_documento(args):
    etapas = ETAPAS if args.cmd == "todo" else [args.cmd]
    for p in indice.proyectos(args.proyecto):
        for e in etapas:
            _etapa(e, p, args)


def cmd_consultar(args):
    from mt import vectores

    p = indice.proyectos(args.proyecto)[0]
    if args.agente:
        from mt.agente import Agente
        r, _, traza = Agente(p).responder(args.pregunta)
        print(f"\nPASOS: {' → '.join(traza)}\n\nRESPUESTA:\n{r.respuesta if r else '(sin respuesta)'}")
        return
    vectores.imprimir(p, args.pregunta, k=args.k, solo_vigentes=not args.anteriores, solo_buscar=args.solo_buscar)


def cmd_evaluar(args):
    from mt import evaluar, llm

    for p in indice.proyectos(args.proyecto):
        print(f"\n[evaluar] {p.id} · {p.nombre}")
        solo = {int(x) for x in args.solo.split(",")} if args.solo else None
        evaluar.evaluar(p, k=args.k, archivo=args.preguntas, agente=args.agente, solo=solo)
        print(f"  {llm.resumen(p.carpeta / 'cache' / 'llm')} · saldo de la clave US$ {llm.saldo_restante() or 0:.3f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("estado")
    s.add_argument("--proyecto")
    s.set_defaults(f=cmd_estado)

    s = sub.add_parser("ocr")
    s.add_argument("--proyecto")
    s.add_argument("--doc")
    s.add_argument("--paginas", help="p. ej. 3,7-9 (solo con --doc)")
    s.add_argument("--forzar", action="store_true")
    s.set_defaults(f=cmd_ocr)

    s = sub.add_parser("marcar")
    s.add_argument("--proyecto")
    s.add_argument("--umbral", type=float, help="confianza media mínima (0–1)")
    s.add_argument("--frac-baja", type=float, help="fracción máxima de palabras con confianza baja")
    s.add_argument("--frac-rara", type=float, help="fracción máxima de palabras que no parecen español")
    s.add_argument("--min-palabras", type=int, help="mínimo de palabras en una página con tinta")
    s.set_defaults(f=cmd_marcar)

    s = sub.add_parser("vlm")
    s.add_argument("--proyecto")
    s.add_argument("--doc")
    s.add_argument("--modelo", help="id de Hugging Face (por defecto VLM_MODELO)")
    s.add_argument("--limite", type=int, help="procesar a lo más N páginas (para probar)")
    s.add_argument("--openrouter", action="store_true", help="modelo de visión de OpenRouter en vez de uno local")
    s.add_argument("--forzar", action="store_true")
    s.set_defaults(f=cmd_vlm)

    s = sub.add_parser("comparar")
    s.add_argument("--proyecto")
    s.set_defaults(f=cmd_comparar)

    for nombre in ETAPAS + ["todo", "chunks_doc"]:
        s = sub.add_parser(nombre)
        s.add_argument("--proyecto")
        s.add_argument("--reglas", action="store_true", help="sin llamar al LLM")
        s.set_defaults(f=cmd_documento)

    s = sub.add_parser("consultar", help="RAG: busca en los chunks y responde citando")
    s.add_argument("pregunta")
    s.add_argument("--proyecto", required=True)
    s.add_argument("--k", type=int, default=10, help="chunks recuperados")
    s.add_argument("--anteriores", action="store_true", help="incluir versiones no vigentes")
    s.add_argument("--solo-buscar", action="store_true", help="solo recuperar, sin respuesta del LLM")
    s.add_argument("--agente", action="store_true", help="el agente busca y lee varias veces antes de responder")
    s.set_defaults(f=cmd_consultar)

    s = sub.add_parser("evaluar", help="RAG contra las respuestas revisadas de revision/preguntas_rag.csv")
    s.add_argument("--proyecto", required=True)
    s.add_argument("--k", type=int, help="chunks recuperados (por defecto el de vectores.responder)")
    s.add_argument("--preguntas", default="preguntas_rag.csv", help="CSV de revision/ (p. ej. preguntas_tematicas.csv)")
    s.add_argument("--agente", action="store_true", help="responde con el agente que busca y lee varias veces")
    s.add_argument("--solo", help="solo estas preguntas, p. ej. 6,31,45")
    s.set_defaults(f=cmd_evaluar)

    args = ap.parse_args(argv)
    if getattr(args, "paginas", None) and not args.doc:
        ap.error("--paginas requiere --doc")
    args.f(args)


if __name__ == "__main__":
    sys.exit(main())
