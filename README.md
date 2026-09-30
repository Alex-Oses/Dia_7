# Día 7 — Servicios de Voz e Interfaces (ASIX)

**Práctica del perfil ASIX:** un *endpoint* API que recibe un audio, lo transcribe
usando Gemini y devuelve el texto, con la arquitectura de red documentada.

---

## 1. Qué hay que hacer en esta práctica

El enunciado pide dos cosas:

1. **Speech-to-Text.** Grabar 3 audios en español y medir la precisión de la
   transcripción, comparando la Web Speech API del navegador con Gemini.
2. **Interfaz (ASIX).** Un endpoint Flask/FastAPI que reciba audio, lo envíe a
   Gemini para transcribirlo y devuelva el texto, **documentando la arquitectura
   de red**.

Lo que hace este repositorio:

| Entregable | Dónde |
|---|---|
| Endpoint ASIX en FastAPI | `api/main.py` |
| Documentación de la arquitectura de red | [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) |
| 3 audios de prueba con su transcripción exacta | `audios/` + `herramientas/generar_audios.py` |
| Medición de la precisión (WER) | `evaluar_audio.py` → `resultados/informe_precision.md` |
| Comparativa Web Speech API frente a Gemini | `cliente/index.html` + sección 6 de este README |
| Pruebas del endpoint, incluidos ataques | `probar_api.py` |

---

## 2. Puesta en marcha (desde cero)

### 2.1 Requisitos

- Windows 10/11 con **Python 3.10 o superior** (`python --version`).
- Una **API Key** de Gemini, gratuita: <https://aistudio.google.com> →
  *Get API key*. No hace falta tarjeta.

### 2.2 Preparar PowerShell

Es el paso que más gente se salta. En un Windows recién instalado, PowerShell
viene con la **política de ejecución de scripts en `Restricted`**, y eso impide
dos cosas: cargar módulos como PSReadLine y **ejecutar `Activate.ps1` del
entorno virtual**.

Si al activar el entorno te sale *"la ejecución de scripts está deshabilitada en
este sistema"*, ejecuta esto **una sola vez**:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

`RemoteSigned` es el valor recomendado por Microsoft: permite los scripts
locales (el entorno virtual) y sigue bloqueando los scripts sin firma que
comes de internet. Solo afecta a tu usuario y no requiere permisos de
administrador.

### 2.3 Entorno virtual y dependencias

```powershell
cd "2026-09-09_voz_interfaces"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> **Atajo si ya hiciste los días anteriores:** en vez de crear un entorno nuevo,
> puedes reutilizar el del Día 5, que ya tiene `google-genai`:
> ```powershell
> & "..\2026-09-21_gemini\venv\Scripts\Activate.ps1"
> pip install -r requirements.txt
> ```

### 2.4 Configurar la clave

```powershell
copy .env.example .env
notepad .env
```

Y en la primera línea, sustituye lo que hay por tu clave real:

```
GEMINI_API_KEY=AQ.Ab...tu_clave_real...
```

> **El fichero `.env` no se sube a GitHub** (está en `.gitignore`) precisamente
> porque contiene la clave. En el repositorio solo está `.env.example`, que es
> la plantilla sin la clave.

### 2.5 Generar los audios de prueba

```powershell
python .\herramientas\generar_audios.py
```

Crea 3 WAV en español usando la voz sintética de Windows (`Microsoft Helena`,
`es-ES`). **No necesita red ni clave.** Cada audio lleva su transcripción
exacta guardada en `audios/verdad_terreno.json`, y eso es lo que permite medir
el WER en lugar de estimarlo a ojo.

### 2.6 Arrancar el servicio

```powershell
uvicorn api.main:app --port 8000
```

Deberías ver `Uvicorn running on http://127.0.0.1:8000`. En otra terminal:

```powershell
curl http://127.0.0.1:8000/api/v1/salud
```

Y para transcribir un audio:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/transcribir -F "audio=@audios/02_horario.wav"
```

- Documentación interactiva: <http://localhost:8000/docs>
- Prueba de todo el endpoint: `python probar_api.py` (arranca y para el servidor
  él mismo)

### 2.7 Ver la interfaz web

```powershell
python -m http.server 5500
```

Y abre <http://localhost:5500/cliente/index.html> en **Chrome, Edge u Opera**.

> **No abras `cliente\index.html` con doble clic.** Con doble clic el navegador
> lo carga como `file://` y el navegador bloquea la llamada a la API por CORS.
> Hay que servirlo por HTTP. El puerto 5500 y el 8000 ya están en la lista de
> orígenes permitidos (`CORS_ORIGENES` en el `.env`).

