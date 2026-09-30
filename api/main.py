"""
Dia 7 (ASIX) - Endpoint de transcripcion de voz.

    POST /api/v1/transcribir   recibe un audio, lo transcribe y devuelve el texto
    GET  /api/v1/salud         comprobacion de estado
    GET  /api/v1/audios        lista los audios de ejemplo (para probarlo)
    GET  /docs                documentacion interactiva (Swagger UI)

Arranque:
    .\\venv\\Scripts\\Activate.ps1
    uvicorn api.main:app --reload --port 8000

Decisiones de seguridad (ver docs/ARQUITECTURA.md para el detalle):
  - La API Key de Gemini vive solo en el servidor. Nunca viaja al navegador.
  - El audio se valida por su contenido real, no por lo que declara el cliente.
  - Tope de tamaño y de duracion para no agotar la cuota.
  - El nombre del fichero NO se usa para escribir en disco: no hay traversal.
  - CORS limitado a origenes de desarrollo, no a "*".
  - Limite de peticiones por cliente y ventana.
  - Los errores hacia el cliente son genericos; el detalle va al log del servidor.
"""
import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config as cfg
from .transcripcion import AudioInvalido, ErrorServicio, transcribir, validar_audio

class FiltroRequestId(logging.Filter):
    """Garantiza que todo registro tenga el campo request_id.

    Sin esto, cualquier log que no venga del middleware (el del limitador de
    peticiones, el de un error, o los de uvicorn) revienta con
    KeyError: 'request_id' al aplicar el formato, y el mensaje se pierde.
    """

    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(request_id)s] %(message)s",
)
log = logging.getLogger("stt")
_filtro = FiltroRequestId()
log.addFilter(_filtro)
for _h in logging.getLogger().handlers:
    _h.addFilter(_filtro)

