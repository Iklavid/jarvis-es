"""Conexión con el sistema de riego por MQTT: estado, alertas y válvulas."""
import re
import threading
import time

import config

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

KEEPALIVE = 60   # con 30 s se caia varias veces al dia por Tailscale


def _numero(texto):
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(texto))
    return float(m.group(0).replace(",", ".")) if m else None


def _hace(segundos):
    m = int(segundos // 60)
    if m < 1:
        return "ahora"
    if m < 60:
        return f"hace {m} min"
    return f"hace {m // 60} h {m % 60} min"


class Riego:
    def __init__(self, avisar=None):
        self.avisar = avisar or (lambda texto: None)
        self.valores = {}          # tema -> (texto, timestamp)
        self.conectado = False
        self._ultimo_aviso = {}    # clave -> timestamp
        self._lluvia_previa = None
        self.cliente = None
        self.manejadores = {}      # prefijo de tema -> función(tema, texto) (coordinación entre equipos)
        self._sin_broker_desde = time.time()   # para avisar si se pierde el broker mucho rato
        if not config.MQTT_HOST or mqtt is None:
            return
        try:
            self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"jarvis-{config.EQUIPO}-{int(time.time())}")
        except AttributeError:  # paho-mqtt 1.x
            self.cliente = mqtt.Client(client_id=f"jarvis-{config.EQUIPO}-{int(time.time())}")
        if config.MQTT_USUARIO:
            self.cliente.username_pw_set(config.MQTT_USUARIO, config.MQTT_CLAVE)
        self.cliente.on_connect = self._conectado
        self.cliente.on_disconnect = self._desconectado
        self.cliente.on_message = self._mensaje
        self.cliente.reconnect_delay_set(2, 60)
        try:
            self.cliente.connect_async(config.MQTT_HOST, config.MQTT_PUERTO, keepalive=KEEPALIVE)
            self.cliente.loop_start()
            print(f"[riego] Conectando a MQTT {config.MQTT_HOST}:{config.MQTT_PUERTO}…")
        except Exception as e:
            print(f"[riego] No conecto con MQTT: {e}")
        threading.Thread(target=self._vigilar, daemon=True).start()

    # ------------------------------------------------------------ MQTT
    def _conectado(self, *args):
        try:
            self._conectado_real(*args)
        except Exception as e:
            print(f"[riego] Error al conectar: {e}")

    def _mensaje(self, *args):
        try:
            self._mensaje_real(*args)
        except Exception as e:
            print(f"[riego] Error con un mensaje MQTT: {e}")

    def _conectado_real(self, cliente, userdata, flags, codigo=0, *args):
        fallo = getattr(codigo, "is_failure", None)
        if (fallo if fallo is not None else codigo != 0):
            self.conectado = False
            print(f"[riego] El broker {config.MQTT_HOST}:{config.MQTT_PUERTO} rechaza la conexión: {codigo}")
            return
        self.conectado = True
        self._sin_broker_desde = None
        for tema in config.MQTT_TEMAS.split(","):
            cliente.subscribe(tema.strip())
        for prefijo in list(self.manejadores):
            cliente.subscribe(prefijo + "#", qos=1)
        print(f"[riego] Conectado a MQTT {config.MQTT_HOST}")

    def _desconectado(self, *args):
        if self.conectado:
            print(f"[riego] Desconectado de MQTT {config.MQTT_HOST}")
        self.conectado = False
        if self._sin_broker_desde is None:
            self._sin_broker_desde = time.time()

    def _mensaje_real(self, cliente, userdata, msg):
        try:
            texto = msg.payload.decode("utf-8", errors="ignore").strip()
        except Exception:
            texto = str(msg.payload)
        for prefijo, funcion in list(self.manejadores.items()):
            if msg.topic.startswith(prefijo):
                try:
                    funcion(msg.topic, texto)
                except Exception as e:
                    print(f"[mqtt] {prefijo}: {e}")
                return
        if msg.topic == "riego/vigilante/aviso":
            # Avisos del vigilante del servidor (placas caidas, cortes de luz, reinicios...)
            if not msg.retain:
                self.avisar("Aviso del riego: " + texto)
            return
        self.valores[msg.topic] = (texto[:300], time.time())
        if "lluvia" in msg.topic.lower():
            self._cambio_lluvia(texto)

    # ---------------------------------------------------------- consultas
    def estado(self):
        """Texto con todos los valores conocidos, para que la IA lo resuma."""
        if not self.cliente:
            return "La conexión MQTT del riego no está configurada (MQTT_HOST en el .env)."
        if not self.valores:
            return ("Sin datos del riego todavía. " +
                    ("Conectado al broker, esperando mensajes." if self.conectado
                     else f"No consigo conectar con el broker {config.MQTT_HOST}:{config.MQTT_PUERTO}."))
        ahora = time.time()
        lineas = [f"{t} = {v} ({_hace(ahora - ts)})"
                  for t, (v, ts) in sorted(self.valores.items()) if not t.endswith("/set")]
        return "Datos actuales del sistema de riego (tema MQTT = valor):\n" + "\n".join(lineas)

    def valvula(self, id_valvula, encender):
        if not self.cliente or not self.conectado:
            return "No estoy conectado al broker MQTT del riego."
        tema = config.VALVULA_TEMA.format(id=id_valvula)
        payload = config.VALVULA_ON if encender else config.VALVULA_OFF
        self.cliente.publish(tema, payload, qos=1)
        print(f"[riego] {tema} <- {payload}")
        return None

    def escuchar(self, prefijo, funcion):
        """Suscribe un prefijo (p. ej. "jarvis/equipos/") y llama a funcion(tema, texto) con cada mensaje."""
        self.manejadores[prefijo] = funcion
        if self.cliente and self.conectado:
            self.cliente.subscribe(prefijo + "#", qos=1)

    def publicar(self, tema, valor, retener=False):
        if not self.cliente or not self.conectado:
            return "No estoy conectado al broker MQTT."
        self.cliente.publish(tema, str(valor), qos=1, retain=retener)
        return None

    # ------------------------------------------------------------ alertas
    def _avisar_una_vez(self, clave, texto, cada=2 * 3600):
        if time.time() - self._ultimo_aviso.get(clave, 0) > cada:
            self._ultimo_aviso[clave] = time.time()
            self.avisar(texto)

    def _cambio_lluvia(self, texto):
        lloviendo = texto.strip().upper() in ("ON", "1", "TRUE", "SI", "SÍ", "LLUVIA")
        if config.ALERTA_LLUVIA and self._lluvia_previa is False and lloviendo:
            self._avisar_una_vez("lluvia", "Aviso del riego: ha empezado a llover.", cada=1800)
        self._lluvia_previa = lloviendo

    def _vigilar(self):
        while True:
            time.sleep(60)
            try:
                self.revisar()
            except Exception as e:
                print(f"[riego] {e}")

    def revisar(self):
        ahora = time.time()
        # Sin broker mas de 2 min: rearranco el cliente MQTT por si su hilo murio
        if (self.cliente and self._sin_broker_desde and ahora - self._sin_broker_desde > 120
                and ahora - getattr(self, "_ultimo_rearranque", 0) > 120):
            self._ultimo_rearranque = ahora
            try:
                self.cliente.loop_stop()
            except Exception:
                pass
            try:
                self.cliente.connect_async(config.MQTT_HOST, config.MQTT_PUERTO, keepalive=KEEPALIVE)
                self.cliente.loop_start()
                print(f"[riego] Sin broker hace {int((ahora - self._sin_broker_desde) // 60)} min: rearranco MQTT")
            except Exception as e:
                print(f"[riego] No pude rearrancar MQTT: {e}")
        # Sin broker mas de 5 min (servidor o red caidos): el vigilante no puede avisar, aviso yo
        if self.cliente and self._sin_broker_desde and ahora - self._sin_broker_desde > 300:
            self._avisar_una_vez("broker", "Aviso del riego: llevo "
                                 f"{int((ahora - self._sin_broker_desde) // 60)} minutos sin conexión con el broker. "
                                 "Puede estar caído el servidor o la red.", cada=3600)
        # El vigilante publica su estado cada 30 s; si se calla 3 min, aviso
        vig = self.valores.get("riego/vigilante/estado")
        if self.conectado and vig and ahora - vig[1] > 180:
            self._avisar_una_vez("vigilante", "Aviso del riego: el vigilante del servidor lleva "
                                 f"{int((ahora - vig[1]) // 60)} minutos sin dar señal.", cada=3600)
        if True:
            for tema, (valor, ts) in list(self.valores.items()):
                t = tema.lower()
                n = _numero(valor)
                if ("deposito" in t or "depósito" in t or "nivel" in t) and n is not None \
                        and config.ALERTA_DEPOSITO and n < config.ALERTA_DEPOSITO:
                    self._avisar_una_vez(tema, f"Aviso del riego: el depósito está al {round(n)} por ciento.")
                if t.startswith("riego/sensores/nodo"):
                    nodo = re.search(r"nodo\s*(\d+)", t).group(1)
                    # Sensor desactivado (interruptor en la S3 o en la web): sin avisos
                    activo = self.valores.get(f"riego/sensor/{nodo}/activo")
                    if activo and activo[0].strip().upper() in ("0", "OFF", "FALSE", "NO"):
                        continue
                    if config.ALERTA_NODO_MIN and ahora - ts > config.ALERTA_NODO_MIN * 60:
                        self._avisar_una_vez(f"mudo{nodo}",
                                             f"Aviso del riego: el nodo {nodo} lleva {int((ahora - ts) // 60)} minutos sin dar señal.")
                    if config.ALERTA_HUMEDAD and n is not None and n < config.ALERTA_HUMEDAD:
                        self._avisar_una_vez(f"seco{nodo}",
                                             f"Aviso del riego: la humedad del nodo {nodo} ha bajado al {round(n)} por ciento.")
