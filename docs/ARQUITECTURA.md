# Arquitectura de red — Día 7 (ASIX)

Documento del servicio de transcripción de voz: qué procesos hay, cómo se
comunican, por qué están en esa máquina y qué pasa cuando algo falla.

---

## 1. Diagrama general

```
┌──────────────────────────────────────────────────────────────────────┐
│ CLIENTE (navegador)                                                  │
│                                                                      │
│  cliente/index.html                                                  │
│   ├── Web Speech API ─────────────────┐  (audio del micrófono)      │
│   └── fetch() multipart/form-data ───┤  (fichero de audio)          │
└───────────────────────────────────────┼──────────────────────────────┘
                                        │  HTTP/1.1 por la RED LOCAL
                                        │  puerto 8000
                                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ SERVIDOR ASIX  (esta máquina, 127.0.0.1)                             │
│                                                                      │
│  uvicorn  ──>  FastAPI  api/main.py                                  │
│                  ├── GET  /api/v1/salud          estado             │
│                  ├── GET  /api/v1/audios         lista ejemplos      │
│                  ├── GET  /api/v1/audio/{n}      sirve un WAV        │
│                  └── POST /api/v1/transcribir    recibe y transcribe │
│                          │                                           │
│                          ├── validacion  (firma del formato, tamaño,  │
│                          │             duración, MIME, extensión)     │
│                          ├── CORS       (lista de orígenes)          │
│                          ├── rate limit (10 peticiones/60 s por IP)   │
│                          ├── id de petición (trazabilidad en el log)  │
│                          └── api/transcripcion.py                     │
│                                  │                                    │
│                                  ├── api/config.py   lee .env         │
│                                  │                     │              │
│                                  │                     ▼              │
│                                  │              GEMINI_API_KEY       │
│                                  │              (solo en memoria)    │
│                                  │                                    │
│                                  └── api/metricas.py  (WER)           │
│                                                                      │
│  resultados/.cache/   respuestas ya pagadas, por hash del audio     │
│  audios/              los 3 audios de prueba                       │
└──────────────────────────────────────┬───────────────────────────────┘
                                       │  HTTPS/443 saliente
                                       │  cabecera x-goog-api-key
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Google Gemini API   generativelanguage.googleapis.com              │
│  (fuera de la red local, en Internet)                                │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. Tabla de flujos

| # | Origen | Destino | Protocolo | Puerto | Qué viaja |
|---|---|---|---|---|---|
| 1 | Navegador | Servidor ASIX | HTTP/1.1 | 8000 (TCP) | `multipart/form-data` con el audio |
| 2 | Navegador | Servidor ASIX | HTTP/1.1 OPTIONS | 8000 | Preflight CORS (si el origen no es el mismo) |
| 3 | Servidor ASIX | Gemini | HTTPS (TLS 1.3) | 443 | Audio en base64 + prompt |
| 4 | Gemini | Servidor ASIX | HTTPS (TLS 1.3) | 443 | JSON con la transcripción |
| 5 | Servidor ASIX | Navegador | HTTP/1.1 | 8000 | JSON con transcripción, modelo, `request_id` |
| 6 | Navegador | Web Speech API | HTTPS | 443 | Audio del micrófono (a servidores de Google, **no local**) |
| 7 | PowerShell | TTS de Windows | Local | — | Texto → WAV (al preparar los audios) |

**Nota de por qué el servidor escucha en `127.0.0.1` y no en `0.0.0.0`:** en una
práctica de ASIX no hay necesidad real de exponerlo a la red. Escuchar en todas
las interfaces significaría que cualquiera de la red de la clase puede usar
vuestra cuota de Gemini. Si hiciera falta, el cambio correcto sería poner un
proxy inverso con TLS delante (sección 5), no abrir el puerto.

## 3. Dónde está la API Key y por qué importa

La clave viaja **solo** en la petición 3, en la cabecera `x-goog-api-key`, desde
el servidor hacia Google.

```
Cliente ──(audio)──> Servidor ──(audio + KEY)──> Google
                         ^
                    la KEY vive aqui
