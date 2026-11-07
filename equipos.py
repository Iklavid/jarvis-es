"""Coordinación entre los equipos que comparten Jarvis (PC, Mac…).

Canal principal: el broker MQTT del riego (todos los equipos llegan a él, p. ej. por Tailscale).
Es inmediato y no depende de que Syncthing sincronice.
  - Latido:  jarvis/equipos/<nombre>   (retenido, cada 30 s)
  - Buzón:   jarvis/buzon/<destino>    (órdenes y resultados entre equipos)
  - Decir:   jarvis/decir/<equipo>/…   (texto que ese equipo dice en voz alta, sin mandarlo al móvil;
             lo usa Home Assistant para el saludo al entrar en la habitación)
Respaldo: si un equipo no llega al broker, usa la carpeta compartida (Syncthing):
  datos/equipos/<nombre>.json y datos/buzon/<destino>/.

- Principal: entre los equipos vivos que pueden serlo, gana el de mayor PRIORIDAD.
  El principal atiende Telegram/WhatsApp y dispara alarmas y avisos del riego.
"""
import json
import os
import re
import threading
import time
import uuid

import config

DIR_EQUIPOS = config.DATOS / "equipos"
DIR_BUZON = config.DATOS / "buzon"
T_EQUIPOS = "jarvis/equipos/"
T_BUZON = "jarvis/buzon/"
T_DECIR = "jarvis/decir/"
VIVO_MQTT = 120     # segundos sin latido por MQTT para considerar un equipo apagado
VIVO_ARCHIVO = 600  # por la carpeta compartida (sincroniza con retraso)
LATIDO = 30
LATIDO_ARCHIVO = 180  # el archivo compartido se escribe poco: cada escritura puede crear copias en conflicto
NOMBRE_VALIDO = re.compile(r"^[A-Za-z0-9_-]+\.json$")  # descarta "pc 2.json", "pc(1).json"…


