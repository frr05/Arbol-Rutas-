"""Etapa 6: qué cambia cada fragmento en los artículos de las Bases, aplicado en orden cronológico.

Los actos se recorren en orden de aplicación y, para cada fragmento que cita artículos de las Bases (y trae una
instrucción, o es de una circular), el LLM recibe el texto VIGENTE de esos artículos (con los cambios anteriores
ya aplicados) y devuelve operaciones: qué párrafo se reemplaza y con qué líneas, qué trozo cambia "donde dice /
debe decir", etc. Nunca escribe texto: solo números de línea, etiquetas de párrafo y trozos que se buscan
literalmente. Cada respuesta se valida contra el estado (si no calza, se prueba otro modelo) y se aplica.

`revision/relaciones_correcciones.csv` (fragmento;articulo;operacion;parrafos;texto_nuevo;donde_dice;debe_decir;
numero_nuevo) reemplaza la respuesta del LLM para ese fragmento. Con `solo_cache=True` (comando `ensamblar`) no
se llama a la red: se reutilizan las respuestas guardadas.
"""
from __future__ import annotations

import csv
import re
from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from . import llm
from .actos import leer_actos
from .bases import documento_bases, leer_estructura
from .consolidar import Estado, Seccion, buscar_en, titulo_y_cuerpo
from .fragmentos import leer_fragmentos
from .indice import Proyecto
from .numerales import clave_numero
from .texto import agrupar_parrafos, leer_lineas

CONTINUACION = 30
MAX_CHARS_PARRAFO = 1500
MAX_PARRAFOS = 45

Op = Literal["reemplaza_articulo", "reemplaza_parrafo", "reemplaza_texto", "elimina_texto", "agrega_texto",
             "agrega_parrafo", "agrega_articulo", "elimina", "aclara", "anexo"]


class Operacion(BaseModel):
    articulo: str | None = Field(None, description="numeral del artículo afectado, tal como aparece en la lista")
    operacion: Op
    parrafos: list[str] = Field(default_factory=list, description="etiquetas [P#] de los párrafos afectados")
    texto_nuevo: list[int] = Field(default_factory=list, description="[primera, última] línea del texto nuevo")
    donde_dice: str | None = None
    debe_decir: str | None = None
    numero_nuevo: str | None = Field(None, description="solo en agrega_articulo")


class Respuesta(BaseModel):
    operaciones: list[Operacion]


INSTRUCCIONES = """Analiza el FRAGMENTO de un acto administrativo que puede modificar las Bases de Licitación de una
concesión vial chilena. Indica qué operaciones ordena sobre los ARTÍCULOS CANDIDATOS (se muestra su texto vigente,
con los cambios anteriores ya aplicados, en párrafos [P1], [P2]…).

NO escribas texto nuevo: señala líneas por su número entre corchetes y párrafos por su etiqueta.
Operaciones posibles:
- reemplaza_parrafo: sustituye uno o más párrafos completos ("se sustituye el primer párrafo por el siguiente",
  "se reemplaza la definición N°3"). parrafos = los reemplazados; texto_nuevo = [primera, última] línea del texto
  nuevo (sin la instrucción ni la numeración del punto; puede seguir en la CONTINUACIÓN).
- reemplaza_articulo: sustituye el artículo completo. texto_nuevo = [primera, última] línea del texto nuevo.
- reemplaza_texto: "donde dice … debe decir …". parrafos = [el párrafo donde está]; donde_dice = el trozo EXACTO
  del texto vigente que cambia, copiado del párrafo, sin comillas ni puntos suspensivos; debe_decir = el trozo
  EXACTO del fragmento que lo reemplaza, copiado sin comillas ni puntos suspensivos.
- elimina_texto: se elimina una frase o palabra dentro de un párrafo. parrafos = [el párrafo]; donde_dice = el
  trozo EXACTO del texto vigente que se elimina.
- agrega_texto: se agrega una frase dentro de un párrafo ("se agrega al final del párrafo la frase…", "a continuación
  de la palabra X se agrega…"). parrafos = [el párrafo]; debe_decir = el trozo EXACTO del fragmento que se agrega;
  donde_dice = el trozo del texto vigente tras el cual va (vacío = al final del párrafo).
- agrega_parrafo: parrafos = [párrafo tras el cual se agrega] (vacío = al final); texto_nuevo = líneas agregadas.
- agrega_articulo: numero_nuevo = numeral del artículo nuevo; texto_nuevo = sus líneas.
- elimina: se eliminan párrafos COMPLETOS o el artículo. parrafos = los eliminados (vacío = el artículo completo).
  Si solo se elimina una frase, usa elimina_texto.
- aclara: el fragmento explica, interpreta o responde una consulta sobre el artículo sin cambiar su texto.
- anexo: no modifica ni aclara ningún artículo de las Bases (solo lo cita, o trata de otra cosa). Usa una sola
  operación anexo sin artículo.
Un fragmento puede ordenar varias operaciones (p. ej. dos definiciones reemplazadas). articulo debe ser uno de los
numerales de la lista de candidatos."""


