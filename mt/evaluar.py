"""Evaluación del RAG contra respuestas revisadas a mano.

Lee `revision/preguntas_rag.csv` (columnas `pregunta` y `respuesta`: la revisada, que sirve de referencia),
responde cada pregunta con `vectores.responder()` y un juez barato (LLM sin razonamiento, en lotes) marca cada
respuesta como correcta, parcial o incorrecta frente a la referencia. → `revision/evaluacion_rag.csv`.
Las respuestas del RAG y del juez quedan en caché: repetir la evaluación sin cambios no gasta.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

from . import config, llm, vectores
from .indice import Proyecto

LOTE_JUEZ = 10
VERSION_JUEZ = config.env("RAG_JUEZ", "v3")


class _Veredicto(BaseModel):
    n: int
    veredicto: Literal["correcta", "parcial", "incorrecta"]
    motivo: str


class _Juicio(BaseModel):
    veredictos: list[_Veredicto]


class _VeredictoV2(BaseModel):
    n: int
    falta: str                       # lo que la pregunta pide expresamente y la respuesta no trae ("" si nada)
    contradice: bool                 # la respuesta afirma algo contrario a la referencia
    veredicto: Literal["correcta", "parcial", "incorrecta"]
    motivo: str


class _JuicioV2(BaseModel):
    veredictos: list[_VeredictoV2]


CRITERIO_V2 = (
    "Para cada caso decide si la RESPUESTA contesta bien la PREGUNTA. La REFERENCIA, revisada por una persona, sirve "
    "para verificar los datos; no es una lista de todo lo que la respuesta debe decir.\n"
    "1. Identifica qué pide la pregunta expresamente (por ejemplo «monto y vigencia» son dos cosas; «el plazo» es "
    "una). En `falta` anota lo pedido expresamente que la respuesta no trae; deja \"\" si no falta nada. Lo que la "
    "referencia agrega y la pregunta no pide (consecuencias, excepciones, complementos) NO cuenta como falta.\n"
    "2. En `contradice` marca true solo si la respuesta afirma un dato contrario a la referencia (otra cifra, otra "
    "fecha, otro nombre, sí en vez de no). Información adicional que no contradice la referencia (otros artículos, "
    "otros actos, detalles extra) NO es contradicción ni resta.\n"
    "3. Veredicto:\n"
    "- correcta: trae el dato principal pedido, `falta` está vacío o es secundario, y `contradice` es false.\n"
    "- parcial: trae parte de lo pedido pero falta algo pedido expresamente, o mezcla el dato correcto con uno que "
    "contradice la referencia.\n"
    "- incorrecta: el dato principal es otro, o dice que no hay información cuando la referencia sí la tiene.\n"
    "Si la referencia dice que no se puede responder o que no aplica, la respuesta es correcta cuando también dice "
    "que los documentos no lo contienen o que no aplica.\n"
    "El motivo va en una frase corta.\n\n")

# v4 «completa»: correcta solo si trae todo el contenido esencial de la referencia que responde la pregunta
CRITERIO_V4 = (
    "Para cada caso decide si la RESPUESTA está COMPLETA frente a la REFERENCIA, revisada por una persona.\n"
    "- correcta: contiene TODOS los datos esenciales de la referencia que responden la pregunta (cifras, plazos, "
    "nombres, condiciones, quién decide, norma aplicable) y no contradice ninguno. La información adicional verdadera "
    "no resta, y tampoco falta lo que la referencia agrega como contexto sin que la pregunta lo pida.\n"
    "- parcial: trae el dato principal pero le falta algún dato esencial de la referencia, o lo mezcla con un dato "
    "que la contradice.\n"
    "- incorrecta: el dato principal es otro, o dice que no hay información cuando la referencia sí la tiene.\n"
    "Si la referencia dice que no se puede responder o que no aplica, la respuesta es correcta cuando también dice "
    "que los documentos no lo contienen o que no aplica.\n"
    "En `falta` anota los datos esenciales de la referencia que la respuesta no trae (\"\" si ninguno); en "
    "`contradice`, si afirma algo contrario a la referencia. El motivo va en una frase corta.\n\n")

REGLAS_V3 = (
    "Reglas adicionales:\n"
    "- Si la pregunta pide un solo dato, basta con darlo bien: no exijas consecuencias, excepciones ni complementos "
    "que la pregunta no nombra.\n"
    "- Si los documentos dan alternativas (tramos, con o sin obra opcional) y la referencia dice cuál rige en este "
    "contrato, la respuesta que no dice cuál rige es parcial.\n"
    "- Si la respuesta pone primero un dato equivocado o parcial y el correcto solo como secundario, es parcial.\n"
    "- Si la respuesta queda cortada a mitad de frase o mezcla elementos que no corresponden a lo preguntado, es "
    "parcial.\n\n")


SISTEMA_JUEZ = ("Evalúas respuestas sobre contratos de concesión de obras públicas de Chile comparándolas con una "
                "respuesta de referencia revisada por una persona. Respondes SOLO con un objeto JSON válido.")


def _juzgar(p: Proyecto, filas: list[dict], version: str | None = None) -> dict[int, BaseModel]:
    """Juez barato en lotes. v1: compara con la referencia (castigaba datos extra verdaderos). v2 (por defecto):
    evalúa solo lo que la pregunta pide, con `falta` y `contradice` explícitos."""
    version = version or VERSION_JUEZ
    salida: dict[int, BaseModel] = {}
    for a in range(0, len(filas), LOTE_JUEZ):
        lote = filas[a:a + LOTE_JUEZ]
        ns = {f["n"] for f in lote}
        casos = "\n\n".join(f"CASO {f['n']}\nPREGUNTA: {f['pregunta']}\nREFERENCIA: {f['referencia']}\n"
                            f"RESPUESTA: {f['respuesta'] or '(sin respuesta)'}" for f in lote)
        esquema = _Juicio if version == "v1" else _JuicioV2
        criterio = CRITERIO_V4 if version == "v4" else CRITERIO_V2 + (REGLAS_V3 if version == "v3" else "")
        prompt = criterio + casos if version != "v1" else (
            "Para cada caso, compara la RESPUESTA con la REFERENCIA.\n"
            "- correcta: contiene el dato principal de la referencia (números, fechas, nombres) y no lo contradice.\n"
            "- parcial: acierta en parte, le falta el dato principal o agrega una afirmación que la contradice.\n"
            "- incorrecta: da otro dato, o dice que no hay información cuando la referencia sí la tiene.\n"
            "Si la referencia dice que no se puede responder o que no aplica, la respuesta es correcta cuando "
            "también dice que los documentos no lo contienen.\n"
            "El motivo va en una frase corta.\n\n"
            + "\n\n".join(f"CASO {f['n']}\nPREGUNTA: {f['pregunta']}\nREFERENCIA: {f['referencia']}\n"
                          f"RESPUESTA: {f['respuesta'] or '(sin respuesta)'}" for f in lote))

        def validar(r) -> str | None:
            vistos = {v.n for v in r.veredictos}
            return None if vistos == ns else f"faltan o sobran casos: {sorted(ns ^ vistos)}"

        try:
            r = llm.pedir_json(p.carpeta / "cache" / "llm", "evaluar", prompt, esquema, sistema=SISTEMA_JUEZ,
                               validar=validar, modelos=vectores.MODELOS_RAG, razonar=False)
            salida.update({v.n: v for v in r.veredictos})
        except llm.SinModelos as e:
            print(f"  juez sin respuesta para los casos {sorted(ns)}: {str(e)[:150]}")
    return salida


def evaluar(p: Proyecto, k: int | None = None, archivo: str = "preguntas_rag.csv", agente: bool = False,
            solo: set[int] | None = None) -> Counter:
    """`archivo`: CSV de `revision/` con `n;pregunta;respuesta` (y opcionales `tema`, `fuente`). Las preguntas mal
    respondidas se asignan a una etapa del pipeline en `revision/errores_pipeline.csv` (`errores.py`)."""
    from . import errores

    with (p.dir_revision / archivo).open(encoding="utf-8-sig", newline="") as fh:
        preguntas = [f for f in csv.DictReader(fh, delimiter=";") if f.get("pregunta")
                     and (not solo or int(f["n"]) in solo)]
    nombre_set = re.sub(r"^preguntas_|\.csv$", "", archivo) + ("_agente" if agente else "")
    if agente:
        from .agente import Agente
    indice = vectores.Indice(p)
    kw = {"k": k} if k else {}

    hallados_de: dict[int, list[dict]] = {}

    trazas: dict[int, str] = {}

    def una(f: dict) -> dict:
        if agente:
            r, hallados, traza = Agente(p, indice).responder(f["pregunta"])
            trazas[int(f["n"])] = " → ".join(traza)
        else:
            r, hallados = vectores.responder(p, f["pregunta"], indice=indice, **kw)
        hallados_de[int(f["n"])] = [c for _, c in hallados]
        usados = [c["id"] for n, (_, c) in enumerate(hallados, 1) if r and n in r.fuentes]
        return {"n": int(f["n"]), "tema": f.get("tema", ""), "pregunta": f["pregunta"], "referencia": f["respuesta"],
                "respuesta": r.respuesta if r else "", "fuentes": " · ".join(usados),
                "hallados": " · ".join(c["id"] + (f" (+{len(c['incluidos']) - 1})" if len(c.get("incluidos", [])) > 1 else "")
                                       for _, c in hallados), "fuente_ref": f.get("fuente", "")}

    with ThreadPoolExecutor(4) as pool:
        filas = list(pool.map(una, preguntas))
    veredictos = _juzgar(p, filas)
    for f in filas:
        v = veredictos.get(f["n"])
        f["veredicto"], f["motivo"] = (v.veredicto, v.motivo) if v else ("sin juez", "")
    salida = ("evaluacion_rag" if nombre_set == "rag" else f"evaluacion_{nombre_set}") + ("_sub" if solo else "")
    for f in filas:
        f["pasos"] = trazas.get(f["n"], "")
    ruta = p.dir_revision / f"{salida}{vectores.SUFIJO}.csv"
    campos = (["n"] + (["tema"] if any(f["tema"] for f in filas) else []) + ["pregunta", "veredicto", "motivo",
              "respuesta", "referencia", "fuentes", "hallados"] + (["pasos"] if agente else []))
    with ruta.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, delimiter=";", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)
    c = Counter(f["veredicto"] for f in filas)
    print(f"  {ruta.name}: {len(filas)} preguntas · correctas {c['correcta']} · parciales {c['parcial']} · "
          f"incorrectas {c['incorrecta']}" + (f" · sin juez {c['sin juez']}" if c["sin juez"] else ""))
    for tema in sorted({f["tema"] for f in filas if f["tema"]}):
        ct = Counter(f["veredicto"] for f in filas if f["tema"] == tema)
        print(f"    {tema}: correctas {ct['correcta']} · parciales {ct['parcial']} · incorrectas {ct['incorrecta']}")
    if not vectores.SUFIJO and (agente or not solo):        # pruebas con otros chunks o subconjuntos: no se registran
        errores.registrar(p, nombre_set, filas, hallados_de)
    malas = [f for f in filas if f["veredicto"] != "correcta"]
    for f in malas:
        print(f"    {f['n']:>2} {f['veredicto']:<10} {f['pregunta'][:70]} — {f['motivo'][:90]}")
    return c


def validar_juez(p: Proyecto, version: str | None = None) -> dict:
    """Contrasta un juez con la revisión manual (`errores_revision.csv`) sobre las respuestas ya guardadas en
    `evaluacion_*.csv`: un fallo revisado como `juez` debería salir correcto; uno de generación, recuperación, OCR o
    ficha, no correcto; y lo que el juez anterior daba por correcto debería seguir correcto. Solo llama al juez."""
    from .errores import _revision
    from .errores import huella as errores_huella

    manual = _revision(p)
    esperado, filas_de = {}, {}
    for nombre_set, archivo in (("rag", "evaluacion_rag.csv"), ("tematicas", "evaluacion_tematicas.csv")):
        ruta = p.dir_revision / archivo
        if not ruta.exists():
            continue
        with ruta.open(encoding="utf-8-sig", newline="") as fh:
            filas = [f | {"n": int(f["n"])} for f in csv.DictReader(fh, delimiter=";")]
        filas_de[nombre_set] = filas
        for f in filas:
            m = manual.get((nombre_set, str(f["n"])))
            if m and m.get("huella") not in (None, "", errores_huella(f["respuesta"])):
                m = None                                     # la revisión era de otra respuesta
            if m and m["etapa"] in ("referencia", "sin_fuente"):
                continue                                     # pregunta o referencia dudosas: no sirven de etiqueta
            if m:
                esperado[(nombre_set, f["n"])] = "correcta" if m["etapa"] == "juez" else "no"
            elif f["veredicto"] == "correcta":
                esperado[(nombre_set, f["n"])] = "correcta"
    aciertos, fallos = Counter(), []
    for nombre_set, filas in filas_de.items():
        juicio = _juzgar(p, filas, version)
        for f in filas:
            e = esperado.get((nombre_set, f["n"]))
            v = juicio.get(f["n"])
            if e is None or v is None:
                continue
            tipo = "etiquetas" if (nombre_set, str(f["n"])) in manual else "correctas previas"
            ok = (v.veredicto == "correcta") == (e == "correcta")
            aciertos[(tipo, ok)] += 1
            if not ok:
                fallos.append(f"{nombre_set} {f['n']}: esperado {e}, juez {v.veredicto} — {v.motivo[:110]}")
    for tipo in ("etiquetas", "correctas previas"):
        total = aciertos[(tipo, True)] + aciertos[(tipo, False)]
        print(f"  juez {version or VERSION_JUEZ} · {tipo}: {aciertos[(tipo, True)]}/{total} de acuerdo")
    for x in fallos:
        print("    " + x)
    return {"aciertos": aciertos, "fallos": fallos}