def _escribir(ruta, datos):
    """Escritura en el sitio (sin renombrar): así se evitan copias en conflicto al sincronizar."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(json.dumps(datos, ensure_ascii=False))


def _leer(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return None


class Red:
    def __init__(self, nucleo, puede_principal):
        self.nucleo = nucleo
        self.nombre = config.EQUIPO
        self.puede_principal = puede_principal      # función -> bool
        self.es_principal = False
        self._pendientes = {}                       # id -> función(texto)
        self._vistos = set()
        self._mqtt_vivos = {}                       # nombre -> datos del último latido por MQTT
        self._iniciado = time.time()
        self._ultimo_archivo = 0
        self.mqtt = getattr(nucleo, "riego", None)
        if self.mqtt is not None and self.mqtt.cliente is not None:
            self.mqtt.escuchar(T_EQUIPOS, self._latido_mqtt)
            self.mqtt.escuchar(T_BUZON + self.nombre + "/", self._buzon_mqtt)
            self.mqtt.escuchar(T_DECIR + self.nombre + "/", self._decir_mqtt)
        (DIR_BUZON / self.nombre).mkdir(parents=True, exist_ok=True)
        self._latir()
        threading.Thread(target=self._bucle_latido, daemon=True).start()
        threading.Thread(target=self._bucle_buzon, daemon=True).start()

    @property
    def mqtt_ok(self):
        return bool(self.mqtt is not None and self.mqtt.cliente is not None and self.mqtt.conectado)

    # ------------------------------------------------------------ latido
    def _datos_latido(self):
        return {"equipo": self.nombre, "so": config.SISTEMA_OS, "prioridad": config.PRIORIDAD,
                "puede_principal": bool(self.puede_principal()), "principal": self.es_principal,
                "ts": time.time()}

    def _latir(self, forzar_archivo=False):
        d = self._datos_latido()
        if self.mqtt_ok:
            self.mqtt.cliente.publish(T_EQUIPOS + self.nombre, json.dumps(d), qos=1, retain=True)
        if forzar_archivo or time.time() - self._ultimo_archivo > LATIDO_ARCHIVO:
            try:
                _escribir(DIR_EQUIPOS / f"{self.nombre}.json", d)
                self._ultimo_archivo = time.time()
            except OSError as e:
                print(f"[equipos] {e}")

    def _latido_mqtt(self, tema, texto):
        try:
            d = json.loads(texto)
        except ValueError:
            return
        if d.get("equipo"):
            d["_recibido"] = time.time()   # se mide con el reloj de este equipo (los relojes pueden diferir)
            self._mqtt_vivos[d["equipo"]] = d

    def vivos(self):
        """{nombre: datos} de los equipos con latido reciente (incluido este)."""
        ahora = time.time()
        res = {self.nombre: self._datos_latido()}
        for nombre, d in self._mqtt_vivos.items():
            if ahora - d.get("_recibido", 0) < VIVO_MQTT and ahora - d.get("ts", 0) < 3600:
                res[nombre] = d
        if DIR_EQUIPOS.exists():
            for ruta in DIR_EQUIPOS.glob("*.json"):
                if not NOMBRE_VALIDO.match(ruta.name):
                    continue
                d = _leer(ruta)
                if not d or d.get("equipo") in res or ahora - d.get("ts", 0) >= VIVO_ARCHIVO:
                    continue
                res[d["equipo"]] = d
        return res

    def principal(self):
        candidatos = [d for d in self.vivos().values() if d.get("puede_principal")]
        if not candidatos:
            return None
        return max(candidatos, key=lambda d: (d.get("prioridad", 0), d["equipo"]))["equipo"]

    def _bucle_latido(self):
        while True:
            try:
                self._latir()
                if time.time() - self._iniciado < LATIDO * 2:  # esperar a ver a los demás antes de decidir
                    time.sleep(LATIDO)
                    continue
                nuevo = self.principal() == self.nombre
                if nuevo != self.es_principal:
                    self.es_principal = nuevo
                    self._latir(forzar_archivo=True)
                    self.nucleo.cambio_principal(nuevo)
            except Exception as e:
                print(f"[equipos] {e}")
            time.sleep(LATIDO)

    def para_prompt(self):
        v = self.vivos()
        p = self.principal()
        partes = []
        for nombre, d in sorted(v.items()):
            etiqueta = f"{nombre} ({d.get('so', '?')}"
            etiqueta += ", ESTE" if nombre == self.nombre else ", encendido"
            etiqueta += ", principal)" if nombre == p else ")"
            partes.append(etiqueta)
        return ", ".join(partes) or self.nombre

    # ------------------------------------------------------------- buzón
    def _depositar(self, destino, datos, prefijo):
        """Entrega un mensaje a otro equipo por MQTT o, si no hay broker, por la carpeta compartida."""
        if self.mqtt_ok:
            self.mqtt.cliente.publish(T_BUZON + destino + "/" + prefijo, json.dumps(datos, ensure_ascii=False), qos=1)
        else:
            _escribir(DIR_BUZON / destino / f"{prefijo}_{datos['id']}_{uuid.uuid4().hex[:4]}.json", datos)

    def enviar(self, destino, accion, peticion, al_terminar=None):
        """Deja una acción para que la ejecute otro equipo. Devuelve False si no está encendido."""
        if destino not in self.vivos():
            return False
        ident = uuid.uuid4().hex[:12]
        if al_terminar:
            self._pendientes[ident] = al_terminar
        self._depositar(destino, {"id": ident, "de": self.nombre, "accion": accion,
                                  "peticion": peticion, "ts": time.time()}, "o")
        return True

    def _recibir(self, tipo, d):
        clave = (tipo, d.get("id"), d.get("de"))
        if not d or clave in self._vistos:
            return
        self._vistos.add(clave)
        if tipo == "o":
            if time.time() - d.get("ts", time.time()) > 3600:  # orden de hace más de 1 h: se descarta
                return
            threading.Thread(target=self._ejecutar, args=(d,), daemon=True).start()
        elif tipo == "r":
            self._resultado(d)

    def _buzon_mqtt(self, tema, texto):
        try:
            d = json.loads(texto)
        except ValueError:
            return
        self._recibir(tema.rsplit("/", 1)[-1], d)

    def _decir_mqtt(self, tema, texto):
        if texto.strip():
            self.nucleo.decir_aviso(texto.strip())

    def _bucle_buzon(self):  # respaldo por la carpeta compartida
        mio = DIR_BUZON / self.nombre
        while True:
            try:
                for ruta in sorted(mio.glob("*.json")):
                    d = _leer(ruta)
                    try:
                        ruta.unlink()
                    except OSError:
                        continue
                    if d:
                        self._recibir(ruta.name[0], d)
            except Exception as e:
                print(f"[buzón] {e}")
            time.sleep(5)

    def _ejecutar(self, orden):
        def devolver(clase, texto):
            self._depositar(orden["de"], {"id": orden["id"], "de": self.nombre, "clase": clase, "texto": texto,
                                          "peticion": orden.get("peticion", "")}, "r")
        print(f"[buzón] Orden de {orden['de']}: {orden['accion']}")
        self.nucleo.ejecutar_para_otro(orden["accion"], devolver)

    def _resultado(self, d):
        cb = self._pendientes.get(d["id"])
        texto = d.get("texto") or ""
        if d.get("clase") == "dato":
            texto = self.nucleo.cerebro.resumir(d.get("peticion", ""), texto)
        if not texto:
            return
        texto = f"[{d['de']}] {texto}" if not cb else texto
        if cb:
            cb(texto)
        else:
            self.nucleo.avisar(texto)
