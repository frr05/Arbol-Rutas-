"""Árbol de nodos versionados → `<concesión>/nodos_versionados.json` (fuente única del documento y de los chunks).

Raíces: BASES (partes → capítulos → artículos → incisos), un nodo por tema (TEMA:obras…) y FORMALIDADES.
- Inciso = párrafo vigente de un artículo (`BL:1.4.1-i2`). Sus versiones anteriores (texto de las Bases o de una
  circular previa) quedan en `versiones` con `vigente: false` y fecha_hasta = fecha del acto que lo cambió.
- Párrafos eliminados: incisos con una sola versión no vigente (`BL:1.4.1-e1`).
- Aclaraciones e instrucciones de las circulares: hijos del artículo (`-a1`, `-ins1`).
- Fragmentos de actos posteriores (decretos, convenios…): hijos del artículo, tema o FORMALIDADES según
  `revision/vinculos.csv`; su texto es el literal del acto.
- Tablas Markdown (transcritas con visión) dentro de un nodo: hijo `TABLA:<nodo>:<k>` con {encabezados, filas}.
Todo el texto es literal (corte y pegado de líneas); cada versión guarda documento, acto, páginas y líneas.
"""
from __future__ import annotations

import json
import re
import time

from . import tablas as tb
from .actos import actos_citados
from .indice import Proyecto
from .relaciones import Procesador, nombre_acto
from .vincular import TEMAS, leer_vinculos, pendientes

ESQUEMA = "mt-nodos-v1"


def ruta_arbol(p: Proyecto):
    return p.carpeta / "nodos_versionados.json"


def leer_arbol(p: Proyecto) -> dict:
    return json.loads(ruta_arbol(p).read_text(encoding="utf-8"))