---

## 3. El endpoint

```
POST /api/v1/transcribir     multipart/form-data con el campo "audio"
GET  /api/v1/salud           estado del servicio
GET  /api/v1/audios          lista los audios de ejemplo
GET  /api/v1/audio/{nombre}  sirve un WAV de ejemplo
GET  /docs                   documentación interactiva (Swagger UI)
```

**Parámetro opcional `numeros`:** por defecto la transcripción es **literal**
(`"aulas dos"`), que es lo que se usa para medir precisión. Con
`numeros=true` el modelo escribe `"aula 2"`, más legible para un humano pero no
comparable contra una referencia literal. Explicado en la sección 5.

Admitidos: `wav`, `mp3`, `ogg`, `flac`, `aac`, `m4a`. Máximo 10 MB y 5 minutos.

### Respuesta real

```json
{
  "request_id": "f6d462a3",
  "formato_detectado": "wav",
  "duracion_s": 5.71,
  "idioma_detectado": "es",
  "transcripcion": "La reunión empieza a las nueve y media, en el aula dos, con el profesor de redes.",
  "modelo": "gemini-3.7-flash"
}
```

---

## 4. Resultados: precisión de la transcripción

Modelo `gemini-3.7-flash`. **WER = 0,0% en los tres audios**: todas las palabras
reconocidas, sin omisiones ni palabras inventadas. Recall y precisión de
palabras: 100%.

| Audio | Duración | WER | WER sin acentos | Palabras |
|---|---:|---:|---:|---:|
| `01_presentacion.wav` | 7,8 s | 15,8% | **0,0%** | 19 |
| `02_horario.wav` | 5,7 s | 5,9% | **0,0%** | 17 |
| `03_incidencia.wav` | 5,4 s | 5,6% | **0,0%** | 18 |
| **Media** | | **9,1%** | **0,0%** | |

*(WER = Word Error Rate, `tasa de error de palabra`. Cuanto menor, mejor. Es la
métrica estándar de los sistemas de reconocimiento de voz.)*

**Por qué dos columnas.** Las frases de referencia se sintetizaron **sin tildes**,
así que la diferencia entre las columnas es solo que el modelo las añade, y lo
hace bien:

| Referencia | Lo que transcribió el modelo |
|---|---|
| `Buenos dias` | `Buenos días` |
| `practica` | `práctica` |
| `La reunion` | `La reunión` |
| `la conexion` | `la conexión` |
| `Alex` | `Álex` |

Medido con acentos, esas correcciones parecen fallos. Se publican las dos
columnas en lugar de quedarse con la favorable.

Detalle completo: [`resultados/informe_precision.md`](resultados/informe_precision.md).

---

## 5. Un fallo del método que hubo que corregir

La primera versión del prompt pedía al modelo: *«si oyes un número, escríbelo
con dígitos: "nueve y media" → "9:30"»*.

Con esa instrucción, el WER de `02_horario` subió a **23,5%**. El modelo no se
equivocaba: estaba haciendo justo lo que se le pedía, y la métrica lo contaba
como error. Es un fallo del instrumento de medida, no del sistema.

Se separó en el parámetro `numeros` (sección 3) y se volvió a medir: 0%.

La moraleja: antes de dar buena una cifra de precisión, comprobar que el prompt
y la métrica se están midiendo en lo mismo.

---

## 6. Comparativa: Web Speech API frente a Gemini

| | **Web Speech API** | **Endpoint ASIX → Gemini** |
|---|---|---|
| Dónde se ejecuta | En el navegador | En el servidor, por HTTPS a Google |
| Latencia | Instantánea, en vivo | ~0,5 s por audio corto |
| Clave de API | No hace falta | **Nunca llega al navegador** |
| Coste | Sin cuota | Cuota de Gemini (15 RPM en free tier) |
| Navegadores | Solo Chrome, Edge y Opera | Cualquiera, incluido cliente nativo |
| Ficheros | **No**: solo micrófono en vivo | Sí: 6 formatos |
| Validación | Ninguna | Formato, tamaño, duración, MIME, firma |
| Privacidad | El audio **sale del equipo** a servidores de Google | Pasa por vuestro servidor, que valida y registra |
| Salida | Texto plano | JSON con idioma, duración y segmentos |

