"""Segundo cerebro: la bóveda de Obsidian en Proyectos/SegundoCerebro (Syncthing).

Jarvis escribe en la nota del día (Diario/AAAA-MM-DD.md), en su apartado:
  «Jarvis, apunta en notas que…» → ## Notas;  avisos que manda al móvil → ## Avisos.
La plantilla es la de Obsidian (Plantillas/Diario.md). Ruta: SEGUNDO_CEREBRO o ../../SegundoCerebro.
"""
import os
import threading
from datetime import datetime
from pathlib import Path

BOVEDA = Path(os.getenv("SEGUNDO_CEREBRO", str(Path(__file__).resolve().parents[2] / "SegundoCerebro")))
APARTADOS = ("Notas", "Hecho", "Avisos", "Salud")
_lock = threading.Lock()


def _nota_del_dia(fecha=None):
    dia = f"{fecha or datetime.now():%Y-%m-%d}"
    ruta = BOVEDA / "Diario" / f"{dia}.md"
    if not ruta.exists():
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(f"# {dia}\n\n" + "".join(f"## {a}\n\n" for a in APARTADOS),
                        encoding="utf-8")
    return ruta


def anotar(texto, apartado="Notas", fecha=None):
    """Añade «- HH:MM texto» al final del apartado de la nota de hoy (o de `fecha`). Devuelve None o el error."""
    texto = " ".join(str(texto).split())
    if not texto:
        return "No hay nada que apuntar."
    if not BOVEDA.exists():
        return "No encuentro la bóveda de notas en este equipo."
    try:
        with _lock:
            ruta = _nota_del_dia(fecha)
            lineas = ruta.read_text(encoding="utf-8").splitlines()
            linea = f"- {datetime.now():%H:%M} {texto}"
            cab = f"## {apartado}"
            if cab not in lineas:
                lineas += ["", cab, ""]
            i = lineas.index(cab) + 1
            while i < len(lineas) and not lineas[i].startswith("## "):
                i += 1
            while i > 0 and not lineas[i - 1].strip():   # justo después de la última línea con texto
                i -= 1
            lineas.insert(i, linea)
            if i + 1 < len(lineas) and lineas[i + 1].startswith("## "):
                lineas.insert(i + 1, "")
            ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    except OSError as e:
        return f"No he podido escribir la nota: {e}"
    return None
