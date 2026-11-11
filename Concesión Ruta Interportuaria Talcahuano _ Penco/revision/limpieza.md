# Limpieza del documento único · RITP

Reglas deterministas (sin LLM) aplicadas solo al Markdown; el árbol y los chunks siguen literales.

- Diccionario: 0 palabras (modelo `spa` de Tesseract) — no disponible: la regla de vocabulario no corrió.
- Vocabulario de la concesión: 8,986 palabras; 0 frecuentes (≥ 3 y en el diccionario).

| Regla | Cambios |
|---|---:|
| barras | 5834 |
| numero | 966 |
| espacios | 369 |
| puntos_indice | 266 |
| guion_corte | 72 |
| grado | 63 |

## puntos_indice

- «................................................................................» → « … »
- «................................................................................................ » → « … »
- « ............................................................................ » → « … »
- « ..................................................................................... » → « … »
- «.......................................................................................................... » → « … »
- « ................................................................................. » → « … »

## espacios

- «Agosto 2001 SANTIAGO, CHILE [ilegible] INDICE 1 BASES ADMINISTRATIVAS … 1. 1.1 I» → «Agosto 2001 SANTIAGO, CHILE [ilegible] INDICE 1 BASES ADMINISTRATIVAS … 1. 1.1 I»
- «1.9 DE LA CONSTRUCCION … 52 1.9.1 Etapa de Ingeniería … 52 1.9.1.1 De la Ingenie» → «1.9 DE LA CONSTRUCCION … 52 1.9.1 Etapa de Ingeniería … 52 1.9.1.1 De la Ingenie»
- «2.3.2.1   Ingeniería Básica                                                     » → «2.3.2.1 Ingeniería Básica 104 2.3.2.1.1 Topografía … 105 2.3.2.1.2 Mecánica de S»
- «2.4     ANTECEDENTES EXPROPIATORIOS … 117» → «2.4 ANTECEDENTES EXPROPIATORIOS … 117»
- «2.5     PLAN DE AUTOCONTROL DE CALIDAD DE LAS OBRAS … 118» → «2.5 PLAN DE AUTOCONTROL DE CALIDAD DE LAS OBRAS … 118»
- «2.6     DE LA EXPLOTACION DE LA CONCESION … 119 2.6.1   Plan de Conservación de » → «2.6 DE LA EXPLOTACION DE LA CONCESION … 119 2.6.1 Plan de Conservación de las Ob»

## numero

- «Nº » → «N°»
- «Nº» → «N°»
- «N° » → «N°»

## grado

- «artículo 2º» → «artículo 2°»
- «artículo 45º» → «artículo 45°»
- «artículo 15º» → «artículo 15°»
- «artículos 98º» → «artículos 98°»
- «artículo 7º» → «artículo 7°»
- «artículo 35º» → «artículo 35°»

## barras

- «|» → « »

## guion_corte

- «Licita- ción» → «Licitación»
- «transcrip- ciones» → «transcripciones»
- «ar- chivo» → «archivo»
- «protoco- lización» → «protocolización»
- «de- creto» → «decreto»
- «Públi- cas» → «Públicas»

