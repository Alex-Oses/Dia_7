"""
Dia 7 (ASIX) - Metricas de calidad de la transcripcion.

La metrica estandar para medir un sistema de reconocimiento de voz es el WER
(Word Error Rate), tambien llamado tasa de error de palabra:

    WER = (S + D + I) / N

    S = sustituciones  (palabras CHANGE)
    D = borrados       (palabras OMITIDAS)
    I = inserciones    (palabras de SOBRA)
    N = palabras de referencia

Cuanto menor es el WER, mejor. 0% es transcripcion perfecta y por encima del
100% ya no es util (el sistema esta inventando mas que transcribiendo).

El calculo es una distancia de Levenshtein sobre PALABRAS, con la
retroinsercion en el camino que permite que una referencia larga y una
hipotesis corta sharean un unico borrado en lugar de contar cada palabra por
dos. Sin eso, "buenos dias" frente a "buenos dias senor" costaria 1 borrado + 2
inserciones en vez de 2 inserciones.
"""
import re
import unicodedata


def normalizar_texto(t: str) -> str:
    """Minusculas, sin puntuacion y con espacios colapsados.

    Los acentos NO se quitan a proposito: en español 'dia' y 'día' se
    pronuncian igual, pero para medir la transcripcion interesa distinguir
    'no' de 'nó' o 'sé' de 'se', asi que quitarlos esconderia errores reales.
    Si lo que se quiere es aislar los errores de PALABRA, usar
    normalizar_texto(..., ignorar_acentos=True).
    """
    t = unicodedata.normalize("NFC", str(t or "")).lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _sin_acentos(t: str) -> str:
    """Quita los diacriticos: 'día' -> 'dia'."""
    t = unicodedata.normalize("NFD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c))


def _operaciones(ref, hip):
    """Levenshtein sobre palabras, guardando que movimiento se eligio en cada celda.

    Devuelve (distancia, sustituciones, borrados, inserciones).
    """
    n, m = len(ref), len(hip)
    dist = [[0] * (m + 1) for _ in range(n + 1)]
    mov = [[None] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        dist[i][0] = i
        mov[i][0] = "D"          # se acabo la hipotesis: sobran referencias
    for j in range(1, m + 1):
        dist[0][j] = j
        mov[0][j] = "I"          # se acabo la referencia: sobran palabras

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hip[j - 1]:
                dist[i][j] = dist[i - 1][j - 1]
                mov[i][j] = "M"   # acierto, no cuenta como error
                continue
            # Con coste unitario, cuando las palabras coinciden la diagonal es
            # siempre la opcion optima, asi que se puede forzar sin perder
            # optimalidad.
            candidatos = (
                (dist[i - 1][j - 1] + 1, "S"),
                (dist[i - 1][j] + 1, "D"),
                (dist[i][j - 1] + 1, "I"),
            )
            dist[i][j], mov[i][j] = min(candidatos, key=lambda t: t[0])

    s = d = ins = 0
    x, y = n, m
    while x > 0 or y > 0:
        paso = mov[x][y]
        if paso == "M":
            x, y = x - 1, y - 1
        elif paso == "S":
            s += 1
            x, y = x - 1, y - 1
        elif paso == "D":
            d += 1
            x -= 1
        elif paso == "I":
            ins += 1
            y -= 1
        else:
            break
    return dist[n][m], s, d, ins

def wer(referencia: str, hipotesis: str, ignorar_acentos: bool = False):
    """Devuelve (wer, {"S":n, "I":n, "D":n}).

    Sin palabras de referencia devuelve (0.0, todo a cero): comparar contra una
    referencia vacia no tiene sentido, y devolver un 0 apareceria como un acierto
    perfecto cuando en realidad no hay nada que transcribir.

    ignorar_acentos=True pliega 'día' y 'dia' para medir solo los errores de
    palabra. Es utile aqui porque las frases de referencia se sintetizaron sin
    tildes: el modelo devuelve la ortografia correcta ('días') y, medido con
    acentos, eso parece un error cuando en realidad es una mejora.
    """
    prep = _sin_acentos if ignorar_acentos else (lambda s: s)
    ref = normalizar_texto(prep(referencia)).split()
    hip = normalizar_texto(prep(hipotesis)).split()
    if not ref:
        return 0.0, {"S": 0, "I": 0, "D": 0}
    dist, s, d, i_ = _operaciones(ref, hip)
    return round(dist / len(ref), 4), {"S": s, "I": i_, "D": d}


def palabras_iguales(referencia: str, hipotesis: str, ignorar_acentos: bool = False):
    """Precision y recall por palabra, de forma independiente del orden."""
    prep = _sin_acentos if ignorar_acentos else (lambda s: s)
    ref = normalizar_texto(prep(referencia)).split()
    hip = normalizar_texto(prep(hipotesis)).split()
    if not ref and not hip:
        return 1.0, 1.0
    comunes = 0
    restante = list(hip)
    for r in ref:
        if r in restante:
            restante.remove(r)
            comunes += 1
    r = comunes / len(ref) if ref else 0.0
    p = comunes / len(hip) if hip else 0.0
    return round(r, 4), round(p, 4)


def comparar(referencia: str, hipotesis: str):
    """Resumen completo de la comparacion, con y sin acentos."""
    w_exacto, ops = wer(referencia, hipotesis, ignorar_acentos=False)
    w_folded, _ = wer(referencia, hipotesis, ignorar_acentos=True)
    r_ex, p_ex = palabras_iguales(referencia, hipotesis, ignorar_acentos=False)
    r_fold, p_fold = palabras_iguales(referencia, hipotesis, ignorar_acentos=True)
    ref = normalizar_texto(referencia).split()
    return {
        "wer": w_exacto,
        "wer_sin_acentos": w_folded,
        "errores": ops,
        "recall_palabras": r_ex,
        "precision_palabras": p_ex,
        "recall_sin_acentos": r_fold,
        "precision_sin_acentos": p_fold,
        "n_palabras_referencia": len(ref),
    }
