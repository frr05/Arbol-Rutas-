# MT-v3 · Documento único y chunks de concesiones DGC

Convierte los PDF de una concesión (Bases, circulares, decretos, convenios, resoluciones…) en:
- **`nodos_versionados.json`**: árbol de nodos con versiones. Es la fuente única.
- **`DOCUMENTO_UNICO_<ID>.md`**: un solo documento para leer, con todos los fragmentos conectados en su artículo o tema.
- **`CHUNKS_<ID>.jsonl`**: chunks para vectorizar, con su ruta como contexto y sus metadatos aparte.
- **`TABLAS_<ID>.json` / `.sqlite`**: todas las tablas, transcritas con visión directamente a JSON validado; se consultan por valor exacto.
- **`EMBEDDINGS_<ID>.npz`**: vectores de los chunks (`baai/bge-m3`), versionados en git para no pagarlos de nuevo.
- **`python main.py consultar`**: RAG sobre esos vectores, con respuesta citada.

Todo el texto es literal de los PDF (OCR con Tesseract y transcripción con Qwen3-VL en páginas difíciles y tablas). El LLM solo decide dónde va cada fragmento. Detalle en `PLAN.md`.

## Datos y organización

Se usan los mismos PDF y la misma organización que en `doc-unico` y `doc-unico-v2`: una carpeta por concesión en la raíz, con su `INDICE.md` y sus subcarpetas por etapa. Los documentos se identifican con los mismos IDs (`TERB.D01` = primer documento del INDICE de Temuco–Río Bueno). **Todo lo que se genera queda dentro de la carpeta de la concesión.** El MVP trabaja con una concesión vial: Temuco–Río Bueno (TERB). El plan completo está en `PLAN.md`.

```
Concesión Ruta 5 Tramo Temuco _ Río Bueno/
├── INDICE.md
├── 1_licitacion/ … 5_otros_documentos/   PDFs originales
├── DOCUMENTO_UNICO_TERB.html       el documento único para leer en el navegador (Bases vigentes + anexos)
├── DOCUMENTO_UNICO_TERB.md         Bases vigentes con los cambios de las circulares aplicados y citados
├── DOCUMENTO_UNICO_TERB_ANEXOS.md  texto completo de los demás actos, en orden cronológico
├── DOCUMENTO_UNICO_TERB.jsonl      un registro por bloque con sus líneas (para consultas futuras)
├── cache/
│   ├── ocr/TERB.D01/p0001.json …   una página por archivo + _ocr.json (registro)
│   ├── vlm/TERB.D01/p0005.json …   texto del modelo de visión junto al texto del OCR
│   ├── texto/TERB.D01.json         líneas numeradas finales (la clave de todo lo que sigue)
│   ├── estructura/bases.json       artículos de las Bases con sus párrafos
│   ├── fragmentos/TERB.D02.json    cortes de cada acto
│   └── llm/                        respuestas del LLM (caché: repetir no gasta cuota)
└── revision/                       todo revisable y corregible a mano (separador ;)
    ├── calidad_ocr.md, paginas_marcadas.csv, comparacion_vlm.md
    ├── actos.csv, estructura_bases.csv, relaciones.csv, pendientes.csv
    ├── limpieza.md                 qué cambió la limpieza del documento único (reglas y palabras corregidas)
    └── verificacion.md
mt/            código (config, indice, ocr, calidad, marcar, vlm, texto, actos, numerales, bases,
               fragmentos, llm, relaciones, consolidar, limpieza, ensamblar, verificar)
main.py        línea de comandos
colab_rescate_vlm.ipynb
```

Cada `pNNNN.json` del OCR tiene el mismo formato de página que `doc-unico-v2`: los items `linea` llevan `texto`, `bbox` en píxeles a 300 DPI y `conf` entre 0 y 1. Además trae:

- `texto`: la página completa.
- `rotacion`: si se giró la página para leerla.
- `calidad`: un diccionario con estas métricas:
  - `conf_media`: confianza media, ponderada por el largo de cada palabra.
  - `frac_baja`: fracción de palabras con confianza menor que 0,60.
  - `frac_rara`: fracción de palabras que no parecen español.
  - `n_palabras`.
  - `tinta`: fracción de píxeles oscuros. Sirve para distinguir una página en blanco de una mal leída.

## Instalación

```bash
pip install -r requirements.txt
# Tesseract con español: Linux/Colab `apt-get install tesseract-ocr tesseract-ocr-spa`;
# Windows: instalador de UB Mannheim (marcar Spanish) y, si no queda en el PATH, TESSERACT_CMD en .env
```

## Uso

### Documento único

