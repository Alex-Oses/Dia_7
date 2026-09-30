"""
Dia 7 (ASIX) - Nucleo de transcripcion de voz con Gemini.

Tres bloques:
  1. Validacion del audio recibido (la parte de seguridad del endpoint).
  2. Llamada a Gemini con la cadena de modelos, reintentos y cache.
  3. Extraccion robusta del JSON de respuesta.

Sobre el audio: se comprueba el TIPO REAL del fichero, no el que dice el
cliente. Un navegador puede enviar un ejecutable renombrado a .wav y con
Content-Type audio/wav; si solo nos fiamos de la extension y del MIME, el
servidor lo acepta. Por eso se leen los primeros bytes ("magic bytes") de cada
formato y se comparan con lo que deberian ser.
"""
import hashlib
import json
import random
import re
import time
import wave
from datetime import datetime

from . import config as cfg

# --------------------------------------------------------------------------
# 1. Identificacion real del formato
# --------------------------------------------------------------------------
# Los primeros bytes de cada formato. Un WAV empieza por "RIFF" y lleva "WAVE"
# en los bytes 8-12.
FIRMAS = {
    "wav": (b"RIFF", 0),
    "mp3": (b"ID3", 0),
    "ogg": (b"OggS", 0),
    "flac": (b"fLaC", 0),
    "aac": (b"\xff\xf1", 0),
    "m4a": (b"ftyp", 4),
}


class AudioInvalido(ValueError):
    """El fichero recibido no es un audio aceptable. Se responde 400."""


def detectar_formato(datos: bytes):
    """Devuelve 'wav'|'mp3'|'ogg'|'flac'|'aac'|'m4a' o None.

    El MP3 no siempre empieza por ID3: si lleva la cabecera ID3 lo delata, y si
    no, tiene un frame sync con dos bytes que empiezan por 0xFF. Se comprueba
    tambien ese caso.
    """
    if len(datos) < 16:
        return None

    for nombre, (firma, offset) in FIRMAS.items():
        if datos[offset:offset + len(firma)] == firma:
            if nombre == "wav" and datos[8:12] != b"WAVE":
                return None  # es un RIFF de video, no de audio
            return nombre

    # MP3 sin cabecera ID3: frame sync 11 bits a 1.
    if datos[0] == 0xFF and (datos[1] & 0xE0) == 0xE0:
        return "mp3"
    return None


def validar_audio(datos: bytes, nombre_enviado: str, content_type: str):
    """Valida tamaño, formato y coherencia entre nombre, MIME y contenido.

    Devuelve (formato, extension) con los datos ya normalizados.
    Lanza AudioInvalido con un motivo legible para el cliente.
    """
    if not datos:
        raise AudioInvalido("El fichero de audio esta vacio.")

    if len(datos) > cfg.MAX_BYTES:
        mb = len(datos) / (1024 * 1024)
        raise AudioInvalido(
            f"El audio ocupa {mb:.1f} MB y el maximo permitido es "
            f"{cfg.MAX_BYTES / (1024 * 1024):.0f} MB."
        )

    formato = detectar_formato(datos)
    if formato is None:
        raise AudioInvalido(
            "El contenido no se reconoce como audio valido. Se esperaba un "
            "WAV, MP3, OGG, FLAC, AAC o M4A real. Si has renombrado un fichero "
            "para que pareciera un audio, esto lo detecta."
        )

    # El MIME declarado tiene que concordar con el contenido real. No es la
    # comprobacion principal (esa es la firma), pero una discrepancia suele
    # significar que el cliente se equivoca y conviene avisarle.
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime and mime not in cfg.TIPOS_AUDIO:
        raise AudioInvalido(
            f"Content-Type '{mime}' no es un tipo de audio admitido."
        )
    if mime:
        ext_mime = cfg.TIPOS_AUDIO[mime]
        if not ext_mime.lstrip(".") == formato:
            raise AudioInvalido(
                f"Discrepancia: el fichero se declara como {mime} pero su "
                f"contenido real es {formato.upper()}."
            )

    # La extension del nombre enviado tambien deberia cuadrar.
    extensiones = {e.lstrip(".").lower() for e in cfg.TIPOS_AUDIO.values()}
    ext = nombre_enviado.rsplit(".", 1)[-1].lower() if "." in nombre_enviado else ""
    if ext and ext not in extensiones:
        raise AudioInvalido(f"Extension '.{ext}' no admitida.")

    # Si es WAV, se comprueba que se puede abrir y se mide la duracion.
    duracion = None
    if formato == "wav":
        try:
            import io
            with wave.open(io.BytesIO(datos), "rb") as w:
                if w.getnframes() == 0:
                    raise AudioInvalido("El WAV no contiene muestras de audio.")
                duracion = round(w.getnframes() / float(w.getframerate() or 1), 2)
                if w.getframerate() < 8000:
                    raise AudioInvalido(
                        f"El WAV es de {w.getframerate()} Hz, demasiado grave para "
                        "transcribir de forma fiable (minimo 8000 Hz)."
                    )
        except AudioInvalido:
            raise
        except wave.Error as e:
            raise AudioInvalido(f"El WAV esta corrupto: {e}") from None
        if duracion > cfg.MAX_SEGUNDOS:
            raise AudioInvalido(
                f"El audio dura {duracion:.0f} s y el maximo es {cfg.MAX_SEGUNDOS} s."
            )

    return formato, _mime_de(formato), duracion


