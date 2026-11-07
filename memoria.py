"""Memoria a largo plazo de Jarvis: un archivo de texto con un dato por línea."""
import json
import threading
import unicodedata
from datetime import datetime

import config

ARCHIVO = config.DATOS / "memoria.md"
HISTORIAL = config.DATOS / "historial.json"
DIARIO = config.DATOS / "conversaciones"
_lock = threading.Lock()

CABECERA = "# Memoria de Jarvis\n# Un dato por línea empezando por '- '. Puedes editarlo a mano.\n\n"


def _norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def leer():
    try:
        return ARCHIVO.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def para_prompt(max_chars=12000):
    """Texto de la memoria para el prompt (sin comentarios)."""
    lineas = [l for l in leer().splitlines() if l.strip() and not l.startswith("#")]
    texto = "\n".join(lineas)
    if len(texto) > max_chars:  # se queda con lo más reciente
        texto = texto[-max_chars:]
        texto = texto[texto.find("\n") + 1:]
    return texto


def recordar(dato):
    dato = " ".join(str(dato).split())
    if not dato:
        return False
    with _lock:
        actual = leer()
        if _norm(dato) in _norm(actual):
            return False
        if not actual:
            actual = CABECERA
        if not actual.endswith("\n"):
            actual += "\n"
        ARCHIVO.write_text(actual + f"- {dato}\n", encoding="utf-8")
    return True


def olvidar(texto):
    """Borra las líneas que contienen todas las palabras de `texto`. Devuelve cuántas."""
    palabras = [p for p in _norm(texto).split() if len(p) > 2]
    if not palabras:
        return 0
    with _lock:
        lineas = leer().splitlines()
        quedan = [l for l in lineas
                  if l.startswith("#") or not all(p in _norm(l) for p in palabras)]
        borradas = len(lineas) - len(quedan)
        if borradas:
            ARCHIVO.write_text("\n".join(quedan) + "\n", encoding="utf-8")
    return borradas


def tam():
    return len(para_prompt(10**9))


# ------------------------------------------------- historial de la charla
def cargar_historial():
    try:
        return json.loads(HISTORIAL.read_text(encoding="utf-8"))
    except Exception:
        return []


def guardar_historial(mensajes):
    try:
        HISTORIAL.write_text(json.dumps(mensajes[-40:], ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[memoria] {e}")


def anotar_diario(quien, texto):
    """Registro legible de todas las conversaciones, un archivo por día."""
    try:
        DIARIO.mkdir(exist_ok=True)
        ruta = DIARIO / f"{datetime.now():%Y-%m-%d}.md"
        with ruta.open("a", encoding="utf-8") as f:
            f.write(f"- {datetime.now():%H:%M} **{quien}**: {texto}\n")
    except Exception:
        pass


def buscar_diario(consulta, max_lineas=25):
    """Busca en conversaciones pasadas."""
    palabras = [p for p in _norm(consulta).split() if len(p) > 3]
    resultados = []
    if not DIARIO.exists():
        return ""
    for ruta in sorted(DIARIO.glob("*.md"), reverse=True)[:60]:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if not palabras or any(p in _norm(linea) for p in palabras):
                resultados.append(f"{ruta.stem} {linea[2:]}")
                if len(resultados) >= max_lineas:
                    return "\n".join(resultados)
    return "\n".join(resultados)
