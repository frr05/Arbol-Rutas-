"""Etapa 3: actos administrativos de cada documento (tipo, número, fecha) y su orden cronológico.

Un PDF puede traer varios actos: `Circulares-Aclaratorias-1-a-3` son tres circulares con fechas distintas. Un acto
nuevo empieza en una página cuyo membrete ("REPÚBLICA DE CHILE / MINISTERIO DE OBRAS PÚBLICAS") va seguido de un
tipo de acto ("CIRCULAR ACLARATORIA N°…", "DECRETO", "RESOLUCIÓN"…). Las publicaciones del Diario Oficial no se
dividen (cada página trae su propio encabezado).

Fecha y número salen de las primeras líneas del acto, del nombre del archivo o del INDICE; si falta la fecha,
el LLM la lee de esas mismas líneas. `revision/actos.csv` (separador ;) se puede editar: las filas con
origen "manual" mandan sobre la detección.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel

from . import llm
from .indice import Documento, Proyecto
from .texto import leer_lineas

_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "set": 9,
          "oct": 10, "nov": 11, "dic": 12}
_RE_FECHA = re.compile(r"(\d\s?\d?)\s*(?:de\s+|[.\-/ ,]\s*)?([A-Za-zÁÉÍÓÚáéíóú]{3,10})\.?,?\s*(?:de\s+|del\s+)?((?:19|20)\d{2})", re.I)
_RE_FECHA_NUM = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./]((?:19|20)\d{2})")
_RE_MEMBRETE = re.compile(r"REP[UÚ]BLICA DE CHILE|GOBIERNO DE CHILE|MINISTERIO DE OBRAS P", re.I)
_TIPOS = [("circular aclaratoria", re.compile(r"CIRCULAR\s+(ACLARA|N\W{0,3}\s*\d)", re.I)),   # "Circular N°2" (Bases antiguas)
          ("convenio complementario", re.compile(r"CONVENIO\s+(COMPLEMENTARIO|AD\s*REFER)", re.I)),
          ("resolucion", re.compile(r"\bRESOLUCI[OÓ]N\b|\bRES\.?\s*(DGOP|EX)", re.I)),
          ("decreto", re.compile(r"\bDECRETO\b|\bD\.?\s?S\.?\s+(MOP\s+)?N", re.I))]
_RE_NUMERO = re.compile(r"\bN(?:[uú]m)?[\W_]{0,3}\s*(\d{1,2}(?:[.,]?\d{3})*|\d{1,5})\b", re.I)
# número en el nombre del archivo o el título del INDICE: "Resol_N-1570", "DS-N-663", "DS-No136", "Res 2965"
_RE_NUMERO_ARCHIVO = re.compile(r"(?<![A-Za-z])(?:N(?:[o°º]|[_\-])?|Res(?:ol)?[_\- ]|DS[_\- ])\s*(\d+)")
# "N°900 de 1996": cita a otro cuerpo legal, no el número del acto
_RE_CITA_ANIO = re.compile(r"\s*,?\s*del?\s+((?:19|20)\d\d)\b", re.I)
_RE_SANTIAGO = re.compile(r"santiago\s*,?", re.I)


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def fecha_en(texto: str) -> str | None:
    """Primera fecha reconocible en un texto (AAAA-MM-DD)."""
    for m in _RE_FECHA.finditer(texto):
        mes = _MESES.get(_sin_tildes(m[2]).lower()[:3])
        dia = int(m[1].replace(" ", ""))
        if mes and 1 <= dia <= 31:
            return f"{m[3]}-{mes:02d}-{dia:02d}"
    if m := _RE_FECHA_NUM.search(texto):
        if 1 <= int(m[2]) <= 12 and 1 <= int(m[1]) <= 31:
            return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return None


def _anio_en(texto: str) -> str | None:
    anios = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", texto)
    return anios[-1] if anios else None


def _meta_indice(p: Proyecto) -> dict[str, str]:
    """Campos "Clave: valor" de las viñetas del INDICE.md (p. ej. "Decreto de adjudicación")."""
    meta = {}
    for linea in (p.carpeta / "INDICE.md").read_text(encoding="utf-8").splitlines():
        if linea.startswith("- "):
            for parte in linea[2:].split(" · "):
                if ":" in parte:
                    clave, valor = parte.split(":", 1)
                    meta[clave.strip().lower()] = valor.strip()
    return meta


class _Meta(BaseModel):
    tipo: str
    numero: str | None
    fecha: str | None


def _inicios(lineas: list[dict], dividir: bool) -> list[int]:
    """Páginas donde empieza un acto (siempre la primera). Solo se dividen los documentos que agrupan varios
    actos del mismo tipo (circulares): en decretos y convenios el membrete reaparece en los actos citados."""
    por_pag: dict[int, list[dict]] = {}
    for ln in lineas:
        if not ln["marginal"]:
            por_pag.setdefault(ln["pag"], []).append(ln)
    paginas = sorted(por_pag)
    if not paginas:
        return []
    if not dividir:
        return [paginas[0]]
    salida = [paginas[0]]
    for pag in paginas[1:]:
        # solo líneas cortas de cabecera: un párrafo largo (texto de visión) puede nombrar al MOP y a un decreto
        primeras = " ".join(ln["texto"] for ln in por_pag[pag][:12] if len(ln["texto"]) < 90)
        if _RE_MEMBRETE.search(primeras) and any(r.search(primeras) for _, r in _TIPOS):
            salida.append(pag)
    return salida


def _numero_propio(texto: str, anio: str) -> str | None:
    """Primer "N° x" de la línea que no sea una cita a otro acto o ley ("DS MOP N°900 de 1996")."""
    for m in _RE_NUMERO.finditer(texto):
        cita = _RE_CITA_ANIO.match(texto, m.end())
        if cita and cita[1] != anio:
            continue
        return m[1]
    return None


def detectar(p: Proyecto, doc: Documento, usar_llm: bool = True) -> list[dict]:
    lineas = leer_lineas(p, doc.id)
    inicios = _inicios(lineas, dividir="circular" in doc.subtipo)
    # fecha del nombre del archivo o del INDICE (confiable cuando existe): la del texto solo se acepta si
    # coincide en el año (el texto cita muchas fechas de otros actos)
    fecha_archivo = fecha_en(Path(doc.archivo).stem.replace("_", " ")) or fecha_en(doc.titulo)
    if doc.subtipo == "decreto adjudicacion":
        fecha_archivo = fecha_archivo or fecha_en(_meta_indice(p).get("decreto de adjudicación", ""))
    anio_archivo = (fecha_archivo or "")[:4] or _anio_en(Path(doc.archivo).stem) or _anio_en(doc.titulo)
    actos = []
    ultima = ""
    for k, pag in enumerate(inicios):
        fin = inicios[k + 1] if k + 1 < len(inicios) else 10 ** 6
        cuerpo = [ln for ln in lineas if pag <= ln["pag"] < fin and not ln["marginal"]]
        cabeza = "\n".join(ln["texto"] for ln in cuerpo[:30])
        # el subtipo del INDICE es más confiable que el texto (que cita otros actos), salvo en las circulares
        tipo = doc.subtipo
        m = _RE_NUMERO_ARCHIVO.search(f"{Path(doc.archivo).stem} {doc.titulo}")
        numero = m[1] if m and "circular" not in doc.subtipo else None
        for ln in ([] if numero else cuerpo[:30]):
            if any(r.search(ln["texto"]) for _, r in _TIPOS) or ln["texto"].lower().startswith("núm"):
                if n := _numero_propio(ln["texto"], anio_archivo):
                    numero = n
                    break
        # fecha: la que sigue a "Santiago," manda; si no, la primera de la cabecera
        fecha = None
        for ln in cuerpo[:40]:
            if _RE_SANTIAGO.search(ln["texto"]) and (f := fecha_en(ln["texto"])):
                fecha, origen = f, "texto"
                break
        if not fecha and (f := fecha_en(cabeza)):
            fecha, origen = f, "texto"
        if fecha and ((anio_archivo and fecha[:4] != anio_archivo) or fecha < ultima):
            fecha = None
        if not fecha and fecha_archivo and len(fecha_archivo) == 10:
            fecha, origen = fecha_archivo, "archivo"
        if not fecha and usar_llm:
            try:
                r = llm.pedir_json(p.carpeta / "cache" / "llm", "actos_meta",
                                   "Estas son las primeras líneas (OCR, con errores) de un acto administrativo chileno. "
                                   "Indica su tipo (circular aclaratoria, decreto, resolución, convenio, bases, etc.), "
                                   "su número oficial y su fecha de emisión en formato AAAA-MM-DD (null si no se lee).\n\n"
                                   + cabeza, _Meta,
                                   validar=lambda o: None if not o.fecha or re.fullmatch(r"(19|20)\d\d-\d\d-\d\d", o.fecha)
                                   else "fecha con formato inválido")
                if r.fecha and (not anio_archivo or r.fecha[:4] == anio_archivo) and r.fecha >= ultima:
                    fecha, origen = r.fecha, "llm"
                numero = numero or r.numero
            except llm.SinModelos as e:
                print(f"    {doc.id} p{pag}: sin LLM para la fecha ({str(e)[:80]})")
        if not fecha:
            fecha, origen = (anio_archivo, "archivo") if anio_archivo else (None, "sin fecha")
        ultima = max(ultima, fecha or "")
        actos.append({"acto": f"{doc.id}.A{k + 1}", "documento": doc.id, "pag_inicio": pag,
                      "pag_fin": (fin - 1) if fin < 10 ** 6 else max(ln["pag"] for ln in lineas),
                      "tipo": tipo, "numero": numero or "", "fecha": fecha or "", "origen": origen,
                      "titulo": doc.titulo})
    return actos


COLUMNAS = ["acto", "documento", "pag_inicio", "pag_fin", "tipo", "numero", "fecha", "origen", "titulo"]


def leer_actos(p: Proyecto) -> list[dict]:
    ruta = p.dir_revision / "actos.csv"
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        filas = [{**f, "pag_inicio": int(f["pag_inicio"]), "pag_fin": int(f["pag_fin"])}
                 for f in csv.DictReader(fh, delimiter=";")]
    return ordenar(p, filas)


def ordenar(p: Proyecto, actos: list[dict]) -> list[dict]:
    """Orden de aplicación: Bases primero; luego por fecha (sin fecha: al final de su etapa) y página."""
    pos = {d.id: k for k, d in enumerate(p.documentos)}
    etapa = {d.id: d.archivo.split("/")[0] for d in p.documentos}
    # las etapas de la ficha ya son cronológicas (licitación < adjudicación < ejecución/modificación); dentro
    # de cada etapa manda la fecha
    def clave(a):
        es_bases = p.doc(a["documento"]).subtipo.startswith("bases licitacion")
        return (0 if es_bases else 1, min(etapa[a["documento"]], "3"), a["fecha"] or "9999",
                pos[a["documento"]], a["pag_inicio"])
    return sorted(actos, key=clave)


def actos_proyecto(p: Proyecto, usar_llm: bool = True) -> list[dict]:
    manuales = {}
    ruta = p.dir_revision / "actos.csv"
    if ruta.exists():
        manuales = {a["acto"]: a for a in leer_actos(p) if a["origen"] == "manual"}
    actos = []
    for d in p.documentos:
        for a in detectar(p, d, usar_llm):
            actos.append(manuales.get(a["acto"], a))
    actos = ordenar(p, actos)
    p.dir_revision.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS, delimiter=";", lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(actos)
    for a in actos:
        print(f"  {a['acto']:<12} págs. {a['pag_inicio']:>3}-{a['pag_fin']:<3} {a['fecha'] or '?':<10} "
              f"{a['tipo']:<24} N° {a['numero'] or '?':<7} ({a['origen']})")
    return actos


def acto_de_linea(actos: list[dict], doc_id: str, pag: int) -> dict:
    for a in actos:
        if a["documento"] == doc_id and a["pag_inicio"] <= pag <= a["pag_fin"]:
            return a
    return next(a for a in actos if a["documento"] == doc_id)


# --- actos citados en los documentos (sin PDF propio) --------------------------------------------------
_RE_CITADO = re.compile(
    r"(?P<tipo>Decreto\s+Supremo(?:\s+MOP)?|D\.\s?S\.(?:\s+MOP)?|Resoluci[oó]n\s+DGOP(?:\s*\(Exenta\))?|"
    r"Convenio\s+Complementario|Convenio\s+Ad[\s-]*Refer[eé]ndum)"
    r"\s*N[^\w\s]?[YºoO°]?[^\w\s]{0,2}\s*(?P<numero>\d{1,2}(?:\.\d{3})+|\d{1,5})\b(?P<resto>.{0,260})", re.I)
# contenido del acto citado: "que aprobó…", "el cual, además, aprobó…", "mediante el cual se acordaron…"
_RE_CLAUSULA = re.compile(r"\b(?:que|el cual|la cual|mediante el cual|por el cual|en virtud del cual)(?:\W+\w+){0,3}?\W+"
                          r"(?:aprob|modific|sancion|establec|fij[oó] las tarifas|acord)\w*.{0,150}", re.I)
_RE_NORMATIVA = re.compile(r"texto refundido|Reglamento de la Ley|Ley de Concesiones|Ley Org[aá]nica", re.I)
_RE_OTRA_CITA = re.compile(r"Decreto|Resoluci|Convenio|\bD\.\s?S\.|\bN\W{0,3}\d", re.I)
MAX_HASTA_CLAUSULA = 120     # la cláusula debe seguir de cerca al número (si no, es de otra cita)


def _familia(tipo: str) -> str:
    t = _sin_tildes(tipo).lower()
    return ("convenio ad referendum" if "referendum" in t else "convenio complementario" if "complementario" in t
            else "resolucion" if "resolucion" in t else "decreto supremo")


def actos_citados(p: Proyecto, actos: list[dict]) -> list[dict]:
    """Actos que los documentos citan con su contenido ("Decreto Supremo MOP N°1.065, de fecha 22 de noviembre de
    2004, que aprobó el Convenio Complementario N°1…") pero que no tienen PDF propio en la carpeta. Sin LLM. Se
    omiten los cuerpos normativos (textos refundidos, reglamentos) y las adjudicaciones; dos números del mismo
    tipo y fecha son una lectura distinta del OCR y se unen. [{tipo, numero, fecha, clausula, documentos}]"""
    propios = {(_familia(a["tipo"]), a["numero"].replace(".", "")) for a in actos if a.get("numero")}
    citados: dict[tuple, dict] = {}
    for d in p.documentos:
        texto = re.sub(r"\s+", " ", " ".join(ln["texto"] for ln in leer_lineas(p, d.id) if not ln["marginal"]))
        for m in _RE_CITADO.finditer(texto):
            familia, numero = _familia(m["tipo"]), m["numero"].replace(".", "")
            clausula = _RE_CLAUSULA.search(m["resto"])
            if not clausula or (familia, numero) in propios:
                continue
            previo = m["resto"][:clausula.start()]
            if (clausula.start() > MAX_HASTA_CLAUSULA or _RE_OTRA_CITA.search(previo)
                    or _RE_NORMATIVA.search(m["resto"][:clausula.end()])):
                continue
            fecha = fecha_en(previo + clausula[0][:20])
            clave = (familia, fecha) if fecha else (familia, numero)
            c = citados.setdefault(clave, {"tipo": familia, "numeros": [], "fecha": fecha,
                                           "clausula": clausula[0].strip(" ,;."), "documentos": []})
            if numero not in c["numeros"]:
                c["numeros"].append(numero)
            if d.id not in c["documentos"]:
                c["documentos"].append(d.id)
    # mismo tipo y número leído con dos fechas distintas → una sola entrada (manda la lectura más citada)
    unidos: list[dict] = []
    for c in sorted(citados.values(), key=lambda c: c["fecha"] or "9999"):
        igual = next((u for u in unidos if u["tipo"] == c["tipo"] and set(u["numeros"]) & set(c["numeros"])), None)
        if igual:
            if len(c["documentos"]) > len(igual["documentos"]):          # manda la lectura más citada
                igual["fecha"], igual["clausula"] = c["fecha"], c["clausula"]
                igual["numeros"] = c["numeros"] + [n for n in igual["numeros"] if n not in c["numeros"]]
            igual["numeros"] += [n for n in c["numeros"] if n not in igual["numeros"]]
            igual["documentos"] += [x for x in c["documentos"] if x not in igual["documentos"]]
        else:
            unidos.append(c)
    salida = []
    for c in unidos:
        if any((c["tipo"], n) in propios for n in c["numeros"]):
            continue
        # el número más citado primero; los demás son otras lecturas del OCR
        c["numero"] = c["numeros"][0]
        salida.append(c)
    return sorted(salida, key=lambda c: (c["fecha"] or "9999", c["tipo"]))


_PALABRA_TIPO = {"convenio": "convenio", "circular": "circular", "decreto": "decreto", "ds": "decreto",
                 "res": "resolucion", "resolucion": "resolucion"}


def actos_nombrados(texto: str, actos: list[dict]) -> set[str]:
    """Ids de los actos que un texto nombra por tipo y número: «Convenio Complementario N°1», «DS MOP N°136»,
    «Res. 1570», «Circular N°4». `actos`: dicts con id, tipo y numero (los de `actos.csv` o de los metadatos)."""
    bajo = texto.lower().translate(str.maketrans("áéíóú", "aeiou"))
    salida = set()
    for a in actos:
        if not a.get("numero"):
            continue
        familia = ("resolucion" if a["tipo"] in ("resolucion", "puesta en servicio") else
                   next((f for f in ("convenio", "circular", "decreto") if f in a["tipo"]), None))
        patron = (rf"\b(convenio|circular|decreto|ds|res|resolucion)\b[^;·?]{{0,40}}?(?:n\W{{0,2}}\s?|\s)"
                  rf"{re.escape(a['numero'])}(?![\d.])")
        if any(_PALABRA_TIPO.get(m[1]) == familia for m in re.finditer(patron, bajo)):
            salida.add(a["id"])
    return salida
