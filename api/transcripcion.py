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
# Formatos que admite Gemini, agrupados por familia. Para cada familia se
# listan los MIME que el cliente puede declarar y el MIME canonico que se le
# envia a Google.
#
# Se comprobaron contra la documentacion oficial, que admite mas formatos de los
# que cubre la primera version de este codigo: audio/webm entre ellos, que es
# justo lo que produce MediaRecorder en Chrome al grabar desde el navegador.
# Aceptar menos de lo que el servicio soporta no es mas seguro, solo mas
# inutil: el audio sigue sin ejecutarse ni guardarse en disco.
FORMATOS = {
    "wav":  (["audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"],
             "audio/wav"),
    "webm": (["audio/webm", "audio/x-matroska"], "audio/webm"),
    "ogg":  (["audio/ogg", "audio/opus", "audio/vorbis", "application/ogg"], "audio/ogg"),
    "flac": (["audio/flac", "audio/x-flac"], "audio/flac"),
    "aiff": (["audio/aiff", "audio/x-aiff"], "audio/aiff"),
    "m4a":  (["audio/mp4", "audio/m4a", "audio/x-m4a", "audio/m4b"], "audio/mp4"),
    "aac":  (["audio/aac", "audio/x-aac", "audio/aacp"], "audio/aac"),
    "mp3":  (["audio/mpeg", "audio/mp3", "audio/mpga", "audio/mpeg3", "audio/x-mpeg-3"],
             "audio/mpeg"),
}

# Formatos que Gemini admite pero que NO tienen firma verificable: son audio PCM
# crudo. No se aceptan a proposito, porque al no existir cabecera cualquier
# fichero podria declararse como audio/l16 y entraria sin ninguna comprobacion.
# Romperia justamente la garantia que da la deteccion por magic bytes.
SIN_FIRMA = {
    "audio/l16": "audio PCM crudo",
    "audio/pcm": "audio PCM crudo",
    "audio/alaw": "audio PCM crudo (A-law)",
    "audio/mulaw": "audio PCM crudo (mu-law)",
}


class AudioInvalido(ValueError):
    """El fichero recibido no es un audio aceptable. Se responde 400."""


def detectar_formato(datos: bytes):
    """Devuelve el nombre de la familia del formato, o None.

    'wav', 'webm', 'ogg', 'flac', 'aiff', 'm4a', 'aac' o 'mp3'.

    Se leen los bytes que identifican cada formato. Ojo con dos casos:
      - Un RIFF puede ser de video (AVI), asi que ademas de "RIFF" se exige
        que en la posicion 8 ponga "WAVE".
      - El MP3 no siempre empieza por ID3: si lleva cabecera la delata, y si no
        tiene un frame sync con dos bytes que empiezan por 0xFF.
      - WebM y Matroska comparten la firma EBML; aqui se tratan igual porque a
        Gemini se le envia como audio/webm.
    """
    if len(datos) < 16:
        return None

    if datos[0:4] == b"RIFF":
        return "wav" if datos[8:12] == b"WAVE" else None
    if datos[0:4] == b"FORM":
        # AIFF y AIFC; 'AIFC' lleva compresi\u00f3n, ambos valen.
        return "aiff" if datos[8:12] in (b"AIFF", b"AIFC") else None
    if datos[0:4] == b"\x1a\x45\xdf\xa3":
        return "webm"                      # EBML: WebM / Matroska
    if datos[0:4] == b"OggS":
        return "ogg"
    if datos[0:4] == b"fLaC":
        return "flac"
    if datos[4:8] == b"ftyp":
        return "m4a"                       # MP4/M4A/M4B
    if datos[0:3] == b"ID3":
        return "mp3"
    if datos[0] == 0xFF and (datos[1] & 0xE0) == 0xE0:
        # AAC (ADTS) y MP3 comparten el sync de 12 bits a 1, asi que hay que
        # mirar los bits de capa, que valen 00 en ADTS y 01/10/11 en MP3.
        if (datos[1] & 0x06) == 0x00:
            return "aac"
        return "mp3"
    return None


def _resumen_bytes(datos: bytes, n=12):
    return " ".join(f"{b:02X}" for b in datos[:n])


