"""Etapa de lectura: escribe `DOCUMENTO_UNICO_<ID>.md` recorriendo el árbol `nodos_versionados.json` (sin LLM).

Un solo documento ordenado, para leer de corrido y para cortar en chunks:
  1. Ficha de la concesión y actos en orden de aplicación.
  2. Bases de Licitación con sus títulos en orden (parte ##, capítulo ###, artículo ####…). Cada artículo tiene
     tres bloques fijos:
       a) texto vigente, un párrafo por inciso; un párrafo que cambió lleva la llamada [c1], [c2]…;
       b) "Cambios en este artículo": qué acto cambió qué párrafo y cómo (texto anterior, o «antes» → «después»
          en una corrección "donde dice / debe decir"), con la instrucción literal de la circular;
       c) "Otros actos sobre este artículo": aclaraciones y fragmentos de decretos, convenios y resoluciones, como
          citas con rótulo para no confundirlos con el texto de las Bases.
  3. Otros contenidos de los actos, por tema (mismo formato de cita).
  4. Formalidades (vistos, firmas, publicaciones ajenas), plegadas.
Cada párrafo termina con una marca oculta `<!-- id · estado · acto · pág. -->` para cortar el Markdown por
párrafos; los chunks oficiales se generan desde el árbol (`chunks.py`).
El texto mostrado pasa por `limpieza.Limpiador` (reglas deterministas, sin LLM: "N*19.300" → "N°19.300", guiones de
corte, "|" de bordes de tabla, palabras corregidas con el diccionario); el árbol y los chunks siguen literales y la
marca de un párrafo limpiado termina en "· limpio".
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .arbol import leer_arbol
from .indice import Proyecto
from .limpieza import ACTIVA, Limpiador
from .texto import agrupar_parrafos, leer_lineas

# qué hizo cada operación sobre el párrafo k (texto para el bloque "Cambios en este artículo")
VERBOS = {"reemplaza_parrafo": "reemplazó el párrafo {k}", "reemplaza_articulo": "reemplazó el texto del artículo "
          "(párrafo {k})", "reemplaza_texto": "corrigió el párrafo {k}", "elimina_texto": "suprimió un trozo del "
          "párrafo {k}", "agrega_texto": "agregó texto al párrafo {k}", "agrega_parrafo": "agregó el párrafo {k}",
          "agrega_articulo": "agregó el párrafo {k}"}
MAX_DIFERENCIAS = 3
_RE_SOLO_NUMERO = re.compile(r"^\s*[-–]?\s*\d{1,3}\s?\)\s*$")          # "20)" que el OCR leyó sin su texto
_RE_EMPIEZA_NUMERO = re.compile(r"^\s*[-–]?\s*\d{1,3}\s?[.)]")


def _enfasis_posible(t: str, c: str) -> bool:
    """¿Puede `c` (* o _) abrir y cerrar un énfasis Markdown? Hacen falta dos signos pegados a texto; un "_" dentro
    de una palabra no cuenta."""
    n = 0
    for k, x in enumerate(t):
        if x != c:
            continue
        antes, despues = t[k - 1:k], t[k + 1:k + 2]
        pegado = (antes and not antes.isspace()) or (despues and not despues.isspace())
        dentro = c == "_" and antes.isalnum() and despues.isalnum()
        n += bool(pegado and not dentro)
    return n >= 2


def md_escapar(t: str) -> str:
    # "$" abre fórmulas en los visores con KaTeX/MathJax: montos ("$50 y de $70") y fórmulas que el modelo de
    # visión transcribe en LaTeX ("$$YP_t = \sum…$$") se muestran tal cual
    t = t.replace("<", "&lt;").replace("$", "\\$")
    for c in "*_":
        if _enfasis_posible(t, c):
            t = t.replace(c, "\\" + c)
    t = t.lstrip()
    if m := re.match(r"^(\d+)([.)])(\s)", t):                 # "2. Numeral…" no debe volverse una lista
        return f"{m[1]}\\{m[2]}{t[m.end() - 1:]}"
    if re.match(r"^[#>+\-|=]", t):
        t = "\\" + t
    return t


def _plano(texto: str) -> str:
    return re.sub(r"\s*\n\s*", " ", texto).strip()


def es_tabla(texto: str) -> bool:
    """Tabla en formato Markdown (así transcribe las tablas el modelo de visión)."""
    filas = [l for l in texto.splitlines() if l.strip()]
    return len(filas) >= 2 and all(l.lstrip().startswith("|") for l in filas)


def bloque_md(texto: str) -> str:
    """Un párrafo como Markdown: las tablas conservan sus filas; el resto se une en una línea."""
    if es_tabla(texto):
        return "\n".join(l.strip().replace("<", "&lt;").replace("$", "\\$") for l in texto.splitlines() if l.strip())
    return md_escapar(_plano(texto))


def _diferencias(antes: str, despues: str) -> list[tuple[str, str]]:
    """Trozos que cambiaron entre dos textos literales, por palabras: [(antes, después)]."""
    a, b = _plano(antes).split(), _plano(despues).split()
    salida = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            salida.append((" ".join(a[i1:i2]), " ".join(b[j1:j2])))
    return salida


def _titulo(n: dict) -> str:
    """Título con el numeral normalizado ("1,3 DEL PROYECTO" → "1.3 DEL PROYECTO")."""
    t, num = n["titulo"], n.get("numero")
    if not num or num == "0" or num.startswith("A"):
        return t
    resto = re.sub(r"^\s*[\dIlL]{1,3}(?:\s?[.,]\s?[\dIlL]{1,3})*[.)]?\s*", "", t)
    return f"{num} {resto}".strip()


def rutas(p: Proyecto):
    return p.carpeta / f"DOCUMENTO_UNICO_{p.id}.md"


class Escritor:
    def __init__(self, p: Proyecto):
        self.p = p
        self.arbol = leer_arbol(p)
        self.nodos = {n["id"]: n for n in self.arbol["nodos"]}
        self.actos = {a["id"]: a for a in self.arbol["actos"]}
        self.orden = {a["id"]: k for k, a in enumerate(self.arbol["actos"])}
        self.lineas = {d.id: leer_lineas(p, d.id) for d in p.documentos}
        self.md: list[str] = []
        self.limpieza = Limpiador(p)
        self.tocado = False                                   # ¿la limpieza cambió el párrafo en curso?
        # páginas con una tabla transcrita con visión: su lectura OCR con "|" se pliega bajo la tabla
        self.pags_tabla = {(n["versiones"][0]["documento"]["id"], pg) for n in self.arbol["nodos"]
                           if n["tipo"] == "tabla" and n["versiones"][0]["documento"]
                           for pg in n["versiones"][0]["paginas"]}

    def texto(self, t: str) -> str:
        """Texto OCR → una línea Markdown limpia."""
        limpio = self.limpieza.limpiar(t)
        self.tocado |= limpio != _plano(t)
        return md_escapar(limpio)

    def bloque(self, t: str) -> str:
        return bloque_md(t) if es_tabla(t) else self.texto(t)

    def tabla_ocr(self, filas: list[dict]) -> str:
        """Filas OCR con "|": tabla Markdown, o plegada si la página ya tiene la tabla transcrita con visión."""
        md = bloque_md("\n".join(ln["texto"] for ln in filas))
        if not all((ln["doc"], ln["pag"]) in self.pags_tabla for ln in filas):
            return md
        pags = sorted({ln["pag"] for ln in filas})
        return (f"<details><summary>Lectura OCR de la tabla (pág. {pags[0]}{'–' + str(pags[-1]) if len(pags) > 1 else ''}"
                f"; vale la transcrita con visión)</summary>\n\n{md}\n\n</details>")

    # --- piezas -------------------------------------------------------------------------------------------
    def bloques(self, v: dict, agrupar: bool = False) -> list[str]:
        """Texto de una versión como bloques Markdown: un párrafo (o, con `agrupar`, los párrafos del original
        unidos por frases) y las tablas Markdown de visión aparte."""
        if not (v["cuenta"] and len(v["lineas"]) == 1):
            return [self.bloque(v["texto"])]
        seg = v["lineas"][0]
        ls = [self.lineas[seg["doc"]][i] | {"doc": seg["doc"]} for i in seg["lineas"]]
        salida = []
        for g in (agrupar_parrafos(ls, unir_sin_punto=True) if agrupar else [ls]):
            tabla, texto = [], []
            for ln in g:
                if ln["texto"].lstrip().startswith("|"):
                    if texto:
                        salida.append(self.texto("\n".join(texto)))
                        texto = []
                    tabla.append(ln)
                else:
                    if tabla:
                        salida.append(self.tabla_ocr(tabla))
                        tabla = []
                    texto.append(ln["texto"])
            if tabla:
                salida.append(self.tabla_ocr(tabla))
            if texto:
                salida.append(self.texto("\n".join(texto)))
        return salida

    def paginas(self, v: dict) -> str:
        pags = v["paginas"]
        return f"pág. {pags[0]}" if len(pags) == 1 else (f"págs. {pags[0]}–{pags[-1]}" if pags else "")

    def cita(self, v: dict) -> str:
        acto = v["acto"]["nombre"] if v["acto"] else "—"
        return f"{acto}{', ' + self.paginas(v) if v['paginas'] else ''}"

    def marca(self, nid: str, v: dict, limpio: bool = False) -> str:
        estado = "vigente" if v["vigente"] else "anterior"
        acto = v["acto"]["nombre"] if v["acto"] else ""
        return (f" <!-- {nid} · {estado} · {acto} · {self.paginas(v)}{' · limpio' if limpio else ''} -->"
                .replace("--  ", "-- "))

    def parrafo(self, nid: str, v: dict, prefijo: str = "", agrupar: bool = False, cita: bool = False) -> list[str]:
        """Bloques de un párrafo con la marca oculta al final de su última línea de texto."""
        self.tocado = False
        bloques = self.bloques(v, agrupar)
        if not bloques:
            return []
        bloques[0] = prefijo + bloques[0]
        marca = self.marca(nid, v, self.tocado)
        k = max((j for j, b in enumerate(bloques) if not (es_tabla(b) or b.startswith("<details>"))), default=None)
        if k is None:
            bloques.append(marca.strip())
        else:
            bloques[k] += marca
        salida = []
        for b in bloques:
            salida += ([f"> {l}" for l in b.splitlines()] + [">"] if cita else [b, ""])
        if cita:
            salida[-1] = ""
        return salida

    def tabla_md(self, n: dict):
        """Tabla JSON (transcrita con visión) como tabla Markdown."""
        if n["tipo"] != "tabla":
            return
        t, v = n["tabla"], n["versiones"][0]
        celda = lambda x: x.replace("|", "\\|").replace("<", "&lt;").replace("$", "\\$")
        self.md += [f"*{md_escapar(n['titulo'])} — tabla transcrita con visión ({self.cita(v)})* "
                    f"<!-- {n['id']} · tabla -->", "",
                    "| " + " | ".join(celda(h) for h in t["encabezados"]) + " |",
                    "|" + "---|" * len(t["encabezados"])] \
                   + ["| " + " | ".join(celda(c) for c in f) + " |" for f in t["filas"]] + [""]
        if t.get("notas"):
            self.md += [f"*Notas de la tabla:* {self.texto(t['notas'])}", ""]

    def otro_acto(self, h: dict, rotulo: str):
        """Fragmento de otro acto como cita con rótulo (no se confunde con el texto de las Bases)."""
        v = h["versiones"][0]
        self.md += self.parrafo(h["id"], v, prefijo=f"**{rotulo}:** ", agrupar=True, cita=True)
        for t in h["hijos"]:
            self.tabla_md(self.nodos[t])

    def nombre_acto(self, acto_id: str | None) -> str:
        return (self.actos.get(acto_id) or {}).get("nombre", "un acto posterior")

    # --- artículos ----------------------------------------------------------------------------------------
    def cambios(self, incisos: list[dict], eliminados: list[dict]) -> list[dict]:
        """Cambios del artículo en orden de aplicación: [{orden, acto, inciso, v, anterior, eliminado}]."""
        salida = []
        for k, h in enumerate(incisos, 1):
            vs = h["versiones"]
            for j, v in enumerate(vs):
                if v["relacion"] == "modificacion" and v["acto"]:
                    salida.append({"orden": self.orden.get(v["acto"]["id"], 999), "acto": v["acto"]["id"],
                                   "inciso": k, "v": v, "anterior": vs[j + 1] if j + 1 < len(vs) else None})
        for h in eliminados:
            v = h["versiones"][0]
            salida.append({"orden": self.orden.get(v["reemplazado_por"], 999), "acto": v["reemplazado_por"],
                           "inciso": None, "v": None, "anterior": v, "eliminado": h["id"]})
        salida.sort(key=lambda c: (c["orden"], c["inciso"] or 0))
        for n, c in enumerate(salida, 1):
            c["n"] = n
        return salida

    def linea_cambio(self, c: dict) -> str:
        if c.get("eliminado"):
            ant = c["anterior"]
            return (f"- **[c{c['n']}] {self.nombre_acto(c['acto'])} eliminó un párrafo.** Texto eliminado "
                    f"({self.cita(ant)}): «{self.texto(ant['texto'])}»")
        v, ant = c["v"], c["anterior"]
        verbo = VERBOS.get(v.get("operacion") or "", "modificó el párrafo {k}").format(k=c["inciso"])
        quien = f"{v['acto']['nombre']}{', ' + self.paginas(v) if v['paginas'] else ''}"
        linea = f"- **[c{c['n']}] {quien} {verbo}.**"
        if ant is None:                     # el historial guardó el texto anterior en otro párrafo del mismo cambio
            return f"{linea} El texto anterior está en la instrucción de la circular."
        if v.get("operacion") in ("reemplaza_texto", "elimina_texto", "agrega_texto"):
            difs = _diferencias(self.limpieza.limpiar(ant["texto"]), self.limpieza.limpiar(v["texto"]))
            if difs and len(difs) <= MAX_DIFERENCIAS:
                trozos = "; ".join(f"«{md_escapar(a) or '—'}» → «{md_escapar(b) or '—'}»" for a, b in difs)
                return f"{linea} {trozos}."
        return f"{linea} Texto anterior ({self.cita(ant)}): «{self.texto(ant['texto'])}»"

    def incisos(self, incisos: list[dict], llamadas: dict[int, list[int]]):
        """Texto vigente, un párrafo por inciso. Una racha de incisos que son solo "20)", "21)"… (el OCR leyó la
        columna de numeración aparte) se empareja, en orden, con los párrafos que siguen si la cantidad calza; si no,
        va en una sola línea en vez de un párrafo por número."""
        solo = [not llamadas.get(k) and bool(_RE_SOLO_NUMERO.match(h["versiones"][0]["texto"]))
                for k, h in enumerate(incisos, 1)]
        numeros: dict[int, str] = {}                          # inciso → número que se le antepone
        rachas: dict[int, list[int]] = {}                     # primer inciso → racha escrita en una línea
        omitir: set[int] = set()
        k = 0
        while k < len(incisos):
            if not solo[k]:
                k += 1
                continue
            fin = k
            while fin < len(incisos) and solo[fin]:
                fin += 1
            racha, siguientes = list(range(k, fin)), []
            for j in range(fin, len(incisos)):
                if solo[j] or _RE_EMPIEZA_NUMERO.match(incisos[j]["versiones"][0]["texto"]):
                    break
                siguientes.append(j)
            if len(racha) > 1 and len(siguientes) == len(racha):
                numeros.update({j: incisos[r]["versiones"][0]["texto"].strip(" -–") for r, j in zip(racha, siguientes)})
                omitir.update(racha)
            elif len(racha) > 1:
                rachas[k] = racha
                omitir.update(racha[1:])
            k = fin
        for k, h in enumerate(incisos):
            if k in omitir:
                continue
            if k in rachas:
                grupo = [incisos[r] for r in rachas[k]]
                self.md += [md_escapar(" ".join(g["versiones"][0]["texto"].strip(" -–") for g in grupo))
                            + "".join(self.marca(g["id"], g["versiones"][0]) for g in grupo), ""]
                continue
            prefijo = "".join(f"**[c{x}]** " for x in llamadas.get(k + 1, [])) + (f"{numeros[k]} " if k in numeros else "")
            self.md += self.parrafo(h["id"], h["versiones"][0], prefijo)

    def articulo(self, n: dict, nivel: int):
        hijos = [self.nodos[h] for h in n["hijos"]]
        de = lambda *tipos: [h for h in hijos if h["tipo"] in tipos]
        incisos, eliminados = de("inciso"), de("inciso_eliminado")
        cambios = self.cambios(incisos, eliminados)
        self.md += [f"{'#' * min(6, nivel)} {self.texto(_titulo(n))}", ""]
        if n.get("agregado_por"):
            self.md += [f"*Artículo agregado por {n['agregado_por']['nombre']}.*", ""]
        elif n.get("eliminado_por"):
            self.md += [f"*Artículo eliminado por {n['eliminado_por']['nombre']}.*", ""]
        elif cambios:
            nombres = list(dict.fromkeys(self.nombre_acto(c["acto"]) for c in cambios))
            self.md += [f"*Modificado por {', '.join(nombres[:-1]) + ' y ' if len(nombres) > 1 else ''}"
                        f"{nombres[-1]}.*", ""]
        # a) texto vigente
        llamadas: dict[int, list[int]] = {}
        for c in cambios:
            if c["inciso"]:
                llamadas.setdefault(c["inciso"], []).append(c["n"])
        self.incisos(incisos, llamadas)
        for h in de("tabla"):
            self.tabla_md(h)
        # b) cambios en este artículo, con la instrucción literal de cada acto
        instrucciones = de("instruccion")
        if cambios or instrucciones:
            self.md += ["**Cambios en este artículo**", ""]
            vistos = set()
            for c in cambios:
                self.md.append(self.linea_cambio(c))
                ultimo = c is cambios[-1] or cambios[cambios.index(c) + 1]["acto"] != c["acto"]
                if ultimo:
                    for h in instrucciones:
                        v = h["versiones"][0]
                        if v["acto"] and v["acto"]["id"] == c["acto"] and h["id"] not in vistos:
                            vistos.add(h["id"])
                            self.md.append(f"  - *Instrucción de la circular:* «{self.texto(v['texto'])}»"
                                           f"{self.marca(h['id'], v)}")
            for h in instrucciones:
                if h["id"] not in vistos:
                    v = h["versiones"][0]
                    self.md.append(f"- **{self.cita(v)}** — instrucción: «{self.texto(v['texto'])}»"
                                   f"{self.marca(h['id'], v)}")
            self.md.append("")
        # c) otros actos sobre este artículo
        otros = sorted(de("aclaracion", "acto_posterior"), key=lambda h: self.orden.get(
            (h["versiones"][0]["acto"] or {}).get("id"), 999))
        if otros:
            self.md += ["**Otros actos sobre este artículo**", ""]
            for h in otros:
                v = h["versiones"][0]
                self.otro_acto(h, ("Aclaración · " if h["tipo"] == "aclaracion" else "") + self.cita(v))
        for h in hijos:
            if h["tipo"] in ("parte", "capitulo", "articulo"):
                self.articulo(h, nivel + 1)

    # --- documento ----------------------------------------------------------------------------------------
    def escribir(self) -> str:
        p, a = self.p, self.arbol
        ficha = [l for l in (p.carpeta / "INDICE.md").read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
        self.md += [f"# Documento único · {p.nombre}", "",
                    "**Cómo leer este documento.** Las Bases de Licitación son el esqueleto, en su orden. Cada artículo "
                    "muestra primero su **texto vigente**; un párrafo que cambió lleva una llamada **[c1]**, **[c2]**… "
                    "Debajo, **Cambios en este artículo** dice qué acto cambió cada párrafo y cómo (con el texto "
                    "anterior), y **Otros actos sobre este artículo** reúne, como citas, los decretos, convenios, "
                    "resoluciones y aclaraciones que lo tratan. Lo que no corresponde a un artículo está al final, por "
                    "tema. Todo el texto es copia de los PDF (OCR o visión)"
                    + (", con una limpieza automática de errores típicos del OCR (signo N°, palabras cortadas con "
                       "guion, letras mal leídas corregidas con un diccionario); la lista de cambios está en "
                       "`revision/limpieza.md` y el texto literal, en `nodos_versionados.json`" if ACTIVA else "")
                    + ". Ante dudas manda el PDF.", "",
                    "## Ficha", ""] + ficha + [""]
        self.md += ["## Actos en orden de aplicación", "", "| Acto | Documento | Fecha | Págs. |", "|---|---|---|---:|"]
        for x in a["actos"]:
            self.md.append(f"| {md_escapar(x['nombre'])} | {md_escapar(x['documento']['titulo'])} | {x['fecha'] or '—'} | "
                           f"{x['paginas'][0]}–{x['paginas'][1]} |")
        self.md += [""]
        for r in a["raices"]:
            n = self.nodos[r]
            if r == "FICHA":                                   # ya está como tabla de actos al inicio
                continue
            if r == "BASES":
                for h in [self.nodos[x] for x in n["hijos"]]:
                    if h["tipo"] == "portada":
                        self.md += ["<details><summary>Portada e índice de las Bases (texto OCR)</summary>", ""]
                        for i in [self.nodos[x] for x in h["hijos"] if self.nodos[x]["tipo"] == "inciso"]:
                            self.md += self.parrafo(i["id"], i["versiones"][0])
                        self.md += ["</details>", ""]
                    else:
                        self.articulo(h, 2)
            elif r.startswith("TEMA:"):
                if "## Otros contenidos de los actos, por tema" not in self.md:
                    self.md += ["## Otros contenidos de los actos, por tema", ""]
                self.md += [f"### {md_escapar(n['titulo'])}", ""]
                for h in [self.nodos[x] for x in n["hijos"]]:
                    self.otro_acto(h, self.cita(h["versiones"][0]))
            elif r == "FORMALIDADES":
                self.md += ["## Formalidades de los actos", ""]
                for h in [self.nodos[x] for x in n["hijos"]]:
                    self.md += [f"<details><summary>{self.cita(h['versiones'][0])}</summary>", ""]
                    self.md += self.parrafo(h["id"], h["versiones"][0], agrupar=True)
                    self.md += ["</details>", ""]
        texto = "\n".join(self.md) + "\n"
        return re.sub(r"\n{3,}", "\n\n", texto)


def ensamblar(p: Proyecto) -> str:
    escritor = Escritor(p)
    md = escritor.escribir()
    ruta = rutas(p)
    ruta.write_text(md, encoding="utf-8")
    if ACTIVA:
        informe = escritor.limpieza.informe(p)
        print(f"  {informe.name}: " + " · ".join(f"{r} {c}" for r, c in escritor.limpieza.cambios.most_common()))
    for viejo in (f"DOCUMENTO_UNICO_{p.id}_ANEXOS.md", f"DOCUMENTO_UNICO_{p.id}.html", f"DOCUMENTO_UNICO_{p.id}.jsonl"):
        (p.carpeta / viejo).unlink(missing_ok=True)
    print(f"  {ruta.name}: {len(md) / 1e6:.1f} MB · {md.count(chr(10))} líneas")
    return md
