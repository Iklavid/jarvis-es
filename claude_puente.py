"""Puente con Claude Code instalado en el PC."""
import json
import shutil
import subprocess
from pathlib import Path

import config
import memoria

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ULTIMA = config.DATOS / "claude_ultima_respuesta.md"
CONTEXTO = config.DATOS / "contexto_claude.md"


def ejecutable():
    for c in (config.CLAUDE, shutil.which("claude"),
              str(Path.home() / ".local" / "bin" / "claude.exe")):
        if c and Path(c).exists():
            return c
    return None


def _preparar_contexto():
    """Le pasamos a Claude lo que Jarvis sabe del usuario."""
    CONTEXTO.write_text(
        "Te escribe Jarvis, el asistente de voz del usuario, en su PC con Windows. "
        "Responde en español. Lo que Jarvis sabe del usuario:\n\n" + memoria.para_prompt(),
        encoding="utf-8")
    return str(CONTEXTO)


def _carpeta(carpeta):
    if carpeta and Path(carpeta).exists():
        return carpeta
    return config.PROYECTOS[0] if config.PROYECTOS and Path(config.PROYECTOS[0]).exists() else str(Path.home())


def preguntar(texto, carpeta=None):
    """Consulta en segundo plano (solo lectura). Devuelve el texto de la respuesta."""
    exe = ejecutable()
    if not exe:
        return ("Claude Code no está instalado en este equipo. Ejecuta "
                + ("instalar_claude.bat" if config.ES_WIN else "instalar_claude_mac.command")
                + " en la carpeta de Jarvis.")
    cmd = [exe, "-p", texto, "--output-format", "json", "--permission-mode", "dontAsk",
           "--append-system-prompt-file", _preparar_contexto()]
    try:
        r = subprocess.run(cmd, cwd=_carpeta(carpeta), capture_output=True, timeout=600,
                           creationflags=SIN_VENTANA)
    except subprocess.TimeoutExpired:
        return "Claude ha tardado más de 10 minutos y he cancelado la consulta."
    salida = r.stdout.decode("utf-8", errors="ignore")
    try:
        datos = json.loads(salida)
        resultado = datos.get("result", "")
    except json.JSONDecodeError:
        resultado = salida or r.stderr.decode("utf-8", errors="ignore")
    ULTIMA.write_text(f"# Pregunta\n{texto}\n\n# Respuesta de Claude\n{resultado}\n", encoding="utf-8")
    return resultado or "Claude no ha devuelto respuesta."


def tarea(texto, carpeta=None):
    """Abre Claude Code en una ventana visible con la tarea, para que la veas y apruebes."""
    exe = ejecutable()
    if not exe:
        return ("Claude Code no está instalado en este equipo. Ejecuta "
                + ("instalar_claude.bat" if config.ES_WIN else "instalar_claude_mac.command")
                + " en la carpeta de Jarvis.")
    import sistema
    seguro = texto.replace('"', "'").replace("\n", " ")
    sistema.terminal([exe, "--append-system-prompt-file", _preparar_contexto(), seguro],
                     carpeta=_carpeta(carpeta), titulo="Claude Code")
    return None


def abrir_ultima():
    if ULTIMA.exists():
        import sistema
        sistema.abrir(ULTIMA)
