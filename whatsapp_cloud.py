"""WhatsApp bidireccional con la API oficial de Meta (WhatsApp Cloud API).

Jarvis tiene su propio número de WhatsApp. Meta avisa de cada mensaje a un webhook
que Jarvis publica en internet con un túnel de ngrok (dominio fijo y gratuito).

Credenciales en la bóveda:
  - "WhatsApp Cloud token"   valor = token permanente,  usuario = Phone number ID
  - "WhatsApp app secret"    valor = clave secreta de la app (para verificar la firma de Meta)
  - "ngrok"                  valor = authtoken,         usuario = tu dominio gratuito (xxxx.ngrok-free.app)
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

import boveda
import config
import movil

ESTADO = config.DATOS / "whatsapp.json"
GRAPH = f"https://graph.facebook.com/{config.WA_GRAPH_VERSION}"
VENTANA = 24 * 3600 - 300   # Meta deja responder libremente 24 h tras tu último mensaje


class WhatsAppCloud:
    def __init__(self, nucleo):
        self.nucleo = nucleo
        if not self.estado.get("verify_token"):
            self._guardar_campos(verify_token="jarvis-" + secrets.token_urlsafe(16))
        self.token = self.phone_id = self.app_secret = None
        self.url_publica = None
        self.error = None
        self._vistos = set()
        self._servidor = None
        self._oyente = None
        self._arrancando = False
        self.recargar()

    # ---------------------------------------------------------- estado
    @property
    def estado(self):  # compartido entre equipos (datos/whatsapp.json)
        try:
            return json.loads(ESTADO.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _guardar_campos(self, **campos):
        e = self.estado
        e.update(campos)
        ESTADO.write_text(json.dumps(e, indent=1), encoding="utf-8")

    def recargar(self):
        antes = (self.token, self.phone_id, self.app_secret)
        self.token = self.phone_id = self.app_secret = None
        n = boveda.buscar("WhatsApp Cloud token")
        if n:
            self.token = boveda.obtener(n)[1]
            self.phone_id = boveda.listar()[n].get("usuario", "").strip()
        n = boveda.buscar("WhatsApp app secret")
        if n:
            self.app_secret = boveda.obtener(n)[1]
        if antes != (self.token, self.phone_id, self.app_secret):
            self.detener()

    def iniciar(self):
        """Abre el webhook y el túnel (solo en el equipo principal)."""
        if not self.activo or self._servidor or self._arrancando:
            return
        self._arrancando = True
        threading.Thread(target=self._arrancar, daemon=True).start()

    def detener(self):
        if self._oyente:
            try:
                import ngrok
                ngrok.disconnect(self.url_publica)
            except Exception:
                pass
            self._oyente = None
            self.url_publica = None
        if self._servidor:
            try:
                self._servidor.shutdown()
                self._servidor.server_close()
            except Exception:
                pass
            self._servidor = None

    @property
    def activo(self):
        return bool(self.token and self.phone_id)

    def ventana_abierta(self):
        return time.time() - self.estado.get("ultimo_mensaje", 0) < VENTANA

    @property
    def url_webhook(self):
        return f"{self.url_publica}/whatsapp" if self.url_publica else None

    # ---------------------------------------------------- servidor+túnel
    def _arrancar(self):
        jarvis = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # verificación del webhook al darlo de alta en Meta
                q = parse_qs(urlparse(self.path).query)
                if (urlparse(self.path).path == "/whatsapp" and q.get("hub.mode") == ["subscribe"]
                        and q.get("hub.verify_token") == [jarvis.estado["verify_token"]]):
                    reto = q.get("hub.challenge", [""])[0].encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(reto)
                    print("[whatsapp] Webhook verificado por Meta")
                else:
                    self.send_response(403)
                    self.end_headers()

            def do_POST(self):
                cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if not jarvis._firma_valida(cuerpo, self.headers.get("X-Hub-Signature-256", "")):
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)  # contestar rápido; Meta reintenta si tardamos
                self.end_headers()
                try:
                    datos = json.loads(cuerpo)
                except ValueError:
                    return
                threading.Thread(target=jarvis._procesar, args=(datos,), daemon=True).start()

        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", config.WA_PUERTO), Manejador)
        except OSError as e:
            self.error = f"No puedo abrir el puerto {config.WA_PUERTO}: {e}"
            print(f"[whatsapp] {self.error}")
            self._arrancando = False
            return
        self._servidor = servidor
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        self._tunel()
        self._arrancando = False

    def _tunel(self):
        n = boveda.buscar("ngrok")
        if not n:
            self.error = "Falta la entrada «ngrok» en la bóveda (authtoken y dominio)."
            print(f"[whatsapp] {self.error}")
            return
        authtoken = boveda.obtener(n)[1]
        dominio = boveda.listar()[n].get("usuario", "").strip().replace("https://", "").strip("/")
        try:
            import ngrok
            oyente = ngrok.forward(config.WA_PUERTO, authtoken=authtoken, domain=dominio or None)
            self._oyente = oyente
            self.url_publica = oyente.url()
            self.error = None
            print(f"[whatsapp] Túnel activo: {self.url_webhook}")
        except Exception as e:
            self.error = f"No se pudo abrir el túnel de ngrok: {e}"
            print(f"[whatsapp] {self.error}")

    def _firma_valida(self, cuerpo, firma):
        if not self.app_secret:
            return False  # sin clave secreta no aceptamos nada
        esperado = "sha256=" + hmac.new(self.app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperado, firma or "")

    # ------------------------------------------------------- entrantes
    def _procesar(self, datos):
        for entrada in datos.get("entry", []):
            for cambio in entrada.get("changes", []):
                for msg in cambio.get("value", {}).get("messages", []):
                    self._mensaje(msg)

    def _mensaje(self, msg):
        mid = msg.get("id")
        if mid in self._vistos:  # Meta puede repetir la entrega
            return
        self._vistos.add(mid)
        remitente = msg.get("from", "")
        if remitente != config.WA_USUARIO:
            print(f"[whatsapp] Ignorado mensaje de {remitente} (no autorizado)")
            return
        self._guardar_campos(ultimo_mensaje=time.time())
        self._api({"status": "read", "message_id": mid})

        tipo = msg.get("type")
        es_voz = tipo == "audio"
        if tipo == "text":
            texto = msg["text"].get("body", "").strip()
        elif es_voz:
            texto = movil.transcribir_audio(self._descargar(msg["audio"]["id"]))
            if not texto:
                self.enviar("No he entendido la nota de voz.")
                return
        else:
            self.enviar("De momento solo entiendo texto y notas de voz.")
            return
        if texto:
            self.nucleo.atender(texto, responder=self.enviar, remoto=True)

    def _descargar(self, media_id):
        h = {"Authorization": f"Bearer {self.token}"}
        try:
            info = requests.get(f"{GRAPH}/{media_id}", headers=h, timeout=20).json()
            return requests.get(info["url"], headers=h, timeout=60).content
        except Exception as e:
            print(f"[whatsapp] descarga: {e}")
            return b""

    # --------------------------------------------------------- salientes
    def _api(self, cuerpo):
        try:
            r = requests.post(f"{GRAPH}/{self.phone_id}/messages",
                              headers={"Authorization": f"Bearer {self.token}"},
                              json={"messaging_product": "whatsapp", **cuerpo}, timeout=20)
            if r.status_code != 200:
                print(f"[whatsapp] Error {r.status_code}: {r.text[:300]}")
            return r.status_code == 200
        except requests.RequestException as e:
            print(f"[whatsapp] {e}")
            return False

    def enviar(self, texto, voz=False):
        """Mensaje de texto a tu número. Solo funciona dentro de la ventana de 24 h (sin notas de voz)."""
        if not (self.activo and texto):
            return False
        return self._api({"to": config.WA_USUARIO, "type": "text", "text": {"body": texto[:4000]}})
