"""WhatsApp de Jarvis como "WhatsApp Web" (dispositivo vinculado) del número 628.

No usa la API de Meta: Jarvis se vincula al WhatsApp del 628 escaneando un QR
(WhatsApp > Ajustes > Dispositivos vinculados), igual que WhatsApp Web.
Solo atiende los mensajes que llegan desde tu número (WA_USUARIO).

La sesión se guarda en cada equipo (fuera de la carpeta compartida). Solo se conecta el equipo principal.
"""
import logging
import sqlite3
import sys
import threading
import time
import types

import config
import movil

# python-magic necesita libmagic (en el Mac no viene). Jarvis no lo usa: se sustituye si falta.
try:
    import magic  # noqa: F401
except Exception:
    _stub = types.ModuleType("magic")
    _stub.from_buffer = lambda *a, **k: "application/octet-stream"
    _stub.from_file = lambda *a, **k: "application/octet-stream"
    sys.modules["magic"] = _stub

for _n in ("neonize", "whatsmeow", "Whatsmeow"):
    logging.getLogger(_n).setLevel(logging.WARNING)

SESION = config.LOCAL / "whatsapp_web.db"
ANTIGUEDAD_MAX = 600   # mensajes con más de 10 min (atrasados) no se contestan


class WhatsAppWeb:
    def __init__(self, nucleo):
        self.nucleo = nucleo
        self.cliente = None
        self.conectado = False
        self.qr = None            # texto del QR mientras se está vinculando
        self.error = None
        self.emparejando = False
        self._hilo = None
        self._parar = threading.Event()
        self._vistos = set()

    # ------------------------------------------------------------ estado
    def vinculado(self):
        try:
            with sqlite3.connect(SESION) as db:
                return db.execute("select count(*) from whatsmeow_device").fetchone()[0] > 0
        except Exception:
            return False

    @property
    def activo(self):
        return self.vinculado()

    def ventana_abierta(self):  # sin la limitación de 24 h de la API oficial
        return self.conectado

    def recargar(self):  # no usa claves de la bóveda
        pass

    # ---------------------------------------------------------- arranque
    def iniciar(self):
        """Conecta (solo el equipo principal, y solo si ya está vinculado)."""
        if self.vinculado():
            self._arrancar()

    def vincular(self):
        """Empieza a vincular: genera el QR (lo muestra la ventana «WhatsApp de Jarvis»)."""
        self.emparejando = True
        self._arrancar()

    def _arrancar(self):
        if self._hilo and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()
        c, self.cliente = self.cliente, None
        if c:
            try:
                c.stop()
            except Exception:
                pass
        self.conectado = False
        self.qr = None

    def desvincular(self):
        c = self.cliente
        if c:
            try:
                c.logout()
            except Exception as e:
                print(f"[whatsapp] logout: {e}")
        self.detener()
        try:
            SESION.unlink(missing_ok=True)
        except Exception:
            pass

    def _bucle(self):
        try:
            from neonize.client import NewClient
            from neonize.events import (ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv,
                                        PairStatusEv, TemporaryBanEv)
            from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps
        except Exception as e:
            self.error = f"Falta el componente de WhatsApp (neonize): {e}. Reinstala con instalar.bat."
            print(f"[whatsapp] {self.error}")
            return
        espera = 10
        while not self._parar.is_set():
            c = NewClient(str(SESION), props=DeviceProps(os="Jarvis", platformType=DeviceProps.DESKTOP))
            self.cliente = c
            c.event.qr(self._al_qr)
            c.event(ConnectedEv)(self._al_conectar)
            c.event(PairStatusEv)(self._al_emparejar)
            c.event(DisconnectedEv)(lambda _c, _e: setattr(self, "conectado", False))
            c.event(LoggedOutEv)(self._al_desvincular)
            c.event(TemporaryBanEv)(self._al_bloqueo)
            c.event(MessageEv)(self._al_mensaje)
            try:
                c.connect()  # bloquea hasta que se llame a stop() o falle
                espera = 10
            except Exception as e:
                self.error = f"Sin conexión con WhatsApp: {e}"
                print(f"[whatsapp] {self.error}")
            self.conectado = False
            if self._parar.wait(espera):
                break
            espera = min(espera * 2, 300)

    # ----------------------------------------------------------- eventos
    def _al_qr(self, _c, datos):
        self.qr = datos.decode() if isinstance(datos, bytes) else str(datos)

    def _al_emparejar(self, _c, ev):
        print("[whatsapp] Vinculado con el WhatsApp de Jarvis")

    def _al_conectar(self, _c, _ev):
        self.conectado = True
        self.error = None
        self.qr = None
        print("[whatsapp] Conectado")
        if self.emparejando:
            self.emparejando = False
            self.nucleo.ui(whatsapp="vinculado")
            if not self.nucleo.es_principal:  # vinculado desde un equipo que no es el principal
                threading.Timer(60, lambda: None if self.nucleo.es_principal else self.detener()).start()

    def _al_desvincular(self, _c, _ev):
        self.error = "WhatsApp ha desvinculado a Jarvis. Vuelve a vincularlo (clic derecho > WhatsApp de Jarvis)."
        print(f"[whatsapp] {self.error}")
        self._parar.set()
        self.conectado = False
        if self.nucleo.telegram.emparejado:
            self.nucleo.telegram.enviar("⚠️ " + self.error)

    def _al_bloqueo(self, _c, ev):
        self.error = f"WhatsApp ha bloqueado temporalmente el número de Jarvis: {ev}"
        print(f"[whatsapp] {self.error}")

    def _es_mio(self, fuente):
        numeros = set()
        for jid in (fuente.Sender, fuente.SenderAlt):
            if jid.User and jid.Server == "s.whatsapp.net":
                numeros.add(jid.User)
        if not numeros and fuente.Sender.Server == "lid" and self.cliente:
            try:
                numeros.add(self.cliente.get_pn_from_lid(fuente.Sender).User)
            except Exception:
                pass
        return config.WA_USUARIO in numeros

    def _al_mensaje(self, _c, msg):
        info = msg.Info
        fuente = info.MessageSource
        if fuente.IsFromMe or fuente.IsGroup or fuente.Chat.Server == "broadcast":
            return
        if info.ID in self._vistos:
            return
        self._vistos.add(info.ID)
        ts = info.Timestamp / 1000 if info.Timestamp > 1e12 else info.Timestamp
        if ts and time.time() - ts > ANTIGUEDAD_MAX:
            return
        if not self._es_mio(fuente):
            print(f"[whatsapp] Ignorado mensaje de {fuente.Sender.User} (no autorizado)")
            return
        threading.Thread(target=self._atender, args=(msg,), daemon=True).start()

    def _atender(self, msg):
        fuente = msg.Info.MessageSource
        chat = fuente.Chat
        m = msg.Message
        try:
            from neonize.utils.enum import ReceiptType
            self.cliente.mark_read(msg.Info.ID, chat=chat, sender=fuente.Sender, receipt=ReceiptType.READ)
        except Exception:
            pass
        texto = (m.conversation or m.extendedTextMessage.text or "").strip()
        es_voz = not texto and m.HasField("audioMessage")
        if es_voz:
            self._escribiendo(chat)
            try:
                datos = self.cliente.download_any(m)
            except Exception as e:
                print(f"[whatsapp] descarga de audio: {e}")
                datos = b""
            texto = movil.transcribir_audio(datos) if datos else ""
            if not texto:
                self._enviar_a(chat, "No he entendido la nota de voz.")
                return
        if not texto:
            self._enviar_a(chat, "De momento solo entiendo texto y notas de voz.")
            return
        self._escribiendo(chat)
        forma = movil.forma_respuesta(es_voz)

        def responder(t):
            if forma == "voz" and self._enviar_voz_a(chat, t):
                return  # solo audio (si el audio falla, va en texto)
            self._enviar_a(chat, t)
            if forma == "ambos":
                self._enviar_voz_a(chat, t)
        self.nucleo.atender(texto, responder=responder, remoto=True)

    # --------------------------------------------------------- salientes
    def _escribiendo(self, chat):
        try:
            from neonize.utils.enum import ChatPresence, ChatPresenceMedia
            self.cliente.send_chat_presence(chat, ChatPresence.CHAT_PRESENCE_COMPOSING,
                                            ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
        except Exception:
            pass

    def _enviar_a(self, jid, texto):
        if not (self.cliente and self.conectado and texto):
            return False
        try:
            self.cliente.send_message(jid, texto[:4000])
            return True
        except Exception as e:
            print(f"[whatsapp] envío: {e}")
            return False

    def _enviar_voz_a(self, jid, texto):
        """Nota de voz con la voz de Jarvis."""
        if not (self.cliente and self.conectado):
            return False
        try:
            from neonize.utils.enum import ChatPresence, ChatPresenceMedia
            self.cliente.send_chat_presence(jid, ChatPresence.CHAT_PRESENCE_COMPOSING,
                                            ChatPresenceMedia.CHAT_PRESENCE_MEDIA_AUDIO)
        except Exception:
            pass
        datos, segundos = movil.voz_ogg(texto)
        if not datos:
            return False
        try:
            from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import AudioMessage, Message
            from neonize.utils.enum import MediaType
            sub = self.cliente.upload(datos, MediaType.MediaAudio)
            msg = Message(audioMessage=AudioMessage(
                URL=sub.url, directPath=sub.DirectPath, mediaKey=sub.MediaKey,
                fileEncSHA256=sub.FileEncSHA256, fileSHA256=sub.FileSHA256, fileLength=sub.FileLength,
                mimetype="audio/ogg; codecs=opus", seconds=segundos, PTT=True,
                mediaKeyTimestamp=int(time.time())))
            self.cliente.send_message(jid, msg)
            return True
        except Exception as e:
            print(f"[whatsapp] nota de voz: {e}")
            return False

    def enviar_a(self, numero, texto):
        """Mensaje a otro número (número con prefijo, sin +)."""
        from neonize.utils.jid import build_jid
        return bool(numero) and self._enviar_a(build_jid(numero), texto)

    def enviar(self, texto, voz=False):
        """Mensaje a tu WhatsApp (avisos del riego, alarmas…). Con voz=True, también como nota de voz."""
        if not (self.cliente and self.conectado):
            return False
        from neonize.utils.jid import build_jid
        jid = build_jid(config.WA_USUARIO)
        ok = self._enviar_a(jid, texto)
        if ok and voz:
            self._enviar_voz_a(jid, texto)
        return ok
