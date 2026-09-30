"""
Pruebas de la validacion de audio y de la metrica WER.

Ejecutar:  python test_validacion.py

No llama a la API de Gemini: comprueba que el endpoint RECHAZA lo que debe
rechazar. En un endpoint ASIX, esto es lo que evita que un cliente mande un
ejecutable, un fichero enorme o un path traversal.
"""
import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.transcripcion import AudioInvalido, detectar_formato, validar_audio

fallos = []


def comprobar(nombre, condicion, detalle=""):
    print(f"  {'OK   ' if condicion else 'FALLO'} {nombre}" + ("" if condicion else f"  <- {detalle}"))
    if not condicion:
        fallos.append(nombre)


def wav_valido(dur=1.0, freq=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(freq)
        w.writeframes(b"\x00\x01" * int(freq * dur))
    return buf.getvalue()


def _wav_vacio():
    """WAV con cabecera valida pero sin ninguna muestra de audio."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"")
    return buf.getvalue()


print("== deteccion de formato por contenido (magic bytes) ==")
comprobar("WAV valido detectado", detectar_formato(wav_valido()) == "wav")
comprobar("MP3 con cabecera ID3", detectar_formato(b"ID3\x04\x00" + b"\x00" * 30) == "mp3")
comprobar("MP3 con frame sync", detectar_formato(b"\xff\xfb\x90\x00" + b"\x00" * 30) == "mp3")
comprobar("OGG", detectar_formato(b"OggS\x00\x02" + b"\x00" * 30) == "ogg")
comprobar("FLAC", detectar_formato(b"fLaC\x00\x00\x00\x22" + b"\x00" * 30) == "flac")
comprobar("M4A", detectar_formato(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 30) == "m4a")
comprobar("AIFF", detectar_formato(b"FORM\x00\x00\x00\x20AIFF" + b"\x00" * 30) == "aiff")
# WebM es lo que produce MediaRecorder en Chrome: cabecera EBML
comprobar("WebM (EBML)", detectar_formato(b"\x1a\x45\xdf\xa3" + b"\x00" * 40) == "webm")
comprobar("AAC ADTS", detectar_formato(b"\xff\xf1\x50\x80" + b"\x00" * 30) == "aac")
comprobar("ejecutable renombrado a .wav -> None", detectar_formato(b"MZ\x90\x00" + b"\x00" * 60) is None)
comprobar("un PDF no pasa por audio", detectar_formato(b"%PDF-1.7\n" + b"\x00" * 60) is None)
comprobar("texto plano no pasa", detectar_formato(b"esto es un texto" * 8) is None)
comprobar("fichero vacio", detectar_formato(b"") is None)
comprobar("muy corto", detectar_formato(b"RIFF") is None)
# RIFF de video: empieza igual que un WAV pero NO es audio
comprobar("RIFF de video (AVI) rechazado", detectar_formato(b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 30) is None)

print("\n== validacion completa ==")
f, m, d = validar_audio(wav_valido(dur=2.0), "prueba.wav", "audio/wav")
comprobar("WAV valido aceptado", (f, m) == ("wav", "audio/wav"))
comprobar("devuelve la duracion", abs(d - 2.0) < 0.1, f"duracion={d}")

f, m, _ = validar_audio(wav_valido(), "prueba.wav", "audio/x-wav")
comprobar("alias de MIME wav aceptado", f == "wav")

print("\n== WebM, que es lo que graba el navegador ==")
webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 400
f, m, d = validar_audio(webm, "grabacion.webm", "audio/webm;codecs=opus")
comprobar("WebM aceptado (Chrome lo produce)", f == "webm")
comprobar("se manda como audio/webm a Gemini", m == "audio/webm", m)
# Si el navegador declara audio/opus dentro de un contenedor Ogg, se respeta.
f, m, _ = validar_audio(b"OggS" + b"\x00" * 400, "g.ogg", "audio/opus")
comprobar("Opus no se degrada a audio/ogg", m == "audio/opus", m)
# WebM y Ogg son contenedores DISTINTOS: declararlos mal se rechaza, que es
# justo la garantia que da comparar el contenido con lo declarado.
try:
    validar_audio(webm, "grabacion.webm", "audio/ogg")
    comprobar("WebM declarado como ogg se rechaza", False, "lo acepto y deberia rechazarlo")
except AudioInvalido:
    comprobar("WebM declarado como ogg se rechaza", True)

print("\n== lo que debe RECHAZAR ==")


def rechaza(nombre, datos, archivo="x.wav", mime="audio/wav"):
    try:
        validar_audio(datos, archivo, mime)
        comprobar(nombre, False, "lo acepto y deberia rechazarlo")
    except AudioInvalido as e:
        comprobar(nombre, True)
        return e
    return None


e = rechaza("fichero vacio", b"")
e = rechaza("ejecutable renombrado", b"MZ\x90\x00" + b"\x00" * 2000, "virus.wav", "audio/wav")
e = rechaza("MIME que no es audio", wav_valido(), "x.wav", "application/x-msdownload")
comprobar("el mensaje explica el motivo", e is not None and "no corresponde" in str(e), str(e))

e = rechaza("extension no admitida", wav_valido(), "documento.exe", "audio/wav")

# MIME y contenido que no cuadran entre si
e = rechaza("discrepancia WAV declarado como MP3", wav_valido(), "x.wav", "audio/mpeg")
comprobar("el error nombra el formato declarado", e is not None and "audio/mpeg" in str(e), str(e))
comprobar("el error nombra el formato real", e is not None and "WAV" in str(e), str(e))
comprobar("el error dice que no corresponde", e is not None and "no corresponde" in str(e), str(e))

# El error de formato desconocido debe enseñar los bytes leidos, para poder
# diagnosticar sin acceso al servidor.
e = rechaza("binario desconocido con pista", b"\x11\x22\x33\x44" + b"\x7f" * 60, "raro.wav", "audio/wav")
comprobar("el error incluye los bytes leidos", e is not None and "11 22 33 44" in str(e), str(e))
comprobar("el error lista los formatos admitidos", e is not None and "webm" in str(e))
e = rechaza("ejecutable con pista concreta", b"MZ\x90\x00" + b"\x00" * 200, "x.wav", "audio/wav")
comprobar("el error reconoce el ejecutable", e is not None and "MZ" in str(e), str(e))
# Los formatos PCM crudos no tienen firma: no se aceptan a proposito.
e = rechaza("PCM crudo declarado como l16", b"\x11" * 2000, "x.l16", "audio/l16")
comprobar("el error explica por que no se acepta el PCM crudo",
          e is not None and "no tiene cabecera" in str(e), str(e))

e = rechaza("WAV sin muestras", _wav_vacio(), "x.wav", "audio/wav")
e = rechaza("WAV con frecuencia inutil", wav_valido(freq=4000), "x.wav", "audio/wav")
e = rechaza("WAV corrupto", b"RIFF" + b"\x00" * 8 + b"WAVE" + b"\xff" * 40, "x.wav", "audio/wav")
e = rechaza("demasiado largo", wav_valido(dur=400), "x.wav", "audio/wav")

print("\n== WER (word error rate) ==")
from api.metricas import wer

# (referencia, hipotesis) -> (WER, errores)
casos = [
    ("hola mundo", "hola mundo", 0.0),
    ("hola mundo", "hola munfo", 0.5),
    ("hola mundo", "hola", 0.5),          # 1 borrado de 2
    ("hola mundo", "hola mundo hola", 0.5),  # 1 insercion de 2
    ("a b c", "", 1.0),                   # 3 borrados de 3
    ("", "algo", 0.0),                    # sin referencia: no se puede puntuar
    ("un dos tres cuatro", "uno dos tres cuatro", 0.25),
]
for ref, hip, esperado in casos:
    r, _ = wer(ref, hip)
    comprobar(f"wer({ref!r}, {hip!r}) = {esperado}", abs(r - esperado) < 1e-9, f"obtenido {r}")

r, detalle = wer("hola mundo", "hola munfo")
comprobar("el detalle senala sustituciones", detalle == {"S": 1, "I": 0, "D": 0}, str(detalle))
r, detalle = wer("hola", "hola mundo")
comprobar("el detalle senala inserciones", detalle["I"] == 1, str(detalle))
r, detalle = wer("hola mundo", "hola")
comprobar("el detalle senala borrados", detalle["D"] == 1, str(detalle))

print("\n== normalizacion para comparar ==")
from api.metricas import normalizar_texto

comprobar("minusculas y sin puntuacion", normalizar_texto("Hola, MUNDO!") == "hola mundo")
comprobar("colapsa espacios", normalizar_texto("hola    mundo") == "hola mundo")
comprobar("acentos conservados para no perder info", normalizar_texto("día") == "día")


print()
if fallos:
    print(f"FALLARON {len(fallos)} prueba(s):")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
print("Todas las pruebas pasan.")
