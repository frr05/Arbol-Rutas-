"""RAG agéntico: el modelo busca, lee y vuelve a buscar hasta tener una respuesta completa.

En cada paso el LLM (barato, sin razonamiento largo) devuelve un JSON `{falta, accion, argumento}` y el código ejecuta
la acción, sin LLM:
  buscar(consulta)        búsqueda híbrida de siempre (`vectores.Indice.buscar`): los mejores trozos con su ruta
  leer_articulo(numeral)  texto vigente completo de un artículo de las Bases (y sus subartículos), con sus tablas
  leer_acto(nombre)       fragmentos de un acto en orden del documento; repetirlo sigue con los siguientes
  ficha()                 ficha de actos (tipo, número, fecha, convenios, actos citados)
  responder               cuando lo leído cubre todo lo que pide la pregunta (`falta` vacío) o se acaban los pasos
La respuesta final sale de otra llamada que solo ve lo leído y cita cada trozo con [n]: el texto sigue siendo literal
y el agente solo elige qué leer. Todo queda en la caché del LLM, así que repetir una pregunta no gasta.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from . import config, llm, vectores
from .actos import actos_nombrados
from .arbol import leer_arbol
from .indice import Proyecto
from .numerales import clave_numero

MAX_PASOS = config.env_int("AGENTE_PASOS", 5)
MIN_ACCIONES = config.env_int("AGENTE_MIN", 0)   # lecturas obligatorias antes de responder; con 2, CM bajó de 6 a 1 de 10
MAX_LEIDO = 6000            # caracteres por lectura de artículo o acto
MAX_EVIDENCIA = 30000       # caracteres de todo lo leído que ve el modelo (se recortan las lecturas más antiguas)
RESULTADOS = 5              # trozos por búsqueda


class Paso(BaseModel):
    falta: str                      # lo que la pregunta pide y aún no está respaldado por lo leído ("" si nada)
    accion: Literal["buscar", "leer_articulo", "leer_acto", "ficha", "responder"]
    argumento: str


SISTEMA_AGENTE = ("Investigas contratos de concesión de obras públicas de Chile con herramientas que leen sus "
                  "documentos (Bases de Licitación, circulares, decretos, convenios, resoluciones). Respondes SOLO "
                  "con un objeto JSON válido que cumple el esquema pedido.")

SISTEMA_RESPUESTA = (
    "Eres un asistente experto en contratos de concesión de obras públicas de Chile. Respondes en español, solo con "
    "la información de los FRAGMENTOS entregados, citando cada afirmación con su número entre corchetes, p. ej. [2]. "
    "La respuesta debe ser COMPLETA: todo lo que la pregunta pide, con las cifras, plazos, condiciones, excepciones, "
    "quién decide y la norma aplicable que estén en los fragmentos; primero el dato principal. Si los documentos dan "
    "alternativas (tramos, con o sin obra opcional), di cuál rige en este contrato si los fragmentos lo dicen. Si una "
    "tabla transcrita con visión dice otra cifra que el texto OCR, prefiere la de la tabla. Si los fragmentos no "
    "contienen la respuesta, dilo. Responde SOLO con un objeto JSON válido que cumple el esquema pedido.")


def _plano(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


class Agente:
    def __init__(self, p: Proyecto, indice: vectores.Indice | None = None):
        self.p = p
        self.indice = indice or vectores.Indice(p)
        arbol = leer_arbol(p)
        self.nodos = {n["id"]: n for n in arbol["nodos"]}
        self.actos = arbol["actos"]
        self.articulos = {clave_numero(n["numero"]): n["id"] for n in arbol["nodos"]
                          if n["tipo"] in ("articulo", "capitulo", "parte") and n.get("numero")
                          and n["id"].startswith("BL:")}
        self.fragmentos: dict[str, list[str]] = {}            # acto → nodos de sus fragmentos en orden
        for n in arbol["nodos"]:
            v = (n.get("versiones") or [{}])[0]
            if n["tipo"] in ("acto_posterior", "aclaracion") and (v.get("acto") or {}).get("id"):
                self.fragmentos.setdefault(v["acto"]["id"], []).append(n["id"])
        for lista in self.fragmentos.values():
            lista.sort()

    # --- herramientas (sin LLM) -----------------------------------------------------------------------
    def _tablas(self, nid: str) -> str:
        return "".join(f"\nTabla transcrita con visión ({t['titulo'] or 'sin título'}):\n"
                       + vectores._tabla_md(t, vectores.MAX_FILAS_ADJUNTAS)
                       for t in self.indice.tablas_de.get(nid, []) if len(t["filas"]) <= vectores.MAX_FILAS_ADJUNTAS)

    def buscar(self, consulta: str) -> list[dict]:
        salida = []
        for _, c in self.indice.buscar(consulta, k=RESULTADOS):
            texto = self.indice.contexto(c) if c["metadatos"].get("id_tabla") else c["texto_embedding"]
            salida.append({"titulo": f"búsqueda «{consulta[:60]}»", "texto": texto[:1500], "chunks": [c]})
        return salida

    def leer_articulo(self, numeral: str) -> list[dict]:
        nid = self.articulos.get(clave_numero(numeral.strip(" .")))
        if not nid:
            return [{"titulo": f"artículo {numeral}", "texto": f"(no existe el artículo {numeral} en las Bases)",
                     "chunks": []}]
        partes, pila = [], [nid]
        while pila and sum(len(x) for x in partes) < MAX_LEIDO:
            n = self.nodos[pila.pop(0)]
            if n["tipo"] in ("articulo", "capitulo", "parte") and n["id"] != nid:
                partes.append(f"\n{n['titulo']}")
            if n["tipo"] == "inciso":
                partes.append(_plano(n["versiones"][0]["texto"]))
            if n["tipo"] in ("articulo", "capitulo", "parte"):
                partes.append(self._tablas(n["id"]))
                pila = [h for h in n["hijos"] if self.nodos[h]["tipo"] in ("inciso", "articulo")] + pila
        n = self.nodos[nid]
        texto = f"{n['ruta']}:\n" + "\n".join(x for x in partes if x)
        return [{"titulo": f"artículo {numeral}", "texto": texto[:MAX_LEIDO],
                 "chunks": [{"id": nid, "metadatos": {"articulo": {"id": nid}, "id_nodo": nid}}]}]

    def leer_acto(self, nombre: str, ya_leidos: set[str]) -> list[dict]:
        ids = actos_nombrados(nombre, self.actos)
        if not ids:
            return [{"titulo": f"acto «{nombre}»", "texto": f"(no se reconoce el acto «{nombre}»; usa la ficha)",
                     "chunks": []}]
        salida = []
        for aid in sorted(ids):
            pendientes = [n for n in self.fragmentos.get(aid, []) if n not in ya_leidos]
            partes, usados = [], []
            for nid in pendientes:
                t = _plano(self.nodos[nid]["versiones"][0]["texto"])
                if partes and sum(len(x) for x in partes) + len(t) > MAX_LEIDO:
                    break
                partes.append(t + self._tablas(nid))
                usados.append(nid)
            acto = next(a for a in self.actos if a["id"] == aid)
            resto = len(pendientes) - len(usados)
            salida.append({"titulo": f"{acto['nombre']} (fragmentos en orden)",
                           "texto": f"{acto['nombre']}:\n" + "\n".join(partes)[:MAX_LEIDO]
                           + (f"\n(quedan {resto} fragmentos: repite leer_acto para seguir)" if resto else ""),
                           "chunks": [{"id": nid, "metadatos": {"id_nodo": nid, "acto": {"id": aid}}} for nid in usados]})
        return salida

    def ficha(self) -> list[dict]:
        c = self.indice.ficha
        return [{"titulo": "ficha de actos", "texto": c["texto"] if c else "(sin ficha)", "chunks": [c] if c else []}]

    # --- ciclo ----------------------------------------------------------------------------------------
    def _evidencia(self, leido: list[dict]) -> str:
        bloques = [f"[{n}] ({x['titulo']}) {x['texto']}" for n, x in enumerate(leido, 1)]
        while sum(len(b) for b in bloques) > MAX_EVIDENCIA and len(bloques) > 1:
            k = next(i for i, b in enumerate(bloques) if not b.endswith("(recortado)"))
            bloques[k] = bloques[k][:300] + " … (recortado)"
            if all(b.endswith("(recortado)") for b in bloques):
                break
        return "\n\n".join(bloques)

    def responder(self, pregunta: str) -> tuple[vectores.Respuesta | None, list[tuple[float, dict]], list[str]]:
        dir_llm = self.p.carpeta / "cache" / "llm"
        leido: list[dict] = []
        traza: list[str] = []
        leidos_acto: set[str] = set()
        for paso in range(1, MAX_PASOS + 1):
            ultimo = paso == MAX_PASOS
            prompt = (
                f"CONCESIÓN: {self.p.nombre}\nPREGUNTA: {pregunta}\n\n"
                "HERRAMIENTAS:\n"
                "- buscar: búsqueda en todos los documentos; argumento = consulta con las palabras que usarían los "
                "documentos.\n"
                "- leer_articulo: texto vigente completo de un artículo de las Bases (con sus subartículos y tablas); "
                "argumento = numeral, p. ej. 1.8.5.2. Úsalo cuando un trozo encontrado sea parte de una lista o de un "
                "artículo más largo.\n"
                "- leer_acto: fragmentos de un acto en orden; argumento = su nombre, p. ej. «Convenio Complementario "
                "N°1» o «DS N°136»; repetirlo continúa con los siguientes.\n"
                "- ficha: lista de todos los actos (tipo, número, fecha); para contar o comparar actos.\n"
                "- responder: solo cuando lo leído respalda TODO lo que pide la pregunta (cada parte, con cifras, "
                "plazos, condiciones, quién y la norma si se pide).\n\n"
                f"LEÍDO HASTA AHORA:\n{self._evidencia(leido) or '(nada)'}\n\n"
                f"ACCIONES HECHAS: {'; '.join(traza) or 'ninguna'}\n"
                f"Paso {paso} de {MAX_PASOS}. En `falta` anota qué parte de la pregunta aún no está respaldada por lo "
                "leído (\"\" si nada). No repitas una acción ya hecha con el mismo argumento."
                + (" Es el último paso: la acción debe ser responder." if ultimo else
                   f" Antes de responder haz al menos {MIN_ACCIONES} acciones de lectura o búsqueda: verifica el dato "
                   "en otra parte (circulares, convenios, decreto de adjudicación) que pueda modificarlo o completarlo."
                   if len(traza) < MIN_ACCIONES else ""))

            def validar(r: Paso) -> str | None:
                if r.accion in ("buscar", "leer_articulo", "leer_acto") and not r.argumento.strip():
                    return "falta el argumento"
                if ultimo and r.accion != "responder":
                    return "en el último paso la acción es responder"
                if r.accion == "responder" and len(traza) < MIN_ACCIONES and not ultimo:
                    return f"antes de responder haz al menos {MIN_ACCIONES} acciones de lectura o búsqueda"
                return None

            try:
                r = llm.pedir_json(dir_llm, "agente", prompt, Paso, sistema=SISTEMA_AGENTE, validar=validar,
                                   modelos=vectores.MODELOS_RAG, razonar=False)
            except llm.SinModelos:
                break
            if r.accion == "responder":
                traza.append("responder")
                break
            traza.append(f"{r.accion}({r.argumento.strip()[:60]})")
            if r.accion == "buscar":
                leido += self.buscar(r.argumento)
            elif r.accion == "leer_articulo":
                leido += self.leer_articulo(r.argumento)
            elif r.accion == "leer_acto":
                nuevos = self.leer_acto(r.argumento, leidos_acto)
                leidos_acto |= {c["id"] for x in nuevos for c in x["chunks"]}
                leido += nuevos
            else:
                leido += self.ficha()
        if self.indice.ficha and not any(x["titulo"] == "ficha de actos" for x in leido):
            leido += self.ficha()                              # como en el RAG de siempre, para contar actos
        prompt = (f"CONCESIÓN: {self.p.nombre}\n\nFRAGMENTOS:\n\n{self._evidencia(leido)}\n\nPREGUNTA: {pregunta}\n\n"
                  "En `respuesta` escribe la respuesta completa citando [n]; en `fuentes`, los números de los "
                  "fragmentos usados.")
        try:
            resp = llm.pedir_json(dir_llm, "agente_respuesta", prompt, vectores.Respuesta, sistema=SISTEMA_RESPUESTA,
                                  modelos=vectores.MODELOS_RAG, razonar=False)
        except llm.SinModelos as e:
            print(f"  agente sin respuesta: {str(e)[:150]}")
            resp = None
        hallados = [(0.0, c | {"id": c.get("id", "?")}) for x in leido for c in x["chunks"] if c]
        return resp, hallados, traza
