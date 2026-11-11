# Muse Spark vs caché de visión · Concesión Ruta Interportuaria Talcahuano – Penco (RITP)

| | |
|---|---|
| Modelo comparado | `muse-spark-1.3-contributor` contra la caché (`qwen/qwen3-vl-30b-a3b-instruct`) |
| Páginas de texto | 1 · similitud media 0.979 · números distintos: solo Qwen 0, solo Spark 2, comunes 53 |
| Páginas de tablas | 1 · acuerdo de celdas medio 0.933 · acuerdo numérico medio 0.75 · JSON inválido de Spark: 0 |
| Spark | costo US$ 0.0012 · 11.9 s por página · truncadas 0 · errores 0 |

*Similitud* = parecido por palabras (0–1). *Acuerdo* = celdas en la misma posición con parecido ≥ 90. Un desacuerdo no dice quién acierta: hay que mirar la imagen (ver `comparacion.html`).

## Por página

- **texto RITP.D01 p5** (conf. OCR 0.286): similitud 0.979 · palabras Qwen/Spark 285/344 · números solo Qwen 0, solo Spark 2 · 13.4 s · stop
- **tablas RITP.D01 p22**: tablas 1/1 · filas 5/5 · celdas iguales 14/15 · números iguales 3/4 · 10.4 s