RE_VERBO = re.compile(r"(reempl[aá]z|sustit[uú]y|modif[ií]c|agr[eé]g|incorp[oó]r|elim[ií]n|supr[ií]m|der[oó]g|"
                      r"intercal|donde\s+dice|debe\s+decir|rectif[ií]c|a[nñ]ád)\w*", re.I)
RE_BASES = re.compile(r"\bBases\b", re.I)


def cita_explicita(texto: str, numero: str, patron: re.Pattern = RE_VERBO, ventana: int = 250) -> bool:
    """True si el texto cita el numeral con `patron` (un verbo de instrucción, o "Bases") a menos de `ventana`
    caracteres. Fuera de las circulares, los decretos y convenios citan numerales de SUS propias cláusulas
    (3.3, 2.12…) que coinciden con artículos de las Bases: sin una instrucción junto a la cita no se aplica nada."""
    from .numerales import RE_CITA

    for m in RE_CITA.finditer(texto):
        if clave_numero(m[1]) != numero:
            continue
        cerca = texto[max(0, m.start() - 60):m.end() + 90]       # "…el numeral 1.6.5 de las Bases de Licitación"
        if RE_BASES.search(cerca) and patron.search(texto[max(0, m.start() - ventana):m.end() + ventana]):
            return True
    return False


def _intentar(dir_llm, prompt: str, alias: list[str]):
    """Consulta de precarga (sin validar contra el estado: eso se hace al aplicar)."""
    try:
        llm.pedir_json(dir_llm, "relaciones", prompt, Respuesta, alias=alias)
    except llm.SinModelos:
        pass


def nombre_acto(a: dict, con_fecha: bool = True) -> str:
    numero = f" N°{a['numero']}" if a.get("numero") else ""
    fecha = f" ({a['fecha']})" if a.get("fecha") and con_fecha else ""
    return f"{a['tipo'].capitalize()}{numero}{fecha}"


# texto citado tras "Dice:" en una instrucción "donde dice / debe decir" (no el "Debe decir:")
_RE_DICE = re.compile(r"(?<![Dd]ebe\s)(?<![Dd]eber[aá]\s)(?<![Qq]ue\s)\b(?:donde\s+)?dice\s*[:.]*\s*[\"“'«…. ]*(.{12,200}?)"
                      r"(?:\.\.\.|…|[\"”»]|\s+[Dd]ebe\s+[Dd]ecir|$)", re.I)
MAX_CANDIDATOS = 4


def _citas_dice(lineas: list[dict]) -> list[str]:
    texto = re.sub(r"\s+", " ", " ".join(ln["texto"] for ln in lineas))
    return [t[:90] for m in _RE_DICE.finditer(texto) if len(t := m[1].strip(" .…\"“”«»'")) >= 12]


def _contiene(sec: Seccion, trozo: str) -> bool:
    texto = "\n".join(p.texto for p in sec.parrafos)
    return len(texto) >= len(trozo) and buscar_en(trozo, texto) is not None


