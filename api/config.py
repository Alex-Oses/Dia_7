"""
Dia 7 (ASIX) - Configuracion del servicio de transcripcion.

Separa la configuracion del codigo y la carga una sola vez al importar. La API
Key se lee del entorno y NUNCA se expone al cliente: vive solo en el servidor.
Es el punto de seguridad mas importante de toda la practica, y por eso esta
aqui separado y comentado, en vez de disperso por el codigo.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIOS_DIR = BASE_DIR / "audios"
RESULTADOS_DIR = BASE_DIR / "resultados"
CACHE_DIR = RESULTADOS_DIR / ".cache"
for d in (AUDIOS_DIR, RESULTADOS_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# --- credenciales (servidor) -------------------------------------------------
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# --- modelos ------------------------------------------------------------------
# El Dia 6 demostro que los modelos mas nuevos devuelven 503 con frecuencia en
# el free tier. Se prueba la cadena en orden y se usa el primero que responda.
CADENA_MODELOS = []
for _m in [
    os.getenv("GEMINI_MODEL", "").strip(),
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]:
    if _m and _m not in CADENA_MODELOS:
        CADENA_MODELOS.append(_m)

_modelo_activo = None


def get_client():
    """Cliente de Gemini. Falla rapido y con un mensaje util si falta la key."""
    if not API_KEY:
        raise RuntimeError(
            "Falta GEMINI_API_KEY. Copia .env.example a .env y pega la key. "
            "La key solo puede estar en el servidor: jamas en el cliente web."
        )
    try:
        from google import genai
    except ImportError:
        raise RuntimeError(
            "Falta la libreria 'google-genai'. Activa el venv del Dia 5:\n"
            "  .\\venv\\Scripts\\Activate.ps1; pip install -r requirements.txt"
        ) from None
    return genai.Client(api_key=API_KEY)


def marcar_modelo(m):
    global _modelo_activo
    _modelo_activo = m


def modelo_usado():
    return _modelo_activo


# --- limites de seguridad -----------------------------------------------------
# Tope de tamaño. La API de Gemini acepta hasta ~20 MB en inline_data; aqui se
# corta antes para no gastar cuota con un fichero enorme.
MAX_BYTES = int(os.getenv("MAX_AUDIO_BYTES", 10 * 1024 * 1024))
MAX_SEGUNDOS = int(os.getenv("MAX_AUDIO_SEGUNDOS", 300))
TIMEOUT_SEGUNDOS = float(os.getenv("GEMINI_TIMEOUT", 120))

# Los formatos admitidos por la API de Gemini (segun su documentacion oficial)
# y los MIME permitidos para cada uno. La lista vive en
# api/transcripcion.py -> FORMATOS, junto con las firmas de cada formato, para
# que la lista blanca y la deteccion por contenido no puedan separarse.
# Se declara aqui solo para que /api/v1/salud pueda anunciarlos.

# Orígenes autorizados a llamar a la API desde el navegador. Se limita a los
# servidores locales de desarrollo: un CORS abierto con "*" permitiria que
# cualquier web de internet enviase peticiones a nuestra API usando nuestra key.
ORIGENES_PERMITIDOS = [
    o.strip()
    for o in os.getenv("CORS_ORIGENES", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500").split(",")
    if o.strip()
]

# Peticiones por cliente y ventana. Evita que un bucle propio agote la cuota
# compartida de Gemini de todo el aula.
RATE_LIMITE = int(os.getenv("RATE_LIMITE", 10))
RATE_VENTANA_S = float(os.getenv("RATE_VENTANA_S", 60))
