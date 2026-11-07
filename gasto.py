"""Control de tokens y gasto de DeepSeek."""
import json
import threading
from datetime import datetime, timezone

import requests

import config

ARCHIVO = config.DATOS / "gasto.json"
_lock = threading.Lock()

# USD por millón de tokens en hora punta (fuera de punta es la mitad)
PRECIOS = {
    "deepseek-flash": {"hit": 0.006, "miss": 0.30, "out": 1.20},
    "deepseek-v4-pro": {"hit": 0.044, "miss": 1.32, "out": 3.96},
}


def _hora_punta(ahora=None):
    a = ahora or datetime.now(timezone.utc)
    return a.weekday() < 5 and (1 <= a.hour < 4 or 6 <= a.hour < 10)


def coste(modelo, uso):
    p = PRECIOS.get(modelo, PRECIOS["deepseek-flash"])
    factor = 1 if _hora_punta() else 0.5
    hit = uso.get("prompt_cache_hit_tokens", 0)
    miss = uso.get("prompt_cache_miss_tokens", uso.get("prompt_tokens", 0) - hit)
    out = uso.get("completion_tokens", 0)
    return (hit * p["hit"] + miss * p["miss"] + out * p["out"]) / 1e6 * factor


def _leer():
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def registrar(modelo, uso):
    """Suma una llamada. Devuelve el gasto de hoy en USD."""
    usd = coste(modelo, uso)
    hoy = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        datos = _leer()
        d = datos.setdefault(hoy, {"usd": 0, "llamadas": 0, "entrada": 0, "cache": 0, "salida": 0})
        d["usd"] += usd
        d["llamadas"] += 1
        d["entrada"] += uso.get("prompt_tokens", 0)
        d["cache"] += uso.get("prompt_cache_hit_tokens", 0)
        d["salida"] += uso.get("completion_tokens", 0)
        ARCHIVO.write_text(json.dumps(datos, indent=1), encoding="utf-8")
        return d["usd"]


def hoy():
    return _leer().get(datetime.now().strftime("%Y-%m-%d"), {}).get("usd", 0.0)


def resumen():
    datos = _leer()
    hoy_k = datetime.now().strftime("%Y-%m-%d")
    mes = datetime.now().strftime("%Y-%m")
    d = datos.get(hoy_k, {})
    mes_usd = sum(v["usd"] for k, v in datos.items() if k.startswith(mes))
    mes_ll = sum(v["llamadas"] for k, v in datos.items() if k.startswith(mes))
    texto = (f"Hoy: {d.get('llamadas', 0)} peticiones, {d.get('entrada', 0)} tokens de entrada "
             f"({d.get('cache', 0)} desde caché) y {d.get('salida', 0)} de salida; "
             f"coste {d.get('usd', 0):.4f} dólares. Este mes: {mes_ll} peticiones, {mes_usd:.4f} dólares.")
    s = saldo()
    if s:
        texto += f" Saldo en la cuenta de DeepSeek: {s}."
    return texto


def saldo():
    if not config.DEEPSEEK_API_KEY:
        return None
    try:
        r = requests.get("https://api.deepseek.com/user/balance",
                         headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}, timeout=10)
        info = r.json()["balance_infos"][0]
        return f"{info['total_balance']} {info['currency']}"
    except Exception:
        return None
