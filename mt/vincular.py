"""Ubica cada fragmento que no quedó aplicado al texto de las Bases (decretos, convenios, resoluciones, el resto
de las circulares) en el artículo con el que se relaciona, en un tema o como formalidad. Nada se reescribe:
solo se decide dónde va cada fragmento literal.

1. Formalidad, sin LLM: publicaciones ajenas del Diario Oficial, VISTOS, CONSIDERANDO, TENIENDO PRESENTE.
2. Cita explícita, sin LLM: el fragmento cita "numeral X de las Bases" (en las circulares basta el numeral).
3. El resto, con LLM barato (Qwen-flash / DeepSeek) en lotes: artículo de la lista, tema o formalidad.

→ `revision/vinculos.csv` (fragmento;acto;destino;tipo;origen). `revision/vinculos_correcciones.csv`
  (fragmento;destino) manda sobre todo lo demás.
"""
from __future__ import annotations

import csv
import re

from pydantic import BaseModel

from . import config, llm
from .indice import Proyecto
from .numerales import citas, clave_numero
from .relaciones import RE_BASES, Procesador, cita_explicita, nombre_acto

TEMAS = {
    "obras": "obras, proyectos de ingeniería, construcción, obras nuevas o adicionales",
    "plazos_y_puesta_en_servicio": "plazos, puestas en servicio, extinción, inicio y término de la concesión",
    "regimen_economico": "pagos, compensaciones, subsidios, ingresos, IVA, cuentas, valores proforma, intereses",
    "tarifas_y_peajes": "tarifas, peajes, plazas de peaje, reajustes, sistemas de cobro",
    "garantias_y_seguros": "boletas de garantía, garantías de construcción o explotación, seguros",
    "multas": "multas, infracciones, sanciones",
    "operacion_y_servicios": "explotación, conservación, mantención, servicios, reglamento de servicio",
    "expropiaciones": "expropiaciones, terrenos, faja fiscal",
    "otros": "otro contenido sustantivo que no cabe en los temas anteriores",
}
RE_FORMALIDAD = re.compile(r"^\s*(VISTOS?|CONSIDERANDO|TENIENDO PRESENTE)\b", re.I)
MODELOS = [m.strip() for m in config.env(
    "LLM_MODELOS_CLASIFICAR", "qwen/qwen3.7-flash,deepseek/deepseek-chat-v3.1").split(",") if m.strip()]
LOTE = 25
MIN_LOTE = 13          # un lote rechazado se parte en dos una sola vez
_RE_ID = re.compile(r"[A-Z][A-Z0-9]*\.D\d+\.F\d+")


def _id(texto: str) -> str:
    """Id del fragmento en la respuesta (los modelos a veces copian «[RITP.D06.F0001] (acto…)»)."""
    m = _RE_ID.search(texto)
    return m[0] if m else texto.strip()


class _Asignacion(BaseModel):
    fragmento: str
    destino: str


class _Lote(BaseModel):
    asignaciones: list[_Asignacion]


def pendientes(pr: Procesador) -> list[dict]:
    """Fragmentos con líneas que no quedaron en el texto de las Bases (ni como instrucción o aclaración)."""
    salida = []
    for acto in pr.actos:
        doc = acto["documento"]
        if doc == pr.bases.id:
            continue
        for f in pr.fragmentos[doc]:
            if f["acto"] != acto["acto"]:
                continue
            libres = pr._libres(doc, f["lineas"])
            if libres:
                salida.append({"frag": f, "acto": acto, "doc": doc, "lineas": libres,
                               "texto": "\n".join(ln["texto"] for ln in libres)})
    return salida


def _correcciones(p: Proyecto) -> dict[str, str]:
    ruta = p.dir_revision / "vinculos_correcciones.csv"
    if not ruta.exists():
        return {}
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        return {f["fragmento"].strip(): f["destino"].strip() for f in csv.DictReader(fh, delimiter=";")
                if (f.get("fragmento") or "").strip()}


def _normalizar(destino: str, claves: set[str]) -> str | None:
    d = destino.strip()
    if d.upper().startswith("FORMALIDAD"):
        return "FORMALIDAD"
    if d.upper().startswith("TEMA:"):
        tema = d.split(":", 1)[1].strip().lower()
        return f"TEMA:{tema}" if tema in TEMAS else None
    numero = clave_numero(d.split(":", 1)[1] if d.upper().startswith("BL:") else d.split(" ")[0])
    while numero and numero not in claves and "." in numero:    # numeral inexistente (2.2.3.4) → su padre (2.2.3)
        numero = numero.rsplit(".", 1)[0]
    return f"BL:{numero}" if numero in claves else None


