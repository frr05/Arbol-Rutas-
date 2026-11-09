"""Estado del documento único y aplicación de operaciones (sin LLM).

Cada sección de las Bases tiene párrafos; cada párrafo es una lista de *segmentos* literales
{"doc", "lineas": [i…], "texto", "cuenta"}. `cuenta` indica que el segmento contiene sus líneas completas (es la
aparición "oficial" de esas líneas, la que verifica `verificar`). Un párrafo armado por "donde dice / debe decir"
pega trozos (cuenta=False): sus líneas completas quedan en el historial (texto anterior) y en la instrucción.

Las operaciones se aplican en orden cronológico. Una operación que no calza (párrafo inexistente, texto que no se
encuentra, líneas ya usadas) no se aplica y queda como pendiente.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .numerales import RE_NUMERAL, clave_numero, orden_numero

UMBRAL_TEXTO = 85


@dataclass
class Parrafo:
    id: str
    segmentos: list[dict]
    acto: str | None = None          # acto que lo introdujo (None = texto original de las Bases)
    cita: str | None = None          # id del fragmento que lo introdujo

    @property
    def texto(self) -> str:
        return "".join(s["texto"] for s in self.segmentos)


@dataclass
class Seccion:
    id: str
    numero: str
    titulo: str
    nivel: int
    titulo_lineas: list[int]
    parrafos: list[Parrafo]
    origen: str = "bases"
    agregada_por: str | None = None
    eliminada_por: str | None = None
    historial: list[dict] = field(default_factory=list)   # {"acto", "fragmento", "operacion", "parrafos": [Parrafo]}
    notas: list[dict] = field(default_factory=list)       # {"acto", "fragmento", "tipo", "segmentos"}
    cambios: list[dict] = field(default_factory=list)     # resumen de operaciones aplicadas


class Estado:
    def __init__(self, proyecto_id: str, doc_bases: str, estructura: dict, lineas_bases: list[dict]):
        self.proyecto_id = proyecto_id
        self.doc_bases = doc_bases
        self.usadas: set[tuple[str, int]] = set()
        self.secciones: list[Seccion] = []
        for s in estructura["secciones"]:
            parrafos = [Parrafo(f"{s['id']}.{p['id']}", [_segmento(doc_bases, p["lineas"], lineas_bases)])
                        for p in s["parrafos"]]
            self.secciones.append(Seccion(s["id"], s["numero"], s["titulo"], s["nivel"], s["titulo_lineas"],
                                          parrafos, origen=s["origen"]))
            for p in s["parrafos"]:
                self.usadas.update((doc_bases, i) for i in p["lineas"])
            self.usadas.update((doc_bases, i) for i in s["titulo_lineas"])
        self.pendientes: list[dict] = []

    # --- consultas ---------------------------------------------------------------------------------
    def seccion(self, numero: str) -> Seccion | None:
        clave = clave_numero(numero)
        return next((s for s in self.secciones if s.numero == clave and not s.eliminada_por), None) \
            or next((s for s in self.secciones if s.numero == clave), None)

    def parrafo(self, sec: Seccion, pid: str) -> int | None:
        """Índice del párrafo por su etiqueta posicional (P1…) o su id estable."""
        if m := re.fullmatch(r"P(\d+)", pid.strip()):
            k = int(m[1]) - 1
            return k if 0 <= k < len(sec.parrafos) else None
        return next((k for k, p in enumerate(sec.parrafos) if p.id == pid), None)

    # --- operaciones ---------------------------------------------------------------------------------
    def libres(self, doc: str, lineas: list[int]) -> bool:
        return not any((doc, i) in self.usadas for i in lineas)

    def usar(self, doc: str, lineas: list[int]):
        self.usadas.update((doc, i) for i in lineas)

    def _nuevos(self, doc: str, grupos: list[list[dict]], acto: str, frag: str) -> list[Parrafo]:
        salida = []
        for k, g in enumerate(grupos, 1):
            ids = [ln["i"] for ln in g]
            self.usar(doc, ids)
            salida.append(Parrafo(f"{frag}.{k}", [{"doc": doc, "lineas": ids, "texto": "\n".join(ln["texto"] for ln in g),
                                                    "cuenta": True}], acto=acto, cita=frag))
        return salida

    def reemplazar(self, sec: Seccion, indices: list[int], nuevos: list[Parrafo], acto: str, frag: str, op: str,
                   posicion: int | None = None):
        indices = sorted(indices)
        viejos = [sec.parrafos[k] for k in indices]
        if viejos:
            sec.historial.append({"acto": acto, "fragmento": frag, "operacion": op, "parrafos": viejos})
        if posicion is None:
            posicion = indices[0] if indices else len(sec.parrafos)
        for k in reversed(indices):
            del sec.parrafos[k]
        sec.parrafos[posicion:posicion] = nuevos
        sec.cambios.append({"acto": acto, "fragmento": frag, "operacion": op})

    def reemplazar_texto(self, sec: Seccion, k: int, donde: str, nuevo_doc: str, nuevo_texto: str,
                         nuevo_lineas: list[int], acto: str, frag: str, op: str = "reemplaza_texto") -> str | None:
        """Pega [antes][nuevo][después] en el párrafo k (nuevo vacío = elimina el trozo). Devuelve un error o None."""
        par = sec.parrafos[k]
        texto = par.texto
        al = fuzz.partial_ratio_alignment(_plano(donde), _plano(texto))
        if al is None or al.score < UMBRAL_TEXTO:
            return f"no se encontró «{donde[:60]}» en el párrafo (similitud {al.score if al else 0:.0f})"
        a, b = al.dest_start, al.dest_end
        a, b = _a_palabra(texto, a, b)
        segmentos = (_trozos(par, 0, a) + [{"doc": nuevo_doc, "lineas": nuevo_lineas, "texto": nuevo_texto, "cuenta": False}]
                     + _trozos(par, b, len(texto)))
        nuevo = Parrafo(f"{par.id}~{frag}", [s for s in segmentos if s["texto"]], acto=acto, cita=frag)
        self.reemplazar(sec, [k], [nuevo], acto, frag, op)
        return None

    def agregar_texto(self, sec: Seccion, k: int, despues_de: str | None, nuevo_doc: str, nuevo_texto: str,
                      acto: str, frag: str) -> str | None:
        """Inserta un trozo literal tras `despues_de` (o al final) del párrafo k."""
        par = sec.parrafos[k]
        texto = par.texto
        if despues_de:
            ubic = buscar_en(despues_de, texto)
            if ubic is None:
                return f"no se encontró «{despues_de[:60]}» en el párrafo"
            corte = ubic[1]
        else:
            corte = len(texto.rstrip())
        despues = _trozos(par, corte, len(texto))
        if despues:
            despues[0]["texto"] = despues[0]["texto"].lstrip()
        segmentos = _trozos(par, 0, corte) + [{"doc": nuevo_doc, "lineas": [], "texto": " " + nuevo_texto + " ",
                                               "cuenta": False}] + despues
        nuevo = Parrafo(f"{par.id}~{frag}", [s for s in segmentos if s["texto"].strip()], acto=acto, cita=frag)
        self.reemplazar(sec, [k], [nuevo], acto, frag, "agrega_texto")
        return None

    def agregar_seccion(self, numero: str, titulo: str, nuevos: list[Parrafo], acto: str, frag: str,
                        titulo_lineas: list[int]) -> Seccion:
        clave = clave_numero(numero)
        sid = f"{self.proyecto_id}.S{clave}"
        while any(s.id == sid for s in self.secciones):
            sid += "+"
        nueva = Seccion(sid, clave, titulo, clave.count(".") + 1, titulo_lineas, nuevos, origen="agregada",
                        agregada_por=acto)
        nueva.cambios.append({"acto": acto, "fragmento": frag, "operacion": "agrega_articulo"})
        orden = orden_numero(clave)
        pos = len(self.secciones)
        for k, s in enumerate(self.secciones):
            if s.numero[0].isdigit() and orden_numero(s.numero) > orden and s.numero != "0":
                pos = k
                break
        self.secciones.insert(pos, nueva)
        return nueva

    def anotar(self, sec: Seccion, acto: str, frag: str, tipo: str, doc: str, lineas: list[dict]):
        ids = [ln["i"] for ln in lineas]
        self.usar(doc, ids)
        sec.notas.append({"acto": acto, "fragmento": frag, "tipo": tipo,
                          "segmentos": [{"doc": doc, "lineas": ids, "texto": "\n".join(ln["texto"] for ln in lineas),
                                         "cuenta": True}]})


def _segmento(doc: str, ids: list[int], lineas: list[dict]) -> dict:
    return {"doc": doc, "lineas": ids, "texto": "\n".join(lineas[i]["texto"] for i in ids), "cuenta": True}


def _trozos(par: Parrafo, a: int, b: int) -> list[dict]:
    """Los segmentos del párrafo recortados al tramo [a, b) del texto: cada trozo conserva su documento y sus
    líneas (un párrafo ya modificado mezcla líneas de las Bases y de una circular)."""
    salida, pos = [], 0
    for s in par.segmentos:
        ini, fin = max(a, pos), min(b, pos + len(s["texto"]))
        if ini < fin:
            salida.append({"doc": s["doc"], "lineas": s["lineas"], "texto": s["texto"][ini - pos:fin - pos],
                           "cuenta": False})
        pos += len(s["texto"])
    return salida


def _plano(t: str) -> str:
    return re.sub(r"\s", " ", t)


def _a_palabra(texto: str, a: int, b: int) -> tuple[int, int]:
    """Extiende [a, b) a palabras completas (la alineación difusa puede cortar al medio de una palabra)."""
    while a > 0 and texto[a - 1].isalnum():
        a -= 1
    while b < len(texto) and texto[b].isalnum():
        b += 1
    return a, b


def buscar_en(texto_corto: str, texto_largo: str) -> tuple[int, int] | None:
    """Posición literal de `texto_corto` dentro de `texto_largo` (búsqueda difusa)."""
    al = fuzz.partial_ratio_alignment(_plano(texto_corto), _plano(texto_largo))
    if al is None or al.score < UMBRAL_TEXTO:
        return None
    return _a_palabra(texto_largo, al.dest_start, al.dest_end)


def titulo_y_cuerpo(grupos: list[list[dict]]) -> tuple[str | None, list[int], list[list[dict]]]:
    """Si el texto nuevo de un artículo agregado empieza con "1.6.5.3 Título", separa el título."""
    if grupos and RE_NUMERAL.match(grupos[0][0]["texto"]):
        primera = grupos[0][0]
        resto = [g for g in ([grupos[0][1:]] + grupos[1:]) if g]
        return primera["texto"], [primera["i"]], resto
    return None, [], grupos
