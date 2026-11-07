"""Panel de estado de Jarvis (solo en el PC: Windows y Linux; en el Mac no se carga).

Muestra CPU, gráfica, RAM y discos de este equipo, y si los servidores responden,
con su disco, RAM y temperatura. A los servidores se les pregunta por SSH con las
claves que ya tienes; en ellos no se instala nada.
Ajuste opcional en local.env (nombre=alias de ~/.ssh/config, separados por ;):
  PANEL_SERVIDORES=Servidor=mi-servidor;Portátil=portatil
"""
import html
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request

import psutil
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

import config

LOCAL_CADA = 5       # segundos entre lecturas de este PC
SERVIDOR_CADA = 60   # segundos entre consultas a los servidores
ESTADO = config.LOCAL / "panel.json"
SIN_VENTANA = 0x08000000 if config.ES_WIN else 0  # que ssh y nvidia-smi no abran consolas en Windows
# En Windows psutil no da temperaturas: se leen del servidor web de LibreHardwareMonitor (Options → Remote Web Server)
LHM_URL = os.getenv("LHM_URL", "http://127.0.0.1:8085/data.json")
VERDE, AMBAR, ROJO, GRIS = "#4cd98a", "#ffc24d", "#ff5c5c", "#5f7f8c"

SERVIDORES = [tuple(s.split("=", 1)) for s in
              os.getenv("PANEL_SERVIDORES", "").split(";")
              if "=" in s]
if config.ES_WIN:
    DISCOS = [("C:", "C:\\"), ("D:", "D:\\")]
else:
    DISCOS = [("Linux", "/"), ("C:", "/mnt/sistema_ssd"), ("D:", "/mnt/almacen_hdd")]

# Lo que se ejecuta en cada servidor (vale para macOS y Linux)
REMOTO = r"""
echo "DISK $(df -kP / | awk 'NR==2{print $2, $4}')"
if [ "$(uname)" = Darwin ]; then
  echo "RAM $(sysctl -n hw.memsize) $(vm_stat | awk -v ps=$(pagesize) '/Pages free/{f=$3+0}/Pages inactive/{i=$3+0}/Pages speculative/{s=$3+0}END{printf "%d", (f+i+s)*ps}')"
  echo "UP $(( $(date +%s) - $(sysctl -n kern.boottime | sed 's/.*sec = \([0-9]*\).*/\1/') ))"
  # Copias de seguridad: disco Servidor, copia de Home Assistant y Time Machine
  if mount | grep -q " on /Volumes/Servidor ("; then
    echo "EXT $(df -kP /Volumes/Servidor | awk 'NR==2{print $2, $4}')"
    f=$(ls -t /Volumes/Servidor/backups/homeassistant/*.tar 2>/dev/null | head -1)
    [ -n "$f" ] && echo "HABK $(( $(date +%s) - $(stat -f %m "$f") ))"
  else
    echo "EXT NO"
  fi
  tm=$(defaults read /Library/Preferences/com.apple.TimeMachine 2>/dev/null | grep -Eo '20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} \+0000' | sort | tail -1)
  [ -n "$tm" ] && echo "TM $(( $(date +%s) - $(date -j -u -f '%Y-%m-%d %H:%M:%S +0000' "$tm" +%s) ))"
  tmutil status 2>/dev/null | grep -q 'Running = 1' && echo "TMRUN"
  # Espejo de Z: (Time Capsule) en Servidor: ultimo resultado OK/ERROR del registro
  e=$(grep -E ' (OK|ERROR):' "$HOME/Library/Logs/espejo-timecapsule.log" 2>/dev/null | tail -1)
  [ -n "$e" ] && echo "ESPEJO $(( $(date +%s) - $(date -j -f '%Y-%m-%d %H:%M:%S' "$(echo "$e" | cut -c1-19)" +%s) )) $(echo "$e" | cut -c21- | cut -d: -f1)"
  # Seguridad: detecciones de Maltrail de las ultimas 24 h (graves y sospechosas)
  if [ -x "$HOME/maltrail/.venv/bin/python" ]; then
"$HOME/maltrail/.venv/bin/python" - <<'PY'
import shlex, glob, os, time, datetime as dt
ign = ("hacs.xyz", "wpad.")
lim = time.time() - 86400
g = m = 0
ult = None
for f in sorted(glob.glob(os.path.expanduser("~/maltrail-logs/20*.log")))[-2:]:
    for l in open(f, errors="replace"):
        if any(x in l.replace("(", "").replace(")", "") for x in ign):
            continue
        try:
            c = shlex.split(l)
            t = dt.datetime.strptime(c[0][:19], "%Y-%m-%d %H:%M:%S").timestamp()
        except (ValueError, IndexError):
            continue
        if len(c) < 11 or t < lim:
            continue
        if "heuristic" in c[10] or "suspicious" in c[9]:
            m += 1
        else:
            g += 1
            if ult is None or t > ult[0]:
                ult = (t, "%s %s (%s)" % (c[9], c[8], c[1]))
print("SEC %d %d %d %s" % (g, m, int(time.time() - ult[0]) if ult else -1, ult[1][:120] if ult else "-"))
PY
  fi
else
  echo "RAM $(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{print t*1024, a*1024}' /proc/meminfo)"
  echo "UP $(cut -d. -f1 /proc/uptime)"
  t=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)
  [ -n "$t" ] && echo "TEMP $((t / 1000))"
fi
"""