def vincular(p: Proyecto, usar_llm: bool = True) -> list[dict]:
    pr = Procesador(p, solo_cache=True)
    pr.procesar()
    claves = {s.numero for s in pr.estado.secciones if s.numero != "0"}
    manual = _correcciones(p)
    filas, resto = [], []
    for item in pendientes(pr):
        f, acto, texto = item["frag"], item["acto"], item["texto"]
        circular = "circular" in acto["tipo"]
        destino = tipo = None
        if f["id"] in manual:
            destino, origen = _normalizar(manual[f["id"]], claves) or manual[f["id"]], "manual"
        elif f["ajeno"]:
            destino, origen = "FORMALIDAD", "regla:ajeno"
        elif RE_FORMALIDAD.match(texto):
            destino, origen = "FORMALIDAD", "regla:vistos"
        else:
            refs = [r for r in (f["refs"] or citas(texto)) if r in claves]
            explicitas = refs if circular else [r for r in refs if cita_explicita(texto, r, RE_BASES)]
            if explicitas:
                destino, origen = f"BL:{explicitas[0]}", "regla:cita"
        if destino is None:
            resto.append(item)
            continue
        filas.append(_fila(item, destino, origen))

    if resto and usar_llm:
        listado = "\n".join(f"BL:{s.numero} — {re.sub(r'\s+', ' ', s.titulo)[:90]}" for s in pr.estado.secciones
                            if s.numero != "0")
        temas = "\n".join(f"TEMA:{t} — {d}" for t, d in TEMAS.items())

        def clasificar(lote: list[dict], etiqueta: str):
            """Un lote al LLM; si ningún modelo lo responde completo, se parte en dos (los modelos omiten
            fragmentos en lotes largos) hasta MIN_LOTE."""
            ids = {it["frag"]["id"] for it in lote}

            def texto_prompt(con_fecha: bool) -> str:
                return (
                    "Clasifica cada FRAGMENTO de un acto administrativo de una concesión vial chilena según dónde debe "
                    "ubicarse en un documento único organizado por los artículos de las Bases de Licitación.\n"
                    "- Si el fragmento trata claramente el tema de un artículo, destino = su id (BL:numeral).\n"
                    "- Si no corresponde a ningún artículo, destino = un tema (TEMA:…).\n"
                    "- Si es solo una formalidad (encabezado, vistos, considerandos, firmas, distribución, timbres, "
                    "texto ilegible), destino = FORMALIDAD.\n"
                    "Responde una asignación por fragmento, con su id exacto.\n\n"
                    f"ARTÍCULOS DE LAS BASES:\n{listado}\n\nTEMAS:\n{temas}\n\nFRAGMENTOS:\n"
                    + "\n\n".join(f"[{it['frag']['id']}] ({nombre_acto(it['acto'], con_fecha)}) "
                                  f"{re.sub(r'\s+', ' ', it['texto'])[:420]}" for it in lote))

            # sin fechas: corregir una fecha en revision/actos.csv no invalida la caché (alias = prompt anterior)
            prompt, alias = texto_prompt(False), [texto_prompt(True)]

            def validar(r: _Lote) -> str | None:
                vistos = {_id(a.fragmento) for a in r.asignaciones}
                if vistos != ids:
                    return f"faltan o sobran fragmentos: {sorted(ids ^ vistos)[:5]}"
                malos = [a.destino for a in r.asignaciones if _normalizar(a.destino, claves) is None]
                return f"destinos inválidos: {malos[:5]}" if malos else None

            try:
                r = llm.pedir_json(p.carpeta / "cache" / "llm", "vincular", prompt, _Lote, validar=validar,
                                   modelos=MODELOS, razonar=False, alias=alias)
            except llm.SinModelos as e:
                if len(lote) > MIN_LOTE and "faltan o sobran" in str(e):
                    mitad = len(lote) // 2
                    clasificar(lote[:mitad], f"{etiqueta}a")
                    clasificar(lote[mitad:], f"{etiqueta}b")
                    return
                print(f"    lote {etiqueta}: sin respuesta ({str(e)[:400]}) → TEMA:otros")
                filas.extend(_fila(it, "TEMA:otros", "sin_llm") for it in lote)
                return
            asignado = {_id(a.fragmento): _normalizar(a.destino, claves) for a in r.asignaciones}
            filas.extend(_fila(it, asignado[it["frag"]["id"]], "llm") for it in lote)

        for k in range(0, len(resto), LOTE):
            clasificar(resto[k:k + LOTE], str(k // LOTE + 1))
    else:
        filas += [_fila(it, "TEMA:otros", "sin_llm") for it in resto]

    p.dir_revision.mkdir(parents=True, exist_ok=True)
    with (p.dir_revision / "vinculos.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["fragmento", "acto", "acto_nombre", "destino", "tipo", "origen", "inicio"],
                           delimiter=";", lineterminator="\n")
        w.writeheader()
        w.writerows(filas)
    from collections import Counter
    c = Counter(f["tipo"] for f in filas)
    print(f"  {p.id}: {len(filas)} fragmentos · artículo {c['articulo']} · tema {c['tema']} · formalidad "
          f"{c['formalidad']} · {sum(f['origen'] == 'llm' for f in filas)} por LLM")
    return filas


def _fila(item: dict, destino: str, origen: str) -> dict:
    tipo = "formalidad" if destino == "FORMALIDAD" else "tema" if destino.startswith("TEMA:") else "articulo"
    return {"fragmento": item["frag"]["id"], "acto": item["acto"]["acto"], "acto_nombre": nombre_acto(item["acto"]),
            "destino": destino, "tipo": tipo, "origen": origen,
            "inicio": re.sub(r"\s+", " ", item["texto"])[:100]}


def leer_vinculos(p: Proyecto) -> dict[str, str]:
    with (p.dir_revision / "vinculos.csv").open(encoding="utf-8-sig", newline="") as fh:
        return {f["fragmento"]: f["destino"] for f in csv.DictReader(fh, delimiter=";")}
