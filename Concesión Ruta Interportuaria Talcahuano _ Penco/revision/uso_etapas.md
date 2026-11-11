# Tiempo, tokens y precio por etapa · Concesión Ruta Interportuaria Talcahuano – Penco (RITP)

Solo llamadas a la red (lo que salió de caché no cuenta). Detalle por modelo en `uso_etapas.csv`.

| Fecha | Etapa | Tiempo | Modelos | Llamadas | Tokens entrada | Tokens salida | De ellos razonamiento | US$ |
|---|---|---:|---|---:|---:|---:|---:|---:|
| 2026-11-11 11:45 | vlm | 0.2 min | meta:muse-spark-1.3-contributor | 3 | 12,585 | 1,480 | 1,268 | 0.0016 |
| 2026-11-11 12:00 | vlm | 14.8 min | meta:muse-spark-1.3-contributor | 483 | 2,023,481 | 488,900 | 166,880 | 0.3001 |
| 2026-11-11 12:01 | texto | 0.0 min | — | 0 | 0 | 0 | 0 | 0.0000 |
| 2026-11-11 12:04 | tablas | 3.6 min | meta:muse-spark-1.3-contributor | 91 | 401,211 | 91,921 | 36,280 | 0.0585 |
| 2026-11-11 12:07 | actos | 0.7 min | deepseek/deepseek-v4.1-flash, qwen/qwen3.7-flash | 3 | 2,084 | 2,649 | 2,568 | 0.0008 |
| 2026-11-11 12:07 | bases | 0.0 min | — | 0 | 0 | 0 | 0 | 0.0000 |
| 2026-11-11 12:07 | fragmentos | 0.0 min | — | 0 | 0 | 0 | 0 | 0.0000 |
| 2026-11-11 12:07 | relaciones (interrumpida: cambio de modelos) | 22.1 min | varios (qwen3.7-flash, deepseek-v4.1-flash, gratuitos) | 47 | 0 | 0 | 0 | 0.0453 |
| 2026-11-11 13:02 | relaciones | 31.8 min | deepseek/deepseek-v4-flash, deepseek/deepseek-v4.1-flash, dots-studio/dots-3-note-preview:free, nvidia/nemotron-3-ultra-550b-a55b:free, qwen/qwen3.7-flash, z-ai/glm-5.3-flash | 152 | 628,081 | 329,535 | 308,830 | 0.1140 |
| 2026-11-11 13:04 | vincular | 2.9 min | deepseek/deepseek-v4-flash, qwen/qwen3.7-flash | 12 | 144,264 | 10,378 | 0 | 0.0110 |
| 2026-11-11 13:05 | arbol | 0.0 min | — | 0 | 0 | 0 | 0 | 0.0000 |
| 2026-11-11 13:05 | ensamblar | 0.0 min | — | 0 | 0 | 0 | 0 | 0.0000 |
| | **Total** | **76.2 min** | | **791** | **3,211,706** | **924,863** | **515,826** | **0.5313** |

## Por modelo

| Modelo | Llamadas | Tokens entrada | Tokens salida | Razonamiento | US$ |
|---|---:|---:|---:|---:|---:|
| deepseek/deepseek-v4-flash | 81 | 404,998 | 130,693 | 112,065 | 0.0715 |
| deepseek/deepseek-v4.1-flash | 9 | 40,193 | 21,861 | 20,545 | 0.0165 |
| dots-studio/dots-3-note-preview:free | 3 | 18,440 | 9,107 | 7,346 | 0.0000 |
| meta:muse-spark-1.3-contributor | 577 | 2,437,277 | 582,301 | 204,428 | 0.3602 |
| nvidia/nemotron-3-ultra-550b-a55b:free | 3 | 17,450 | 17 | 17 | 0.0000 |
| qwen/qwen3.7-flash | 68 | 276,055 | 167,288 | 158,073 | 0.0290 |
| varios (qwen3.7-flash, deepseek-v4.1-flash, gratuitos) | 47 | 0 | 0 | 0 | 0.0453 |
| z-ai/glm-5.3-flash | 3 | 17,293 | 13,596 | 13,352 | 0.0089 |
