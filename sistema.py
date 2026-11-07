"""Pequeñas utilidades que cambian entre Windows y macOS."""
import os
import shlex
import subprocess

import config


def abrir(ruta):
    """Abre un archivo o carpeta con el programa por defecto."""
    if config.ES_WIN:
        os.startfile(str(ruta))
    elif config.ES_MAC:
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)])


def terminal(argumentos, carpeta=None, titulo="Jarvis"):
    """Abre una ventana de terminal visible que ejecuta `argumentos` (lista) en `carpeta`."""
    if config.ES_MAC:
        cmd = " ".join(shlex.quote(str(a)) for a in argumentos)
        if carpeta:
            cmd = f"cd {shlex.quote(str(carpeta))} && {cmd}"
        esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{esc}"',
                          "-e", 'tell application "Terminal" to activate'])
        return
    linea = " ".join(f'"{a}"' if (" " in str(a) or not a) else str(a) for a in argumentos)
    destino = f'/D "{carpeta}" ' if carpeta else ""
    subprocess.Popen(f'start "{titulo}" {destino}cmd /k "{linea}"', shell=True)
