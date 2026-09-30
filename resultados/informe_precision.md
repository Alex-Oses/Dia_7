# Precision de la transcripcion de voz (Dia 7 - ASIX)

Modelos: gemini-3.7-flash  
Audios evaluados: **3**

## Resultado global

| Metrica | Valor |
|---|---|
| WER medio (sensible a acentos) | **9.1%** |
| WER medio (sin acentos) | **0.0%** |
| Recall de palabras | 100.0% |
| Precision de palabras | 100.0% |

> **Por que dos WER.** Las frases de referencia se sintetizaron sin tildes (`practica`), pero el modelo devuelve la ortografia correcta (`práctica`). Medido con acentos, esa mejora cuenta como dos errores de palabra. El WER sin acentos aísla los errores de vocabulario reales. Se publican los dos en vez de quedarse con el mas favorable.

## Detalle por audio

| Audio | Dur. | WER | WER s/acentos | S | I | D | Palabras ref. |
|---|---:|---:|---:|---:|---:|---:|---:|
| `01_presentacion.wav` | 7.79s | 15.8% | 0.0% | 3 | 0 | 0 | 19 |
| `02_horario.wav` | 5.71s | 5.9% | 0.0% | 1 | 0 | 0 | 17 |
| `03_incidencia.wav` | 5.44s | 5.6% | 0.0% | 1 | 0 | 0 | 18 |

## Transcripciones

### `01_presentacion.wav`
- **Nota**: Presentacion personal. Frase larga con nombre propio y pausa natural.
- **Idioma detectado**: es | **Modelo**: gemini-3.7-flash
- **Segmentos devueltos**: 1
- **Referencia**  : Buenos dias, me llamo Alex y hoy presento la practica de servicios de voz para el grupo de sistemas.
- **Transcripcion**: Buenos días, me llamo Álex y hoy presento la práctica de servicios de voz para el grupo de sistemas.

Diferencias palabra a palabra:
  - posicion 2: referencia `dias` / obtenida `días`
  - posicion 5: referencia `alex` / obtenida `álex`
  - posicion 10: referencia `practica` / obtenida `práctica`

### `02_horario.wav`
- **Nota**: Cita con hora y lugar. Comprueba que los numeros se transcriben bien.
- **Idioma detectado**: es | **Modelo**: gemini-3.7-flash
- **Segmentos devueltos**: 1
- **Referencia**  : La reunion empieza a las nueve y media, en el aula dos, con el profesor de redes.
- **Transcripcion**: La reunión empieza a las nueve y media, en el aula dos, con el profesor de redes.

Diferencias palabra a palabra:
  - posicion 2: referencia `reunion` / obtenida `reunión`

### `03_incidencia.wav`
- **Nota**: Parte de incidencia. Frase realista del ambito ASIX, con varios terminos tecnicos.
- **Idioma detectado**: es | **Modelo**: gemini-3.7-flash
- **Segmentos devueltos**: 2
- **Referencia**  : El servidor del aula no funciona, hemos perdido la conexion a internet desde las nueve de la mañana.
- **Transcripcion**: El servidor del aula no funciona, hemos perdido la conexión a internet desde las nueve de la mañana.

Diferencias palabra a palabra:
  - posicion 10: referencia `conexion` / obtenida `conexión`
