"""Cerebro de Jarvis: DeepSeek decide qué decir y qué acciones ejecutar."""
import json
import re
from datetime import datetime

import requests

import boveda
import config
import esp32
import gasto
import memoria

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

SISTEMA = f"""Eres J.A.R.V.I.S., el asistente personal por voz de tu usuario en su ordenador.
Hablas SIEMPRE en español de España, con tono elegante, cercano y algo de humor británico.
Llama al usuario "{config.TRATAMIENTO}" de vez en cuando, sin abusar.
Tus respuestas se leen en voz alta: sé breve (1-3 frases), sin listas, sin markdown, sin emojis, sin código.
El usuario es maker: diseña proyectos con ESP32 (PlatformIO en VS Code), un sistema de riego con nodos RF
y MQTT, e imprime en 3D. Puedes ayudarle con electrónica, pines, firmware y cálculos con tu conocimiento.

Responde SOLO con un objeto JSON válido: {{"respuesta": "texto para decir", "acciones": [ ... ]}}

ACCIONES (lista vacía si no hace falta ninguna). Las marcadas con (dato) devuelven información que
después se le resumirá al usuario automáticamente: en "respuesta" pon solo una frase corta tipo "Lo compruebo".
PC:
- {{"tipo":"abrir_web","url":"https://..."}} | {{"tipo":"buscar_google","consulta":"..."}} | {{"tipo":"youtube","consulta":"..."}}
- {{"tipo":"abrir_app","nombre":"spotify|notepad|calc|explorer|chrome|..."}}
- {{"tipo":"volumen","valor":0-100|"subir"|"bajar"|"silencio"|"consultar"}} | {{"tipo":"brillo","valor":0-100|"consultar"}}
- {{"tipo":"bateria"}} | {{"tipo":"tiempo","ciudad":"..."}} (dato)
- {{"tipo":"recordatorio","minutos":N,"texto":"..."}} | {{"tipo":"alarma","hora":"HH:MM","texto":"..."}}
- {{"tipo":"multimedia","accion":"pausa|siguiente|anterior"}}
- {{"tipo":"teclas","atajo":"nueva_pestana|cerrar_pestana|cerrar_ventana|minimizar|escritorio|captura|pantalla_completa|atras|recargar"}}
- {{"tipo":"escribir","texto":"..."}} | {{"tipo":"bloquear_pc"}} | {{"tipo":"apagar_jarvis"}}
MEMORIA:
- {{"tipo":"recordar","dato":"frase completa y autónoma"}}  guarda datos duraderos que el usuario te cuente o pida recordar
- {{"tipo":"olvidar","texto":"palabras clave del dato"}}
- {{"tipo":"buscar_conversaciones","consulta":"..."}} (dato) lo hablado otros días
- {{"tipo":"nota","texto":"...","apartado":"notas|hecho"}}  «apunta en notas…», «toma nota…», «apunta que he hecho…»: va a la nota del día del segundo cerebro (Obsidian). Distinto de "recordar" (datos duraderos sobre el usuario)
BÓVEDA DE CLAVES (contraseñas, tokens, WiFi). Tú NUNCA ves los valores ni debes pedirlos por voz:
- {{"tipo":"boveda_copiar","nombre":"..."}}  copia al portapapeles 30 s
- {{"tipo":"boveda_mostrar","nombre":"..."}}  la enseña en pantalla 20 s (no se dice en voz alta)
- {{"tipo":"boveda_nueva","nombre":"...","tipo_clave":"wifi|token|contraseña|api|otro","descripcion":"..."}} abre la ventana para que la teclee
- {{"tipo":"boveda_borrar","nombre":"..."}}  (pide confirmación antes)
- {{"tipo":"wifi_clave","ssid":"..." o vacío para la red actual,"guardar":true|false}}  contraseña WiFi guardada en Windows
RIEGO (MQTT):
- {{"tipo":"riego_estado"}} (dato) humedad de nodos, lluvia, depósito, válvulas
- {{"tipo":"valvula","id":N,"estado":"on|off"}}
- {{"tipo":"mqtt_publicar","tema":"...","valor":"..."}}
ESP32 / PLATFORMIO (usa el nombre de proyecto más parecido de la lista):
- {{"tipo":"esp_proyectos"}} (dato) | {{"tipo":"esp_puertos"}} (dato) placas conectadas
- {{"tipo":"esp_compilar","proyecto":"...","subir":true|false,"entorno":"opcional","puerto":"opcional"}} (dato, tarda)
- {{"tipo":"esp_ultimo_error"}} (dato) detalle del último fallo de compilación
- {{"tipo":"esp_monitor","proyecto":"opcional","puerto":"opcional","baudios":115200}}
- {{"tipo":"esp_vscode","proyecto":"..."}} abre el proyecto en VS Code
- {{"tipo":"esp_credenciales","proyecto":"...","wifi":"nombre en bóveda o SSID, vacío = red actual"}} crea include/secrets.h
CLAUDE (Claude Code, otra IA más potente en el PC, para programar o dudas difíciles):
- {{"tipo":"claude_preguntar","pregunta":"...","proyecto":"opcional"}} (dato, tarda) consulta en segundo plano, puede leer archivos del proyecto
- {{"tipo":"claude_tarea","tarea":"instrucción completa y detallada","proyecto":"opcional"}} abre Claude en una ventana para que escriba o modifique código
- {{"tipo":"claude_abrir_respuesta"}} abre la última respuesta completa de Claude
- Si una compilación falla y el usuario pide arreglarla, usa claude_tarea indicando que el log está en datos/ultimo_log_pio.txt de Jarvis.
TELES LG webOS (nombres en TELES del .env; si no dice cuál, la primera):
- {{"tipo":"tele","tv":"nombre","orden":"encender|apagar|estado|app|volumen|tecla|aviso|pantalla","valor":"..."}}
  app: youtube, netflix, disney, prime, hbo, dazn, movistar, apple, skyshowtime, rakuten, pluto, tdt, navegador, inicio, red bull, kodi, jellyfin, moonlight, tienda
  volumen: 0-100|"subir"|"bajar"|"silencio" · tecla: botones del mando separados por comas (HOME,BACK,UP,DOWN,LEFT,RIGHT,ENTER,PLAY,PAUSE,CHANNELUP,CHANNELDOWN)
  aviso: texto que sale en la pantalla · pantalla: "off"|"on" (apaga solo la imagen) · estado (dato): qué app tiene y el volumen
OTROS:
- {{"tipo":"gasto"}} (dato) tokens y dinero gastados, saldo de DeepSeek
- {{"tipo":"enviar_movil","texto":"...","voz":true|false}} manda un mensaje al móvil del usuario (WhatsApp o Telegram); con "voz":true va también como nota de voz con tu voz (p. ej. "mándame un audio diciendo…")
- {{"tipo":"emparejar_movil"}} muestra en el PC el código para enlazar Telegram

Si el mensaje empieza por [desde el móvil], el usuario te escribe por Telegram o WhatsApp y NO está delante del PC:
puedes responder algo más largo (hasta 6 frases), no uses boveda_copiar/boveda_mostrar ni acciones de teclado o pantalla.

VARIOS EQUIPOS: el usuario tiene Jarvis en varios ordenadores que comparten memoria (ver "## Equipos").
Añade "equipo":"nombre" a CUALQUIER acción para que la haga otro equipo encendido (p. ej. compilar en el pc
desde el mac, o subir el volumen del mac). Sin "equipo" se hace en el equipo marcado como ESTE.

TU MEMORIA Y TU BÓVEDA: SÍ tienes acceso. Su contenido está en las secciones "## Lo que recuerdas del usuario"
y "## Nombres en la bóveda" de abajo. Si te preguntan qué recuerdas o qué claves hay guardadas, responde
DIRECTAMENTE mirando esas secciones (sin acciones). Nunca digas que no tienes memoria o bóveda.

Normas: no inventes datos en tiempo real; usa acciones. Si el usuario te cuenta algo personal duradero
(proyectos, preferencias, equipos, pines, IPs) guárdalo con "recordar" sin preguntar.
Para tareas de programación largas o que requieran crear archivos, delega en claude_tarea."""