app = FastAPI(
    title="Servicio de transcripcion de voz (Dia 7 - ASIX)",
    description=(
        "Recibe un audio, lo envia a Gemini y devuelve su transcripcion.\n\n"
        "La API Key de Gemini se mantiene en el servidor: el cliente web nunca la ve."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.ORIGENES_PERMITIDOS,   # nunca "*" con una key en juego
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Identificacion de peticion: sin esto no se puede seguir una peticion concreta
# en los logs cuando varias llegan a la vez.
# --------------------------------------------------------------------------
@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:8]
    request.state.request_id = rid
    inicio = time.perf_counter()
    respuesta = await call_next(request)
    ms = (time.perf_counter() - inicio) * 1000
    log.info("%s %s -> %s en %.0f ms", rid, request.method, request.url.path, ms)
    respuesta.headers["X-Request-ID"] = rid
    return respuesta


# --------------------------------------------------------------------------
# Limite de peticiones por cliente. Un bucle propio desde una sola IP no
# puede vaciar la cuota compartida de Gemini de todo el aula.
# --------------------------------------------------------------------------
_historico = defaultdict(deque)


def limitar_por_ip(request: Request):
    ahora = time.time()
    ip = request.client.host if request.client else "desconocida"
    cola = _historico[ip]
    while cola and ahora - cola[0] > cfg.RATE_VENTANA_S:
        cola.popleft()
    if len(cola) >= cfg.RATE_LIMITE:
        espera = int(cfg.RATE_VENTANA_S - (ahora - cola[0])) + 1
        log.warning("rate limit: %s supero %d peticiones", ip, cfg.RATE_LIMITE)
        raise HTTPException(
            status_code=429,
            detail=f"Demasiadas peticiones. Maximo {cfg.RATE_LIMITE} cada "
                   f"{int(cfg.RATE_VENTANA_S)} s. Reintenta en {espera} s.",
        )
    cola.append(ahora)


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/api/v1/salud", tags=["estado"])
async def salud():
    """Comprueba que el servicio responde y si hay credencial configurada."""
    return {
        "estado": "ok",
        "servicio": "transcripcion-de-voz",
        "version": app.version,
        "gemini_configurado": bool(cfg.API_KEY),
        "modelo_preferido": cfg.CADENA_MODELOS[0] if cfg.CADENA_MODELOS else None,
        "modelo_en_uso": cfg.modelo_usado(),
        "formatos_admitidos": sorted({e.lstrip(".") for e in cfg.TIPOS_AUDIO.values()}),
        "max_bytes": cfg.MAX_BYTES,
        "max_segundos": cfg.MAX_SEGUNDOS,
    }


@app.get("/api/v1/audios", tags=["ejemplos"])
async def listar_audios():
    """Lista los audios de prueba incluidos en el repositorio."""
    manifest = cfg.AUDIOS_DIR / "verdad_terreno.json"
    if not manifest.exists():
        return {"audios": [], "aviso": "Ejecuta herramientas/generar_audios.py"}
    import json
    datos = json.loads(manifest.read_text(encoding="utf-8"))
    for a in datos.get("audios", []):
        a["url"] = f"/api/v1/audio/{a['archivo']}"
    return {"metodo": datos.get("metodo"), "formato": datos.get("formato"), "audios": datos.get("audios", [])}


@app.get("/api/v1/audio/{nombre}", tags=["ejemplos"])
async def servir_audio(nombre: str):
    """Sirve un audio de ejemplo. El nombre se valida para impedir traversal."""
    seguro = _nombre_seguro(nombre)
    ruta = cfg.AUDIOS_DIR / seguro
    if not ruta.is_file() or ruta.parent.resolve() != cfg.AUDIOS_DIR.resolve():
        raise HTTPException(status_code=404, detail="Audio no encontrado.")
    from fastapi.responses import FileResponse
    return FileResponse(ruta, media_type="audio/wav", filename=seguro)


@app.post("/api/v1/transcribir", tags=["transcripcion"])
async def endpoint_transcribir(
    request: Request,
    audio: UploadFile = File(..., description="Audio WAV, MP3, OGG, FLAC, AAC o M4A"),
    idioma: str | None = None,
    numeros: bool = False,
    _: None = Depends(limitar_por_ip),
):
    """Recibe un audio y devuelve su transcripcion.

    El parametro `numeros` controla una decision de diseno con coste: si se
    activa, el modelo escribe "9:30" donde se oyo "nueve y media". El texto es
    mas legible, pero deja de ser literal, asi que no debe activarse al medir
    precision con el WER.

    Respuestas:
      200  transcripcion correcta
      400  el fichero no es un audio valido
      413  supera el tamano maximo
      429  demasiadas peticiones desde esta IP
      503  Gemini no disponible
    """
    rid = getattr(request.state, "request_id", "-")
    log.info("%s transcribiendo '%s' (%s)", rid, audio.filename, audio.content_type)

    # Se lee con un tope: no se descarga el fichero entero de la memoria solo
    # para descubrir que pesa 2 GB.
    leido = await audio.read(cfg.MAX_BYTES + 1)
    await audio.close()

    try:
        formato, mime, duracion = validar_audio(leido, audio.filename or "", audio.content_type or "")
    except AudioInvalido as e:
        log.warning("%s audio rechazado: %s", rid, e)
        codigo = 413 if "maximo permitido" in str(e) else 400
        raise HTTPException(status_code=codigo, detail=str(e)) from None

    try:
        resultado = transcribir(leido, mime, etiqueta=f"{rid}:{formato}",
                                normalizar_numeros=numeros)
    except ErrorServicio as e:
        log.error("%s fallo la transcripcion: %s", rid, e)
        # Al cliente no se le manda el detalle tecnico ni la traza: se registra
        # en el log del servidor, que es donde se puede investigar.
        raise HTTPException(
            status_code=503,
            detail="No se pudo completar la transcripcion en este momento. "
                   "Reintenta en unos segundos.",
        ) from None

    return {
        "request_id": rid,
        "archivo": audio.filename,
        "formato_detectado": formato,
        "bytes": len(leido),
        "duracion_s": duracion,
        "idioma_detectado": resultado.get("idioma"),
        "idioma_solicitado": idioma,
        "numeros_normalizados": numeros,
        "transcripcion": resultado.get("transcripcion"),
        "segmentos": resultado.get("segmentos"),
        "modelo": resultado.get("modelo"),
        "served_from_cache": resultado.get("cache"),
    }


@app.exception_handler(AudioInvalido)
async def _audio_invalido(request: Request, exc: AudioInvalido):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _nombre_seguro(nombre: str) -> str:
    """Deja solo el nombre de fichero, sin rutas ni caracteres raros.

    Es la defensa contra path traversal (..\\..\\windows\\system32). Aunque el
    endpoint comprueba ademas que la ruta resuelta este dentro de la carpeta, no
    se depende de una sola comprobacion.
    """
    import re
    return re.sub(r"[^A-Za-z0-9._-]", "_", nombre).lstrip(".")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)