def _plano(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


# siglas de los nombres de archivo de la DGC, explicadas en la ficha de actos
SIGLAS_DGC = {"PSP": "Puesta en Servicio Provisoria", "PSD": "Puesta en Servicio Definitiva",
              "DS": "Decreto Supremo", "DO": "Diario Oficial", "CA": "Circular Aclaratoria",
              "BALI": "Bases de Licitación"}


_RE_FIN_FRASE = re.compile(r"[.:;?!»”\")]\s*$")
_RE_ENUMERACION = re.compile(r"^\s*(?:[a-zA-Z]\)|\d{1,3}[.)-]\s|\d{1,2}(?:\.\d{1,2})+\s|[-•–*e]\s)")


def _parrafos_logicos(parrafos: list) -> list:
    """Une párrafos ORIGINALES (sin cambios de circulares) que el OCR cortó: el anterior no termina la frase y el
    siguiente no empieza una enumeración, o el siguiente empieza con minúscula. El texto unido sigue siendo la
    unión literal de las líneas; los párrafos modificados no se tocan (sus versiones quedan intactas)."""
    from .consolidar import Parrafo

    salida = []
    for par in parrafos:
        previo = salida[-1] if salida else None
        original = not par.acto and not par.cita and len(par.segmentos) == 1
        if previo is not None and original and not previo.acto and not previo.cita and len(previo.segmentos) == 1 \
                and previo.segmentos[0]["doc"] == par.segmentos[0]["doc"]:
            a, b = previo.texto.rstrip(), par.texto.lstrip()
            minuscula = b[:1].islower()
            if minuscula or (not _RE_FIN_FRASE.search(a) and not _RE_ENUMERACION.match(b)):
                s0, s1 = previo.segmentos[0], par.segmentos[0]
                salida[-1] = Parrafo(previo.id, [{"doc": s0["doc"], "lineas": s0["lineas"] + s1["lineas"],
                                                  "texto": s0["texto"] + "\n" + s1["texto"], "cuenta": True}])
                continue
        salida.append(par)
    return salida


def _operacion(s, par) -> str | None:
    """Tipo de cambio que dejó el párrafo como está (reemplaza_parrafo, reemplaza_texto, agrega_parrafo…)."""
    if not par.cita:
        return None
    return next((c["operacion"] for c in s.cambios if c["fragmento"] == par.cita), "modificacion")


class Constructor:
    def __init__(self, p: Proyecto):
        self.p = p
        self.pr = Procesador(p, solo_cache=True)
        self.pr.procesar()
        self.actos = {a["acto"]: a for a in self.pr.actos}
        self.orden_acto = {a["acto"]: k for k, a in enumerate(self.pr.actos)}
        self.acto_bases = next(a["acto"] for a in self.pr.actos if a["documento"] == self.pr.bases.id)
        self.nodos: dict[str, dict] = {}
        self.orden: list[str] = []
        self.tablas: list[dict] = []

    # --- utilidades ---------------------------------------------------------------------------------
    def fecha(self, acto_id: str | None) -> str | None:
        return (self.actos.get(acto_id) or {}).get("fecha") or None

    def acto_meta(self, acto_id: str | None) -> dict | None:
        a = self.actos.get(acto_id)
        return {"id": a["acto"], "tipo": a["tipo"], "numero": a["numero"] or None, "fecha": a["fecha"] or None,
                "nombre": nombre_acto(a)} if a else None

    def doc_meta(self, doc_id: str) -> dict:
        d = self.p.doc(doc_id)
        return {"id": d.id, "titulo": d.titulo, "archivo": d.archivo}

    def version(self, segmentos: list[dict], vigente: bool, acto_id: str | None, relacion: str,
                hasta: str | None = None, reemplazado_por: str | None = None, operacion: str | None = None) -> dict:
        lineas = self.pr.lineas
        docs = [s["doc"] for s in segmentos]
        paginas = sorted({lineas[s["doc"]][i]["pag"] for s in segmentos for i in s["lineas"]})
        fuentes = sorted({lineas[s["doc"]][i]["fuente"] for s in segmentos for i in s["lineas"]})
        return {"texto": "".join(s["texto"] for s in segmentos), "vigente": vigente,
                "fecha_desde": self.fecha(acto_id), "fecha_hasta": hasta, "acto": self.acto_meta(acto_id),
                "documento": self.doc_meta(docs[-1]) if docs else None, "paginas": paginas,
                "lineas": [{"doc": s["doc"], "lineas": s["lineas"]} for s in segmentos if s["lineas"]],
                "fuente": fuentes, "relacion": relacion, "operacion": operacion, "reemplazado_por": reemplazado_por,
                "orden": self.orden_acto.get(acto_id),            # orden de aplicación (sirve si falta la fecha)
                "cuenta": all(s["cuenta"] for s in segmentos)}

    def agregar(self, nodo: dict, padre: str | None):
        nodo.setdefault("hijos", [])
        nodo["padre"] = padre
        self.nodos[nodo["id"]] = nodo
        self.orden.append(nodo["id"])
        if padre:
            self.nodos[padre]["hijos"].append(nodo["id"])
        return nodo

    # --- tablas (visión → JSON) -------------------------------------------------------------------------
    def tablas_json(self):
        """Cada tabla de `cache/tablas` cuelga del nodo que tiene más líneas en sus páginas (si es un inciso, de su
        artículo). El texto literal de esa página sigue en el nodo; la tabla JSON es su lectura estructurada."""
        from collections import Counter

        lineas = self.pr.lineas
        duenos: dict[tuple[str, int], Counter] = {}
        vigentes: dict[tuple[str, int], Counter] = {}
        for nid in self.orden:
            n = self.nodos[nid]
            for v in n.get("versiones", []):
                if not v["cuenta"]:
                    continue
                for seg in v["lineas"]:
                    for i in seg["lineas"]:
                        clave = (seg["doc"], lineas[seg["doc"]][i]["pag"])
                        duenos.setdefault(clave, Counter())[nid] += 1
                        vigentes.setdefault(clave, Counter())[v["vigente"]] += 1
        por_pagina: Counter = Counter()
        for t in tb.tablas_json(self.p):
            claves = [(t["documento"], pg) for pg in t["paginas"] if (t["documento"], pg) in duenos]
            if not claves:
                continue
            clave = (t["documento"], t["paginas"][0])
            votos = sum((duenos[c] for c in claves), Counter())
            dueno = self.nodos[votos.most_common(1)[0][0]]
            if dueno["tipo"] == "inciso":
                dueno = self.nodos[dueno["padre"]]
            por_pagina[clave] += 1
            tid = f"TABLA:{t['documento']}.p{t['paginas'][0]:04d}.{por_pagina[clave]}"
            acto = next((a for a in self.pr.actos if a["documento"] == t["documento"]
                         and a["pag_inicio"] <= t["paginas"][0] <= a["pag_fin"]), None)
            nombre = t["titulo"] or f"Tabla de la pág. {t['paginas'][0]}"
            vig = sum((vigentes[c] for c in claves), Counter())
            v = {"texto": "", "vigente": vig[True] >= vig[False],
                 "fecha_desde": self.fecha(acto["acto"]) if acto else None, "fecha_hasta": None,
                 "acto": self.acto_meta(acto["acto"]) if acto else None, "documento": self.doc_meta(t["documento"]),
                 "paginas": t["paginas"], "lineas": [], "fuente": ["vlm-json"], "relacion": "tabla",
                 "reemplazado_por": None, "orden": self.orden_acto.get(acto["acto"]) if acto else None, "cuenta": False}
            self.agregar({"id": tid, "tipo": "tabla", "titulo": nombre, "ruta": f"{dueno['ruta']} › tabla «{nombre}»",
                          "tabla": {"encabezados": t["encabezados"], "filas": t["filas"], "notas": t["notas"]},
                          "versiones": [v]}, dueno["id"])
            self.tablas.append({"id": tid, "titulo": t["titulo"], "encabezados": t["encabezados"], "filas": t["filas"],
                                "notas": t["notas"], "documento": t["documento"], "paginas": t["paginas"],
                                "nodo": dueno["id"], "ruta": dueno["ruta"]})

    # --- Bases ----------------------------------------------------------------------------------------
    def bases(self):
        est = self.pr.estado
        raiz = self.agregar({"id": "BASES", "tipo": "raiz", "titulo": "Bases de Licitación", "ruta": "Bases de Licitación"},
                            None)
        numeros = {s.numero for s in est.secciones}
        rutas: dict[str, str] = {}
        for s in est.secciones:
            nid = f"BL:{s.numero}"
            padre = raiz["id"]
            if s.numero != "0":
                partes = s.numero.split(".")
                for k in range(len(partes) - 1, 0, -1):
                    if ".".join(partes[:k]) in numeros:
                        padre = f"BL:{'.'.join(partes[:k])}"
                        break
            tipo = "portada" if s.numero == "0" else {1: "parte", 2: "capitulo"}.get(s.nivel, "articulo")
            etiqueta = {"parte": "", "capitulo": "", "articulo": "artículo "}.get(tipo, "")
            ruta = f"{rutas.get(padre, 'Bases de Licitación')} › {etiqueta}{_plano(s.titulo)[:90]}"
            rutas[nid] = ruta
            doc_titulo = self.pr.bases.id if not s.agregada_por else s.agregada_por.rsplit(".", 1)[0]
            nodo = {"id": nid, "tipo": tipo, "numero": s.numero, "titulo": _plano(s.titulo), "ruta": ruta,
                    "titulo_lineas": {"doc": doc_titulo, "lineas": s.titulo_lineas,
                                      "texto": "\n".join(self.pr.lineas[doc_titulo][i]["texto"] for i in s.titulo_lineas)},
                    "agregado_por": self.acto_meta(s.agregada_por), "eliminado_por": self.acto_meta(s.eliminada_por)}
            self.agregar(nodo, padre)
            self._incisos(s, nodo)

    def _historial_de(self, s, par, usados: set) -> list[dict]:
        """Versiones anteriores de un párrafo (recursivo), de la más reciente a la más antigua."""
        salida = []
        if not par.cita:
            return salida
        for k, h in enumerate(s.historial):
            if h["fragmento"] != par.cita or k in usados:
                continue
            usados.add(k)
            hasta = self.fecha(h["acto"]) or self.fecha(par.acto)
            for viejo in h["parrafos"]:
                salida.append(self.version(viejo.segmentos, False, viejo.acto or self.acto_bases,
                                           "original" if not viejo.acto else "modificacion", hasta=hasta,
                                           reemplazado_por=h["acto"], operacion=_operacion(s, viejo)))
                salida += self._historial_de(s, viejo, usados)
        return salida

    def _incisos(self, s, nodo: dict):
        usados: set[int] = set()
        for k, par in enumerate(_parrafos_logicos(s.parrafos), 1):
            vigente = self.version(par.segmentos, True, par.acto or self.acto_bases,
                                   "original" if not par.acto else "modificacion", operacion=_operacion(s, par))
            self.agregar({"id": f"{nodo['id']}-i{k}", "tipo": "inciso", "numero": k, "titulo": f"inciso {k}",
                          "ruta": f"{nodo['ruta']} › inciso {k}",
                          "versiones": [vigente] + self._historial_de(s, par, usados)}, nodo["id"])
        # párrafos eliminados (historial sin sucesor)
        e = 0
        for k, h in enumerate(s.historial):
            if k in usados:
                continue
            for viejo in h["parrafos"]:
                e += 1
                self.agregar({"id": f"{nodo['id']}-e{e}", "tipo": "inciso_eliminado", "titulo": f"inciso eliminado {e}",
                              "ruta": f"{nodo['ruta']} › inciso eliminado {e}",
                              "versiones": [self.version(viejo.segmentos, False, viejo.acto or self.acto_bases,
                                                         "eliminado", hasta=self.fecha(h["acto"]),
                                                         reemplazado_por=h["acto"], operacion=h["operacion"])]},
                             nodo["id"])
        a = i = 0
        for n in s.notas:
            if n["tipo"] not in ("aclaracion", "instruccion"):
                continue
            if n["tipo"] == "aclaracion":
                a += 1
                nid, titulo = f"{nodo['id']}-a{a}", f"aclaración {a}"
            else:
                i += 1
                nid, titulo = f"{nodo['id']}-ins{i}", f"instrucción {i}"
            self.agregar({"id": nid, "tipo": n["tipo"], "titulo": f"{titulo} ({self.acto_meta(n['acto'])['nombre']})",
                          "ruta": f"{nodo['ruta']} › {titulo}", "fragmento": n["fragmento"],
                          "versiones": [self.version(n["segmentos"], True, n["acto"],
                                                     "aclara" if n["tipo"] == "aclaracion" else "instruccion")]},
                         nodo["id"])

    # --- actos posteriores, temas y formalidades ----------------------------------------------------------
    def posteriores(self):
        vinculos = leer_vinculos(self.p)
        items = sorted(pendientes(self.pr), key=lambda it: (self.orden_acto[it["acto"]["acto"]], it["frag"]["id"]))
        temas_usados = [t for t in TEMAS if any(vinculos.get(it["frag"]["id"]) == f"TEMA:{t}" for it in items)]
        hijos: dict[str, list] = {}
        for it in items:
            destino = vinculos.get(it["frag"]["id"], "TEMA:otros")
            hijos.setdefault(destino, []).append(it)
        # dentro de los artículos
        for destino, its in hijos.items():
            if destino.startswith("BL:") and destino in self.nodos:
                for it in its:
                    self._fragmento(it, destino, "acto_posterior")
        # temas al final
        for t in temas_usados:
            tid = f"TEMA:{t}"
            self.agregar({"id": tid, "tipo": "tema", "titulo": TEMAS[t].capitalize(),
                          "ruta": f"Otros contenidos de los actos › {t.replace('_', ' ')}"}, None)
            for it in hijos.get(tid, []):
                self._fragmento(it, tid, "acto_posterior")
        # destinos inválidos o artículos inexistentes → otros
        for destino, its in hijos.items():
            if destino.startswith("BL:") and destino not in self.nodos:
                if "TEMA:otros" not in self.nodos:
                    self.agregar({"id": "TEMA:otros", "tipo": "tema", "titulo": TEMAS["otros"].capitalize(),
                                  "ruta": "Otros contenidos de los actos › otros"}, None)
                for it in its:
                    self._fragmento(it, "TEMA:otros", "acto_posterior")
        self.agregar({"id": "FORMALIDADES", "tipo": "raiz", "titulo": "Formalidades de los actos",
                      "ruta": "Formalidades (vistos, considerandos, firmas, publicaciones ajenas)"}, None)
        for it in hijos.get("FORMALIDAD", []):
            self._fragmento(it, "FORMALIDADES", "formalidad")

    def _fragmento(self, it: dict, padre: str, tipo: str):
        f, acto = it["frag"], it["acto"]
        seg = {"doc": it["doc"], "lineas": [ln["i"] for ln in it["lineas"]], "texto": it["texto"], "cuenta": True}
        nombre = nombre_acto(acto)
        self.agregar({"id": f["id"], "tipo": tipo, "titulo": nombre, "ruta": f"{self.nodos[padre]['ruta']} › {nombre}",
                      "fragmento": f["id"], "ajeno": f["ajeno"],
                      "versiones": [self.version([seg], True, acto["acto"],
                                                 "formalidad" if tipo == "formalidad" else "acto_posterior")]}, padre)

    # --- ficha de actos -------------------------------------------------------------------------------
    def _titulos(self, doc_id: str) -> str:
        """Título del documento y sus otros nombres en la ficha del MOP (p. ej. el decreto que aprueba un convenio)."""
        d = self.p.doc(doc_id)
        otros = [re.sub(r"\s+—\s+idéntico a .*$", "", x) for x in d.alias]
        titulo = d.titulo + (f" (también publicado como: {', '.join(otros)})" if otros else "")
        siglas = [f"{s} = {v}" for s, v in SIGLAS_DGC.items() if re.search(rf"(?<![A-Za-z]){s}(?![A-Za-z])", titulo)]
        return titulo + (f" [{'; '.join(siglas)}]" if siglas else "")

    def ficha(self):
        """Resumen de los actos (tipo, número, fecha y documento) armado por el código desde `revision/actos.csv`, más
        los actos que los documentos citan sin tener PDF propio (`actos.actos_citados`),
        para preguntas que cuentan o comparan actos ("¿cuántos convenios?", "¿última modificación?"), que un RAG
        no responde mirando solo algunos fragmentos. No es texto literal de los PDF (`cuenta: false`)."""
        grupos: dict[str, list[dict]] = {}
        for a in self.pr.actos:
            grupos.setdefault(a["tipo"], []).append(a)
        lineas = [f"Ficha de actos de la concesión {self.p.nombre}, elaborada a partir de los documentos "
                  f"({len(self.pr.actos)} actos):"]
        for tipo, actos in grupos.items():
            detalle = "; ".join(f"{nombre_acto(a)} — {self._titulos(a['documento'])}" for a in actos)
            lineas.append(f"- {tipo.capitalize()}: {len(actos)} ({detalle}).")
        fechados = [a for a in self.pr.actos if a["fecha"]]
        if fechados:
            primero = min(fechados, key=lambda a: a["fecha"])
            ultimo = max(fechados, key=lambda a: a["fecha"])
            lineas.append(f"- Primer acto: {nombre_acto(primero)}. Último acto: {nombre_acto(ultimo)} — "
                          f"{self.p.doc(ultimo['documento']).titulo}.")
        citados = actos_citados(self.p, self.pr.actos)
        if citados:
            lineas.append(f"- Actos citados en los documentos, sin PDF propio en la carpeta ({len(citados)}):")
            for c in citados:
                otros = f" (el OCR también lee N°{', N°'.join(c['numeros'][1:])})" if len(c["numeros"]) > 1 else ""
                donde = ", ".join(self.p.doc(d).titulo for d in c["documentos"])
                lineas.append(f"  · {c['tipo'].capitalize()} N°{c['numero']}{otros}"
                              f"{' (' + c['fecha'] + ')' if c['fecha'] else ''}, {c['clausula'][:160]} "
                              f"[citado en: {donde}].")
        v = {"texto": "\n".join(lineas), "vigente": True, "fecha_desde": None, "fecha_hasta": None, "acto": None,
             "documento": None, "paginas": [], "lineas": [], "fuente": ["actos.csv"], "relacion": "ficha",
             "reemplazado_por": None, "orden": None, "cuenta": False}
        self.agregar({"id": "FICHA", "tipo": "ficha", "titulo": "Ficha de actos de la concesión",
                      "ruta": "Ficha de actos de la concesión", "versiones": [v]}, None)

    # --- salida ---------------------------------------------------------------------------------------
    def en_orden(self) -> list[str]:
        """Recorrido en profundidad (orden de lectura del documento)."""
        salida, pila = [], [n for n in reversed(self.orden) if self.nodos[n]["padre"] is None]
        while pila:
            n = pila.pop()
            salida.append(n)
            pila.extend(reversed(self.nodos[n]["hijos"]))
        return salida

    def construir(self) -> dict:
        self.bases()
        self.posteriores()
        self.tablas_json()
        self.ficha()
        datos = {"esquema": ESQUEMA, "generado": time.strftime("%Y-%m-%d %H:%M"),
                 "concesion": {"id": self.p.id, "nombre": self.p.nombre},
                 "actos": [self.acto_meta(a["acto"]) | {"documento": self.doc_meta(a["documento"]),
                                                        "paginas": [a["pag_inicio"], a["pag_fin"]]}
                           for a in self.pr.actos],
                 "raices": [n for n in self.orden if self.nodos[n]["padre"] is None],
                 "nodos": [self.nodos[n] for n in self.en_orden()]}
        ruta_arbol(self.p).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        tb.guardar(self.p, self.tablas)
        from collections import Counter
        c = Counter(n["tipo"] for n in datos["nodos"])
        print(f"  {self.p.id}: {len(datos['nodos'])} nodos · " + " · ".join(f"{k} {v}" for k, v in c.most_common())
              + f" · {sum(len(n.get('versiones', [])) - 1 for n in datos['nodos'] if n['tipo'] == 'inciso')} versiones anteriores")
        return datos


def construir(p: Proyecto) -> dict:
    return Constructor(p).construir()
