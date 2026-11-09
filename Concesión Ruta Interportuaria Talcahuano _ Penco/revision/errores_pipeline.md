# Errores del pipeline · RITP

Cada pregunta mal respondida por el RAG (`evaluar`) se asigna a la etapa donde se pierde la respuesta (reglas de `mt/errores.py`, corregidas a mano en `revision/errores_revision.csv`).

## Última corrida, por etapa

| Etapa | Qué mejorar | rag | tematicas | rag_agente | tematicas_agente | Total |
|---|---|---:|---:|---:|---:|---:|
| generacion | vectores.responder (prompt, contexto, modelo) | 4 | 7 | 2 | 2 | 15 |
| recuperacion | chunks.py / vectores.py (búsqueda) | 3 | 0 | 0 | 0 | 3 |
| sin_fuente | preguntas de evaluación (fuente) | 1 | 0 | 1 | 0 | 2 |

## rag: detalle

- **4** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases art. 1.3.1
- **7** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: [1] Decreto adjudicacion N°900 (2002-01-31) págs. 1–2 (RITP.D04.F0002)
- **20** (parcial, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: DS MOP N°112, pág. 2; Bases art. 1.7.5
- **30** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases art. 1.3.1 y 1.3.2
- **38** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: [1] Publicacion diario oficial N°47 (2002) pág. 4 (RITP.D05.F0029#p2) · [3] Bases licitaci
- **45** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: [1] Bases licitacion págs. 57–58 (BL:1.8.5.2) · [8] Decreto adjudicacion N°900 (2002-01-31
- **48** (parcial, sin_fuente, reglas): la fuente no se pudo ubicar en el árbol — fuente: Índice de documentos de la concesión (INDICE.md)
- **50** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: [1] Decreto supremo N°136 (2023-07-25) pág. 3 (RITP.D13.F0013)

## tematicas: errores por tema y etapa

| Tema | generacion | recuperacion | sin_fuente |
|---|---:|---:|---:|
| A licitación y ofertas | 1 | 0 | 0 |
| B plazos y etapas | 1 | 0 | 0 |
| C económico y riesgos | 3 | 0 | 0 |
| D obras y modificaciones | 2 | 0 | 0 |

## tematicas: detalle

- **5** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases 1.4.6, pág. 23
- **15** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases 1.9.4, págs. 73–74
- **23** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases 1.8.11, págs. 60–61
- **24** (parcial, generacion, revisión): Omitió el monto (valor total de la obra) y el deducible del 1%, que estaban en el chunk citado [1]. — fuente: Bases 1.8.12, pág. 62
- **27** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases 1.19, págs. 107–108
- **35** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Convenio Complementario N°1, págs. 1–15
- **38** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases 2.6.6.1–2.6.6.3

## rag_agente: detalle

- **38** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: [1] Publicacion diario oficial N°47 (2002) pág. 4 (RITP.D05.F0029#p2) · [3] Bases licitaci
- **45** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: [1] Bases licitacion págs. 57–58 (BL:1.8.5.2) · [8] Decreto adjudicacion N°900 (2002-01-31
- **48** (incorrecta, sin_fuente, reglas): la fuente no se pudo ubicar en el árbol — fuente: Índice de documentos de la concesión (INDICE.md)

## tematicas_agente: errores por tema y etapa

| Tema | generacion | recuperacion | sin_fuente |
|---|---:|---:|---:|
| D obras y modificaciones | 2 | 0 | 0 |

## tematicas_agente: detalle

- **35** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Convenio Complementario N°1, págs. 1–15
- **38** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases 2.6.6.1–2.6.6.3

## Otros errores encontrados al revisar (sin pregunta asociada)

| Etapa | Dónde | Detalle |
|---|---|---|
| estructura | Bases 1.9.5 | Los artículos 1.9.6, 1.9.7 y 1.9.8 no se detectaron y su texto quedó dentro de 1.9.5. |
| estructura | Bases 2.6.6 | Falta el título 2.6.6 |
| ocr | Bases 1.15.4 (Tabla N°6) | La tabla de factores por vehículo es ilegible en el OCR |
| tablas | Bases 2.2.1.2 (Tabla N°9, pág. 111) | La tabla de puentes no se mandó a visión: el OCR la deja como «Pao Superior Las Gavia Puente Las Ballenas 3,30 110 [| 14 |». |
| ocr | Bases 1.6.2 y 1.7.5 | Basura del OCR mezclada con el texto («ME: EXA o o IA», «Decreto Supremo de o de la Concesión»). |
| ocr | Publicación Diario Oficial (2002) | Números mal leídos en los cuerpos legales: «N°200, de 1926» por «N°900, de 1996» y «N°350» por «N°850». |

## Historial

| Fecha | Set | Errores | generacion | recuperacion | sin_fuente |
|---|---|---:|---:|---:|---:|
| 2026-11-03 20:21 | rag | 14 | 5 | 2 | 0 |
| 2026-11-03 21:31 | rag | 5 | 0 | 2 | 0 |
| 2026-11-05 00:38 | rag | 7 | 4 | 1 | 1 |
| 2026-11-05 02:02 | rag | 8 | 4 | 3 | 1 |
| 2026-11-05 02:13 | rag | 8 | 4 | 3 | 1 |
| 2026-11-03 20:21 | tematicas | 11 | 4 | 1 | 0 |
| 2026-11-03 21:32 | tematicas | 5 | 2 | 1 | 0 |
| 2026-11-05 00:39 | tematicas | 7 | 7 | 0 | 0 |
| 2026-11-04 02:36 | rag_agente | 3 | 2 | 0 | 1 |
| 2026-11-04 02:39 | rag_agente | 3 | 1 | 0 | 1 |
| 2026-11-04 02:41 | rag_agente | 3 | 2 | 0 | 1 |
| 2026-11-04 02:36 | tematicas_agente | 2 | 2 | 0 | 0 |
| 2026-11-04 02:40 | tematicas_agente | 2 | 2 | 0 | 0 |
| 2026-11-04 02:41 | tematicas_agente | 2 | 2 | 0 | 0 |