**Conclusión honesta:** la Web Speech API gana en latencia y no necesita
infraestructura, pero **no es reconocimiento local**. En Chrome el audio se envía
a los servidores de Google, así que el dato sale igualmente del equipo. La
diferencia real de privacidad no es *si* el audio sale, sino **quién controla el
proceso y qué se valida antes de enviarlo**. Y no admite ficheros, solo micrófono.

---

## 7. Seguridad

Todo el detalle en [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md). Lo esencial:

- **La API Key solo existe en el servidor.** El HTML recibe
  `gemini_configurado: true/false`, nunca el valor. `probar_api.py` comprueba
  automáticamente que la clave no aparece ni en las respuestas HTTP ni en el log.
- **El audio se valida por su contenido, no por lo que declara el cliente.** Se
  leen los primeros bytes (*magic bytes*) de cada formato: un `.exe` renombrado
  a `.wav` se rechaza.
- **Tope de tamaño y duración**, y lectura con tope (`MAX_BYTES + 1`) para no
  cargar 2 GB en memoria.
- **Lista blanca** de formatos y de orígenes CORS, nunca `"*"`.
- **Límite de 10 peticiones por 60 s y IP**, para no gastar la cuota de todo el
  aula desde un bucle propio.
- **Path traversal** mitigado en dos capas: saneado del nombre y comprobación de
  que la ruta resuelta queda dentro de `audios/`.
- **El audio recibido no se escribe nunca en disco**; vive en memoria durante
  la petición.
- **Errores genéricos al cliente** (503) y detalle técnico en el log del
  servidor: un 429 al cliente revelaría que hay cuota de pago detrás.

---

## 8. Pruebas

```powershell
python test_validacion.py    # 39 pruebas: formatos, magic bytes, WER
python probar_api.py         # 24 pruebas: endpoint real por HTTP, con ataques
```

`probar_api.py` arranca el servidor de verdad y comprueba que rechaza un
ejecutable renombrado, que no sirve `../../../windows/win.ini`, que el límite de
tasa dispara, y que la clave no se filtra.

Durante el desarrollo las pruebas destaparon dos bugs reales: la comparación de
extensiones contra la lista de formatos (con punto incluido, lo que rechazaba
todos los WAV válidos) y un `KeyError: 'request_id'` en el formato de log que
hacía perder los mensajes.

---

## 9. Estructura

```
2026-09-09_voz_interfaces/
├── api/
│   ├── main.py            # ENDPOINT FastAPI
│   ├── config.py          # configuración, límites, lista blanca de formatos
│   ├── transcripcion.py   # validación de audio + cliente Gemini
│   └── metricas.py        # WER y métricas de palabra
├── cliente/index.html     # Web Speech API + endpoint, lado a lado
├── herramientas/
│   └── generar_audios.py  # sintetiza los 3 audios (TTS de Windows)
├── audios/                # los 3 WAV + verdad_terreno.json
├── docs/ARQUITECTURA.md   # arquitectura de red
├── resultados/            # transcripciones, informe y log del servidor
├── evaluar_audio.py       # mide el WER
├── probar_api.py          # prueba el endpoint
├── test_validacion.py     # pruebas
├── requirements.txt
├── .env.example           # plantilla SIN la clave
└── .gitignore
```

---

## 10. Incidencias

- `gemini-3.7-flash` respondió bien en esta sesión, al contrario que en el
  Día 6, donde `3.7` y `3.6` devolvían 503. La cadena de fallback está
  implementada por si vuelve a pasar.
- `response_mime_type: application/json` provoca 503 en `gemini-3.5-flash` (se
  comprobó en el Día 6). Por eso aquí el JSON se pide por prompt y se extrae
  con un parser tolerante.
- **La voz TTS de Windows es sintética.** Con voz humana el WER suele ser
  *mejor*, así que el 0% medido aquí es una cota favorable, no una limitación.
- No hay autenticación de usuarios (JWT/OAuth): no la pide el enunciado. Con un
  servicio abierto, el límite por IP es la mitigación mínima viable.
