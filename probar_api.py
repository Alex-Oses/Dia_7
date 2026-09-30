"""
Dia 7 (ASIX) - Prueba de extremo a extremo del endpoint.

Arranca el servidor, envia peticiones reales y comprueba tanto el camino bueno
como los que TIENEN que fallar. Un endpoint de ASIX se juzga por lo que
rechaza, no solo por lo que transcribe.

Uso:  python probar_api.py
"""
import io
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
# Puerto propio de la prueba, distinto del 8000 del servicio. Asi esta prueba
# puede ejecutarse con un servidor manual ya arrancado, en vez de fallar porque
# el puerto esta ocupado (o, peor, medirse contra el servidor ajeno).
PUERTO = int(os.environ.get("PUERTO_PRUEBA", 8011))
BASE_URL = f"http://{HOST}:{PUERTO}"

fallos = []


def comprobar(nombre, condicion, detalle=""):
    print(f"  {'OK   ' if condicion else 'FALLO'} {nombre}" + ("" if condicion else f"\n         -> {detalle}"))
    if not condicion:
        fallos.append(nombre)


def esperar_servidor(proceso, intentos=40):
    for _ in range(intentos):
        if proceso.poll() is not None:
            return False
        try:
            if httpx.get(f"{BASE_URL}/api/v1/salud", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False


def wav(dur=1.0, freq=16000, muestras=None):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(freq)
        w.writeframes(muestras if muestras is not None else b"\x00\x01" * int(freq * dur))
    return buf.getvalue()


def post_audio(cliente, datos, nombre="x.wav", mime="audio/wav", **params):
    return cliente.post(
        f"{BASE_URL}/api/v1/transcribir",
        files={"audio": (nombre, datos, mime)},
        params=params or None,
        timeout=180.0,
    )


def main():
    print("Arrancando el servidor (uvicorn)...")
    log = open(BASE / "resultados" / "servidor.log", "w", encoding="utf-8")
    proceso = subprocess.Popen(
        [sys.executable, "-u", "-m", "uvicorn", "api.main:app", "--host", HOST, "--port", str(PUERTO)],
        cwd=str(BASE), stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        if not esperar_servidor(proceso):
            print("El servidor no arranco. Log:")
            print((BASE / "resultados" / "servidor.log").read_text(encoding="utf-8")[-2000:])
            return 1
        print(f"Servidor listo en el puerto {PUERTO}.\n")

        with httpx.Client() as c:
            print("== 1. endpoints de estado ==")
            r = c.get(f"{BASE_URL}/api/v1/salud")
            comprobar("GET /salud responde 200", r.status_code == 200)
            salud = r.json()
            comprobar("declara si hay key configurada", "gemini_configurado" in salud, str(salud))
            comprobar("la key NO aparece en la respuesta", "AQ.Ab" not in r.text and "AIza" not in r.text)
            comprobar("anuncia los formatos admitidos", "wav" in salud.get("formatos_admitidos", []))

            r = c.get(f"{BASE_URL}/docs")
            comprobar("GET /docs sirve la documentacion", r.status_code == 200 and "swagger" in r.text.lower())

            r = c.get(f"{BASE_URL}/api/v1/audios")
            comprobar("GET /audios lista los ejemplos", r.status_code == 200 and len(r.json()["audios"]) == 3)

            print("\n== 2. camino bueno ==")
            audio = BASE / "audios" / "02_horario.wav"
            datos = audio.read_bytes()
            r = post_audio(c, datos, audio.name, "audio/wav")
            comprobar("POST /transcribir responde 200", r.status_code == 200, r.text[:300])
            if r.status_code == 200:
                j = r.json()
                comprobar("devuelve transcripcion no vacia", bool(j.get("transcripcion")))
                comprobar("detecta el formato real del fichero", j.get("formato_detectado") == "wav")
                comprobar("detecta el idioma en espanol", j.get("idioma_detectado", "").startswith("es"))
                comprobar("devuelve la duracion del audio", j.get("duracion_s") and 5 < j["duracion_s"] < 6.5)
                comprobar("incluye un id de peticion para rastrear", bool(j.get("request_id")))
                comprobar("la cabecera X-Request-ID viene en la respuesta", "X-Request-ID" in r.headers)
                print(f"         transcripcion: {j['transcripcion']}")

                r2 = post_audio(c, datos, audio.name, "audio/wav")
                comprobar("la segunda peticion sale de la cache", r2.json().get("served_from_cache") is True)

            print("\n== 3. lo que el endpoint DEBE rechazar ==")
            r = post_audio(c, b"MZ\x90\x00" + b"\x00" * 3000, "virus.wav", "audio/wav")
            comprobar("rechaza un ejecutable renombrado a .wav", r.status_code == 400, f"{r.status_code} {r.text[:150]}")
            comprobar("el error explica el motivo", "audio" in r.json().get("detail", "").lower())

            r = post_audio(c, b"", "vacio.wav", "audio/wav")
            comprobar("rechaza un fichero vacio", r.status_code == 400, str(r.status_code))

            r = post_audio(c, wav(), "x.exe", "audio/wav")
            comprobar("rechaza una extension no admitida", r.status_code == 400, str(r.status_code))

            r = post_audio(c, wav(), "x.wav", "application/octet-stream")
            comprobar("rechaza un Content-Type que no es audio", r.status_code == 400, str(r.status_code))

            r = post_audio(c, wav(), "x.wav", "audio/mpeg")
            comprobar("rechaza MIME que no cuadra con el contenido", r.status_code == 400, str(r.status_code))

            r = post_audio(c, b"%PDF-1.7\n" + b"\x00" * 2000, "doc.wav", "audio/wav")
            comprobar("rechaza un PDF renombrado", r.status_code == 400, str(r.status_code))

            print("\n== 4. path traversal en servir_audio ==")
            for objetivo in ["../../../windows/win.ini", "..%2f..%2f.env", "....//....//.env", "01_presentacion.wav"]:
                r = c.get(f"{BASE_URL}/api/v1/audio/{objetivo}")
                if objetivo == "01_presentacion.wav":
                    comprobar("sigue sirviendo un audio legitimo", r.status_code == 200, str(r.status_code))
                else:
                    comprobar(f"no sirve '{objetivo}'", r.status_code in (400, 404), str(r.status_code))
                    if r.status_code == 200:
                        print("         -> ¡FUGA DE FICHEROS!")

            print("\n== 5. limite de peticiones por IP ==")
            # El limite por defecto es 10 peticiones por 60 s y a este punto ya
            # se han hecho varias, asi que deberia dispararse. Se mide cuantas
            # veces el servidor responde 429 por cuota propia del endpoint.
            codigos = []
            for _ in range(14):
                try:
                    codigos.append(post_audio(c, wav(0.2), "corto.wav", "audio/wav").status_code)
                except httpx.HTTPError as e:
                    codigos.append(f"error:{type(e).__name__}")
            n429 = codigos.count(429)
            print(f"         codigos: {codigos}")
            comprobar("el endpoint limita las peticiones por IP", n429 > 0,
                       f"ningun 429 en {len(codigos)} peticiones")
    finally:
        proceso.terminate()
        try:
            proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso.kill()
        log.close()

    print("\n== 6. resumen del log del servidor ==")
    texto_log = (BASE / "resultados" / "servidor.log").read_text(encoding="utf-8", errors="replace")
    lineas = [l for l in texto_log.splitlines() if l.strip()]
    for l in lineas[:14]:
        print("   " + l[:150])
    comprobar("el servidor registra las peticiones", any("->" in l for l in lineas))
    comprobar("el log NO contiene la API Key", "AQ.Ab" not in texto_log and "AIza" not in texto_log)

    print()
    if fallos:
        print(f"FALLARON {len(fallos)} prueba(s):")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("Todas las pruebas del endpoint pasan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
