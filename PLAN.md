# Plan · Documento único conectado y chunks para vectorizar (MT-v3)

## Objetivo

A partir de los PDF de una concesión (Bases, circulares, decretos, convenios, resoluciones, reglamentos) se genera:

- **`nodos_versionados.json`**: árbol de nodos con versiones. Es la fuente única de verdad.
- **`DOCUMENTO_UNICO_<ID>.md`**: vista de lectura. Es un solo documento donde todos los fragmentos están conectados en su artículo o en su tema. No hay anexos ni hipervínculos.
- **`CHUNKS_<ID>.jsonl`**: chunks para vectorizar. Cada uno lleva su ruta jerárquica como contexto y sus metadatos aparte.
- **`TABLAS_<ID>.json` + `TABLAS_<ID>.sqlite`**: tablas transcritas con visión, consultables por valor exacto.
- **`EMBEDDINGS_<ID>.npz`** + `consultar`: vectores de los chunks y RAG con respuesta citada.

Pilotos: **RITP** (Ruta Interportuaria Talcahuano–Penco: 14 PDF, 486 págs.) para validar y **TERB** (Ruta 5 Temuco–Río Bueno: 26 PDF, 929 págs.).

## Principios

1. **Solo cortar y pegar.** Ningún texto se reescribe ni se interpreta. El LLM decide solo dónde va cada fragmento literal y qué cambia una circular (líneas y párrafos).
2. **Solo las circulares reescriben las Bases**, con 4 tipos de cambio: reemplazar un párrafo, cambiar un texto ("donde dice / debe decir"), agregar y eliminar. Los decretos, convenios y resoluciones modifican el contrato: sus fragmentos van completos al artículo que tratan o a su tema.
3. **Versiones.** Cada inciso guarda su versión vigente y las anteriores, con fecha, acto y páginas. Las versiones anteriores también se vectorizan, con `vigente: false`.
4. **Literalidad verificable.** Cada línea no marginal de cada documento aparece exactamente una vez en el árbol.
5. **Modelos baratos.** Qwen-flash o DeepSeek para clasificar y aplicar cambios; Qwen3-VL para visión. El saldo real de la clave se vigila con `/api/v1/key`.

## Etapas (`python main.py <etapa> --proyecto X`)

| # | Etapa | Qué hace | Salida |
|---|---|---|---|
| 1 | `ocr`, `marcar`, `vlm --openrouter` | Tesseract; páginas mal leídas; rescate con Qwen3-VL | `cache/ocr`, `cache/vlm`, `revision/paginas_marcadas.csv` |
| 2 | `texto` | Texto final por página (OCR o visión), párrafos y marginales | `cache/texto` |
| 2b | `tablas` | Páginas con tablas → visión → **JSON validado** (el texto de la página no cambia) | `cache/tablas`, `revision/paginas_tabla.csv` |
| 3 | `actos` | Actos de cada PDF (separa las circulares), fecha y orden | `revision/actos.csv` |
| 4 | `bases` | Artículos de las Bases contrastados con su índice | `cache/estructura`, `revision/estructura_bases.csv` |
| 5 | `fragmentos` | Corte de cada acto por sus puntos | `cache/fragmentos` |
| 6 | `relaciones` | LLM: cambios de las circulares aplicados al texto de las Bases | `revision/relaciones.csv` |
| 8 | `vincular` | LLM barato: artículo, tema o formalidad de cada fragmento restante | `revision/vinculos.csv` |
| 9 | `arbol` | Árbol de nodos versionados + tablas JSON/SQLite | `nodos_versionados.json`, `TABLAS_*` |
| 10 | `ensamblar` | Vista de lectura desde el árbol | `DOCUMENTO_UNICO_<ID>.md` |
| 11 | `chunks` | Chunks desde el árbol | `CHUNKS_<ID>.jsonl` |
| 12 | `embeddings` | Vectores `baai/bge-m3` de los chunks a embeber; reutiliza los ya hechos | `EMBEDDINGS_<ID>.npz` |
| 13 | `verificar` | Literalidad, chunks, tablas y vectores | `revision/verificacion.md` |
| — | `consultar "…"` | RAG híbrido (vectores + BM25): recupera chunks (vigentes por defecto) y responde citando | consola |
| — | `evaluar` | RAG contra respuestas revisadas a mano, con juez barato | `revision/evaluacion_rag.csv` |

## Chunking (desde el árbol, no desde el Markdown)

- **Un chunk = un nodo.** Es un artículo con su texto vigente; si pasa de ~2 000 caracteres, un chunk por inciso, con traslape de la oración anterior.
- **Contexto.** Cada chunk lleva delante su ruta: "Bases de Licitación › 1. BASES ADMINISTRATIVAS › … › artículo 1.4.1 … › inciso 2".
- **Metadatos aparte:** `id_nodo`, concesión, documento, acto, páginas, `fecha_vigencia_desde`/`hasta`, `vigente`, tema y líneas.
- **Tablas.** Se guarda un chunk resumen con título, ubicación, columnas y número de filas; las filas van a SQLite (`valor_numerico` normalizado).
- **Formalidades e instrucciones:** `embeber: false`.

## Revisión manual (lo editado a mano manda)

- `revision/actos.csv`: fechas y números de los actos (`origen` = `manual`).
- `revision/estructura_correcciones.csv`: encabezados de las Bases.
- `revision/relaciones_correcciones.csv`: cambios de las circulares.
- `revision/vinculos_correcciones.csv`: destino de cada fragmento (`fragmento;destino`).
- `revision/paginas_tabla.csv`: qué páginas con tabla se procesan (`incluir`).
- `revision/texto_fuente.csv`: OCR o visión por página.