def _mensaje_rechazo(datos, content_type, nombre):
    """Explica por que se rechaza, con la evidencia que permite arreglarlo."""
    inicio = _resumen_bytes(datos)
    mime = (content_type or "").split(";")[0].strip().lower()
    partes = [
        f"El contenido no se reconoce como audio valido. "
        f"Los primeros bytes son: {inicio}."
    ]
    if datos[0:4] == b"\x1a\x45\xdf\xa3":
        partes.append(
            "Esos bytes son una cabecera EBML, es decir un fichero WebM/Matroska: "
            "justo lo que produce el navegador al grabar con MediaRecorder. "
            "Este endpoint ya lo admite, asi que si te aparece este error es que "
            "el Content-Type no ha llegado. Prueba con la opcion de subir un "
            "fichero en lugar de grabar."
        )
    if mime in SIN_FIRMA:
        partes.append(
            f"El Content-Type declara {mime} ({SIN_FIRMA[mime]}), que no tiene "
            f"cabecera que verificar, asi que no se puede confirmar que sea audio y "
            f"no se acepta. Conv\u00edertelo a WAV."
        )
    if not mime:
        partes.append("El cliente no declaro Content-Type, y sin el no se puede "
                     "decidir a que formato corresponde.")
    if datos[0:2] == b"MZ":
        partes.append("MZ es la firma de un ejecutable de Windows.")
    if datos[0:5] == b"%PDF-":
        partes.append("El fichero empieza por %PDF, o sea que es un PDF.")
    partes.append(
        "Formatos admitidos: " + ", ".join(sorted(FORMATOS)) + "."
    )
    return " ".join(partes)


def validar_audio(datos: bytes, nombre_enviado: str, content_type: str):
    """Valida tamaño, formato y coherencia entre nombre, MIME y contenido.

    Devuelve (formato, mime_para_gemini, duracion_s). El MIME que se devuelve es
    el que se le manda a Google: si el cliente declaro uno valido de la familia
    se respeta (asi 'audio/opus' no se degrada a 'audio/ogg'), y si no, se usa el
    canonico de la familia.

    Lanza AudioInvalido con un motivo que incluye los bytes leidos, para que se
    pueda diagnosticar sin tener que mirar el servidor.
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
    mime = (content_type or "").split(";")[0].strip().lower()

    if formato is None:
        raise AudioInvalido(_mensaje_rechazo(datos, content_type, nombre_enviado))

    permitidos, canonico = FORMATOS[formato]

    if mime and mime not in permitidos:
        raise AudioInvalido(
            f"El contenido real es {formato.upper()}, pero el Content-Type declarado "
            f"es '{mime}', que no corresponde. Declara uno de: {', '.join(permitidos)}."
        )

    # Si el cliente no declaro nada, se usa el canonico de la familia.
    mime_para_gemini = mime if mime in permitidos else canonico

    # La extension del nombre enviado tambien deberia cuadrar. No es obligatorio
    # (hay navegadores que inventan nombres), asi que solo se avisa.
    extensiones = {m.rsplit("/", 1)[-1] for m in permitidos}
    ext = nombre_enviado.rsplit(".", 1)[-1].lower() if "." in nombre_enviado else ""
    if ext and ext not in extensiones and ext not in {"webm", "mkv", "mka"}:
        raise AudioInvalido(
            f"La extension '.{ext}' no corresponde a un {formato.upper()} "
            f"(se esperaba una de: {', '.join(sorted(extensiones))})."
        )

    # Si es WAV, se comprueba que se puede abrir y se mide la duracion.
    duracion = None
    if formato == "wav":
        try:
            import io
            with wave.open(io.BytesIO(datos), "rb") as w:
                if w.getnframes() == 0:
                    raise AudioInvalido("El WAV no contiene muestras de audio.")
                freq = w.getframerate()
                if freq < 8000:
                    raise AudioInvalido(
                        f"El WAV es de {freq} Hz, demasiado grave para transcribir de "
                        f"forma fiable (minimo 8000 Hz)."
                    )
                duracion = round(w.getnframes() / float(freq or 1), 2)
        except AudioInvalido:
            raise
        except wave.Error as e:
            raise AudioInvalido(f"El WAV esta corrupto: {e}") from None
        if duracion > cfg.MAX_SEGUNDOS:
            raise AudioInvalido(
                f"El audio dura {duracion:.0f} s y el maximo es {cfg.MAX_SEGUNDOS} s."
            )

    return formato, mime_para_gemini, duracion


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