def _mime_de(formato):
    for mime, ext in cfg.TIPOS_AUDIO.items():
        if ext.lstrip(".") == formato:
            return mime
    return "application/octet-stream"


# --------------------------------------------------------------------------
# 2. Prompt y llamada a Gemini
# --------------------------------------------------------------------------
# response_mime_type queda DESACTIVADO a proposito: con gemini-3.5-flash
# provoca 503 de forma constante (visto en el Dia 6). El JSON se pide por
# prompt y se extrae con un parser tolerante.
PROMPT = """Transcribe el audio que recibes.

Reglas de transcripcion:
- Reproduce el audio TAL CUAL se ha dicho. No traduzcas, no resumas, no corrijas.
- No anadas palabras que no se oyen, aunque la frase suene poco natural.
- Escribe las palabras EXACTAMENTE como se pronuncian. Si alguien dice "nueve
  y media", escribe "nueve y media", NO "9:30".
- Manten la puntuacion natural del habla.
- Si hay palabras ininteligibles, escribelas como [inaudible].
- Si el audio esta en espanol, la transcripcion va en espanol.

Devuelve SOLO este JSON, sin markdown ni texto alrededor:
{{"transcripcion": "...", "idioma": "es|en|...", "duracion_s": 0.0, "segmentos": [{{"inicio": 0.0, "fin": 0.0, "texto": "..."}}]}}

El campo "segmentos" lleva la division del audio en sus partes habladas, con
inicio y fin en segundos. Si no puedes localizar los tiempos, deja la lista
vacia.
"""

# Variante para cuando lo que se quiere es una transcripcion legible para un
# humano (resumen de una reunion, por ejemplo) y no medir precision.
#
# NO se usa al medir el WER: al pedir "9:30" en vez de "nueve y media" el modelo
# hace lo correcto y la metrica lo cuenta como error. Durante el desarrollo de
# esta practica se cayo en eso, y el WER de 02_horario subio a 23,5% por esta
# instruccion y no por la calidad del reconocimiento.
PROMPT_NUMEROS = """Igual que el anterior, con UNA diferencia: escribe los numeros
con digitos, como se leen en un informe ("nueve y media" -> "9:30", "aula dos"
-> "aula 2"). Sirve para obtener texto legible, no para medir precision.
"""


