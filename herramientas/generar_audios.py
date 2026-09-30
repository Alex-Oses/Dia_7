"""
Dia 7 (ASIX) - Genera los 3 audios de prueba en espanol.

Usa el sintetizador de voz de Windows (Microsoft Helena, es-ES) mediante
System.Speech, que esta disponible en cualquier Windows y no necesita red ni
API key. La ventaja para este dia es que la frase exacta de cada audio es
CONOCIDA, asi que la precision de la transcripcion se puede medir con rigor en
lugar de estimarse a ojo.

La sintesis se delega en PowerShell porque la libreria de Windows no es
accesible desde Python. Despues se normaliza cada WAV a mono 16 kHz 16 bits,
que es lo que espera la API de audio de Gemini.

Uso:  python generar_audios.py
"""
import json
import subprocess
import sys
import wave
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # raiz del proyecto, no la carpeta herramientas/
AUDIOS = BASE / "audios"
AUDIOS.mkdir(parents=True, exist_ok=True)

# (id, frase, velocidad, nota)  velocidad en -10..10 (negativo = mas lento)
FRASES = [
    (
        "01_presentacion",
        "Buenos dias, me llamo Alex y hoy presento la practica de servicios de voz "
        "para el grupo de sistemas.",
        -2,
        "Presentacion personal. Frase larga con nombre propio y pausa natural.",
    ),
    (
        "02_horario",
        "La reunion empieza a las nueve y media, en el aula dos, con el profesor "
        "de redes.",
        0,
        "Cita con hora y lugar. Comprueba que los numeros se transcriben bien.",
    ),
    (
        "03_incidencia",
        "El servidor del aula no funciona, hemos perdido la conexion a internet "
        "desde las nueve de la mañana.",
        1,
        "Parte de incidencia. Frase realista del ambito ASIX, con varios terminos tecnicos.",
    ),
]

VOZ = "Microsoft Helena Desktop Female"


def sintetizar(texto, destino: Path, velocidad, voz=VOZ):
    """Convierte texto en WAV usando el TTS de Windows via PowerShell."""
    guion = (
        "Add-Type -AssemblyName System.Speech; "
        f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SelectVoice('{voz}'); "
        f"$s.Rate = {velocidad}; "
        f"$s.SetOutputToWaveFile('{destino}'); "
        f"$s.Speak('{texto}'); "
        "$s.Dispose()"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", guion],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not destino.exists():
        raise RuntimeError(f"TTS fallo: {r.stderr.strip() or r.stdout.strip()}")


def normalizar(entrada: Path, salida: Path, tasa=16000):
    """Re-muestrea a mono 16 kHz 16 bits usando solo la libreria estandar."""
    with wave.open(str(entrada), "rb") as w:
        canales, anchura, freq, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        crudo = w.readframes(n)

    if anchura == 2:
        muestras = crudo
    elif anchura == 1:
        muestras = bytes((b - 128) if b > 127 else (b + 128) for b in crudo)  # unsigned 8 -> signed 16
    elif anchura == 4:
        muestras = crudo  # se recorta a 16 bits al reescribir
    else:
        raise RuntimeError(f"anchura de muestra no soportada: {anchura} bytes")

    if canales == 2:
        muestras = muestras[::2]  # downmix stereo -> mono tomando el canal izquierdo

    if freq != tasa and anchura == 2:
        import array
        buf = array.array("h")
        buf.frombytes(muestras)
        total = len(buf)
        nuevo = array.array("h", [0]) * int(total * tasa / freq)
        for i in range(len(nuevo)):
            pos = i * freq / tasa
            j = int(pos)
            nuevo[i] = buf[j] if j < total else 0
        muestras = nuevo.tobytes()

    with wave.open(str(salida), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tasa)
        w.writeframes(muestras)

    with wave.open(str(salida), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def main():
    print("Generando audios en espanol con la voz de Windows...")
    manifiesto = {
        "version": "1.0",
        "generado": datetime.now().isoformat(),
        "metodo": "Sintesis de voz de Windows (System.Speech), voz "
                  f"'{VOZ}' (es-ES). Sin red y sin API key.",
        "formato": "WAV mono 16 kHz 16 bits PCM (el que espera la API de audio de Gemini)",
        "nota": (
            "La frase exacta de cada audio figura en 'transcripcion_real'. Es la verdad "
            "terreno contra la que se mide el WER de la transcripcion, de modo que la "
            "precision es medible y no una estimacion. La voz es sintetica, no humana: las "
            "cifras con voz humana suelen ser MEJORES que estas, asi que el WER medido aqui "
            "es una cota pesimista."
        ),
        "audios": [],
    }

    for ident, frase, velocidad, nota in FRASES:
        crudo = AUDIOS / f"{ident}_crudo.wav"
        final = AUDIOS / f"{ident}.wav"
        print(f"\n  {ident}")
        print(f"    \"{frase}\"")
        sintetizar(frase, crudo, velocidad)
        dur = normalizar(crudo, final)
        crudo.unlink(missing_ok=True)
        kb = final.stat().st_size / 1024
        print(f"    -> {final.name}  {dur:.1f} s  {kb:.0f} KB")
        manifiesto["audios"].append({
            "id": ident,
            "archivo": final.name,
            "duracion_s": round(dur, 2),
            "bytes": final.stat().st_size,
            "transcripcion_real": frase,
            "velocidad_tts": velocidad,
            "nota": nota,
        })

    (AUDIOS / "verdad_terreno.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nManifiesto: {(AUDIOS / 'verdad_terreno.json').relative_to(BASE)}")
    print(f"Reproducir uno:  Start-Process {AUDIOS / '01_presentacion.wav'}")


if __name__ == "__main__":
    sys.exit(main())
