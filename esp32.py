"""Herramientas para proyectos ESP32 con PlatformIO."""
import difflib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

import config

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ULTIMO_LOG = config.DATOS / "ultimo_log_pio.txt"
IGNORAR = {".pio", ".git", "node_modules", ".vscode", "__pycache__", ".venv", "lib", "test"}
CHIPS_ESP = ("cp210", "ch340", "ch910", "ch343", "ftdi", "303a", "10c4", "1a86", "espressif", "usb jtag", "usb serial")


def _norm(t):
    t = unicodedata.normalize("NFD", str(t).lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def pio():
    if Path(config.PIO).exists():
        return config.PIO
    return shutil.which("pio") or shutil.which("platformio")


# ----------------------------------------------------------- proyectos
_cache = {"t": 0, "lista": []}


def proyectos(refrescar=False):
    """Lista de (nombre, ruta) de carpetas con platformio.ini."""
    if not refrescar and time.time() - _cache["t"] < 300:
        return _cache["lista"]
    encontrados = []
    for base in config.PROYECTOS:
        base = Path(base)
        if not base.exists():
            continue
        for raiz, dirs, archivos in os.walk(base):
            prof = len(Path(raiz).relative_to(base).parts)
            dirs[:] = [d for d in dirs if d not in IGNORAR and not d.startswith(".")] if prof < 4 else []
            if "platformio.ini" in archivos:
                encontrados.append((Path(raiz).name, raiz))
                dirs[:] = []
    _cache.update(t=time.time(), lista=sorted(encontrados))
    return _cache["lista"]


def nombres_para_prompt():
    lista = proyectos()
    return ", ".join(n for n, _ in lista[:60]) if lista else "(ninguno encontrado)"


def buscar_proyecto(nombre):
    lista = proyectos()
    if not lista:
        return None
    n = _norm(nombre)
    for nom, ruta in lista:
        if _norm(nom) == n:
            return nom, ruta
    contiene = [(nom, ruta) for nom, ruta in lista if n and (n in _norm(nom) or _norm(nom) in n)]
    if len(contiene) == 1:
        return contiene[0]
    opciones = {_norm(nom): (nom, ruta) for nom, ruta in (contiene or lista)}
    mejor = difflib.get_close_matches(n, opciones.keys(), n=1, cutoff=0.45)
    return opciones[mejor[0]] if mejor else None


def entornos(ruta):
    try:
        texto = (Path(ruta) / "platformio.ini").read_text(encoding="utf-8", errors="ignore")
        return re.findall(r"^\s*\[env:([^\]]+)\]", texto, re.M)
    except Exception:
        return []


# -------------------------------------------------------- compilar/subir
def _resumen_build(salida, codigo, segundos, subir):
    lineas = salida.splitlines()
    if codigo == 0:
        partes = [f"{'Subida' if subir else 'Compilación'} correcta en {round(segundos)} segundos."]
        for tipo in ("RAM", "Flash"):
            m = re.search(rf"{tipo}:\s*\[.*?\]\s*([\d.]+)%", salida)
            if m:
                partes.append(f"{tipo} al {m.group(1)} por ciento.")
        return " ".join(partes)
    errores = [l.strip() for l in lineas if re.search(r"\berror\b|Error:|fatal", l, re.I)]
    if any("could not open port" in l.lower() or "no serial data" in l.lower() for l in lineas):
        return "Falló la subida: no puedo abrir el puerto serie. Revisa el cable o cierra el monitor serie."
    if any("timed out waiting for packet header" in l.lower() or "failed to connect" in l.lower() for l in lineas):
        return "Falló la subida: la placa no responde. Mantén pulsado BOOT mientras sube."
    detalle = "\n".join(errores[:6]) or "\n".join(lineas[-8:])
    return f"Falló tras {round(segundos)} s con {len(errores)} errores. Primeros errores:\n{detalle}"


def compilar(ruta, subir=False, entorno=None, puerto=None):
    exe = pio()
    if not exe:
        return "No encuentro PlatformIO en este PC."
    cmd = [exe, "run", "-d", ruta]
    if entorno:
        cmd += ["-e", entorno]
    if subir:
        cmd += ["-t", "upload"]
        if puerto:
            cmd += ["--upload-port", puerto]
    inicio = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900, creationflags=SIN_VENTANA)
        salida = (r.stdout + r.stderr).decode("utf-8", errors="ignore")
        codigo = r.returncode
    except subprocess.TimeoutExpired:
        return "La compilación ha tardado más de 15 minutos y la he cancelado."
    ULTIMO_LOG.write_text(f"$ {' '.join(cmd)}\n\n{salida}", encoding="utf-8")
    return _resumen_build(salida, codigo, time.time() - inicio, subir)


