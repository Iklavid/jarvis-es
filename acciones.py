"""Acciones que Jarvis puede hacer en el equipo (Windows o macOS)."""
import json
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta

import psutil
import pyautogui
import requests

import config
import notas

pyautogui.FAILSAFE = False
ARCHIVO_ALARMAS = config.DATOS / "alarmas.json"

CODIGOS_TIEMPO = {
    0: "despejado", 1: "casi despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "con niebla", 48: "con niebla", 51: "con llovizna débil", 53: "con llovizna",
    55: "con llovizna intensa", 61: "con lluvia débil", 63: "con lluvia", 65: "con lluvia fuerte",
    71: "con nieve débil", 73: "con nieve", 75: "con nieve intensa", 80: "con chubascos",
    81: "con chubascos", 82: "con chubascos fuertes", 95: "con tormenta", 96: "con tormenta y granizo",
    99: "con tormenta y granizo",
}


def _osascript(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()


def notificar(titulo, mensaje=""):
    if config.ES_MAC:
        esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
        _osascript(f'display notification "{esc(mensaje)}" with title "{esc(titulo)}"')
        return
    if not config.ES_WIN:  # Linux (KDE): notificación del escritorio
        try:
            subprocess.run(["notify-send", "-a", "J.A.R.V.I.S.", "-i", "audio-input-microphone",
                            str(titulo), str(mensaje)], timeout=5)
        except Exception as e:
            print(f"[aviso] {titulo} {mensaje} ({e})")
        return
    try:
        from winotify import Notification, audio
        n = Notification(app_id="J.A.R.V.I.S.", title=titulo, msg=mensaje, duration="short")
        n.set_audio(audio.Default, loop=False)
        n.show()
    except Exception as e:
        print(f"[aviso] {titulo} {mensaje} ({e})")


# ------------------------------------------------------------- volumen
def _endpoint_volumen():
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL, CoInitialize
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    CoInitialize()
    altavoces = AudioUtilities.GetSpeakers()
    if hasattr(altavoces, "EndpointVolume"):  # pycaw moderno
        return altavoces.EndpointVolume
    interfaz = altavoces.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interfaz, POINTER(IAudioEndpointVolume))


def _volumen_mac(valor):
    actual = int(_osascript("output volume of (get volume settings)") or 50)
    if valor == "consultar":
        return f"El volumen está al {actual} por ciento."
    if valor == "silencio":
        _osascript("set volume output muted (not (output muted of (get volume settings)))")
        return None
    n = {"subir": actual + 15, "bajar": actual - 15}.get(valor, valor)
    _osascript(f"set volume output volume {max(0, min(100, int(n)))} without output muted")
    return None


def _volumen_linux(valor):
    s = "@DEFAULT_AUDIO_SINK@"
    if valor == "consultar":
        r = subprocess.run(["wpctl", "get-volume", s], capture_output=True, text=True).stdout
        try:
            return f"El volumen está al {round(float(r.split()[1]) * 100)} por ciento."
        except (IndexError, ValueError):
            return "No consigo leer el volumen."
    if valor == "silencio":
        subprocess.run(["wpctl", "set-mute", s, "toggle"])
        return None
    if valor in ("subir", "bajar"):
        subprocess.run(["wpctl", "set-mute", s, "0"])
        subprocess.run(["wpctl", "set-volume", "-l", "1.0", s, "15%+" if valor == "subir" else "15%-"])
        return None
    n = max(0, min(100, int(valor)))
    subprocess.run(["wpctl", "set-mute", s, "0"])
    subprocess.run(["wpctl", "set-volume", s, f"{n}%"])
    return None


def volumen(valor):
    if config.ES_MAC:
        return _volumen_mac(valor)
    if not config.ES_WIN:
        return _volumen_linux(valor)
    if valor == "subir":
        pyautogui.press("volumeup", presses=5)
        return None
    if valor == "bajar":
        pyautogui.press("volumedown", presses=5)
        return None
    if valor == "silencio":
        pyautogui.press("volumemute")
        return None
    try:
        vol = _endpoint_volumen()
        if valor == "consultar":
            return f"El volumen está al {round(vol.GetMasterVolumeLevelScalar() * 100)} por ciento."
        n = max(0, min(100, int(valor)))
        vol.SetMute(0, None)
        vol.SetMasterVolumeLevelScalar(n / 100, None)
        return None
    except Exception as e:
        print(f"[volumen] {e}")
        return "No he podido acceder al control de volumen."


