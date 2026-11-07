"""Jarvis en el móvil: bot de Telegram (bidireccional) y avisos por WhatsApp (CallMeBot).

Credenciales en la bóveda (clic derecho en Jarvis > Bóveda de claves):
  - "Telegram bot"        tipo token: el token que da @BotFather
  - "WhatsApp CallMeBot"  tipo api:   valor = apikey, usuario = tu número con prefijo (+34...)
"""
import asyncio
import json
import os
import random
import subprocess
import tempfile
import threading
import time
import urllib.parse

import requests

import boveda
import config

ESTADO = config.DATOS / "telegram.json"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COMANDOS = [("riego", "Estado del riego"), ("puertos", "Placas ESP32 conectadas"),
            ("gasto", "Gasto de DeepSeek"), ("ayuda", "Qué puedo hacer")]
AYUDA = ("Soy J.A.R.V.I.S. Escríbeme o mándame una nota de voz como si me hablaras en el PC: "
         "«¿cómo está el riego?», «abre la válvula 2 diez minutos», «compila el nodo puente», "
         "«pregúntale a Claude…», «recuérdame en 20 minutos…».")


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def voz_ogg(texto):
    """Genera una nota de voz (ogg/opus) con la voz de Jarvis. Devuelve (bytes, segundos) o (None, 0)."""
    import edge_tts
    import re
    texto = re.sub(r"[*_`#]", "", texto or "").strip()[:900]
    if not texto:
        return None, 0
    mp3 = tempfile.mktemp(suffix=".mp3")
    ogg = tempfile.mktemp(suffix=".ogg")
    try:
        asyncio.run(edge_tts.Communicate(texto, config.VOZ, rate=config.VELOCIDAD_VOZ).save(mp3))
        for codec in (["-c:a", "libopus"], ["-c:a", "opus", "-strict", "-2"]):
            r = subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", mp3, "-vn", "-ac", "1", "-ar", "48000",
                                *codec, "-b:a", "32k", "-f", "ogg", ogg],
                               capture_output=True, creationflags=SIN_VENTANA, timeout=90)
            if r.returncode == 0 and os.path.exists(ogg) and os.path.getsize(ogg) > 100:
                break
            print(f"[voz móvil] ffmpeg {codec[1]}: {r.stderr.decode(errors='ignore')[-300:]}")
        else:
            return None, 0
        with open(ogg, "rb") as f:
            datos = f.read()
        # duración: posición de la última página Ogg (opus va siempre a 48 kHz)
        i = datos.rfind(b"OggS")
        segundos = int.from_bytes(datos[i + 6:i + 14], "little") / 48000 if i >= 0 else 0
        return datos, max(1, round(segundos))
    except Exception as e:
        print(f"[voz móvil] {e}")
        return None, 0
    finally:
        for p in (mp3, ogg):
            try:
                os.remove(p)
            except Exception:
                pass


def forma_respuesta(remitio_voz):
    """Cómo contestar en el móvil, según RESPUESTA_VOZ:
    voz (por defecto): a un audio se contesta solo con audio, y a un texto solo con texto.
    siempre: texto y audio a todo.   nunca: solo texto."""
    modo = config.RESPUESTA_VOZ
    if modo == "siempre":
        return "ambos"
    if modo == "nunca" or not remitio_voz:
        return "texto"
    return "voz"


# ================================================================ WhatsApp
def whatsapp(texto):
    """Envía un WhatsApp a tu propio número con CallMeBot. Devuelve True si se envió."""
    nombre = boveda.buscar("WhatsApp CallMeBot")
    if not nombre:
        return False
    telefono = boveda.listar()[nombre].get("usuario", "")
    _, apikey = boveda.obtener(nombre)
    if not telefono or not apikey:
        return False
    try:
        r = requests.get("https://api.callmebot.com/whatsapp.php",
                         params={"phone": telefono, "text": texto[:900], "apikey": apikey}, timeout=20)
        return r.status_code == 200
    except requests.RequestException as e:
        print(f"[whatsapp] {e}")
        return False


def transcribir_audio(datos):
    """Convierte una nota de voz (ogg/opus, mp3, m4a…) a texto en español."""
    ogg = tempfile.mktemp(suffix=".audio")
    wav = tempfile.mktemp(suffix=".wav")
    try:
        with open(ogg, "wb") as f:
            f.write(datos)
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", ogg, "-ar", "16000", "-ac", "1", wav],
                       creationflags=SIN_VENTANA, timeout=60)
        import speech_recognition as sr
        rec = sr.Recognizer()
        with sr.AudioFile(wav) as fuente:
            audio = rec.record(fuente)
        return rec.recognize_google(audio, language=config.IDIOMA)
    except Exception as e:
        print(f"[móvil] transcripción: {e}")
        return ""
    finally:
        for p in (ogg, wav):
            try:
                os.remove(p)
            except Exception:
                pass