# ------------------------------------------------------------- puertos
def puertos():
    exe = pio()
    lista = []
    if exe:
        try:
            r = subprocess.run([exe, "device", "list", "--json-output"], capture_output=True,
                               timeout=30, creationflags=SIN_VENTANA)
            lista = json.loads(r.stdout.decode("utf-8", errors="ignore") or "[]")
        except Exception as e:
            print(f"[esp32] {e}")
    if not lista:
        try:
            from serial.tools import list_ports
            lista = [{"port": p.device, "description": p.description, "hwid": p.hwid}
                     for p in list_ports.comports()]
        except Exception:
            pass
    return lista


def puertos_texto():
    lista = puertos()
    if not lista:
        return "No hay ningún puerto serie conectado."
    lineas = []
    for p in lista:
        desc = f"{p.get('description', '')} {p.get('hwid', '')}"
        esp = any(c in desc.lower() for c in CHIPS_ESP)
        lineas.append(f"{p['port']}: {p.get('description', '')}{' (probablemente un ESP32)' if esp else ''}")
    return "Puertos serie:\n" + "\n".join(lineas)


def puerto_esp():
    for p in puertos():
        if any(c in f"{p.get('description', '')} {p.get('hwid', '')}".lower() for c in CHIPS_ESP):
            return p["port"]
    return None


# --------------------------------------------------------- ventanas
def monitor(ruta=None, puerto=None, baudios=None):
    exe = pio()
    if not exe:
        return "No encuentro PlatformIO."
    args = [exe, "device", "monitor"]
    if ruta:
        args += ["-d", ruta]
    if puerto:
        args += ["-p", puerto]
    if baudios:
        args += ["-b", str(int(baudios))]
    elif not ruta:
        args += ["-b", "115200"]
    import sistema
    sistema.terminal(args, titulo="Monitor serie")
    return None


def abrir_vscode(ruta):
    if config.ES_MAC:
        subprocess.Popen(["open", "-a", "Visual Studio Code", ruta])
        return
    code = shutil.which("code") or str(Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd")
    subprocess.Popen(f'"{code}" "{ruta}"', shell=True, creationflags=SIN_VENTANA)


def escribir_secrets(ruta, ssid, clave):
    """Crea include/secrets.h con las credenciales y lo excluye de git."""
    inc = Path(ruta) / "include"
    inc.mkdir(exist_ok=True)
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    (inc / "secrets.h").write_text(
        "#pragma once\n// Generado por Jarvis. No lo subas a git.\n"
        f'#define WIFI_SSID "{esc(ssid)}"\n#define WIFI_PASS "{esc(clave)}"\n'
        f'#define MQTT_HOST "{esc(config.MQTT_HOST)}"\n#define MQTT_PORT {config.MQTT_PUERTO}\n',
        encoding="utf-8")
    gi = Path(ruta) / ".gitignore"
    if (Path(ruta) / ".git").exists() or gi.exists():
        actual = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if "secrets.h" not in actual:
            gi.write_text((actual.rstrip("\n") + "\n" if actual.strip() else "") + "include/secrets.h\n", encoding="utf-8")
    return str(inc / "secrets.h")
