"""Crítico de cambios: revisa cada respuesta del LLM de `relaciones` ya aplicada al estado.

Recibe la instrucción literal del fragmento y, para cada artículo tocado, los párrafos que cambiaron (antes →
después). Devuelve `{correcto, problema}`: no escribe texto. Si rechaza, `relaciones.Procesador` deshace el cambio y
pide al LLM una corrección con el motivo; si también la rechaza, el fragmento queda pendiente y va a los anexos.

- `CRITICO=no` lo apaga. Modelos en `LLM_MODELOS_CRITICO` (por defecto otra familia que el aplicador).
- Las críticas quedan en la caché del LLM (tarea "critico"): `arbol` y `vincular`, que repiten las relaciones solo
  desde caché, reproducen las mismas decisiones.
- `revision/criticas.csv`: fragmento;intento;correcto;problema;operaciones.
"""
from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel

from . import config, llm

ACTIVO = config.env("CRITICO", "si").lower() in ("si", "sí", "1", "true")
MODELOS = [m.strip() for m in config.env(
    "LLM_MODELOS_CRITICO", "deepseek/deepseek-v4-flash,qwen/qwen3.7-flash").split(",") if m.strip()]
MAX_CHARS = 1500


class Critica(BaseModel):
    correcto: bool
    problema: str


SISTEMA = ("Revisas cambios aplicados a las Bases de Licitación de una concesión de obras públicas de Chile. "
           "Respondes SOLO con un objeto JSON válido que cumple el esquema pedido.")

INSTRUCCIONES = """Un sistema leyó la INSTRUCCIÓN de un acto administrativo y la aplicó a los artículos de las Bases.
Revisa si el resultado es el que ordena la instrucción. Abajo están las operaciones que aplicó y, para cada artículo,
los párrafos que cambiaron (ANTES → DESPUÉS). El texto viene de OCR: no evalúes errores de lectura ni de redacción.

Marca `correcto: false` solo si hay un error claro:
- se cambió otro artículo u otro párrafo que el que indica la instrucción;
- el texto reemplazado, eliminado o agregado no es el que ordena la instrucción (sobra o falta una parte);
- se agregó como texto nuevo parte de la instrucción misma ("Reemplázase el párrafo…", "Donde dice…");
- la instrucción ordena modificar un artículo candidato y no se aplicó ningún cambio, o se aplicó como aclaración;
- se aplicó un cambio pero el fragmento solo aclara, interpreta o cita el artículo sin modificarlo.
Si está bien, `correcto: true` y `problema` vacío. Si no, en `problema` explica en una o dos frases qué está mal y qué
debería hacerse (artículo, párrafo o trozo correcto), sin reescribir el texto."""


def _parrafos(sec) -> list[tuple[str, str]]:
    return [(p.id, p.texto) for p in sec.parrafos] if sec else []


def _recorte(t: str) -> str:
    t = " ".join(t.split())
    return t if len(t) <= MAX_CHARS else t[:MAX_CHARS] + "…"


def diferencias(antes, despues) -> list[str]:
    """Bloques de texto con los párrafos que cambiaron entre dos estados (`consolidar.Estado`), por sección."""
    previas = {s.id: s for s in antes.secciones}
    bloques = []
    for s in despues.secciones:
        a = previas.get(s.id)
        pa, pd = _parrafos(a), _parrafos(s)
        if pa == pd and (a is not None and a.eliminada_por == s.eliminada_por):
            continue
        ids_a, ids_d = {i for i, _ in pa}, {i for i, _ in pd}
        lineas = [f"### {s.numero} — {' '.join(s.titulo.split())[:100]}"
                  + (" (artículo agregado)" if a is None else "") + (" (artículo eliminado)" if s.eliminada_por and
                                                                       not (a and a.eliminada_por) else "")]
        for k, (i, t) in enumerate(pa, 1):
            if i not in ids_d:
                lineas.append(f"ANTES [P{k}]: {_recorte(t)}")
        for k, (i, t) in enumerate(pd, 1):
            if i not in ids_a:
                lineas.append(f"DESPUÉS [P{k}]: {_recorte(t)}")
        bloques.append("\n".join(lineas))
    return bloques


def revisar(dir_llm: Path, acto: str, instruccion: str, candidatos: list[str], operaciones: str,
            bloques: list[str], solo_cache: bool) -> Critica:
    cambios = "\n\n".join(bloques) if bloques else "(no se aplicó ningún cambio al texto de las Bases)"
    prompt = (f"{INSTRUCCIONES}\n\nACTO: {acto}\n\nINSTRUCCIÓN (fragmento del acto):\n{instruccion}\n\n"
              f"ARTÍCULOS CANDIDATOS: {', '.join(candidatos)}\n\nOPERACIONES APLICADAS:\n{operaciones}\n\n"
              f"CAMBIOS:\n{cambios}")
    return llm.pedir_json(dir_llm, "critico", prompt, Critica, sistema=SISTEMA, modelos=MODELOS,
                          solo_cache=solo_cache,
                          validar=lambda c: None if c.correcto or c.problema.strip() else "falta el problema")


def exportar(dir_revision: Path, filas: list[dict]):
    dir_revision.mkdir(parents=True, exist_ok=True)
    with (dir_revision / "criticas.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["fragmento", "intento", "correcto", "problema", "operaciones"],
                           delimiter=";", lineterminator="\n")
        w.writeheader()
        w.writerows(filas)