def _extraer_json(bruto: str):
    """Saca el objeto JSON de la respuesta del modelo, sea cual sea el envoltorio."""
    if not bruto:
        raise ValueError("respuesta vacia")
    texto = bruto.strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*(.+?)```", texto, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    inicio = texto.find("{")
    if inicio != -1:
        profundidad, dentro, comillas, escape = 0, False, False, False
        for i, ch in enumerate(texto[inicio:], inicio):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                comillas = not comillas
            if comillas:
                continue
            if ch == "{":
                profundidad += 1
                dentro = True
            elif ch == "}":
                profundidad -= 1
                if dentro and profundidad == 0:
                    try:
                        return json.loads(texto[inicio:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"no se encontro JSON en la respuesta: {bruto[:300]}")


class ErrorServicio(Exception):
    """Fallo recuperable al llamar a Gemini. Lo traduce el endpoint a 503."""


def _es_reintentable(e):
    m = f"{type(e).__name__}: {e}"
    return any(x in m for x in (
        "429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "INTERNAL",
        "504", "DEADLINE_EXCEEDED", "overloaded",
    ))


def transcribir(datos: bytes, mime: str, *, cache_dir=None, etiqueta="audio",
                normalizar_numeros=False):
    """Transcribe un audio con Gemini. Devuelve el dict ya parseado.

    Recorre la cadena de modelos: ante un 503 baja al siguiente en vez de
    fallar, porque en el free tier la saturacion es algo habitual y no un error
    de los datos. Guarda la respuesta en cache para no pagar dos veces la misma
    peticion (clave = hash de los bytes del audio + variante de prompt).

    normalizar_numeros=False (por defecto) transcribe de forma literal, que es
    lo que hay que usar para medir precision. True produce texto mas legible a
    cambio de no ser comparable contra una referencia literal.
    """
    cliente = cfg.get_client()
    cache_dir = cache_dir or cfg.CACHE_DIR
    variante = "num" if normalizar_numeros else "lit"
    clave = hashlib.sha256(datos + variante.encode()).hexdigest()[:24]
    ruta_cache = cache_dir / f"stt_{clave}.json"

    if ruta_cache.exists():
        try:
            guardado = json.loads(ruta_cache.read_text(encoding="utf-8"))
            if "resultado" in guardado:
                guardado["resultado"]["cache"] = True
                return guardado["resultado"]
        except json.JSONDecodeError:
            pass

    contenido = [
        {"inline_data": {"mime_type": mime, "data": datos}},
        PROMPT + (PROMPT_NUMEROS if normalizar_numeros else ""),
    ]

    memoria = [cfg.modelo_usado()] if cfg.modelo_usado() else []
    candidatos, visto = [], set()
    for m in memoria + cfg.CADENA_MODELOS:
        if m and m not in visto:
            visto.add(m)
            candidatos.append(m)

    ultimo = None
    for modelo in candidatos:
        try:
            texto = _llamar_con_reintentos(cliente, contenido, modelo, etiqueta)
            resultado = _normalizar(_extraer_json(texto))
            resultado["modelo"] = modelo
            resultado["cache"] = False
            cfg.marcar_modelo(modelo)
            ruta_cache.write_text(
                json.dumps({"resultado": resultado, "fecha": datetime.now().isoformat()},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            return resultado
        except ErrorServicio as e:
            ultimo = e
            print(f"  {etiqueta}: {modelo} no disponible ({str(e)[:60]}), se prueba el siguiente")
            continue

    raise ErrorServicio(f"ningun modelo respondio: {ultimo}")


def _llamar_con_reintentos(cliente, contenido, modelo, etiqueta, intentos=2):
    for i in range(intentos):
        try:
            resp = cliente.models.generate_content(
                model=modelo,
                contents=contenido,
                config={
                    "temperature": 0.0,   # transcribir no es una tarea creativa
                    "top_p": 0.95,
                    "max_output_tokens": 2048,
                },
            )
            texto = (resp.text or "").strip()
            if not texto:
                raise RuntimeError("respuesta vacia de la API")
            return texto
        except Exception as e:  # noqa: BLE001
            if not _es_reintentable(e) or i == intentos - 1:
                raise ErrorServicio(f"{type(e).__name__}: {e}") from e
            espera = 12 * (i + 1) + random.randint(0, 4)
            print(f"  {etiqueta}: {modelo} saturado, reintento en {espera}s")
            time.sleep(espera)
    raise ErrorServicio("agotados los reintentos")


def _normalizar(d):
    """Deja los campos con la forma esperada, aunque el modelo se desvíe."""
    def _lista_segmentos(valor):
        salida = []
        for s in (valor or []):
            if not isinstance(s, dict):
                continue
            try:
                salida.append({
                    "inicio": round(float(s.get("inicio", 0) or 0), 2),
                    "fin": round(float(s.get("fin", 0) or 0), 2),
                    "texto": str(s.get("texto", "")).strip(),
                })
            except (TypeError, ValueError):
                continue
        return salida

    return {
        "transcripcion": str(d.get("transcripcion", "")).strip(),
        "idioma": str(d.get("idioma", "")).strip().lower(),
        "duracion_s": d.get("duración_s") or d.get("duracion_s"),
        "segmentos": _lista_segmentos(d.get("segmentos")),
    }
