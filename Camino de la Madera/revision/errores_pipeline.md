# Errores del pipeline · CM

Cada pregunta mal respondida por el RAG (`evaluar`) se asigna a la etapa donde se pierde la respuesta (reglas de `mt/errores.py`, corregidas a mano en `revision/errores_revision.csv`).

## Última corrida, por etapa

| Etapa | Qué mejorar | rag | rag_agente | Total |
|---|---|---:|---:|---:|
| generacion | vectores.responder (prompt, contexto, modelo) | 6 | 2 | 8 |
| recuperacion | chunks.py / vectores.py (búsqueda) | 4 | 2 | 6 |

## rag: detalle

- **9** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: DS MOP N°133 (adjudicación), págs. 1–2 (verificado en la imagen)
- **10** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: DS MOP N°133 (adjudicación), pág. 1 (vistos); Circulares 2, 3 y 4
- **15** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: [1] Oferta economica pág. 1 (CM.D05.F0001)
- **22** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases de Licitación art. 1.5.8, págs. 33–34
- **31** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: Bases de Licitación art. 1.1.3, págs. 10–11; DS MOP N°133 (adjudicación), pág. 2
- **33** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: Bases de Licitación art. 1.1.3
- **37** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: DS MOP N°133 (adjudicación), pág. 7
- **38** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases de Licitación; Convenio Complementario N°1
- **45** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Bases de Licitación art. 1.5.46; DS MOP N°133 (adjudicación), pág. 6
- **48** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Convenio de Extinción, págs. 3–4; Res. 2633

## rag_agente: detalle

- **9** (incorrecta, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: DS MOP N°133 (adjudicación), págs. 1–2 (verificado en la imagen)
- **10** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: DS MOP N°133 (adjudicación), pág. 1 (vistos); Circulares 2, 3 y 4
- **33** (incorrecta, recuperacion, reglas): ningún chunk de la fuente quedó entre los encontrados — fuente: Bases de Licitación art. 1.1.3
- **48** (parcial, generacion, reglas): la fuente estuvo en el contexto y la respuesta quedó mal o incompleta — fuente: Convenio de Extinción, págs. 3–4; Res. 2633

## Historial

| Fecha | Set | Errores | generacion | recuperacion |
|---|---|---:|---:|---:|
| 2026-11-03 21:23 | rag | 10 | 6 | 4 |
| 2026-11-03 21:25 | rag | 10 | 5 | 3 |
| 2026-11-03 21:28 | rag | 13 | 7 | 3 |
| 2026-11-03 21:33 | rag | 10 | 6 | 4 |
| 2026-11-04 02:38 | rag_agente | 4 | 2 | 2 |
| 2026-11-04 02:41 | rag_agente | 9 | 6 | 3 |
| 2026-11-04 02:42 | rag_agente | 4 | 2 | 2 |