```bash
python main.py todo --proyecto RITP          # texto → tablas → … → arbol → ensamblar → chunks → embeddings → verificar
python main.py relaciones --proyecto TERB    # solo la etapa con LLM (reanudable; lo ya respondido está en caché)
python main.py ensamblar --proyecto TERB     # rehace el documento sin llamar al LLM (p. ej. tras corregir CSV)
python main.py verificar --proyecto TERB
python main.py vlm --proyecto TERB --openrouter   # rescate de páginas mal leídas con Qwen3-VL vía OpenRouter
```

**Qué modifica las Bases.** Solo las circulares aclaratorias reescriben el texto de las Bases: sus cambios se aplican en cada artículo, que guarda sus versiones anteriores. Los decretos, convenios y resoluciones posteriores a la adjudicación modifican el contrato (obras, plazos, pagos): cada fragmento va completo, literal, bajo el artículo que trata o en una sección por tema (`vincular`). Si se trata a esos actos como modificaciones, el LLM confunde las cláusulas numeradas de los convenios ("3.3", "2.12") con artículos de las Bases.

Necesita `OPENROUTER_API_KEY` en `.env` (ver `.env.example`). Primero usa modelos gratuitos y, si se agotan, modelos de pago hasta `LLM_TOPE_USD`. Lo que se corrige a mano en `revision/` (`actos.csv`, `estructura_correcciones.csv`, `relaciones_correcciones.csv`) manda sobre la detección y el LLM; para aplicarlo basta con `ensamblar`.

### RAG

```bash
python main.py embeddings --proyecto RITP    # vectoriza los chunks nuevos o cambiados (el resto se reutiliza)
python main.py consultar "¿Cuál es el plazo de la concesión?" --proyecto RITP
python main.py consultar "multas por atraso" --proyecto RITP --solo-buscar --k 10   # solo recuperar
python main.py consultar "¿Qué decía antes el artículo 1.6.3?" --proyecto RITP --anteriores
```

La búsqueda es híbrida: vectores más BM25 por palabras exactas, fusionados con RRF. Filtra `vigente = true` salvo `--anteriores` y pone primero los chunks del artículo citado en la pregunta. Cada fila de tabla es un chunk propio. Al texto OCR se le adjuntan las tablas de visión de su página, y la ficha de actos siempre va en el contexto para las preguntas de conteo.

`python main.py evaluar --proyecto RITP` responde las preguntas de `revision/preguntas_rag.csv` y un juez barato las compara con las respuestas revisadas → `revision/evaluacion_rag.csv`. Resultados (correctas / parciales / incorrectas de 50): RITP 36 / 8 / 6 y Camino de la Madera 33 / 10 / 7. Camino de la Madera (CM) no se usó para ajustar el sistema; partió en 29 / 10 / 11. Cambiar el prompt o el chunking mueve el resultado ±3 preguntas, así que las decisiones se toman con el total de las dos concesiones. Para usar los vectores en otra base vectorial: `ids` y `vectores` de `EMBEDDINGS_<ID>.npz` (normalizados, float16) se unen por `id` con `CHUNKS_<ID>.jsonl`, que trae el texto y los metadatos.

### OCR

```bash
python main.py estado                          # avance por documento: OCR / marcadas / rescatadas
python main.py ocr --proyecto TERB             # paso 1 (reanudable; no repite documentos ya hechos)
python main.py ocr --doc TERB.D01 --paginas 5-8 --forzar   # rehacer algunas páginas
python main.py marcar --proyecto TERB          # paso 2 con los umbrales por defecto
python main.py marcar --umbral 0.6 --frac-baja 0.4         # … o con otros
python main.py vlm --proyecto TERB --limite 3  # paso 3 (necesita GPU; ver el notebook)
python main.py comparar                        # rehace el informe OCR vs modelo
```

El paso 3 se corre en Colab con `colab_rescate_vlm.ipynb`. Ese notebook clona el repo, rescata las páginas marcadas y sube los resultados al terminar cada documento. Las dependencias del modelo están en `requirements-vlm.txt`.

### Regla de marcado (paso 2)

Una página con tinta se marca si cumple **cualquiera** de estas condiciones:

| Condición | Valor por defecto | Opción en la línea de comandos |
|---|---|---|
| Confianza media | < 0,70 | `--umbral` |
| Palabras con confianza baja | > 30 % | `--frac-baja` |
| Palabras que no parecen español | > 25 % | `--frac-rara` |
| Cantidad de palabras | < 15 | `--min-palabras` |

Los mismos umbrales se pueden fijar en `.env` (ver `.env.example`). `revision/calidad_ocr.md` muestra cuántas páginas se marcarían con cada umbral de confianza, para calibrarlo.
