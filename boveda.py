"""Bóveda de claves compartida entre equipos.

Las claves se guardan CIFRADAS en datos/boveda.json (sincronizado con Syncthing) con una
frase maestra que solo tú conoces. Cada equipo pide la frase una vez y guarda la clave
derivada en su llavero local (Administrador de credenciales de Windows / Llavero del Mac / llavero de KDE o GNOME en Linux).
Los nombres y descripciones van sin cifrar: es lo único que ve la IA.
"""
import base64
import json
import os
import re
import subprocess
import threading
import unicodedata

import config

try:
    import keyring
except ImportError:  # pragma: no cover
    keyring = None

SERVICIO = "JARVIS"
CLAVE_LOCAL = "clave_boveda"
ARCHIVO = config.DATOS / "boveda.json"
_lock = threading.Lock()
TIPOS = ("wifi", "token", "contraseña", "api", "otro")
VERIFICADOR = "jarvis-boveda-ok"
_fernet_cache = {}


class BovedaBloqueada(Exception):
    pass


def _norm(t):
    t = unicodedata.normalize("NFD", str(t).lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


# ------------------------------------------------------------- archivo
def _leer():
    try:
        datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 2, "entradas": {}}
    if "version" not in datos:  # formato antiguo: índice + valores en el llavero
        return {"version": 1, "entradas": datos}
    return datos


def _escribir(datos):
    tmp = ARCHIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ARCHIVO)


# ------------------------------------------------------------- cifrado
def _derivar(frase, sal):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal, iterations=390000)
    return base64.urlsafe_b64encode(kdf.derive(frase.encode("utf-8")))


def _fernet():
    from cryptography.fernet import Fernet
    clave = keyring.get_password(SERVICIO, CLAVE_LOCAL) if keyring else None
    if not clave:
        raise BovedaBloqueada("La bóveda está bloqueada en este equipo.")
    if clave not in _fernet_cache:
        _fernet_cache[clave] = Fernet(clave.encode())
    return _fernet_cache[clave]


def existe():
    return _leer().get("version") == 2 and "sal" in _leer()


def desbloqueada():
    if not existe():
        return False
    try:
        f = _fernet()
        return f.decrypt(_leer()["verificador"].encode()).decode() == VERIFICADOR
    except Exception:
        return False


def crear(frase):
    """Crea la bóveda con una frase maestra (solo la primera vez, en un equipo)."""
    from cryptography.fernet import Fernet
    if existe():
        raise ValueError("La bóveda ya existe: usa desbloquear().")
    if len(frase) < 8:
        raise ValueError("La frase maestra debe tener al menos 8 caracteres.")
    sal = os.urandom(16)
    clave = _derivar(frase, sal)
    f = Fernet(clave)
    with _lock:
        antiguo = _leer()
        datos = {"version": 2, "sal": base64.b64encode(sal).decode(),
                 "verificador": f.encrypt(VERIFICADOR.encode()).decode(), "entradas": {}}
        # migrar entradas del formato antiguo (valores en el llavero local)
        for nombre, d in antiguo.get("entradas", {}).items():
            valor = keyring.get_password(SERVICIO, nombre) if antiguo.get("version") == 1 else None
            if valor:
                datos["entradas"][nombre] = {**d, "valor": f.encrypt(valor.encode()).decode()}
        _escribir(datos)
    keyring.set_password(SERVICIO, CLAVE_LOCAL, clave.decode())


def desbloquear(frase):
    """Desbloquea la bóveda en este equipo. Devuelve True si la frase es correcta."""
    from cryptography.fernet import Fernet, InvalidToken
    datos = _leer()
    if not existe():
        return False
    clave = _derivar(frase, base64.b64decode(datos["sal"]))
    try:
        if Fernet(clave).decrypt(datos["verificador"].encode()).decode() != VERIFICADOR:
            return False
    except InvalidToken:
        return False
    keyring.set_password(SERVICIO, CLAVE_LOCAL, clave.decode())
    return True


def bloquear():
    try:
        keyring.delete_password(SERVICIO, CLAVE_LOCAL)
    except Exception:
        pass


# ------------------------------------------------------------- consultas
def listar():
    """{nombre: {"tipo":..., "descripcion":..., "usuario":...}} (sin valores)."""
    return {n: {k: v for k, v in d.items() if k != "valor"} for n, d in _leer().get("entradas", {}).items()}


def para_prompt():
    ind = listar()
    if not ind:
        return "(vacía)"
    estado = "" if desbloqueada() else " [BLOQUEADA en este equipo: hay que escribir la frase maestra]"
    return "; ".join(
        f"{n} [{d.get('tipo', 'otro')}]" + (f" ({d['descripcion']})" if d.get("descripcion") else "")
        for n, d in sorted(ind.items())) + estado


