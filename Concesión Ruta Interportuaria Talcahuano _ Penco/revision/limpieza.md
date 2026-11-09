# Limpieza del documento único · RITP

Reglas deterministas (sin LLM) aplicadas solo al Markdown; el árbol y los chunks siguen literales.

- Diccionario: 100,093 palabras (modelo `spa` de Tesseract).
- Vocabulario de la concesión: 10,355 palabras; 3,588 frecuentes (≥ 3 y en el diccionario).

| Regla | Cambios |
|---|---:|
| barras | 2314 |
| numero | 961 |
| espacios | 256 |
| puntos_indice | 203 |
| guion_corte | 146 |
| vocabulario | 107 |
| grado | 102 |

## puntos_indice

- «...................................................» → « … »
- «..............................................................» → « … »
- «.............................................» → « … »
- «............................................» → « … »
- «........................................» → « … »
- «..................................................» → « … »

## numero

- «Nº » → «N°»
- «Nº» → «N°»
- «N*» → «N°»
- «N“» → «N°»
- «N%» → «N°»
- «N* » → «N°»

## grado

- «artículo 87”» → «artículo 87°»
- «artículo 2”» → «artículo 2°»
- «artículo 45%» → «artículo 45°»
- «artículo 15%» → «artículo 15°»
- «artículos 98”» → «artículos 98°»
- «artículo 7”» → «artículo 7°»

## espacios

- «1.1 Por Decreto Supremo del Ministerio de Obras Públicas MOP N°112, de 31 de ene» → «1.1 Por Decreto Supremo del Ministerio de Obras Públicas MOP N°112, de 31 de ene»
- «TRAMITADO 15 ABR 2002 DIRECCION DE RESOLUCIONES MINISTERIO DE HACIENDA DIRECCION» → «TRAMITADO 15 ABR 2002 DIRECCION DE RESOLUCIONES MINISTERIO DE HACIENDA DIRECCION»
- «El Acta de Evaluación de las Ofertas Técnicas de fecha 20 de diciembre de 2001 ;» → «El Acta de Evaluación de las Ofertas Técnicas de fecha 20 de diciembre de 2001; »
- «Construcción del ramal Talcahuano-Concepción, conforme a lo dispuesto en el arti» → «Construcción del ramal Talcahuano-Concepción, conforme a lo dispuesto en el arti»
- «¿Cuáles son las obras preexistentes, que deben considerase para el mantenimiento» → «¿Cuáles son las obras preexistentes, que deben considerase para el mantenimiento»
- «Remítase a lo señalado en las Bases de Licitación y, en particular, la Tabla N°1» → «Remítase a lo señalado en las Bases de Licitación y, en particular, la Tabla N°1»

## guion_corte

- «Subsecreta- ria» → «Subsecretaria»
- «refundi- do» → «refundido»
- «Talca- huano» → «Talcahuano»
- «noviem- bre» → «noviembre»
- «conside- rarán» → «considerarán»
- «Alessan- dri» → «Alessandri»

## vocabulario

- «Itnta» → «Itata»
- «Aeclaratoria» → «Aclaratoria»
- «Ineresos» → «Ingresos»
- «Inoresos» → «Ingresos»
- «Inereso» → «Ingreso»
- «Inoreso» → «Ingreso»

## barras

- «|» → « »

## Todas las correcciones por vocabulario

| Palabra OCR | Reemplazo | Veces |
|---|---|---:|
| Actaratoria | Aclaratoria | 1 |
| Aeclaratoria | Aclaratoria | 2 |
| Antopista | Autopista | 2 |
| Bailenas | Ballenas | 1 |
| Bezannilla | Bezanilla | 1 |
| Catzada | Calzada | 1 |
| Cireular | Circular | 1 |
| Colocsción | Colocación | 1 |
| Concesfones | Concesiones | 1 |
| Conceslonaria | Concesionaria | 1 |
| Concestonario | Concesionario | 1 |
| Concestones | Concesiones | 1 |
| Concspción | Concepción | 1 |
| Concésionario | Concesionario | 1 |
| Construeción | Construcción | 1 |
| Daspeje | Despeje | 1 |
| Definitrva | Definitiva | 1 |
| Garantifado | Garantizado | 1 |
| Gaseoducto | Gasoducto | 1 |
| Hormagón | Hormigón | 1 |
| Ineluye | Incluye | 1 |
| Inereso | Ingreso | 1 |
| Ineresos | Ingresos | 1 |
| Infermar | Informar | 1 |
| Inoreso | Ingreso | 2 |
| Inoresos | Ingresos | 1 |
| Itnta | Itata | 1 |
| Iñostroza | Inostroza | 1 |
| Jetra | Letra | 1 |
| Luvias | Lluvias | 1 |
| Ochava | Octava | 1 |
| PubIICaS | Publicas | 1 |
| Referencisles | Referenciales | 1 |
| Releno | Relleno | 1 |
| Soieras | Soleras | 1 |
| Taicahuano | Talcahuano | 1 |
| Taleahuano | Talcahuano | 1 |
| Terrapienes | Terraplenes | 1 |
| Tofas | Todas | 1 |
| Yalor | Valor | 1 |
| Yapores | Vapores | 1 |
| aceesos | accesos | 1 |
| acriones | acciones | 1 |
| alendario | calendario | 1 |
| aleuno | alguno | 1 |
| ancarias | bancarias | 1 |
| antececentes | antecedentes | 1 |
| apital | capital | 1 |
| complementatio | complementario | 1 |
| coordenado | coordinado | 1 |
| debidamerte | debidamente | 1 |
| deseribe | describe | 3 |
| disporibles | disponibles | 1 |
| eberá | deberá | 1 |
| ejoramiento | mejoramiento | 1 |
| eruce | cruce | 1 |
| erupo | grupo | 2 |
| especíal | especial | 1 |
| eviiar | evitar | 1 |
| exproplaciones | expropiaciones | 1 |
| eñaladas | señaladas | 1 |
| fiene | tiene | 1 |
| fierra | tierra | 1 |
| hotifique | notifique | 1 |
| hubisra | hubiera | 1 |
| humeral | numeral | 1 |
| infegrante | integrante | 1 |
| inteerado | integrado | 1 |
| iscal | fiscal | 1 |
| iseño | diseño | 1 |
| lessandri | alessandri | 1 |
| loneitud | longitud | 1 |
| luminación | iluminación | 1 |
| mbientales | ambientales | 1 |
| máteriales | materiales | 1 |
| ndicado | indicado | 1 |
| nspección | inspección | 1 |
| ontará | contará | 1 |
| ostergar | postergar | 1 |
| parcas | marcas | 1 |
| posibilldad | posibilidad | 1 |
| prsentes | presentes | 1 |
| própuesto | propuesto | 1 |
| pulmenar | pulmonar | 1 |
| resentes | presentes | 1 |
| reálice | realice | 1 |
| seeún | según | 1 |
| sefalización | señalización | 1 |
| slempre | siempre | 2 |
| slguientes | siguientes | 1 |
| specificaciones | especificaciones | 1 |
| sácios | socios | 1 |
| tembién | también | 1 |
| trasiado | traslado | 1 |
| trábajos | trabajos | 3 |
| uniferme | uniforme | 1 |
| venículo | vehículo | 1 |
| Árcas | Áreas | 1 |

