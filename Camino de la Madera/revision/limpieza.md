# Limpieza del documento único · CM

Reglas deterministas (sin LLM) aplicadas solo al Markdown; el árbol y los chunks siguen literales.

- Diccionario: 100,093 palabras (modelo `spa` de Tesseract).
- Vocabulario de la concesión: 6,959 palabras; 2,457 frecuentes (≥ 3 y en el diccionario).

| Regla | Cambios |
|---|---:|
| barras | 624 |
| numero | 557 |
| espacios | 206 |
| vocabulario | 96 |
| grado | 58 |
| guion_corte | 41 |
| puntos_indice | 23 |

## puntos_indice

- « . . ..... .. » → « … »
- «... .... ..... . » → « … »
- «... . . » → « … »
- «... ... » → « … »
- «... . .. .. ... » → « … »
- «... ....... » → « … »

## espacios

- «MINISTERIO DE OBRAS PUBLICAS DIRECCION GENERAL DE OBRAS PUBLICAS COORDINACION GE» → «MINISTERIO DE OBRAS PUBLICAS DIRECCION GENERAL DE OBRAS PUBLICAS COORDINACION GE»
- «1.5.48 54 1.5.50 . 56 1.5.51 o 56 1.5.52 o . 57 II. BASES TECNICAS . o. o. o... » → «1.5.48 54 1.5.50. 56 1.5.51 o 56 1.5.52 o. 57 II. BASES TECNICAS. o. o. o... o o»
- «11.1 INTRODUCCION ». . e … 9.0.0 … o... 0... 58 11.2 DEL AREA DE LA CONCESION' o» → «11.1 INTRODUCCION». . e … 9.0.0 … o... 0... 58 11.2 DEL AREA DE LA CONCESION' o »
- «11.9.4 11.9.5 11.9.6 11.9.7 11.9.8 11.9.9 Ill. BASES ECONOMICAS e o o o o o o o » → «11.9.4 11.9.5 11.9.6 11.9.7 11.9.8 11.9.9 Ill. BASES ECONOMICAS e o o o o o o o »
- «- Valor de la Unidad de Fomento: El valor de la Unidad de Fomento será el que fi» → «- Valor de la Unidad de Fomento: El valor de la Unidad de Fomento será el que fi»
- «Pregunta N°10 Cuál será la política tarifaria que aplicará el MOP para el peaje » → «Pregunta N°10 Cuál será la política tarifaria que aplicará el MOP para el peaje »

## numero

- «N* » → «N°»
- «N” » → «N°»
- «N? » → «N°»
- «N*» → «N°»
- «N'» → «N°»
- «N' » → «N°»

## vocabulario

- «gervicio» → «servicio»
- «factultad» → «facultad»
- «atorizados» → «autorizados»
- «durtante» → «durante»
- «ganeamiento» → «saneamiento»
- «actarar» → «aclarar»

## grado

- «Artículo 7”» → «Artículo 7°»
- «Artículo 16*» → «Artículo 16°»
- «Artículo 25%» → «Artículo 25°»
- «Artículo 24*» → «Artículo 24°»
- «Artículo 89%» → «Artículo 89°»
- «Artículo 26%» → «Artículo 26°»

## barras

- «|» → « »

## guion_corte

- «subcon- tratar» → «subcontratar»
- «resi- dente» → «residente»
- «by- pass» → «bypass»
- «infra- estructura» → «infraestructura»
- «entre- garán» → «entregarán»
- «ambienta- les» → «ambientales»

## Todas las correcciones por vocabulario

| Palabra OCR | Reemplazo | Veces |
|---|---|---:|
| Adenás | Además | 1 |
| Adminisirador | Administrador | 1 |
| Anbiental | Ambiental | 1 |
| Anmbiental | Ambiental | 1 |
| Asfaltico | Asfáltico | 1 |
| Concesiomario | Concesionario | 1 |
| Contretos | Contratos | 1 |
| Dbras | Obras | 1 |
| Diaño | Daño | 1 |
| Droceso | Proceso | 1 |
| Fconómicas | Económicas | 1 |
| Hidroelectrica | Hidroeléctrica | 1 |
| Malparaíso | Valparaíso | 1 |
| Nacimento | Nacimiento | 1 |
| Noyiembre | Noviembre | 1 |
| Oncepto | Concepto | 1 |
| Regiamento | Reglamento | 1 |
| Sanfiago | Santiago | 1 |
| Supramo | Supremo | 1 |
| Teajuste | Reajuste | 1 |
| Tlevar | Llevar | 1 |
| aAsfalto | asfalto | 1 |
| accines | acciones | 1 |
| actarar | aclarar | 1 |
| adjudicadción | adjudicación | 1 |
| alizado | alzado | 1 |
| antal | anual | 1 |
| aprucba | aprueba | 1 |
| ateriales | materiales | 1 |
| atorizados | autorizados | 1 |
| aturaleza | naturaleza | 1 |
| axtinción | extinción | 1 |
| camíno | camino | 1 |
| ccnvenio | convenio | 1 |
| cnergía | energía | 1 |
| comcesionario | concesionario | 1 |
| conlinuación | continuación | 1 |
| consitirá | consistirá | 1 |
| correponden | corresponden | 1 |
| cunplan | cumplan | 1 |
| cunplimiento | cumplimiento | 2 |
| delermina | determina | 1 |
| diclembre | diciembre | 1 |
| dministración | administración | 1 |
| durtante | durante | 1 |
| ecesite | necesite | 1 |
| emrgencia | emergencia | 1 |
| enplazamiento | emplazamiento | 1 |
| enpréstitos | empréstitos | 1 |
| eslipulado | estipulado | 1 |
| específicado | especificado | 1 |
| excedenles | excedentes | 1 |
| explolar | explotar | 1 |
| factultad | facultad | 1 |
| fómula | fórmula | 1 |
| ganeamiento | saneamiento | 1 |
| gervicio | servicio | 1 |
| havan | hayan | 1 |
| icluyendo | incluyendo | 1 |
| instpector | inspector | 1 |
| júlio | julio | 1 |
| lasificación | clasificación | 1 |
| lítulo | título | 1 |
| motario | notario | 1 |
| nanera | manera | 1 |
| nateriales | materiales | 1 |
| ncluyen | incluyen | 1 |
| nodificados | modificados | 1 |
| norbre | nombre | 1 |
| nuneral | numeral | 1 |
| nábil | hábil | 1 |
| olorgar | otorgar | 1 |
| oncesionario | concesionario | 1 |
| operción | operación | 1 |
| parlir | partir | 2 |
| piazo | plazo | 1 |
| prblicación | publicación | 1 |
| ptico | óptico | 1 |
| púbiica | pública | 1 |
| quíen | quien | 1 |
| repecto | respecto | 1 |
| resentes | presentes | 1 |
| réchazo | rechazo | 1 |
| sanamiento | saneamiento | 1 |
| sjemplo | ejemplo | 1 |
| supervición | supervisión | 1 |
| tamto | tanto | 1 |
| vaior | valor | 2 |
| Íftem | Ítem | 1 |
| Íngresos | Ingresos | 1 |
| Íítem | Ítem | 1 |
| índique | indique | 1 |
| úblicas | públicas | 1 |