def _candidatos(estado: Estado, frag: dict, lineas: list[dict]) -> list[Seccion]:
    secs = [s for s in (estado.seccion(r) for r in frag["refs"]) if s]
    if not secs and frag["verbo"]:
        # numeral ilegible ("En 1.114,32 Valoración…"): se busca por título en las primeras líneas
        cabeza = " ".join(ln["texto"] for ln in lineas[:3]).lower()
        puntaje = [(fuzz.partial_ratio(re.sub(r"^[\d.\s]+", "", s.titulo).lower(), cabeza), s)
                   for s in estado.secciones if len(s.titulo) > 12 and s.numero != "0"]
        secs = [s for sc, s in sorted(puntaje, key=lambda x: -x[0])[:3] if sc >= 85]
    # texto citado tras "Dice:" que no está en los artículos citados (numeral equivocado o que la estructura no
    # encontró): se agrega la sección donde sí está. Si todas las citas están, el prompt no cambia.
    faltan = [t for t in _citas_dice(lineas) if not any(_contiene(s, t) for s in secs)]
    for t in faltan:                                     # cada cita "Dice:" ausente aporta la sección donde está
        extra = [s for s in estado.secciones if s.numero != "0" and s not in secs and _contiene(s, t)]
        secs += extra[:1]
        if len(secs) >= MAX_CANDIDATOS:
            break
    vistos, salida = set(), []
    for s in secs:
        if s.id not in vistos:
            vistos.add(s.id)
            salida.append(s)
    return salida[:MAX_CANDIDATOS]


def _texto_seccion(sec: Seccion) -> str:
    filas = [f"### {sec.numero} — {sec.titulo}"]
    for k, p in enumerate(sec.parrafos[:MAX_PARRAFOS], 1):
        t = re.sub(r"\s*\n\s*", " ", p.texto)
        filas.append(f"[P{k}] {t[:MAX_CHARS_PARRAFO]}{'…' if len(t) > MAX_CHARS_PARRAFO else ''}")
    if len(sec.parrafos) > MAX_PARRAFOS:
        filas.append(f"(… {len(sec.parrafos) - MAX_PARRAFOS} párrafos más)")
    if not sec.parrafos:
        filas.append("(sin párrafos)")
    return "\n".join(filas)


def _correcciones(p: Proyecto) -> dict[str, list[Operacion]]:
    ruta = p.dir_revision / "relaciones_correcciones.csv"
    if not ruta.exists():
        return {}
    salida: dict[str, list[Operacion]] = {}
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        for f in csv.DictReader(fh, delimiter=";"):
            if not (f.get("fragmento") or "").strip():
                continue
            nuevo = [int(x) for x in re.findall(r"\d+", f.get("texto_nuevo") or "")][:2]
            op = Operacion(articulo=(f.get("articulo") or None), operacion=f["operacion"].strip(),
                           parrafos=[x.strip() for x in (f.get("parrafos") or "").split(",") if x.strip()],
                           texto_nuevo=nuevo, donde_dice=f.get("donde_dice") or None,
                           debe_decir=f.get("debe_decir") or None, numero_nuevo=f.get("numero_nuevo") or None)
            salida.setdefault(f["fragmento"].strip(), []).append(op)
    return salida