```

Si la clave estuviera en el `index.html`, cualquiera que visitara la página
pulsando F12 la leería, y con ella podría gastar vuestra cuota desde su propio
equipo sin tocar vuestro servidor. Por eso:

- la clave se lee de `.env`, que está en `.gitignore`;
- el HTML solo recibe `gemini_configurado: true|false`, nunca el valor;
- hay una comprobación automática en `probar_api.py` que verifica que ni la
  respuesta de la API ni el log del servidor contienen la clave.

## 4. Capas de seguridad del endpoint

| Capa | Qué hace | Dónde |
|---|---|---|
| Límite de tasa | 10 peticiones / 60 s por IP. Evita que un bucle propio vacíe la cuota compartida del aula. | `limitar_por_ip` en `api/main.py` |
| Tamaño máximo | Se leen como mucho `MAX_BYTES + 1`. Un fichero de 2 GB se corta antes de cargarlo entero en memoria. | `endpoint_transcribir` |
| Lista blanca de formatos | Solo WAV/MP3/OGG/FLAC/AAC/M4A. Lo que no está prohibido no se acepta. | `config.TIPOS_AUDIO` |
| **Firma del fichero** | Se leen los primeros bytes y se comparan con los del formato declarado. Un `.exe` renombrado a `.wav` se rechaza. | `detectar_formato` |
| Coherencia MIME ↔ contenido | Si el cliente dice `audio/mpeg` y el contenido es un RIFF/WAVE, se rechaza. | `validar_audio` |
| Duración y frecuencia | Un WAV de 400 s o de 4 kHz (inservible) se rechaza. | `validar_audio` |
| Path traversal | El nombre recibido se sanea y además se comprueba que la ruta resuelta queda dentro de `audios/`. Dos capas, no una. | `_nombre_seguro` + `servir_audio` |
| CORS | Lista de orígenes de desarrollo. Nunca `"*"`. | `CORSMiddleware` |
| No escritura en disco | El audio recibido **no se guarda nunca**. Vive en memoria mientras dura la petición. | `api/main.py` |
| Errores al cliente | Detalle genérico (`503`). El motivo técnico va al log del servidor. | `endpoint_transcribir` |
| Trazabilidad | `request_id` en petición, respuesta y log. Permite seguir una llamada concreta. | middleware + cabecera `X-Request-ID` |

### Qué NO se ha hecho y por qué

- **Autenticación de usuarios (JWT, OAuth):** no se pide en el enunciado y añade
  complejidad. Con un servicio abierto, un atacante podría consumir la cuota
  desde cualquier IP; el rate limit por IP es la mitigación mínima viable aquí.
- **Antivirus sobre el audio:** el audio no se guarda ni se ejecuta, y solo se
  acepta si su firma corresponde a un formato de audio, así que el riesgo es
  bajo. En un sistema real, el análisis ocurre en un sandbox.
- **Base de datos:** no hay persistencia. Es un prototipo de stateless.

## 5. Cómo desplegarlo en serio (fuera del aula)

Si esto se publicase, la cadena sería:

```
Internet ──HTTPS/443──> Nginx o Caddy ──HTTP/127.0.0.1:8000──> uvicorn
   (certificado TLS)     (termina TLS,         (red interna,
                          limita tasa,         nunca expuesta)
                          filtra IPs)
```

Y añadiría: autenticación, auditoría de quién llama, cuota por usuario, y
rotación periódica de la clave.

## 6. Modos de fallo y respuesta

| Fallo | Detecta | Respuesta HTTP | Qué ve el cliente |
|---|---|---|---|
| Audio que no es audio | `detectar_formato` → `None` | 400 | "El contenido no se reconoce como audio valido" |
| Audio demasiado grande | `len(datos) > MAX_BYTES` | 413 | Tamaño real y máximo permitido |
| WAV de 400 s o 4 kHz | `validar_audio` | 400 | Motivo concreto |
| MIME que no cuadra | `detectar_formato` vs `content_type` | 400 | "Discrepancia: declara X, el contenido es Y" |
| Path traversal | `_nombre_seguro` + ruta resuelta | 404 | "Audio no encontrado" |
| Demasiadas peticiones | `limitar_por_ip` | 429 | Cuántas faltan y cuánto esperar |
| Gemini saturado (503) | cadena de modelos | 503 | "Reintenta en unos segundos" (el detalle va al log) |
| Cuota agotada (429) | `es_reintentable` | 503 | Igual, sin filtrar el estado de cuota || Sin API Key en `.env` | `config.get_client` | 503 | Mensaje en el log, no en la respuesta |

**Distinción importante entre 429 y 503 hacia el cliente:** el endpoint
devolvería 503 en ambos casos. Un 429 de Gemini al cliente revelaría que hay una
cuota de pago detrás y que se está agotando; no es información que deba salir.
El detalle real queda en el log del servidor, que es donde se investiga.

## 7. Latencia observada

Medido en la ejecución real (`resultados/servidor.log`):

| Operación | Latencia |
|---|---|
| `GET /api/v1/salud` | 1 ms |
| `POST /transcribir` con audio en caché | 29 ms |
| `POST /transcribir` sin caché (llamada real a Gemini) | 480 ms (audio de 5,7 s) |

La caché es lo que hace que el segundo uso del mismo audio sea instantáneo: la
clave es el hash SHA-256 de los bytes, así que el mismo fichero nunca se paga
dos veces.

## 8. Inventario de puertos

| Puerto | Protocolo | Servicio | Expuesto |
|---|---|---|---|
| 8000/tcp | HTTP | uvicorn (FastAPI) | Solo loopback |
| 443/tcp | HTTPS | Salida a generativelanguage.googleapis.com | Salida |
| 443/tcp | HTTPS | Salida a la Web Speech API de Chrome | Salida (la hace el navegador, no el servidor) |

No hay ningún puerto de escucha entrante desde Internet. Los tres audios de
prueba se generan en local con el sintetizador de Windows, sin red.
