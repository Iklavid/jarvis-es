"""Teles LG webOS por la red local (puerto 3001, el de la app LG ThinQ).

Se configuran en el .env, una por tele separadas por ;  (nombre|IP|MAC para encenderla|nombre en la bóveda):
  TELES=salon|192.168.1.20|aa:bb:cc:dd:ee:ff|TV salon;cuarto|192.168.1.21|aa:bb:cc:dd:ee:00|TV cuarto
La clave de emparejamiento de cada una se guarda sola en la bóveda la primera vez.
Solo llegan los equipos de esa red; desde otro equipo añade "equipo":"<nombre>".
"""
import asyncio
import json
import socket
import ssl

import os

import boveda

TELES = {}
for _t in os.getenv("TELES", "").split(";"):
    _p = [x.strip() for x in _t.split("|")]
    if len(_p) == 4 and _p[0]:
        TELES[_p[0].lower()] = {"ip": _p[1], "mac": _p[2], "boveda": _p[3], "nombre": f"la tele {_p[0]}"}
SINONIMOS = {}

# Nombre que dice el usuario -> id de la app en webOS
APPS = {"youtube": "youtube.leanback.v4", "netflix": "netflix", "disney": "com.disney.disneyplus-prod",
        "prime": "amazon", "amazon": "amazon", "hbo": "com.wbd.stream", "max": "com.wbd.stream",
        "dazn": "dazn", "movistar": "com.telefonica.app.nux", "apple": "com.apple.appletv",
        "skyshowtime": "com.skyshowtime.tv", "rakuten": "ui30", "pluto": "com.plutotv.app",
        "tdt": "com.webos.app.livetv", "television": "com.webos.app.livetv", "televisión": "com.webos.app.livetv",
        "navegador": "com.webos.app.browser", "alexa": "amazon.alexa.view", "inicio": "com.webos.app.home",
        "multimedia": "com.webos.app.mediadiscovery", "luna": "com.amazon.luna",
        "red bull": "tv.redbull.lg", "redbull": "tv.redbull.lg", "kodi": "org.xbmc.kodi",
        "jellyfin": "org.jellyfin.webos", "moonlight": "com.limelight.webos", "tienda": "com.webos.app.discovery"}

PERMISOS = ["LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE", "CONTROL_AUDIO", "CONTROL_DISPLAY",
            "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV", "CONTROL_POWER",
            "CONTROL_TV_SCREEN", "READ_APP_STATUS", "READ_CURRENT_CHANNEL", "READ_INPUT_DEVICE_LIST",
            "READ_RUNNING_APPS", "READ_TV_CHANNEL_LIST", "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE",
            "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD", "READ_INSTALLED_APPS", "READ_SETTINGS",
            "WRITE_NOTIFICATION_ALERT"]


def _tele(nombre):
    n = (nombre or next(iter(TELES), "")).lower().strip()
    n = SINONIMOS.get(n, n)
    return TELES.get(n)


def _ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    return ctx


async def _sesion(t, peticiones, tecla=None):
    import websockets
    _, clave = boveda.obtener(t["boveda"])
    carga = {"forcePairing": False, "pairingType": "PROMPT", "manifest": {
        "manifestVersion": 1, "appVersion": "1.1", "permissions": PERMISOS,
        "signed": {"appId": "com.lge.test", "vendorId": "com.lge", "created": "20140509",
                   "localizedAppNames": {"": "Jarvis"}}}}
    if clave:
        carga["client-key"] = clave
    respuestas = []
    async with websockets.connect(f"wss://{t['ip']}:3001", ssl=_ssl(), open_timeout=5, max_size=None) as ws:
        await ws.send(json.dumps({"id": "reg", "type": "register", "payload": carga}))
        while True:
            r = json.loads(await asyncio.wait_for(ws.recv(), 60))
            if r.get("type") == "registered":
                nueva = r.get("payload", {}).get("client-key")
                if nueva and nueva != clave:
                    boveda.guardar(t["boveda"], nueva, "token", f"Emparejamiento de {t['nombre']} ({t['ip']})")
                break
            if r.get("type") == "error":
                raise RuntimeError(r.get("error"))
        for i, (uri, payload) in enumerate(peticiones):
            msg = {"id": f"m{i}", "type": "request", "uri": uri}
            if payload is not None:
                msg["payload"] = payload
            await ws.send(json.dumps(msg))
            while True:
                r = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if r.get("id") == f"m{i}":
                    respuestas.append(r.get("payload", {}))
                    break
        if tecla:
            await ws.send(json.dumps({"id": "ptr", "type": "request",
                                      "uri": "ssap://com.webos.service.networkinput/getPointerInputSocket"}))
            while True:
                r = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if r.get("id") == "ptr":
                    break
            async with websockets.connect(r["payload"]["socketPath"], ssl=_ssl()) as p:
                for b in tecla:
                    await p.send(f"type:button\nname:{b.upper()}\n\n")
                    await asyncio.sleep(0.3)
    return respuestas