def brillo(valor):
    if config.ES_MAC:
        return "En el Mac todavía no puedo cambiar el brillo; usa las teclas de brillo."
    try:
        import screen_brightness_control as sbc
        if valor == "consultar":
            return f"El brillo está al {sbc.get_brightness()[0]} por ciento."
        sbc.set_brightness(max(0, min(100, int(valor))))
        return None
    except Exception as e:
        print(f"[brillo] {e}")
        return "No puedo cambiar el brillo de esta pantalla."


def bateria():
    b = psutil.sensors_battery()
    if b is None:
        return "Este equipo no tiene batería; estamos enchufados a la red."
    estado = "y cargando" if b.power_plugged else "sin cargador"
    return f"La batería está al {round(b.percent)} por ciento {estado}."


# -------------------------------------------------------------- tiempo
def tiempo(ciudad):
    ciudad = (ciudad or config.CIUDAD or "").strip()
    if not ciudad:
        return "Dime de qué ciudad quieres saber el tiempo."
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": ciudad, "count": 1, "language": "es"}, timeout=10,
        ).json()
        if not geo.get("results"):
            return f"No encuentro la ciudad {ciudad}."
        lugar = geo["results"][0]
        datos = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lugar["latitude"], "longitude": lugar["longitude"],
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 1,
            }, timeout=10,
        ).json()
        act, dia = datos["current"], datos["daily"]
        cielo = CODIGOS_TIEMPO.get(act["weather_code"], "con tiempo variable")
        return (
            f"En {lugar['name']} ahora hay {round(act['temperature_2m'])} grados y está {cielo}. "
            f"Hoy la máxima será de {round(dia['temperature_2m_max'][0])} y la mínima de "
            f"{round(dia['temperature_2m_min'][0])}, con un {dia['precipitation_probability_max'][0] or 0} "
            f"por ciento de probabilidad de lluvia."
        )
    except Exception as e:
        print(f"[tiempo] {e}")
        return "No he podido consultar el tiempo ahora mismo."


