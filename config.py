"""Configuración de Jarvis.

- `jarvis.env` en la carpeta compartida (Proyectos/Jarvis, sincronizada con Syncthing) vale para todos los equipos.
- `.env` junto al programa vale para esa instalación.
- `local.env` en la carpeta local del equipo tiene prioridad y vale solo para ese equipo.
"""
import os
import platform
import socket
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
ES_WIN = sys.platform == "win32"
ES_MAC = sys.platform == "darwin"

if ES_WIN:
    LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis"
elif ES_MAC:
    LOCAL = Path.home() / "Library" / "Application Support" / "Jarvis"
else:
    LOCAL = Path.home() / ".jarvis"
LOCAL.mkdir(parents=True, exist_ok=True)


def _cargar_env(ruta):
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


# Carpeta compartida entre equipos (Syncthing): memoria, bóveda, alarmas, ajustes comunes…
_PROYECTOS = Path.home() / "Proyectos" / "Jarvis"


_cargar_env(LOCAL / "local.env")   # primero lo local: manda sobre lo compartido
COMPARTIDA = Path(os.getenv("JARVIS_COMPARTIDA") or
                  (_PROYECTOS if _PROYECTOS.exists() else CARPETA))
_cargar_env(COMPARTIDA / "jarvis.env")  # ajustes comunes a todos los equipos
_cargar_env(CARPETA / ".env")           # ajustes de la instalación (clave de DeepSeek del PC, etc.)


def _int(nombre, defecto):
    try:
        return int(os.getenv(nombre, defecto))
    except ValueError:
        return int(defecto)


# ----------------------------------------------------------- equipo
EQUIPO = os.getenv("EQUIPO", "pc" if ES_WIN else "mac" if ES_MAC else socket.gethostname()).lower()
SISTEMA_OS = "Windows" if ES_WIN else "macOS" if ES_MAC else platform.system()
# Cuanto más alto, más preferencia para ser el equipo principal (Telegram, WhatsApp, avisos)
PRIORIDAD = int(os.getenv("PRIORIDAD", 2 if ES_WIN else 1))

# ---------------------------------------------------------------- IA
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODELO = os.getenv("DEEPSEEK_MODELO", "deepseek-flash")
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
# Límite de gasto diario en dólares (0 = sin límite). Jarvis avisa al superarlo.
LIMITE_DIARIO_USD = float(os.getenv("LIMITE_DIARIO_USD", "0.50"))

# --------------------------------------------------------------- voz
PALABRA_CLAVE = os.getenv("PALABRA_CLAVE", "jarvis").lower()
# «Hey Jarvis» detectado en el propio equipo (openWakeWord): no sale audio hasta oírlo
PALABRA_LOCAL = os.getenv("PALABRA_LOCAL", "0") == "1"
UMBRAL_PALABRA = float(os.getenv("UMBRAL_PALABRA", "0.5"))
VOZ = os.getenv("VOZ", "es-ES-AlvaroNeural")
VELOCIDAD_VOZ = os.getenv("VELOCIDAD_VOZ", "+8%")
IDIOMA = os.getenv("IDIOMA", "es-ES")
TRATAMIENTO = os.getenv("TRATAMIENTO", "señor")
CIUDAD = os.getenv("CIUDAD", "")
ATAJO = os.getenv("ATAJO", "ctrl+alt+j")
SEGUIMIENTO = _int("SEGUIMIENTO", 8)
MEMORIA_TURNOS = _int("MEMORIA_TURNOS", 6)

# ------------------------------------------------------- riego / MQTT
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PUERTO = _int("MQTT_PUERTO", 1883)
MQTT_USUARIO = os.getenv("MQTT_USUARIO", "")
MQTT_CLAVE = os.getenv("MQTT_CLAVE", "")
MQTT_TEMAS = os.getenv("MQTT_TEMAS", "riego/#")
VALVULA_TEMA = os.getenv("VALVULA_TEMA", "riego/valvula/{id}/set")
VALVULA_ON = os.getenv("VALVULA_ON", "ON")
VALVULA_OFF = os.getenv("VALVULA_OFF", "OFF")
ALERTA_DEPOSITO = _int("ALERTA_DEPOSITO", 20)       # % mínimo del depósito
ALERTA_NODO_MIN = _int("ALERTA_NODO_MIN", 30)       # minutos sin datos de un nodo
ALERTA_HUMEDAD = _int("ALERTA_HUMEDAD", 0)          # % mínimo de humedad (0 = no avisar)
ALERTA_LLUVIA = os.getenv("ALERTA_LLUVIA", "1") == "1"

# ---------------------------------------------------- ESP32 / proyectos
# Por defecto, la carpeta Proyectos que contiene a Jarvis 
_proy_defecto = next((str(p) for p in CARPETA.parents if p.name.lower() == "proyectos"),
                     str(Path.home() / "Proyectos"))
PROYECTOS = [p.strip() for p in os.getenv("PROYECTOS", _proy_defecto).split(";") if p.strip()]
PIO = os.getenv("PIO", str(Path.home() / ".platformio" / "penv" /
                           ("Scripts/pio.exe" if ES_WIN else "bin/pio")))

# ------------------------------------------------------------- móvil
# Dónde reenviar los avisos (alarmas, riego, resultados): telegram, whatsapp, ambos o ninguno
AVISOS_MOVIL = os.getenv("AVISOS_MOVIL", "telegram,whatsapp").lower()
BOVEDA_REMOTA = os.getenv("BOVEDA_REMOTA", "0") == "1"  # permitir pedir claves desde el móvil
# WhatsApp propio de Jarvis (Cloud API): tu número, con prefijo y sin "+", es el único autorizado
WA_USUARIO = os.getenv("WA_USUARIO", "").replace("+", "").replace(" ", "")
RESPUESTA_VOZ = os.getenv("RESPUESTA_VOZ", "voz").lower()  # móvil: voz (a un audio, solo audio) | siempre | nunca
WA_MODO = os.getenv("WA_MODO", "web").lower()   # web = vinculado como WhatsApp Web · cloud = API de Meta
WA_PUERTO = _int("WA_PUERTO", 8765)
WA_GRAPH_VERSION = os.getenv("WA_GRAPH_VERSION", "v23.0")

# ------------------------------------------------------------ Claude
CLAUDE = os.getenv("CLAUDE", "")  # ruta a claude.exe; vacío = buscarlo solo

DATOS = COMPARTIDA / "datos"
DATOS.mkdir(parents=True, exist_ok=True)
