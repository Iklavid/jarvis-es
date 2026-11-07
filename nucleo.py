"""Bucle principal: escuchar → pensar → actuar → hablar."""
import queue
import random
import threading
import time

import acciones
import boveda
import config
import equipos
import notas
import gasto
import movil
import voz
import whatsapp_cloud
import whatsapp_web
from cerebro import Cerebro
from riego import Riego

SALUDOS = [
    "Sistemas en línea. ¿En qué puedo ayudarle, {t}?",
    "Buenas, {t}. Todo listo y a su disposición.",
    "J.A.R.V.I.S. operativo. Le escucho, {t}.",
    "De vuelta en línea, {t}. ¿Qué hacemos hoy?",
]
CONFIRMACIONES = ["¿Sí, {t}?", "Le escucho.", "Dígame.", "¿Qué necesita?"]
PARAR = ("para", "cállate", "silencio", "calla", "basta")


class Nucleo:
    """estado: 'reposo' | 'escuchando' | 'pensando' | 'hablando' | 'apagado'"""

    def __init__(self, al_cambiar=None):
        self.al_cambiar = al_cambiar or (lambda **kw: None)
        self.activo = True
        self.cerebro = Cerebro()
        self.red = None
        # Alarmas y avisos del riego solo los dispara el equipo principal (para no oírlos dos veces)
        self.agenda = acciones.Agenda(self.avisar, activo=lambda: self.es_principal)
        self.riego = Riego(self.avisar_riego)
        self.oido = None
        self.seguimiento_hasta = 0
        self._avisos = queue.Queue()
        self._lock_turno = threading.Lock()
        self._limite_avisado = False
        self.remoto = False
        self._responder = None
        threading.Thread(target=self._repartir_avisos, daemon=True).start()
        self.telegram = movil.Telegram(self)
        self.whatsapp = (whatsapp_cloud.WhatsAppCloud(self) if config.WA_MODO == "cloud"
                         else whatsapp_web.WhatsAppWeb(self))
        self.red = equipos.Red(self, puede_principal=lambda: not boveda.existe() or boveda.desbloqueada())
        self.cerebro.equipos = self.red.para_prompt
        threading.Thread(target=self._vigilar_boveda, daemon=True).start()

    # ------------------------------------------------------ multiequipo
    @property
    def es_principal(self):
        return bool(self.red and self.red.es_principal)

    def cambio_principal(self, es):
        """Este equipo pasa a ser (o deja de ser) el que atiende Telegram, WhatsApp y avisos."""
        print(f"[equipos] {config.EQUIPO} {'pasa a ser' if es else 'deja de ser'} el equipo principal")
        if es:
            self.telegram.iniciar()
            self.whatsapp.iniciar()
        else:
            self.telegram.detener()
            self.whatsapp.detener()
        self.ui(principal=es)

    def _vigilar_boveda(self):
        """Si cambian las claves (desde este u otro equipo), recarga Telegram y WhatsApp."""
        firma = boveda.firma()
        while True:
            time.sleep(15)
            nueva = boveda.firma()
            if nueva != firma:
                firma = nueva
                self.telegram.recargar()
                self.whatsapp.recargar()
                if self.es_principal:
                    self.telegram.iniciar()
                    self.whatsapp.iniciar()

    def ejecutar_para_otro(self, accion, devolver):
        """Ejecuta una acción pedida por otro equipo y le devuelve el resultado."""
        with self._lock_turno:
            self._responder = lambda texto: devolver("decir", texto)
            try:
                r = acciones.ejecutar(accion, self)
                if r:
                    devolver(*r)
            except Exception as e:
                devolver("decir", f"Error en {config.EQUIPO}: {e}")
            finally:
                self._responder = None

    # --------------------------------------------------------- utilidades
    def _estado(self, estado, **extra):
        self.al_cambiar(estado=estado, **extra)

    def ui(self, **evento):
        """Eventos para la interfaz (abrir ventanas, mostrar secretos…)."""
        self.al_cambiar(**evento)

    def decir(self, texto):
        self._estado("hablando", jarvis=texto)
        voz.decir(texto)
        self._estado("reposo")

    def avisar(self, texto):
        """Avisos espontáneos (alarmas, riego): se dicen cuando Jarvis está libre."""
        self._avisos.put(texto)

    def decir_aviso(self, texto):
        """Avisos solo de voz en este equipo (saludo de Home Assistant): no van al móvil."""
        self._avisos.put((texto, False))

    def avisar_riego(self, texto):
        """Avisos del riego: todos los equipos los dicen; solo el principal los manda al movil."""
        self._avisos.put((texto, self.es_principal))

    def _repartir_avisos(self):
        while True:
            item = self._avisos.get()
            texto, al_movil = item if isinstance(item, tuple) else (item, True)
            if al_movil:
                self.al_movil(texto)
            with self._lock_turno:
                acciones.notificar("J.A.R.V.I.S.", texto)
                self.decir(texto)

    def whatsapp_aviso(self, texto):
        """Por el WhatsApp de Jarvis si está conectado (o dentro de la ventana de 24 h con la API de Meta);
        si no, por CallMeBot."""
        if self.whatsapp.activo and self.whatsapp.ventana_abierta() and self.whatsapp.enviar(texto):
            return True
        return movil.whatsapp("Jarvis: " + texto)

    def al_movil(self, texto):
        """Reenvía un aviso a Telegram y/o WhatsApp según AVISOS_MOVIL."""
        canales = config.AVISOS_MOVIL
        notas.anotar(texto, "Avisos")   # el diario de Obsidian (solo lo escribe el principal)
        def enviar():
            if "telegram" in canales and self.telegram.emparejado:
                self.telegram.enviar("🔔 " + texto)
            if "whatsapp" in canales:
                self.whatsapp_aviso(texto)
        threading.Thread(target=enviar, daemon=True).start()

    def apagar(self):
        self.activo = False
        self.whatsapp.detener()

    def activar_escucha(self):
        """Atajo de teclado / clic: escucha sin palabra clave."""
        if voz.hablando.is_set():
            voz.callar()
        self.seguimiento_hasta = time.time() + 10
        self._estado("escuchando")
        voz.pitido()

    def en_segundo_plano(self, funcion, peticion):
        responder = self._responder

        def tarea():
            try:
                datos = funcion()
            except Exception as e:
                datos = f"Error: {e}"
            texto = self.cerebro.resumir(peticion, datos)
            if responder:
                responder(texto)
            else:
                self.avisar(texto)
        threading.Thread(target=tarea, daemon=True).start()

    def _quitar_palabra_clave(self, texto):
        i = texto.find(config.PALABRA_CLAVE)
        return texto[i + len(config.PALABRA_CLAVE):].strip(" ,.") if i >= 0 else texto

    # ------------------------------------------------------------- ciclo
    def atender(self, orden, responder=None, remoto=False):
        """Procesa una orden. `responder` envía las respuestas a otro sitio (p. ej. Telegram)."""
        salida = responder or self.decir
        with self._lock_turno:
            self.remoto, self._responder = remoto, responder
            try:
                self._estado("pensando", usuario=("📱 " if remoto else "") + orden)
                respuesta, lista = self.cerebro.pensar(orden, remoto=remoto)
                if respuesta:
                    salida(respuesta)
                for accion in lista:
                    destino = str(accion.pop("equipo", "") or "").lower()
                    if destino and destino != config.EQUIPO:
                        if self.red.enviar(destino, accion, orden, al_terminar=responder or self.avisar):
                            continue
                        salida(f"El equipo {destino} no está encendido ahora mismo.")
                        continue
                    resultado = acciones.ejecutar(accion, self)
                    if not resultado:
                        continue
                    clase, texto = resultado
                    if clase == "dato":
                        self._estado("pensando")
                        texto = self.cerebro.resumir(orden, texto)
                    salida(texto)
                self._comprobar_limite()
            finally:
                self.remoto, self._responder = False, None
                self._estado("reposo", gasto=gasto.hoy())

    def _comprobar_limite(self):
        if config.LIMITE_DIARIO_USD and not self._limite_avisado and gasto.hoy() > config.LIMITE_DIARIO_USD:
            self._limite_avisado = True
            self.decir(f"Aviso: hoy ya hemos gastado más de {config.LIMITE_DIARIO_USD} dólares en DeepSeek.")

    def ejecutar(self):
        try:
            self.oido = voz.Oido()
        except Exception as e:
            self.decir("No encuentro ningún micrófono. Puede escribirme en la caja de texto.")
            print(f"[oído] {e}")
            return
        despertador = None
        if config.PALABRA_LOCAL:
            try:
                despertador = voz.Despertador()
                print("[oído] Palabra de activación en el equipo: «hey Jarvis»")
            except Exception as e:
                print(f"[oído] Sin palabra de activación local ({e}); sigo como antes")
        t = config.TRATAMIENTO
        self.decir(random.choice(SALUDOS).format(t=t))
        self._estado("reposo", gasto=gasto.hoy())

        while self.activo:
            if time.time() > self.seguimiento_hasta:
                self._estado("reposo")
            if despertador and time.time() >= self.seguimiento_hasta:
                # Fuera de una conversación no se manda nada a Google hasta oír «hey Jarvis»
                if despertador.esperar(lambda: self.activo and time.time() >= self.seguimiento_hasta):
                    self.activar_escucha()
                continue
            espera = max(1, self.seguimiento_hasta - time.time()) if despertador else None
            texto = self.oido.escuchar(espera=espera)
            if not texto or not self.activo:
                continue
            en_conversacion = bool(despertador) or time.time() < self.seguimiento_hasta

            if config.PALABRA_CLAVE in texto:
                print(f"USTED: {texto}")
                orden = self._quitar_palabra_clave(texto)
                if not orden:  # solo dijo "Jarvis"
                    self.decir(random.choice(CONFIRMACIONES).format(t=t))
                    self._estado("escuchando")
                    orden = self.oido.escuchar(espera=8)
                    if not orden:
                        continue
                    print(f"USTED: {orden}")
            elif en_conversacion:
                print(f"USTED: {texto}")
                orden = texto
            else:
                continue  # conversación ajena: se ignora

            if orden.strip(" .!") in PARAR:
                voz.callar()
                continue
            self.atender(orden)
            self.seguimiento_hasta = time.time() + config.SEGUIMIENTO
            self._estado("escuchando")

        self.decir(f"Desconectando. Hasta luego, {t}.")
        self._estado("apagado")


def en_segundo_plano(al_cambiar=None):
    nucleo = Nucleo(al_cambiar)
    hilo = threading.Thread(target=nucleo.ejecutar, daemon=True)
    hilo.start()
    return nucleo, hilo