# ------------------------------------------------- recordatorios/alarmas
class Agenda:
    """Alarmas compartidas entre equipos (datos/alarmas.json). Solo las dispara el principal."""

    def __init__(self, avisar, activo=None):
        self.avisar = avisar  # función(texto) que habla
        self.activo = activo or (lambda: True)
        self.lock = threading.Lock()
        threading.Thread(target=self._vigilar, daemon=True).start()

    @property
    def alarmas(self):
        return self._cargar()

    def _cargar(self):
        try:
            return json.loads(ARCHIVO_ALARMAS.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _guardar(self, lista):
        ARCHIVO_ALARMAS.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")

    def nueva(self, momento, texto):
        with self.lock:
            lista = self._cargar()
            lista.append({"cuando": momento.isoformat(timespec="minutes"), "texto": texto})
            self._guardar(lista)

    def recordatorio(self, minutos, texto):
        self.nueva(datetime.now() + timedelta(minutes=float(minutos)), texto or "Tu recordatorio")

    def alarma(self, hora, texto):
        h, m = [int(x) for x in str(hora).split(":")[:2]]
        momento = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        if momento <= datetime.now():
            momento += timedelta(days=1)
        self.nueva(momento, texto or "Alarma")

    def _vigilar(self):
        while True:
            time.sleep(15)
            if not self.activo():
                continue
            ahora = datetime.now()
            with self.lock:
                lista = self._cargar()
                vencidas = [a for a in lista if datetime.fromisoformat(a["cuando"]) <= ahora]
                if vencidas:
                    self._guardar([a for a in lista if a not in vencidas])
            for a in vencidas:
                notificar("Recordatorio", a["texto"])
                self.avisar(f"{config.TRATAMIENTO.capitalize()}, le recuerdo: {a['texto']}")


# ------------------------------------------------------------- teclado
ATAJOS = {
    "nueva_pestana": ("ctrl", "t"), "cerrar_pestana": ("ctrl", "w"),
    "cerrar_ventana": ("alt", "f4"), "minimizar": ("win", "down"),
    "escritorio": ("win", "d"), "captura": ("win", "shift", "s"),
    "pantalla_completa": ("f11",), "atras": ("alt", "left"), "recargar": ("f5",),
}
if config.ES_MAC:
    ATAJOS = {
        "nueva_pestana": ("command", "t"), "cerrar_pestana": ("command", "w"),
        "cerrar_ventana": ("command", "w"), "minimizar": ("command", "m"),
        "escritorio": ("command", "f3"), "captura": ("command", "shift", "4"),
        "pantalla_completa": ("command", "ctrl", "f"), "atras": ("command", "["), "recargar": ("command", "r"),
    }
PEGAR = ("command", "v") if config.ES_MAC else ("ctrl", "v")
MULTIMEDIA = {"pausa": "playpause", "siguiente": "nexttrack", "anterior": "prevtrack"}
APPS_MAC = {"notepad": "TextEdit", "bloc de notas": "TextEdit", "calc": "Calculator", "calculadora": "Calculator",
            "explorer": "Finder", "chrome": "Google Chrome", "terminal": "Terminal", "vscode": "Visual Studio Code",
            "code": "Visual Studio Code", "word": "Microsoft Word", "excel": "Microsoft Excel"}

APPS_LINUX = {"notepad": "kate", "bloc de notas": "kate", "editor": "kate", "calc": "kcalc", "calculadora": "kcalc",
              "explorer": "dolphin", "explorador": "dolphin", "archivos": "dolphin", "terminal": "konsole",
              "cmd": "konsole", "vscode": "code", "code": "code", "visual studio code": "code",
              "chrome": "google-chrome-stable brave firefox", "navegador": "brave firefox google-chrome-stable"}


def _abrir_app_linux(nombre):
    import shutil
    from pathlib import Path
    clave = nombre.lower()
    candidatos = APPS_LINUX[clave].split() if clave in APPS_LINUX else [clave]
    for cmd in candidatos:
        if shutil.which(cmd):
            subprocess.Popen([cmd], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    # Si no es un comando, buscar su lanzador en el menú de aplicaciones por nombre
    for carpeta in (Path.home() / ".local/share/applications", Path("/usr/share/applications"),
                    Path("/var/lib/flatpak/exports/share/applications")):
        if not carpeta.exists():
            continue
        for f in sorted(carpeta.glob("*.desktop")):
            try:
                lineas = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            nombres = [l.split("=", 1)[1].lower() for l in lineas if l.startswith(("Name=", "Name[es]="))]
            if any(clave in n for n in nombres):
                subprocess.Popen(["gio", "launch", str(f)], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    notificar("No encuentro la aplicación", nombre)


def abrir_app(nombre):
    nombre = (nombre or "").strip()
    if config.ES_MAC:
        app = APPS_MAC.get(nombre.lower(), nombre)
        if subprocess.run(["open", "-a", app], capture_output=True).returncode != 0:
            subprocess.run(["open", "-a", app.title()], capture_output=True)
        return
    if not config.ES_WIN:
        return _abrir_app_linux(nombre)
    directos = {"notepad", "calc", "explorer", "cmd", "mspaint", "taskmgr", "control", "powershell"}
    if nombre.lower() in directos:
        subprocess.Popen(f'start "" {nombre.lower()}', shell=True)
        return
    # Para el resto, igual que haría una persona: tecla Windows, escribir y Enter
    pyautogui.press("win")
    time.sleep(0.6)
    pyautogui.write(nombre, interval=0.03)
    time.sleep(0.8)
    pyautogui.press("enter")


def youtube(consulta):
    try:
        import pywhatkit
        pywhatkit.playonyt(consulta)
    except Exception:
        webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(consulta))


# ---------------------------------------------------------- ejecutor
# Cada acción devuelve:
#   None                -> nada que decir
#   ("decir", texto)    -> se dice tal cual
#   ("dato", texto)     -> la IA lo resume antes de decirlo
# Las acciones lentas se lanzan con ctx.en_segundo_plano(funcion, peticion).

def _proyecto(ctx, nombre, obligatorio=True):
    import esp32
    if not nombre:
        return None, (("decir", "¿De qué proyecto, señor?") if obligatorio else None)
    p = esp32.buscar_proyecto(nombre)
    if not p:
        return None, ("decir", f"No encuentro ningún proyecto llamado {nombre}.")
    return p, None


def ejecutar(accion, ctx):
    import boveda
    import claude_puente
    import esp32
    import gasto
    import memoria

    tipo = accion.get("tipo")
    try:
        # ------------------------------------------------------- PC
        if tipo == "abrir_web":
            url = accion.get("url", "")
            webbrowser.open(url if url.startswith("http") else "https://" + url)
        elif tipo == "buscar_google":
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(accion.get("consulta", "")))
        elif tipo == "youtube":
            youtube(accion.get("consulta", ""))
        elif tipo == "abrir_app":
            abrir_app(accion.get("nombre"))
        elif tipo == "volumen":
            r = volumen(accion.get("valor", "consultar"))
            return ("decir", r) if r else None
        elif tipo == "brillo":
            r = brillo(accion.get("valor", "consultar"))
            return ("decir", r) if r else None
        elif tipo == "bateria":
            return ("decir", bateria())
        elif tipo == "tiempo":
            return ("decir", tiempo(accion.get("ciudad")))
        elif tipo == "recordatorio":
            ctx.agenda.recordatorio(accion.get("minutos", 5), accion.get("texto"))
        elif tipo == "alarma":
            ctx.agenda.alarma(accion.get("hora"), accion.get("texto"))
        elif tipo == "multimedia":
            pyautogui.press(MULTIMEDIA.get(accion.get("accion"), "playpause"))
        elif tipo == "teclas":
            combo = ATAJOS.get(accion.get("atajo"))
            if combo:
                pyautogui.hotkey(*combo)
        elif tipo == "escribir":
            import pyperclip
            pyperclip.copy(accion.get("texto", ""))
            pyautogui.hotkey(*PEGAR)
        elif tipo == "bloquear_pc":
            if config.ES_MAC:
                subprocess.Popen(["pmset", "displaysleepnow"])
            elif config.ES_WIN:
                subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
            else:
                subprocess.Popen(["loginctl", "lock-session"])
        elif tipo == "apagar_jarvis":
            ctx.apagar()

        # --------------------------------------------------- memoria
        elif tipo == "recordar":
            memoria.recordar(accion.get("dato", ""))
        elif tipo == "olvidar":
            n = memoria.olvidar(accion.get("texto", ""))
            if not n:
                return ("decir", "No encuentro nada así en mi memoria.")
        elif tipo == "nota":
            error = notas.anotar(accion.get("texto", ""), "Hecho" if accion.get("apartado") == "hecho" else "Notas")
            if error:
                return ("decir", error)
        elif tipo == "buscar_conversaciones":
            return ("dato", memoria.buscar_diario(accion.get("consulta", "")) or "No hay conversaciones que coincidan.")

        # ---------------------------------------------------- bóveda
        elif tipo in ("boveda_copiar", "boveda_mostrar"):
            if getattr(ctx, "remoto", False) and not config.BOVEDA_REMOTA:
                return ("decir", "Por seguridad no envío claves de la bóveda al móvil. "
                                 "Si lo quieres, activa BOVEDA_REMOTA=1 en el archivo .env.")
            nombre, valor = boveda.obtener(accion.get("nombre", ""))
            if valor and getattr(ctx, "remoto", False):
                ctx.telegram.enviar_secreto(nombre, valor)
                return None
            if not valor:
                return ("decir", f"No tengo ninguna clave llamada {accion.get('nombre', '')} en la bóveda.")
            if tipo == "boveda_copiar":
                boveda.copiar_temporal(valor)
                notificar("Bóveda", f"«{nombre}» copiada. Se borra del portapapeles en 30 s.")
            else:
                ctx.ui(secreto=(nombre, valor))
        elif tipo == "boveda_nueva":
            ctx.ui(abrir="boveda", prefill={"nombre": accion.get("nombre", ""),
                                            "tipo": accion.get("tipo_clave", "otro"),
                                            "descripcion": accion.get("descripcion", "")})
        elif tipo == "boveda_borrar":
            if not boveda.borrar(accion.get("nombre", "")):
                return ("decir", "No encuentro esa clave en la bóveda.")
        elif tipo == "wifi_clave":
            ssid, clave = boveda.wifi_clave(accion.get("ssid") or None)
            if not clave:
                return ("decir", f"No encuentro la contraseña de la red {ssid or 'actual'} en Windows.")
            boveda.copiar_temporal(clave)
            if accion.get("guardar"):
                boveda.guardar(ssid, clave, "wifi", "Red WiFi", usuario=ssid)
            ctx.ui(secreto=(ssid, clave))
            return ("decir", f"La contraseña de {ssid} está copiada y en pantalla"
                             + (", y la he guardado en la bóveda." if accion.get("guardar") else "."))

        # ----------------------------------------------------- riego
        elif tipo == "riego_estado":
            return ("dato", ctx.riego.estado())
        elif tipo == "valvula":
            on = str(accion.get("estado", "on")).lower() in ("on", "1", "abrir", "encender", "true")
            r = ctx.riego.valvula(accion.get("id", 1), on)
            return ("decir", r) if r else None
        elif tipo == "mqtt_publicar":
            r = ctx.riego.publicar(accion.get("tema", ""), accion.get("valor", ""))
            return ("decir", r) if r else None

        # ----------------------------------------------------- ESP32
        elif tipo == "esp_proyectos":
            lista = esp32.proyectos(refrescar=True)
            return ("dato", "Proyectos PlatformIO: " + ", ".join(n for n, _ in lista) if lista
                    else f"No encuentro proyectos con platformio.ini en {'; '.join(config.PROYECTOS)}.")
        elif tipo == "esp_puertos":
            return ("dato", esp32.puertos_texto())
        elif tipo == "esp_compilar":
            p, err = _proyecto(ctx, accion.get("proyecto"))
            if err:
                return err
            subir = bool(accion.get("subir"))
            ctx.en_segundo_plano(
                lambda: f"Proyecto {p[0]}: " + esp32.compilar(p[1], subir, accion.get("entorno"), accion.get("puerto")),
                f"{'subir' if subir else 'compilar'} el proyecto {p[0]}")
        elif tipo == "esp_ultimo_error":
            log = esp32.ULTIMO_LOG.read_text(encoding="utf-8")[-4000:] if esp32.ULTIMO_LOG.exists() else "No hay log."
            return ("dato", log)
        elif tipo == "esp_monitor":
            p = esp32.buscar_proyecto(accion["proyecto"]) if accion.get("proyecto") else None
            r = esp32.monitor(p[1] if p else None, accion.get("puerto"), accion.get("baudios"))
            return ("decir", r) if r else None
        elif tipo == "esp_vscode":
            p, err = _proyecto(ctx, accion.get("proyecto"))
            if err:
                return err
            esp32.abrir_vscode(p[1])
        elif tipo == "esp_credenciales":
            p, err = _proyecto(ctx, accion.get("proyecto"))
            if err:
                return err
            ssid, clave = boveda.credenciales_wifi(accion.get("wifi") or None)
            if not clave:
                return ("decir", "No encuentro esa red WiFi ni en la bóveda ni en Windows.")
            ruta = esp32.escribir_secrets(p[1], ssid, clave)
            return ("decir", f"He creado secrets punto h en {p[0]} con la red {ssid}. "
                             "Inclúyelo con hashtag include secrets punto h.")

        # ---------------------------------------------------- Claude
        elif tipo == "claude_preguntar":
            p = esp32.buscar_proyecto(accion["proyecto"]) if accion.get("proyecto") else None
            pregunta = accion.get("pregunta", "")
            ctx.en_segundo_plano(lambda: claude_puente.preguntar(pregunta, p[1] if p else None),
                                 f"pregunta a Claude: {pregunta}")
        elif tipo == "claude_tarea":
            p = esp32.buscar_proyecto(accion["proyecto"]) if accion.get("proyecto") else None
            r = claude_puente.tarea(accion.get("tarea", ""), p[1] if p else None)
            return ("decir", r) if r else None
        elif tipo == "claude_abrir_respuesta":
            claude_puente.abrir_ultima()

        # ------------------------------------------------------ teles
        elif tipo == "tele":
            import tele
            r = tele.ordenar(accion)
            return ("dato" if accion.get("orden") == "estado" else "decir", r)

        # ----------------------------------------------------- otros
        elif tipo == "gasto":
            return ("dato", gasto.resumen())
        elif tipo == "enviar_movil":
            texto = accion.get("texto", "")
            voz = bool(accion.get("voz"))
            if ctx.remoto and ctx._responder and not voz:
                ctx._responder(texto)  # ya hablamos por el móvil: va al mismo chat
            elif ctx.telegram.emparejado and not (ctx.whatsapp.activo and ctx.whatsapp.ventana_abierta()):
                ctx.telegram.enviar(texto, voz_tambien=voz)
            elif ctx.whatsapp.activo and ctx.whatsapp.ventana_abierta():
                ctx.whatsapp.enviar(texto, voz=voz)
            elif not ctx.whatsapp_aviso(texto):
                return ("decir", "No tengo configurado ni Telegram ni WhatsApp para enviarte mensajes.")
        elif tipo == "emparejar_movil":
            if not ctx.telegram.activo:
                return ("decir", "Primero guarda el token del bot en la bóveda con el nombre Telegram bot y reinícieme.")
            ctx.ui(codigo=ctx.telegram.nuevo_codigo(), bot=ctx.telegram.estado.get("bot", ""))
        else:
            print(f"[acciones] Acción desconocida: {accion}")
    except Exception as e:
        print(f"[acciones] Error en {tipo}: {e}")
        return ("decir", "Algo ha fallado al hacer eso.")
    return None