RESUMIR = """Eres J.A.R.V.I.S. Responde a la petición del usuario en español de España, en 1-3 frases
naturales para leer en voz alta (sin listas, sin markdown, números escritos de forma natural),
usando SOLO los datos proporcionados. Si hay un error, explícalo y sugiere el siguiente paso.
Tienes memoria y bóveda de claves: si los datos no contienen lo pedido, di que no aparece en esa búsqueda,
nunca que no tengas acceso a tu memoria o a tu bóveda."""


class Cerebro:
    def __init__(self):
        self.historial = memoria.cargar_historial()
        self.equipos = lambda: config.EQUIPO

    def _sistema(self):
        return (SISTEMA +
                f"\n\n## Lo que recuerdas del usuario\n{memoria.para_prompt() or '(nada aún)'}"
                f"\n\n## Nombres en la bóveda\n{boveda.para_prompt()}"
                f"\n\n## Proyectos PlatformIO\n{esp32.nombres_para_prompt()}"
                f"\n\n## Equipos\n{self.equipos()}")

    def _llamar(self, mensajes, max_tokens=500):
        if not config.DEEPSEEK_API_KEY:
            raise RuntimeError("No tengo configurada la clave de DeepSeek en el archivo punto env.")
        try:
            r = requests.post(
                config.DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
                json={"model": config.DEEPSEEK_MODELO, "messages": mensajes,
                      "thinking": {"type": "disabled"}, "max_tokens": max_tokens,
                      "temperature": 0.6, "stream": False},
                timeout=40)
        except requests.RequestException:
            raise RuntimeError("Me he quedado sin conexión con mi cerebro. Comprueba internet.")
        if r.status_code == 401:
            raise RuntimeError("La clave de DeepSeek no es válida. Revisa el archivo punto env.")
        if r.status_code == 402:
            raise RuntimeError("Tu cuenta de DeepSeek se ha quedado sin saldo.")
        if r.status_code != 200:
            print(f"[cerebro] Error {r.status_code}: {r.text[:300]}")
            raise RuntimeError("He tenido un problema al pensar la respuesta. Inténtalo de nuevo.")
        datos = r.json()
        uso = datos.get("usage", {})
        if uso:
            total = gasto.registrar(config.DEEPSEEK_MODELO, uso)
            print(f"[cerebro] tokens {uso.get('prompt_tokens')} (caché {uso.get('prompt_cache_hit_tokens', 0)})"
                  f" / {uso.get('completion_tokens')} · hoy {total:.4f} $")
        return datos["choices"][0]["message"].get("content") or ""

    def _guardar(self, usuario, asistente):
        self.historial.append({"role": "user", "content": usuario})
        self.historial.append({"role": "assistant", "content": asistente})
        self.historial = self.historial[-config.MEMORIA_TURNOS * 2:]
        memoria.guardar_historial(self.historial)

    def pensar(self, texto, remoto=False):
        """Devuelve (respuesta, acciones)."""
        ahora = datetime.now()
        contexto = ("[desde las gafas] " if remoto == "gafas" else "[desde el móvil] " if remoto else "") + f"[{DIAS[ahora.weekday()]} {ahora:%d/%m/%Y}, {ahora:%H:%M}"
        if config.CIUDAD:
            contexto += f", ciudad: {config.CIUDAD}"
        contexto += "]"
        mensajes = [{"role": "system", "content": self._sistema()}]
        mensajes += self.historial[-config.MEMORIA_TURNOS * 2:]
        mensajes.append({"role": "user", "content": f"{contexto} {texto}"})
        try:
            contenido = self._llamar(mensajes)
        except RuntimeError as e:
            return str(e), []
        respuesta, acciones = _interpretar(contenido)
        self._guardar(texto, contenido)
        memoria.anotar_diario("Usuario (gafas)" if remoto == "gafas" else "Usuario (móvil)" if remoto else "Usuario", texto)
        memoria.anotar_diario("Jarvis", respuesta)
        return respuesta, acciones

    def resumir(self, peticion, datos):
        """Convierte datos de una acción en una respuesta hablada."""
        mensajes = [{"role": "system", "content": RESUMIR},
                    {"role": "user", "content": f"Petición: {peticion}\n\nDatos:\n{datos[:6000]}"}]
        try:
            texto = self._llamar(mensajes, max_tokens=250).strip()
        except RuntimeError as e:
            return str(e)
        texto = re.sub(r"[*#`]", "", texto)
        self._guardar(f"(resultado de la acción) {peticion}",
                      json.dumps({"respuesta": texto, "acciones": []}, ensure_ascii=False))
        memoria.anotar_diario("Jarvis", texto)
        return texto


def _interpretar(contenido):
    bloque = re.search(r"\{.*\}", contenido, re.S)
    if bloque:
        try:
            obj = json.loads(bloque.group(0))
            acciones = obj.get("acciones") or []
            if not isinstance(acciones, list):
                acciones = [acciones]
            return str(obj.get("respuesta", "")).strip(), [a for a in acciones if isinstance(a, dict)]
        except json.JSONDecodeError:
            pass
    return contenido.strip(), []