def _wol(mac):
    datos = bytes.fromhex("FF" * 6 + mac.replace(":", "") * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            s.sendto(datos, (os.getenv("TELES_BROADCAST", "255.255.255.255"), 9))


def ordenar(accion):
    """Ejecuta {"tv":..., "orden":..., "valor":...} y devuelve el texto para decir."""
    t = _tele(accion.get("tv"))
    if not t:
        return "No sé de qué tele me hablas: tengo " + (", ".join(TELES) or "ninguna configurada (TELES en el .env)") + "."
    orden = (accion.get("orden") or "estado").lower()
    valor = accion.get("valor")
    if orden == "encender":
        _wol(t["mac"])
        return f"Encendiendo {t['nombre']}."
    tecla = None
    if orden == "app":
        nombre = str(valor or "").lower().strip()
        app = APPS.get(nombre) or next((v for k, v in APPS.items() if k in nombre), nombre)
        peticiones = [("ssap://system.launcher/launch", {"id": app})]
    elif orden == "volumen":
        if valor in ("subir", "bajar"):
            peticiones = [(f"ssap://audio/volume{'Up' if valor == 'subir' else 'Down'}", None)] * 3
        elif valor == "silencio":
            peticiones = [("ssap://audio/setMute", {"mute": True})]
        else:
            peticiones = [("ssap://audio/setVolume", {"volume": max(0, min(100, int(valor)))})]
    elif orden == "tecla":
        peticiones, tecla = [], [b for b in str(valor or "").split(",") if b.strip()]
    elif orden == "aviso":
        peticiones = [("ssap://system.notifications/createAlert", {
            "title": "Jarvis", "message": str(valor or ""), "modal": False, "isSysReq": True,
            "buttons": [{"label": "OK", "focus": True, "buttonType": "ok"}], "type": "confirm"})]
    elif orden == "apagar":
        peticiones = [("ssap://system/turnOff", None)]
    elif orden == "pantalla":
        apagar = str(valor).lower() in ("off", "apagar", "0", "false")
        peticiones = [(f"ssap://com.webos.service.tvpower/power/turn{'Off' if apagar else 'On'}Screen", None)]
    else:  # estado
        peticiones = [("ssap://com.webos.applicationManager/getForegroundAppInfo", None),
                      ("ssap://audio/getVolume", None)]
    try:
        r = asyncio.run(_sesion(t, peticiones, tecla))
    except (OSError, asyncio.TimeoutError) as e:
        return f"No llego a {t['nombre']}; puede que esté apagada. ({e.__class__.__name__})"
    except Exception as e:
        if "Try Again Later" in str(e):
            return f"{t['nombre'].capitalize()} está en reposo; pídeme que la encienda."
        raise
    if orden == "estado":
        app = r[0].get("appId", "")
        nombre = next((k for k, v in APPS.items() if v == app), app)
        vol = r[1].get("volumeStatus", {}).get("volume", r[1].get("volume"))
        return f"{t['nombre'].capitalize()} está encendida, en {nombre}, con el volumen a {vol}."
    fallo = next((x for x in r if x.get("returnValue") is False), None)
    if fallo:
        return f"{t['nombre'].capitalize()} no lo ha aceptado: {fallo.get('errorText', 'error')}."
    return "Hecho."