def buscar(nombre):
    """Devuelve el nombre exacto guardado que mejor encaja, o None."""
    ind = listar()
    if nombre in ind:
        return nombre
    n = _norm(nombre)
    if not n:
        return None
    for k in ind:
        if _norm(k) == n:
            return k
    candidatos = [k for k in ind if n in _norm(k) or _norm(k) in n]
    return candidatos[0] if len(candidatos) == 1 else None


def guardar(nombre, valor, tipo="otro", descripcion="", usuario=""):
    nombre = nombre.strip()
    if not nombre or not valor:
        raise ValueError("Falta el nombre o el valor")
    f = _fernet()
    with _lock:
        datos = _leer()
        datos["entradas"][nombre] = {
            "tipo": tipo if tipo in TIPOS else "otro", "descripcion": descripcion.strip(),
            "usuario": usuario.strip(), "valor": f.encrypt(valor.encode()).decode()}
        _escribir(datos)


def obtener(nombre):
    """(nombre_exacto, valor). Valor None si no existe o la bóveda está bloqueada."""
    clave = buscar(nombre)
    if not clave:
        return None, None
    cifrado = _leer()["entradas"][clave].get("valor")
    try:
        return clave, _fernet().decrypt(cifrado.encode()).decode()
    except Exception:
        return clave, None


def borrar(nombre):
    clave = buscar(nombre)
    if not clave:
        return False
    with _lock:
        datos = _leer()
        datos["entradas"].pop(clave, None)
        _escribir(datos)
    return True


def firma():
    """Cambia cuando se modifica la bóveda (para recargar servicios)."""
    try:
        return ARCHIVO.stat().st_mtime, desbloqueada()
    except FileNotFoundError:
        return 0, False


def copiar_temporal(valor, segundos=30):
    """Copia al portapapeles y lo limpia pasados unos segundos si nadie lo ha cambiado."""
    import pyperclip
    pyperclip.copy(valor)

    def limpiar():
        try:
            if pyperclip.paste() == valor:
                pyperclip.copy("")
        except Exception:
            pass
    threading.Timer(segundos, limpiar).start()


# --------------------------------------------------------- WiFi del sistema
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _netsh(*args):
    r = subprocess.run(["netsh", "wlan", *args], capture_output=True, creationflags=SIN_VENTANA)
    return r.stdout.decode("cp850", errors="ignore")


def wifi_actual():
    if config.ES_MAC:
        for iface in ("en0", "en1"):
            r = subprocess.run(["networksetup", "-getairportnetwork", iface], capture_output=True, text=True)
            if ":" in r.stdout and "not associated" not in r.stdout.lower():
                return r.stdout.split(":", 1)[1].strip()
        return None
    for linea in _netsh("show", "interfaces").splitlines():
        if re.match(r"\s*SSID\s*:", linea):
            return linea.split(":", 1)[1].strip()
    return None


def wifi_clave(ssid=None):
    """Contraseña de una red WiFi guardada en el sistema. Devuelve (ssid, clave)."""
    ssid = ssid or wifi_actual()
    if not ssid:
        return None, None
    if config.ES_MAC:  # el Mac pedirá tu contraseña de usuario para permitirlo
        r = subprocess.run(["security", "find-generic-password", "-D", "AirPort network password",
                            "-a", ssid, "-w"], capture_output=True, text=True)
        return ssid, (r.stdout.strip() or None)
    salida = _netsh("show", "profile", f"name={ssid}", "key=clear")
    if "no se encuentra" in salida.lower() or "not found" in salida.lower():
        perfiles = re.findall(r":\s*(.+)", _netsh("show", "profiles"))
        parecidos = [p.strip() for p in perfiles if _norm(ssid) in _norm(p)]
        if not parecidos:
            return ssid, None
        ssid = parecidos[0]
        salida = _netsh("show", "profile", f"name={ssid}", "key=clear")
    m = re.search(r"(Contenido de la clave|Key Content)\s*:\s*(.+)", salida)
    return ssid, (m.group(2).strip() if m else None)


def credenciales_wifi(nombre=None):
    """(ssid, clave) desde la bóveda (tipo wifi) o desde el sistema."""
    if nombre:
        clave_b = buscar(nombre)
        if clave_b and listar()[clave_b].get("tipo") == "wifi":
            return listar()[clave_b].get("usuario") or clave_b, obtener(clave_b)[1]
    return wifi_clave(nombre)
