"""
Dia 7 (ASIX) - Evalua la precision de la transcripcion de los 3 audios.

Procesa cada audio de audios/verdad_terreno.json, transcribe con Gemini y
compara con la frase original mediante el WER (word error rate), que es la
metrica estandar de los sistemas de reconocimiento de voz.

El WER se reporta de dos formas a proposito:
  - wer             : sensible a acentos
  - wer_sin_acentos : pliega 'día' y 'dia'
Las frases de referencia se sintetizaron SIN tildes ('practica'), pero el
modelo devuelve la ortografia correcta ('práctica'). Medido con acentos, esa
mejora pareceria un error. Se publica el dato crudo y el dato plegado, con su
explicacion, en vez de elegir el que queda mejor.

Uso:
    python evaluar_audio.py             # transcribe lo que falte (cache)
    python evaluar_audio.py --forzar    # ignora la cache y vuelve a pagar
    python evaluar_audio.py --servidor  # pasa por el endpoint HTTP en vez de llamar directo
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import config as cfg
from api import transcripcion as t
from api.metricas import comparar, normalizar_texto

BASE = Path(__file__).resolve().parent
SALIDA_JSON = BASE / "resultados" / "transcripciones.json"
SALIDA_MD = BASE / "resultados" / "informe_precision.md"


def transcribir_via_servidor(ruta: Path):
    """Pasa el audio por el endpoint HTTP en vez de llamar a Gemini directamente.

    Sirve para comprobar el camino completo (validacion + red + serializacion),
    que es lo que un cliente real usaria. Requiere el servidor arrancado.
    """
    import httpx
    url = "http://127.0.0.1:8000/api/v1/transcribir"
    with open(ruta, "rb") as f:
        r = httpx.post(url, files={"audio": (ruta.name, f, "audio/wav")}, timeout=cfg.TIMEOUT_SEGUNDOS)
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description="Dia 7 - evalua la precision de la transcripcion")
    ap.add_argument("--forzar", action="store_true", help="ignora la cache y repite las llamadas")
    ap.add_argument("--servidor", action="store_true", help="usar el endpoint HTTP en vez de la llamada directa")
    args = ap.parse_args()

    manifiesto = json.loads((cfg.AUDIOS_DIR / "verdad_terreno.json").read_text(encoding="utf-8"))
    audios = manifiesto["audios"]
    print(f"Audios a evaluar: {len(audios)}")
    print(f"Modelo preferido: {cfg.CADENA_MODELOS[0]}\n")

    previos = {}
    if SALIDA_JSON.exists() and not args.forzar:
        previos = {a["id"]: a for a in json.loads(SALIDA_JSON.read_text(encoding="utf-8")).get("audios", [])}

    filas = []
    for i, a in enumerate(audios, 1):
        ruta = cfg.AUDIOS_DIR / a["archivo"]
        if not ruta.exists():
            print(f"  {a['id']}: falta {a['archivo']}, ejecuta herramientas/generar_audios.py")
            continue

        if a["id"] in previos and not args.forzar:
            print(f"[{i}/{len(audios)}] {a['id']}: ya evaluado, se conserva")
            filas.append(previos[a["id"]])
            continue

        print(f"[{i}/{len(audios)}] {a['id']}  ({a['duracion_s']}s)")
        try:
            if args.servidor:
                r = transcribir_via_servidor(ruta)
                transcripcion = r.get("transcripcion", "")
                modelo = r.get("modelo")
                idioma = r.get("idioma_detectado")
                segmentos = r.get("segmentos") or []
            else:
                datos = ruta.read_bytes()
                formato, mime, dur = t.validar_audio(datos, ruta.name, "audio/wav")
                r = t.transcribir(datos, mime, etiqueta=a["id"])
                transcripcion = r["transcripcion"]
                modelo = r.get("modelo")
                idioma = r.get("idioma")
                segmentos = r.get("segmentos") or []
        except Exception as e:  # noqa: BLE001
            print(f"    FALLO: {str(e)[:160]}")
            continue

        m = comparar(a["transcripcion_real"], transcripcion)
        fila = {
            "id": a["id"],
            "archivo": a["archivo"],
            "duracion_s": a["duracion_s"],
            "nota": a.get("nota"),
            "transcripcion_real": a["transcripcion_real"],
            "transcripcion_obtenida": transcripcion,
            "idioma_detectado": idioma,
            "modelo": modelo,
            "n_segmentos": len(segmentos),
            "segmentos": segmentos,
            **m,
        }
        filas.append(fila)
        print(f"    referencia : {a['transcripcion_real']}")
        print(f"    obtenida   : {transcripcion}")
        print(f"    WER {m['wer']:.1%} | sin acentos {m['wer_sin_acentos']:.1%} | "
              f"palabras S{m['errores']['S']} I{m['errores']['I']} D{m['errores']['D']}")

    if not filas:
        print("No hay resultados.")
        return 1

    resumen = {
        "generado": datetime.now().isoformat(),
        "modelos": sorted({f.get("modelo") or "?" for f in filas}),
        "n_audios": len(filas),
        "wer_medio": round(sum(f["wer"] for f in filas) / len(filas), 4),
        "wer_medio_sin_acentos": round(sum(f["wer_sin_acentos"] for f in filas) / len(filas), 4),
        "recall_medio": round(sum(f["recall_sin_acentos"] for f in filas) / len(filas), 4),
        "precision_medio": round(sum(f["precision_sin_acentos"] for f in filas) / len(filas), 4),
    }

    SALIDA_JSON.write_text(json.dumps({"resumen": resumen, "audios": filas}, ensure_ascii=False, indent=2), encoding="utf-8")
    escribir_md(filas, resumen)

    print("\n" + "=" * 66)
    print(f"  WER medio (con acentos)     : {resumen['wer_medio']:.1%}")
    print(f"  WER medio (sin acentos)     : {resumen['wer_medio_sin_acentos']:.1%}")
    print(f"  Recall medio de palabras    : {resumen['recall_medio']:.1%}")
    print(f"  Precision media de palabras : {resumen['precision_medio']:.1%}")
    print("=" * 66)
    print(f"  {SALIDA_JSON.relative_to(BASE)}")
    print(f"  {SALIDA_MD.relative_to(BASE)}")
    return 0


def escribir_md(filas, resumen):
    L = []
    L.append("# Precision de la transcripcion de voz (Dia 7 - ASIX)\n")
    L.append(f"Modelos: {', '.join(resumen['modelos'])}  ")
    L.append(f"Audios evaluados: **{resumen['n_audios']}**\n")

    L.append("## Resultado global\n")
    L.append("| Metrica | Valor |")
    L.append("|---|---|")
    L.append(f"| WER medio (sensible a acentos) | **{resumen['wer_medio']:.1%}** |")
    L.append(f"| WER medio (sin acentos) | **{resumen['wer_medio_sin_acentos']:.1%}** |")
    L.append(f"| Recall de palabras | {resumen['recall_medio']:.1%} |")
    L.append(f"| Precision de palabras | {resumen['precision_medio']:.1%} |")
    L.append("\n> **Por que dos WER.** Las frases de referencia se sintetizaron sin tildes "
             "(`practica`), pero el modelo devuelve la ortografia correcta (`práctica`). "
             "Medido con acentos, esa mejora cuenta como dos errores de palabra. El WER sin "
             "acentos aísla los errores de vocabulario reales. Se publican los dos en vez de "
             "quedarse con el mas favorable.\n")

    L.append("## Detalle por audio\n")
    L.append("| Audio | Dur. | WER | WER s/acentos | S | I | D | Palabras ref. |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for f in filas:
        L.append(f"| `{f['archivo']}` | {f['duracion_s']}s | {f['wer']:.1%} | "
                 f"{f['wer_sin_acentos']:.1%} | {f['errores']['S']} | {f['errores']['I']} | "
                 f"{f['errores']['D']} | {f['n_palabras_referencia']} |")

    L.append("\n## Transcripciones\n")
    for f in filas:
        L.append(f"### `{f['archivo']}`")
        L.append(f"- **Nota**: {f.get('nota')}")
        L.append(f"- **Idioma detectado**: {f['idioma_detectado']} | **Modelo**: {f['modelo']}")
        L.append(f"- **Segmentos devueltos**: {f['n_segmentos']}")
        L.append(f"- **Referencia**  : {f['transcripcion_real']}")
        L.append(f"- **Transcripcion**: {f['transcripcion_obtenida']}\n")

        ref = normalizar_texto(f["transcripcion_real"]).split()
        hip = normalizar_texto(f["transcripcion_obtenida"]).split()
        if ref != hip:
            L.append("Diferencias palabra a palabra:")
            for i in range(max(len(ref), len(hip))):
                a = ref[i] if i < len(ref) else "-"
                b = hip[i] if i < len(hip) else "-"
                if a != b:
                    L.append(f"  - posicion {i + 1}: referencia `{a}` / obtenida `{b}`")
            L.append("")

    SALIDA_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
