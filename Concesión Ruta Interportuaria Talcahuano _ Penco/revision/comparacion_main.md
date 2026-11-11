# Comparación con main · RITP (visión Muse Spark, sin OCR)

**main** = OCR de Tesseract + Qwen3-VL solo en las páginas mal leídas. **Spark** (rama `prueba-con-spark`) = Muse Spark 1.3-contributor (API de Meta) en las 486 páginas, sin texto del OCR. Las relaciones y `vincular` usan OpenRouter en ambas.

## Árbol de nodos (`nodos_versionados.json`)

| | main | Spark | Diferencia |
|---|---:|---:|---:|
| **Nodos totales** | 2,390 | 2,536 | +146 |
| `inciso` | 1,662 | 1,634 | -28 |
| `articulo` | 279 | 290 | +11 |
| `acto_posterior` | 204 | 284 | +80 |
| `tabla` | 45 | 92 | +47 |
| `formalidad` | 75 | 88 | +13 |
| `instruccion` | 69 | 77 | +8 |
| `capitulo` | 33 | 33 | +0 |
| `aclaracion` | 4 | 17 | +13 |
| `tema` | 6 | 7 | +1 |
| `parte` | 7 | 7 | +0 |
| `inciso_eliminado` | 2 | 3 | +1 |
| `raiz` | 2 | 2 | +0 |
| `ficha` | 1 | 1 | +0 |
| `portada` | 1 | 1 | +0 |
| Versiones de texto | 2,362 | 2,390 | +28 |
| Nodos con más de una versión (modificados) | 64 | 75 | +11 |

## Relaciones (cambios de las circulares aplicados a las Bases)

| Estado | main | Spark |
|---|---:|---:|
| aplicada | 88 | 106 |
| pendiente | 20 | 10 |
| nota | 13 | 23 |
| anexo | 10 | 14 |
| Artículos modificados | 66 | 73 |

- Solo en Spark: 1.13.3, 1.14.7, 1.8.17.1, 1.8.3, 1.9.7, 2.2.3.2, 2.7, 3.2, A2
- Solo en main: 2.8, A1

## Otros

| | main | Spark |
|---|---:|---:|
| Fragmentos de los actos | 356 | 477 |
| Fragmentos que citan artículos de las Bases | 160 | 215 |
| Filas en `vinculos.csv` | 279 | 372 |
| Tablas (`TABLAS_RITP.json`) | 45 | 92 |
| Documento único (líneas / MB) | 10,558 / 1.48 | 10,514 / 1.56 |
| Líneas útiles de las Bases (D01) | 7.761 | 5.958 |

`CHUNKS_RITP.jsonl` y `EMBEDDINGS_RITP.npz` siguen siendo los de main (no se regeneraron) y `verificar` no se corrió.