class Procesador:
    def __init__(self, p: Proyecto, usar_llm: bool = True, solo_cache: bool = False):
        self.p, self.usar_llm, self.solo_cache = p, usar_llm, solo_cache
        self.estructura = leer_estructura(p)
        self.bases = documento_bases(p)
        self.lineas = {d.id: leer_lineas(p, d.id) for d in p.documentos}
        self.actos = leer_actos(p)
        self.estado = Estado(p.id, self.bases.id, self.estructura, self.lineas[self.bases.id])
        self.fragmentos = {d.id: leer_fragmentos(p, d.id) for d in p.documentos if d.id != self.bases.id}
        self.correcciones = _correcciones(p)
        self.filas: list[dict] = []
        self.ajenos: list[dict] = []
        self.sin_respuesta: list[str] = []

    # --- utilidades ---------------------------------------------------------------------------------
    def _libres(self, doc: str, ids: list[int]) -> list[dict]:
        return [self.lineas[doc][i] for i in ids
                if (doc, i) not in self.estado.usadas and not self.lineas[doc][i]["marginal"]]

    def _continuacion(self, doc: str, ultimo: int) -> list[dict]:
        salida = []
        for ln in self.lineas[doc][ultimo + 1:]:
            if len(salida) >= CONTINUACION:
                break
            if not ln["marginal"] and (doc, ln["i"]) not in self.estado.usadas:
                salida.append(ln)
        return salida

    # --- validación y aplicación -------------------------------------------------------------------
    def _validar(self, ops: list[Operacion], candidatos: list[Seccion], permitidas: dict[int, dict],
                 texto_frag: str) -> str | None:
        """Rechaza la respuesta (y se prueba otro modelo) solo si ninguna operación es válida; las inválidas
        de una respuesta aceptada quedan como pendientes al aplicar."""
        if not ops:
            return "lista de operaciones vacía (usa una operación anexo)"
        errores = [self._validar_op(op, candidatos, permitidas, texto_frag) for op in ops]
        return None if any(e is None for e in errores) else errores[0]

    def _validar_op(self, op: Operacion, candidatos: list[Seccion], permitidas: dict[int, dict],
                    texto_frag: str) -> str | None:
        claves = {s.numero for s in candidatos}
        for op in [op]:
            if op.operacion == "anexo":
                continue
            if op.operacion == "agrega_articulo":
                if not op.numero_nuevo or not re.search(r"\d", op.numero_nuevo):
                    return "agrega_articulo sin numero_nuevo"
            elif not op.articulo or clave_numero(op.articulo) not in claves:
                return f"el artículo {op.articulo} no está entre los candidatos {sorted(claves)}"
            sec = self.estado.seccion(op.articulo) if op.articulo else None
            if sec and any(self.estado.parrafo(sec, x) is None for x in op.parrafos):
                return f"párrafo inexistente en {sec.numero}: {op.parrafos}"
            if op.operacion in ("reemplaza_parrafo", "reemplaza_articulo", "agrega_parrafo", "agrega_articulo"):
                if len(op.texto_nuevo) != 2 or op.texto_nuevo[0] > op.texto_nuevo[1]:
                    return f"{op.operacion} necesita texto_nuevo = [primera, última]"
                if not all(i in permitidas for i in op.texto_nuevo):
                    return f"texto_nuevo {op.texto_nuevo} fuera de las líneas del fragmento"
            if op.operacion == "reemplaza_parrafo" and not op.parrafos:
                return "reemplaza_parrafo sin párrafos"
            if op.operacion in ("reemplaza_texto", "elimina_texto", "agrega_texto"):
                necesita_donde = op.operacion != "agrega_texto"
                necesita_debe = op.operacion != "elimina_texto"
                if (necesita_donde and not op.donde_dice) or (necesita_debe and not op.debe_decir):
                    return f"{op.operacion} necesita " + " y ".join(
                        x for x, n in (("donde_dice", necesita_donde), ("debe_decir", necesita_debe)) if n)
                if op.operacion == "agrega_texto" and not op.parrafos:
                    return "agrega_texto necesita el párrafo"
                if necesita_debe and buscar_en(op.debe_decir, texto_frag) is None:
                    return f"debe_decir «{op.debe_decir[:50]}» no está en el fragmento"
                pars = [sec.parrafos[self.estado.parrafo(sec, x)] for x in op.parrafos] if op.parrafos else sec.parrafos
                if op.donde_dice and not any(buscar_en(op.donde_dice, par.texto) for par in pars):
                    # el LLM eligió mal el artículo: si el texto citado está en otro candidato (uno solo), va ahí
                    otros = [c for c in candidatos if c is not sec and not op.parrafos and _contiene(c, op.donde_dice)]
                    if len(otros) != 1:
                        return f"donde_dice «{op.donde_dice[:50]}» no está en el texto vigente de {sec.numero}"
                    op.articulo = otros[0].numero
        return None

    def _aplicar(self, op: Operacion, frag: dict, acto: dict, permitidas: dict[int, dict], texto_frag: str,
                 doc: str) -> tuple[Seccion | None, str | None]:
        est = self.estado
        sec = est.seccion(op.articulo) if op.articulo else None
        ks = [est.parrafo(sec, x) for x in op.parrafos] if sec else []
        nuevas = []
        if op.texto_nuevo:
            a, b = op.texto_nuevo
            nuevas = [ln for i, ln in sorted(permitidas.items()) if a <= i <= b and (doc, i) not in est.usadas]
        grupos = agrupar_parrafos(nuevas, unir_sin_punto=True)
        o, f = op.operacion, frag["id"]
        if o in ("reemplaza_parrafo", "reemplaza_articulo", "agrega_parrafo") and not nuevas:
            return sec, "las líneas del texto nuevo ya fueron usadas por otra operación"
        if o == "reemplaza_parrafo":
            est.reemplazar(sec, ks, est._nuevos(doc, grupos, acto["acto"], f), acto["acto"], f, o)
        elif o == "reemplaza_articulo":
            est.reemplazar(sec, list(range(len(sec.parrafos))), est._nuevos(doc, grupos, acto["acto"], f),
                           acto["acto"], f, o)
        elif o == "agrega_parrafo":
            pos = (ks[0] + 1) if ks else len(sec.parrafos)
            est.reemplazar(sec, [], est._nuevos(doc, grupos, acto["acto"], f), acto["acto"], f, o, posicion=pos)
        elif o == "elimina":
            todos = not ks
            est.reemplazar(sec, ks or list(range(len(sec.parrafos))), [], acto["acto"], f, o)
            if todos:
                sec.eliminada_por = acto["acto"]
        elif o in ("reemplaza_texto", "elimina_texto", "agrega_texto"):
            nuevo_texto = ""
            if o != "elimina_texto":
                ubic = buscar_en(op.debe_decir, texto_frag)
                if ubic is None:
                    return sec, "debe_decir no encontrado"
                nuevo_texto = texto_frag[ubic[0]:ubic[1]].strip(" “”\"'«».…")
            candidatos_k = ks or list(range(len(sec.parrafos)))
            if o == "agrega_texto":
                if error := est.agregar_texto(sec, candidatos_k[0], op.donde_dice, doc, nuevo_texto, acto["acto"], f):
                    return sec, error
            else:
                k = next((k for k in candidatos_k if buscar_en(op.donde_dice, sec.parrafos[k].texto)), None)
                if k is None:
                    return sec, "donde_dice no encontrado en el texto vigente"
                if error := est.reemplazar_texto(sec, k, op.donde_dice, doc, nuevo_texto, [], acto["acto"], f, o):
                    return sec, error
        elif o == "agrega_articulo":
            if not nuevas:
                return None, "las líneas del texto nuevo ya fueron usadas"
            titulo, titulo_lineas, cuerpo = titulo_y_cuerpo(grupos)
            if titulo_lineas:
                est.usar(doc, titulo_lineas)
            sec = est.agregar_seccion(op.numero_nuevo, titulo or f"{clave_numero(op.numero_nuevo)} [artículo agregado]",
                                      est._nuevos(doc, cuerpo, acto["acto"], f), acto["acto"], f, titulo_lineas)
        return sec, None

    # --- recorrido ------------------------------------------------------------------------------------
    def _preparar(self, acto: dict, frag: dict) -> dict | None:
        """Lo que necesita el LLM para un fragmento, según el estado vigente (None = no se consulta)."""
        doc = acto["documento"]
        lineas = self._libres(doc, frag["lineas"])
        if not lineas or frag["ajeno"]:
            return None
        candidatos = _candidatos(self.estado, frag, lineas)
        if not candidatos or not (frag["verbo"] or "circular" in acto["tipo"]):
            return None
        if "circular" not in acto["tipo"]:
            # En TERB los actos posteriores a la adjudicación (decretos, convenios, resoluciones) modifican el
            # CONTRATO (obras, plazos, pagos) y citan las Bases como referencia; al LLM le costaba distinguir
            # esas citas de una modificación del texto. Solo las circulares reescriben las Bases; el resto va
            # completo a los anexos y el ensamblador enlaza cada artículo con los actos que lo citan.
            return None
        continuacion = self._continuacion(doc, frag["lineas"][-1])
        # El prompt nombra el acto sin fecha: corregir una fecha en revision/actos.csv no invalida la caché.
        cuerpo = (f" · {acto['titulo']}\n\n"
                  f"ARTÍCULOS CANDIDATOS (texto vigente):\n\n"
                  + "\n\n".join(_texto_seccion(s) for s in candidatos)
                  + "\n\nFRAGMENTO:\n" + "\n".join(f"[{ln['i']}] {ln['texto']}" for ln in lineas)
                  + ("\n\nCONTINUACIÓN (líneas que siguen al fragmento):\n"
                     + "\n".join(f"[{ln['i']}] {ln['texto']}" for ln in continuacion) if continuacion else ""))
        prompt = f"{INSTRUCCIONES}\n\nACTO: {nombre_acto(acto, con_fecha=False)}{cuerpo}"
        alias = [f"{INSTRUCCIONES}\n\nACTO: {nombre_acto(acto)}{cuerpo}"]      # caché anterior (con fecha)
        return {"lineas": lineas, "candidatos": candidatos, "permitidas": {ln["i"]: ln for ln in lineas + continuacion},
                "texto_frag": "\n".join(ln["texto"] for ln in lineas + continuacion), "prompt": prompt,
                "alias": alias}

    def procesar(self, solo_docs: set[str] | None = None, hilos: int = 4):
        """Recorre los fragmentos en orden cronológico. Mientras aplica uno, consulta en paralelo los `hilos`
        siguientes con el estado de ese momento (precarga): si al llegar su turno el prompt no cambió, la
        respuesta ya está en caché; si cambió (otro fragmento modificó el mismo artículo), se consulta de nuevo."""
        from concurrent.futures import ThreadPoolExecutor

        dir_llm = self.p.carpeta / "cache" / "llm"
        tareas = [(a, f) for a in self.actos if a["documento"] != self.bases.id
                  and not (solo_docs and a["documento"] not in solo_docs)
                  for f in self.fragmentos[a["documento"]] if f["acto"] == a["acto"]]
        precarga: dict[str, object] = {}
        pool = ThreadPoolExecutor(hilos) if (self.usar_llm and not self.solo_cache and hilos > 1) else None

        def precargar(desde: int):
            for acto, frag in tareas[desde:desde + 3 * hilos]:
                if frag["id"] in precarga or frag["id"] in self.correcciones:
                    continue
                prep = self._preparar(acto, frag)
                if prep:
                    precarga[frag["id"]] = (prep["prompt"], pool.submit(
                        lambda pr=prep["prompt"], al=prep["alias"]: _intentar(dir_llm, pr, al)))
                if sum(1 for _, fu in precarga.values() if not fu.done()) >= hilos:
                    break

        try:
            for k, (acto, frag) in enumerate(tareas):
                doc = acto["documento"]
                if frag["ajeno"] and self._libres(doc, frag["lineas"]):
                    self.ajenos.append(frag)
                    continue
                if pool:
                    precargar(k)
                prep = self._preparar(acto, frag)
                if not prep:
                    continue
                origen = "manual" if frag["id"] in self.correcciones else "llm"
                try:
                    if origen == "manual":
                        ops = self.correcciones[frag["id"]]
                    else:
                        if not self.usar_llm:
                            continue
                        previo = precarga.pop(frag["id"], None)
                        if previo and previo[0] == prep["prompt"]:
                            previo[1].result()          # espera la precarga (deja la respuesta en caché)
                        ops = llm.pedir_json(dir_llm, "relaciones", prep["prompt"], Respuesta,
                                             validar=lambda r: self._validar(r.operaciones, prep["candidatos"],
                                                                             prep["permitidas"], prep["texto_frag"]),
                                             solo_cache=self.solo_cache, alias=prep["alias"]).operaciones
                except llm.SinModelos as e:
                    self.sin_respuesta.append(frag["id"])
                    if not self.solo_cache:
                        print(f"    {frag['id']}: sin respuesta válida ({str(e)[:140]})")
                    continue
                self._registrar(frag, acto, doc, ops, prep["lineas"], prep["permitidas"], prep["texto_frag"], origen)
        finally:
            if pool:
                pool.shutdown(wait=False, cancel_futures=True)

    def _registrar(self, frag, acto, doc, ops, lineas, permitidas, texto_frag, origen):
        if not self.solo_cache:
            print(f"    {frag['id']} → " + ", ".join(f"{op.operacion} {op.articulo or ''}".strip() for op in ops),
                  flush=True)
        modificadas, aclaradas = [], []
        candidatos = [s for s in (self.estado.seccion(op.articulo) for op in ops if op.articulo) if s]
        circular = "circular" in acto["tipo"]
        for op in ops:
            if not circular and op.operacion != "anexo" and origen == "llm":
                numero = clave_numero(op.numero_nuevo if op.operacion == "agrega_articulo" and op.numero_nuevo
                                      else (op.articulo or "")).split(" ")[0]
                if op.operacion == "aclara":
                    explicita = cita_explicita(texto_frag, numero, RE_BASES)
                elif op.operacion == "agrega_articulo":
                    explicita = bool(RE_VERBO.search(texto_frag) and RE_BASES.search(texto_frag))
                else:
                    explicita = cita_explicita(texto_frag, numero)
                if not explicita:
                    self._fila(frag, acto, op, "descartada: sin instrucción explícita sobre ese artículo de las Bases",
                               origen)
                    continue
            if origen == "llm" and op.operacion != "anexo" and \
                    (error := self._validar_op(op, candidatos, permitidas, texto_frag)):
                self.estado.pendientes.append({"fragmento": frag["id"], "acto": acto["acto"], "articulo": op.articulo,
                                               "operacion": op.operacion, "motivo": error})
                self._fila(frag, acto, op, f"pendiente: {error}", origen)
                continue
            if op.operacion == "anexo":
                self._fila(frag, acto, op, "anexo", origen)
                continue
            if op.operacion == "aclara":
                sec = self.estado.seccion(op.articulo)
                if sec:
                    aclaradas.append(sec)
                self._fila(frag, acto, op, "nota" if sec else "pendiente: artículo no existe", origen)
                continue
            sec, error = self._aplicar(op, frag, acto, permitidas, texto_frag, doc)
            if error:
                self.estado.pendientes.append({"fragmento": frag["id"], "acto": acto["acto"], "articulo": op.articulo,
                                               "operacion": op.operacion, "motivo": error})
            else:
                modificadas.append(sec)
            self._fila(frag, acto, op, f"pendiente: {error}" if error else "aplicada", origen)
        # el resto de las líneas del fragmento (la instrucción, o el texto completo de una aclaración) queda como
        # nota en el primer artículo afectado; si no afectó a ninguno, queda para los anexos
        resto = self._libres(doc, frag["lineas"])
        destino = modificadas[0] if modificadas else (aclaradas[0] if aclaradas else None)
        if destino and resto:
            self.estado.anotar(destino, acto["acto"], frag["id"], "instruccion" if modificadas else "aclaracion",
                               doc, resto)
        for sec in (modificadas[1:] + aclaradas[(0 if modificadas else 1):]):
            sec.notas.append({"acto": acto["acto"], "fragmento": frag["id"],
                              "tipo": "referencia", "segmentos": [], "destino": destino.id if destino else None})

    def _fila(self, frag, acto, op: Operacion, estado: str, origen: str):
        self.filas.append({"fragmento": frag["id"], "acto": acto["acto"], "acto_nombre": nombre_acto(acto),
                           "pags": ",".join(map(str, frag["pags"])), "articulo": op.articulo or "",
                           "operacion": op.operacion, "parrafos": ",".join(op.parrafos),
                           "texto_nuevo": "-".join(map(str, op.texto_nuevo)), "donde_dice": op.donde_dice or "",
                           "debe_decir": op.debe_decir or "", "numero_nuevo": op.numero_nuevo or "",
                           "estado": estado, "origen": origen})

    def exportar(self):
        p = self.p
        p.dir_revision.mkdir(parents=True, exist_ok=True)
        columnas = ["fragmento", "acto", "acto_nombre", "pags", "articulo", "operacion", "parrafos", "texto_nuevo",
                    "donde_dice", "debe_decir", "numero_nuevo", "estado", "origen"]
        with (p.dir_revision / "relaciones.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=columnas, delimiter=";", lineterminator="\n")
            w.writeheader()
            w.writerows(self.filas)
        with (p.dir_revision / "pendientes.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["fragmento", "acto", "articulo", "operacion", "motivo"], delimiter=";", lineterminator="\n")
            w.writeheader()
            w.writerows(self.estado.pendientes)
            for f in self.sin_respuesta:
                w.writerow({"fragmento": f, "motivo": "sin respuesta del LLM (queda en anexos)"})