# ================================================================ Telegram
class Telegram:
    def __init__(self, nucleo):
        self.nucleo = nucleo
        self.token = None
        self._hilo = None
        self._parar = threading.Event()
        self.recargar()

    def recargar(self):
        nombre = boveda.buscar("Telegram bot")
        nuevo = boveda.obtener(nombre)[1] if nombre else None
        if nuevo != self.token:
            self.detener()
            self.token = nuevo

    def iniciar(self):
        """Empieza a recibir mensajes (solo lo hace el equipo principal)."""
        if not self.token or (self._hilo and self._hilo.is_alive()):
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()

    @property
    def estado(self):  # compartido entre equipos (datos/telegram.json)
        return self._leer()

    @property
    def codigo(self):
        e = self._leer()
        return e.get("codigo") if time.time() - e.get("codigo_ts", 0) < 600 else None

    # ------------------------------------------------------- utilidades
    @property
    def activo(self):
        return bool(self.token)

    @property
    def emparejado(self):
        return bool(self.estado.get("chat_id"))

    def _leer(self):
        try:
            return json.loads(ESTADO.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _guardar_campos(self, **campos):
        e = self._leer()
        for k, v in campos.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        ESTADO.write_text(json.dumps(e), encoding="utf-8")

    def _api(self, metodo, espera=30, files=None, **params):
        url = f"https://api.telegram.org/bot{self.token}/{metodo}"
        try:
            if files:
                r = requests.post(url, data=params, files=files, timeout=espera)
            else:
                r = requests.post(url, json=params, timeout=espera)
            return r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"[telegram] {metodo}: {e}")
            return {}

    def enviar(self, texto, chat_id=None, voz_tambien=False, forma=None):
        """forma: "texto" | "voz" (solo audio; si falla, texto) | "ambos"."""
        chat_id = chat_id or self.estado.get("chat_id")
        if not (self.token and chat_id and texto):
            return
        forma = forma or ("ambos" if voz_tambien else "texto")
        if forma == "voz" and self._enviar_voz(chat_id, texto):
            return
        self._api("sendMessage", chat_id=chat_id, text=texto[:4000])
        if forma == "ambos":
            self._enviar_voz(chat_id, texto)

    def enviar_secreto(self, nombre, valor):
        """Clave oculta tras spoiler y borrada del chat al minuto."""
        import html
        r = self._api("sendMessage", chat_id=self.estado.get("chat_id"), parse_mode="HTML",
                      text=f"🔐 <b>{html.escape(nombre)}</b>\n<tg-spoiler><code>{html.escape(valor)}</code></tg-spoiler>\n"
                           "<i>Se borrará en 60 segundos.</i>")
        mid = r.get("result", {}).get("message_id")
        if mid:
            threading.Timer(60, lambda: self._api("deleteMessage", chat_id=self.estado.get("chat_id"),
                                                  message_id=mid)).start()

    def _enviar_voz(self, chat_id, texto):
        self._api("sendChatAction", chat_id=chat_id, action="record_voice")
        datos, _ = voz_ogg(texto)
        if not datos:
            return False
        r = self._api("sendVoice", espera=60, files={"voice": ("jarvis.ogg", datos, "audio/ogg")}, chat_id=chat_id)
        if not r.get("ok"):
            print(f"[telegram] voz: {r}")
        return bool(r.get("ok"))

    def _transcribir(self, file_id):
        info = self._api("getFile", file_id=file_id).get("result", {})
        if not info.get("file_path"):
            return ""
        datos = requests.get(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}", timeout=60).content
        return transcribir_audio(datos)

    # ------------------------------------------------------ emparejar
    def nuevo_codigo(self):
        codigo = f"{random.randint(0, 999999):06d}"
        self._guardar_campos(codigo=codigo, codigo_ts=time.time())
        return codigo

    def olvidar_movil(self):
        self._guardar_campos(chat_id=None)

    # ---------------------------------------------------------- bucle
    def _bucle(self):
        yo = self._api("getMe").get("result") or {}
        yo = yo if isinstance(yo, dict) else {}
        self._guardar_campos(bot=yo.get("username", ""))
        self._api("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMANDOS])
        print(f"[telegram] Bot @{yo.get('username', '')} activo en {config.EQUIPO}")
        offset = None
        while not self._parar.is_set():
            r = self._api("getUpdates", espera=35, offset=offset, timeout=25, allowed_updates=["message"])
            if self._parar.is_set():
                break
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    self._procesar(upd.get("message") or {})
                except Exception as e:
                    print(f"[telegram] {e}")
            if not r.get("ok"):
                time.sleep(5)

    def _procesar(self, msg):
        chat_id = msg.get("chat", {}).get("id")
        texto = (msg.get("text") or "").strip()
        if not chat_id:
            return

        # Emparejamiento: solo el chat que envía el código que muestra el PC
        if not self.emparejado or chat_id != self.estado.get("chat_id"):
            if self.codigo and texto == self.codigo:
                self._guardar_campos(chat_id=chat_id, codigo=None)
                self.enviar("✅ Emparejado. Ya puedes hablar conmigo desde aquí.\n\n" + AYUDA, chat_id)
                self.nucleo.avisar("Móvil emparejado con Telegram.")
            else:
                self._api("sendMessage", chat_id=chat_id,
                          text="Este bot es privado. Para emparejar, pide el código en el PC "
                               "(clic derecho en Jarvis > Emparejar móvil) y envíamelo.")
            return

        es_voz = "voice" in msg or "audio" in msg
        if es_voz:
            self._api("sendChatAction", chat_id=chat_id, action="typing")
            fid = (msg.get("voice") or msg.get("audio"))["file_id"]
            texto = self._transcribir(fid)
            if not texto:
                self.enviar("No he entendido la nota de voz.", chat_id)
                return

        comandos = {"/riego": "¿cómo está el riego?", "/puertos": "¿qué placas ESP32 hay conectadas?",
                    "/gasto": "¿cuánto llevo gastado?"}
        if texto in ("/start", "/ayuda"):
            self.enviar(AYUDA, chat_id)
            return
        texto = comandos.get(texto.split("@")[0], texto)
        if not texto:
            return
        self._api("sendChatAction", chat_id=chat_id, action="typing")
        forma = forma_respuesta(es_voz)
        self.nucleo.atender(texto, responder=lambda t: self.enviar(t, chat_id, forma=forma),
                            remoto=True)