def _color(valor, ambar, rojo):
    return ROJO if valor >= rojo else AMBAR if valor >= ambar else VERDE


def _c(texto, valor, ambar, rojo):
    return '<span style="color:%s">%s</span>' % (_color(valor, ambar, rojo), texto)


def _gb(b):
    return ("%.1f" % (b / 1024 ** 3)).replace(".", ",")


def _tiempo(seg):
    d, h = divmod(int(seg) // 3600, 24)
    return "%d d %d h" % (d, h) if d else "%d h" % h


def _temp_cpu():
    if config.ES_WIN:
        return _temp_lhm()
    try:
        sensores = psutil.sensors_temperatures()
    except Exception:
        return None
    for nombre in ("k10temp", "zenpower", "coretemp", "cpu_thermal"):
        if sensores.get(nombre):
            return max(s.current for s in sensores[nombre])
    return None


def _temp_lhm():
    """Temperatura de la CPU según LibreHardwareMonitor (Tctl/Tdie o Package si los hay; si no, la más alta)."""
    try:
        with urllib.request.urlopen(LHM_URL, timeout=2) as r:
            arbol = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    temps, pila = [], [arbol]
    while pila:
        n = pila.pop()
        pila += n.get("Children") or []
        sid = n.get("SensorId", "")
        if sid.startswith(("/amdcpu", "/intelcpu")) and "/temperature/" in sid:
            try:
                temps.append((n.get("Text", ""), float(n["Value"].split()[0].replace(",", "."))))
            except (KeyError, ValueError, IndexError):
                pass
    preferida = [v for txt, v in temps if any(p in txt for p in ("Tctl", "Tdie", "Package"))]
    return max(preferida or [v for _, v in temps], default=None)


def _gpu():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=temperature.gpu,utilization.gpu,power.draw,clocks.gr",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5, creationflags=SIN_VENTANA)
        campos = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        num = [float(x) if x.replace(".", "", 1).isdigit() else None for x in campos]  # el driver da decimales; [N/A] si falta
        return None if None in num[:2] else (num[0], num[1], num[2], num[3])
    except Exception:
        return None


def datos_locales():
    discos = []
    for nombre, ruta in DISCOS:
        if not config.ES_WIN and ruta != "/" and not os.path.ismount(ruta):
            continue
        try:
            discos.append((nombre, psutil.disk_usage(ruta).percent))
        except OSError:
            pass
    return {"cpu": psutil.cpu_percent(None), "temp": _temp_cpu(), "gpu": _gpu(),
            "ram": psutil.virtual_memory(), "discos": discos}


def datos_servidor(alias):
    ssh = shutil.which("ssh")
    if not ssh:
        return {"error": "no encuentro ssh"}
    try:
        r = subprocess.run([ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", alias, "sh -s"],
                           input=REMOTO.encode(), capture_output=True, timeout=20, creationflags=SIN_VENTANA)
    except subprocess.TimeoutExpired:
        return {"error": "sin conexión"}
    salida = r.stdout.decode(errors="ignore")
    if "DISK" not in salida:
        if "Host key verification failed" in r.stderr.decode(errors="ignore"):
            return {"error": "falta verificar su huella SSH"}
        return {"error": "sin conexión"}
    d = {}
    for linea in salida.splitlines():
        p = linea.split()
        try:
            if p[0] == "DISK":
                total, libre = int(p[1]) * 1024, int(p[2]) * 1024
                d["disco"] = (100 - libre * 100 / total, libre)
            elif p[0] == "RAM":
                d["ram"] = 100 - int(p[2]) * 100 / int(p[1])
            elif p[0] == "UP":
                d["up"] = int(p[1])
            elif p[0] == "TEMP":
                d["temp"] = int(p[1])
            elif p[0] == "EXT":
                if p[1] == "NO":
                    d["ext"] = None                     # disco Servidor no montado
                else:
                    total, libre = int(p[1]) * 1024, int(p[2]) * 1024
                    d["ext"] = (100 - libre * 100 / total, libre)
            elif p[0] == "HABK":
                d["habk"] = int(p[1])
            elif p[0] == "TM":
                d["tm"] = int(p[1])
            elif p[0] == "TMRUN":
                d["tmrun"] = True
            elif p[0] == "ESPEJO":
                d["espejo"] = (int(p[1]), p[2] == "OK")
            elif p[0] == "SEC":
                g, m, seg, texto = linea.split(" ", 4)[1:5]
                d["sec"] = (int(g), int(m), int(seg), texto)
        except (IndexError, ValueError, ZeroDivisionError):
            pass
    return d


# ------------------------------------------------------------------ riego
# Telemetria del riego: se lee de lo que Jarvis ya recibe por MQTT (nucleo.riego.valores),
# sin conexiones nuevas. Un sensor desactivado con su interruptor no se muestra.
RIEGO_PLACAS = [("S3", "riego/s3/info", "riego/s3/online"),
                ("Sensores", "riego/sensores/info", None),
                ("Radio", "riego/puente/info", "riego/puente/online")]
RIEGO_NOMBRES_V = {"2": "Arboles", "3": "Huerto", "4": "Parterre", "5": "P. Superior"}


def datos_riego(ventana):
    riego = getattr(getattr(ventana, "nucleo", None), "riego", None)
    valores = getattr(riego, "valores", None)
    if not valores:
        return None
    ahora = time.time()

    def val(tema):
        v = valores.get(tema)
        return (v[0].strip(), ahora - v[1]) if v else (None, None)

    def num(tema):
        v, _ = val(tema)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    placas = []
    for nombre, t_info, t_online in RIEGO_PLACAS:
        _, edad = val(t_info)
        en_linea = edad is not None and edad < 180
        if t_online and val(t_online)[0] == "0":
            en_linea = False
        placas.append((nombre, en_linea))

    nodos = []
    for n in range(1, 9):
        if (val("riego/sensor/%d/activo" % n)[0] or "1").upper() in ("0", "OFF", "FALSE", "NO"):
            continue
        h, edad = val("riego/sensores/nodo%d" % n)
        if h is None:
            continue
        nodos.append((n, num("riego/sensores/nodo%d" % n), edad))

    abiertas, cerradas = [], []
    for i in range(1, 10):
        e, _ = val("riego/valvula/%d/estado" % i)
        if e is None:
            continue
        on = e.upper().startswith("ON")
        if i in (1, 9):                 # entradas normalmente abiertas: solo interesa si estan cerradas
            if not on:
                cerradas.append(i)
        elif on:
            abiertas.append(i)

    return {"conectado": bool(getattr(riego, "conectado", False)), "placas": placas,
            "temp": num("riego/clima/temperatura"), "hum": num("riego/clima/humedad"),
            "lluvia": (val("riego/clima/lluvia")[0] or "").upper() in ("ON", "1", "TRUE", "SI"),
            "deposito": num("riego/deposito/nivel"), "nodos": nodos,
            "abiertas": abiertas, "cerradas": cerradas,
            "inteligente": (val("riego/s3/inteligente")[0] or "").upper() == "ON"}


def _color_riego(r):
    if r is None or not r["conectado"]:
        return GRIS
    if r["cerradas"] or not all(ok for _, ok in r["placas"]):
        return ROJO
    if r["deposito"] is not None and r["deposito"] < 20:
        return AMBAR
    return VERDE


# ------------------------------------------------------------------ copias
# Se leen del Mac en la misma consulta SSH (claves EXT, HABK, TM). Edades en segundos.
COPIA_TM_AMBAR, COPIA_TM_ROJO = 3 * 3600, 26 * 3600     # Time Machine copia cada hora
COPIA_HA_AMBAR, COPIA_HA_ROJO = 26 * 3600, 50 * 3600    # Home Assistant, una vez al dia
ESPEJO_AMBAR, ESPEJO_ROJO = 30 * 3600, 50 * 3600          # espejo de Z:, una vez al dia (03:00)


def _tiene_copias(s):
    return bool(s) and "error" not in s and ("ext" in s or "tm" in s)


def _color_copias(servidores):
    con = [s for s in servidores if _tiene_copias(s)]
    if not con:
        return GRIS
    peor = VERDE
    for s in con:
        cols = []
        if s.get("ext", 0) is None:
            cols.append(ROJO)
        elif "ext" in s:
            cols.append(_color(s["ext"][0], 85, 95))
            cols.append(_color(s["habk"], COPIA_HA_AMBAR, COPIA_HA_ROJO) if "habk" in s else ROJO)
        cols.append(_color(s["tm"], COPIA_TM_AMBAR, COPIA_TM_ROJO) if "tm" in s else AMBAR)
        if "espejo" in s:
            edad, ok = s["espejo"]
            cols.append(_color(edad, ESPEJO_AMBAR, ESPEJO_ROJO) if ok else ROJO)
        for c in cols:
            if c == ROJO or (c == AMBAR and peor == VERDE):
                peor = c
    return peor


def _hace(seg):
    m = int(seg) // 60
    if m < 60:
        return "hace %d min" % m
    h = m // 60
    return "hace %d h" % h if h < 48 else "hace %d días" % (h // 24)


def _fila_copias(s):
    partes = []
    if "tm" in s:
        txt = "Time Machine " + ("copiando ahora" if s.get("tmrun") else _hace(s["tm"]))
        partes.append(_c(txt, s["tm"], COPIA_TM_AMBAR, COPIA_TM_ROJO))
    else:
        partes.append('<span style="color:%s">Time Machine ?</span>' % AMBAR)
    if s.get("ext", 0) is None:
        partes.append('<span style="color:%s">disco Servidor NO montado</span>' % ROJO)
    elif "ext" in s:
        partes.append("HA " + (_c(_hace(s["habk"]), s["habk"], COPIA_HA_AMBAR, COPIA_HA_ROJO) if "habk" in s
                               else '<span style="color:%s">sin copias</span>' % ROJO))
        p, libre = s["ext"]
        partes.append("Servidor %s (%s GB libres)" % (_c("%.0f %%" % p, p, 85, 95), _gb(libre)))
    if "espejo" in s:
        edad, ok = s["espejo"]
        partes.append("Espejo Z: " + (_c(_hace(edad), edad, ESPEJO_AMBAR, ESPEJO_ROJO) if ok
                                       else '<span style="color:%s">ERROR %s</span>' % (ROJO, _hace(edad))))
    return "Copias: " + " · ".join(partes)


# ------------------------------------------------------------------ seguridad
# Detecciones de Maltrail en el Mac (clave SEC de la misma consulta SSH), ultimas 24 h.
# Graves: el equipo contacto con algo de una lista de amenazas confirmada.
# Sospechosas: deteccion por comportamiento (heuristica).
def _seguridad(servidores):
    for s in servidores:
        if s and "sec" in s:
            return s["sec"]
    return None


def _color_seguridad(sec):
    if sec is None:
        return GRIS
    return ROJO if sec[0] else AMBAR if sec[1] else VERDE


class _Senal(QObject):
    local = Signal(dict)
    servidor = Signal(str, dict)


class PanelEstado(QWidget):
    def __init__(self, ventana):
        super().__init__()
        self._ventana = ventana
        self._local = None
        self._servidores = {nombre: None for nombre, _ in SERVIDORES}
        self._ultima_grave = None
        cfg = self._leer()
        self.activo = cfg.get("activo", True)
        self.abierto = cfg.get("abierto", False)

        self.cabecera = QLabel()
        self.cabecera.setTextFormat(Qt.RichText)
        self.cabecera.setCursor(Qt.PointingHandCursor)
        self.cabecera.setToolTip("Pulsa para desplegar o recoger el estado de los equipos")
        self.cabecera.setStyleSheet("font: 9pt 'Segoe UI'; color: #7fe3ff; padding-top: 6px;"
                                    "border-top: 1px solid rgba(0,200,255,60);")
        self.cabecera.installEventFilter(self)
        self.detalle = QLabel()
        self.detalle.setTextFormat(Qt.RichText)
        self.detalle.setStyleSheet("font: 9pt 'Segoe UI'; color: #bfefff;")
        capa = QVBoxLayout(self)
        capa.setContentsMargins(0, 4, 0, 0)
        capa.setSpacing(3)
        capa.addWidget(self.cabecera)
        capa.addWidget(self.detalle)

        self._senal = _Senal()
        self._senal.local.connect(self._con_local)
        self._senal.servidor.connect(self._con_servidor)
        threading.Thread(target=self._bucle_local, daemon=True).start()
        threading.Thread(target=self._bucle_servidores, daemon=True).start()
        ventana.installEventFilter(self)
        self._pintar()
        self.setVisible(self.activo)

    # ---------------------------------------------------------- ajustes
    def _leer(self):
        try:
            return json.loads(ESTADO.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _guardar(self):
        try:
            ESTADO.write_text(json.dumps({"activo": self.activo, "abierto": self.abierto}), encoding="utf-8")
        except OSError:
            pass

    def mostrar(self, si):
        """Desde el menú: mostrar u ocultar el panel entero."""
        self.activo = bool(si)
        self.setVisible(self.activo and self._ventana.width() >= 300)
        self._guardar()

    def eventFilter(self, obj, e):
        if obj is self.cabecera and e.type() == QEvent.MouseButtonRelease:
            self.abierto = not self.abierto
            self._guardar()
            self._pintar()
            return True
        if obj is self._ventana and e.type() == QEvent.Resize:
            # en el modo "Pequeña" (solo el círculo) no cabe: se esconde
            self.setVisible(self.activo and self._ventana.width() >= 300)
        return False

    # ----------------------------------------------------------- lecturas
    def _bucle_local(self):
        psutil.cpu_percent(None)
        while True:
            time.sleep(LOCAL_CADA)
            try:
                self._senal.local.emit(datos_locales())
            except Exception as e:
                print("[panel]", e)

    def _bucle_servidores(self):
        while True:
            for nombre, alias in SERVIDORES:
                try:
                    self._senal.servidor.emit(nombre, datos_servidor(alias))
                except Exception as e:
                    print("[panel]", nombre, e)
            time.sleep(SERVIDOR_CADA)

    def _con_local(self, d):
        self._local = d
        self._pintar()

    def _con_servidor(self, nombre, d):
        self._servidores[nombre] = d
        sec = d.get("sec") if isinstance(d, dict) else None
        if sec and sec[0] and sec[3] != self._ultima_grave:
            # alerta grave nueva: el panel se despliega solo (al arrancar, solo si es de hace < 2 min)
            if self._ultima_grave is not None or 0 <= sec[2] < 120:
                self.abierto = True
                self._guardar()
            self._ultima_grave = sec[3]
        self._pintar()

    # ------------------------------------------------------------- dibujo
    @staticmethod
    def _punto(color):
        return '<span style="color:%s">●</span>' % color

    def _color_pc(self):
        d = self._local
        if not d:
            return GRIS
        uso = max([d["ram"].percent] + [p for _, p in d["discos"]])
        temps = [t for t in (d["temp"], d["gpu"][0] if d["gpu"] else None) if t is not None]
        t = max(temps) if temps else 0
        return ROJO if uso >= 95 or t >= 90 else AMBAR if uso >= 85 or t >= 80 else VERDE

    @staticmethod
    def _color_servidor(s):
        if s is None:
            return GRIS
        if "error" in s:
            return ROJO
        disco = s.get("disco", (0, 0))[0]
        if disco >= 95:
            return ROJO
        return AMBAR if disco >= 85 or s.get("ram", 0) >= 90 or s.get("temp", 0) >= 80 else VERDE

    def _pintar(self):
        flecha = "▾" if self.abierto else "▸"
        puntos = [self._punto(self._color_pc()) + " PC"]
        for nombre, _ in SERVIDORES:
            puntos.append(self._punto(self._color_servidor(self._servidores[nombre])) + " " + nombre.split()[0])
        riego = datos_riego(self._ventana)
        puntos.append(self._punto(_color_riego(riego)) + " Riego")
        puntos.append(self._punto(_color_copias(self._servidores.values())) + " Copias")
        sec = _seguridad(self._servidores.values())
        puntos.append(self._punto(_color_seguridad(sec)) + " Seguridad")
        self.cabecera.setText(flecha + " Estado &nbsp;&nbsp; " + " &nbsp; ".join(puntos))

        filas = []
        d = self._local
        if d:
            linea = "CPU " + _c("%.0f %%" % d["cpu"], d["cpu"], 80, 95)
            if d["temp"] is not None:
                linea += " · " + _c("%.0f °C" % d["temp"], d["temp"], 75, 90)
            if d["gpu"]:
                t, u, w, mhz = d["gpu"]
                linea += " &nbsp;&nbsp; GPU " + _c("%.0f %%" % u, u, 80, 95) + " · " + _c("%.0f °C" % t, t, 75, 85)
                if w is not None:
                    linea += " · %.0f W" % w
                if mhz is not None:
                    linea += " · %.0f MHz" % mhz
            r = d["ram"]
            filas += ["<b>Este PC</b>", linea,
                      "RAM %s (%s de %s GB)" % (_c("%.0f %%" % r.percent, r.percent, 80, 92), _gb(r.used), _gb(r.total)),
                      " · ".join("%s %s" % (n, _c("%.0f %%" % p, p, 85, 95)) for n, p in d["discos"])]
        else:
            filas.append("<b>Este PC</b> leyendo…")
        for nombre, _ in SERVIDORES:
            s = self._servidores[nombre]
            cab = "%s <b>%s</b> " % (self._punto(self._color_servidor(s)), nombre)
            if s is None:
                filas.append(cab + "comprobando…")
                continue
            if "error" in s:
                filas.append(cab + s["error"])
                continue
            filas.append(cab + "conectado" + (" · encendido " + _tiempo(s["up"]) if "up" in s else ""))
            partes = []
            if "disco" in s:
                p, libre = s["disco"]
                partes.append("Disco %s (%s GB libres)" % (_c("%.0f %%" % p, p, 85, 95), _gb(libre)))
            if "ram" in s:
                partes.append("RAM " + _c("%.0f %%" % s["ram"], s["ram"], 80, 92))
            if "temp" in s:
                partes.append(_c("%d °C" % s["temp"], s["temp"], 75, 85))
            if partes:
                filas.append(" · ".join(partes))
            if _tiene_copias(s):
                filas.append(_fila_copias(s))
        filas += self._filas_riego(riego)
        filas += self._filas_seguridad(sec)
        self.detalle.setText("<br>".join(filas))
        self.detalle.setVisible(self.abierto)

    def _filas_seguridad(self, sec):
        cab = "%s <b>Seguridad</b> " % self._punto(_color_seguridad(sec))
        if sec is None:
            return [cab + "sin datos todavía"]
        g, m, seg, texto = sec
        filas = [cab + '<span style="color:%s">%d grave%s</span> · <span style="color:%s">%d sospechosa%s</span> (24 h)' % (
            ROJO if g else VERDE, g, "" if g == 1 else "s", AMBAR if m else VERDE, m, "" if m == 1 else "s")]
        if g and seg >= 0:
            filas.append('<span style="color:%s">Última grave %s: %s</span>' % (ROJO, _hace(seg), html.escape(texto)))
        return filas

    def _filas_riego(self, r):
        cab = "%s <b>Riego</b> " % self._punto(_color_riego(r))
        if r is None:
            return [cab + "sin datos todavía"]
        if not r["conectado"]:
            return [cab + "sin conexión con el broker"]
        filas = [cab + " · ".join(
            '<span style="color:%s">%s</span>' % (VERDE if ok else ROJO, n) for n, ok in r["placas"])
            + (" · <span style=\"color:%s\">inteligente</span>" % VERDE if r["inteligente"] else "")]
        clima = []
        if r["temp"] is not None:
            clima.append("%.1f °C" % r["temp"])
        if r["hum"] is not None:
            clima.append("%.0f %% aire" % r["hum"])
        clima.append('<span style="color:%s">%s</span>' % ((AMBAR, "lloviendo") if r["lluvia"] else (VERDE, "seco")))
        if r["deposito"] is not None:
            clima.append("Depósito " + _c("%.0f %%" % r["deposito"], 100 - r["deposito"], 70, 85))
        filas.append(" · ".join(clima))
        if r["nodos"]:
            partes = []
            for n, h, edad in r["nodos"]:
                if h is None or edad > config.ALERTA_NODO_MIN * 60:
                    partes.append('N%d <span style="color:%s">sin señal</span>' % (n, GRIS))
                else:
                    partes.append("N%d %s" % (n, _c("%.0f %%" % h, 100 - h, 70, 85)))
            filas.append("Suelo: " + " · ".join(partes))
        if r["cerradas"]:
            filas.append('<span style="color:%s">Entrada cerrada: %s</span>' % (
                ROJO, ", ".join("V%d" % v for v in r["cerradas"])))
        if r["abiertas"]:
            filas.append("Regando: " + ", ".join(
                "V%d %s" % (v, RIEGO_NOMBRES_V.get(str(v), "")) for v in r["abiertas"]))
        return filas
