"""Interfaz HUD animada de Jarvis (PySide6)."""
import math
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QPainter, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                               QDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import boveda
import config
import sistema
import gasto
import nucleo

COLORES = {
    "reposo": QColor(0, 200, 255),
    "escuchando": QColor(60, 140, 255),
    "pensando": QColor(255, 170, 40),
    "hablando": QColor(80, 255, 230),
    "apagado": QColor(110, 110, 120),
}
ROTULOS = {
    "reposo": 'Diga "Jarvis…"',
    "escuchando": "Escuchando…",
    "pensando": "Procesando…",
    "hablando": "",
    "apagado": "Desconectado",
}


class Puente(QObject):
    cambio = Signal(dict)
    atajo = Signal()


class Reactor(QWidget):
    """Círculo animado central."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(380, 380)
        self.estado = "reposo"
        self.color = QColor(COLORES["reposo"])
        self.t0 = time.time()
        temporizador = QTimer(self)
        temporizador.timeout.connect(self.update)
        temporizador.start(16)

    def poner_estado(self, estado):
        self.estado = estado

    def _mezclar(self, destino, f=0.08):
        c = self.color
        self.color = QColor(
            int(c.red() + (destino.red() - c.red()) * f),
            int(c.green() + (destino.green() - c.green()) * f),
            int(c.blue() + (destino.blue() - c.blue()) * f),
        )

    def paintEvent(self, _):
        self._mezclar(COLORES.get(self.estado, COLORES["reposo"]))
        t = time.time() - self.t0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - 12
        col = self.color

        def c(alpha):
            k = QColor(col)
            k.setAlpha(max(0, min(255, int(alpha))))
            return k

        velocidad = {"pensando": 3.0, "hablando": 1.6, "escuchando": 1.2}.get(self.estado, 0.6)
        if self.estado == "hablando":
            pulso = 0.5 + 0.5 * abs(math.sin(t * 7)) * (0.6 + 0.4 * math.sin(t * 13))
        elif self.estado == "escuchando":
            pulso = 0.6 + 0.4 * math.sin(t * 4)
        else:
            pulso = 0.5 + 0.2 * math.sin(t * 1.5)

        # Halo
        g = QRadialGradient(QPointF(cx, cy), R)
        g.setColorAt(0.0, c(90 * pulso))
        g.setColorAt(0.45, c(25))
        g.setColorAt(1.0, c(0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), R, R)

        # Anillos con marcas
        p.setBrush(Qt.NoBrush)
        for radio, n, largo, sentido, grosor in ((R * 0.95, 72, 6, 1, 1.2), (R * 0.80, 36, 10, -1, 2)):
            p.save()
            p.translate(cx, cy)
            p.rotate(sentido * t * 12 * velocidad)
            p.setPen(QPen(c(170), grosor))
            for i in range(n):
                a = 2 * math.pi * i / n
                largo_i = largo * (1.8 if i % 6 == 0 else 1)
                p.drawLine(QPointF(math.cos(a) * radio, math.sin(a) * radio),
                           QPointF(math.cos(a) * (radio - largo_i), math.sin(a) * (radio - largo_i)))
            p.restore()

        # Arcos giratorios
        for radio, ini, tramo, vel, grosor, alfa in (
            (R * 0.70, 0, 70, 50, 5, 230), (R * 0.70, 180, 40, 50, 5, 160),
            (R * 0.62, 90, 120, -80, 2, 200), (R * 0.88, 30, 30, 30, 3, 150),
            (R * 0.88, 210, 55, 30, 3, 150),
        ):
            p.setPen(QPen(c(alfa), grosor, Qt.SolidLine, Qt.RoundCap))
            rect = QRectF(cx - radio, cy - radio, 2 * radio, 2 * radio)
            p.drawArc(rect, int((ini + t * vel * velocidad) * 16), int(tramo * 16))

        # Barras de "voz" alrededor del núcleo
        nb = 48
        for i in range(nb):
            a = 2 * math.pi * i / nb
            if self.estado == "hablando":
                amp = 6 + 22 * abs(math.sin(t * 9 + i * 0.7) * math.sin(t * 3.1 + i * 1.3))
            elif self.estado == "escuchando":
                amp = 4 + 8 * abs(math.sin(t * 5 + i * 0.5))
            else:
                amp = 3 + 2 * math.sin(t * 2 + i)
            r0 = R * 0.42
            p.setPen(QPen(c(200), 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                       QPointF(cx + math.cos(a) * (r0 + amp), cy + math.sin(a) * (r0 + amp)))

        # Núcleo
        rn = R * 0.30 * (0.92 + 0.08 * pulso)
        g2 = QRadialGradient(QPointF(cx, cy), rn)
        g2.setColorAt(0.0, QColor(255, 255, 255, 240))
        g2.setColorAt(0.35, c(230))
        g2.setColorAt(1.0, c(40))
        p.setPen(QPen(c(255), 2))
        p.setBrush(QBrush(g2))
        p.drawEllipse(QPointF(cx, cy), rn, rn)

        # Texto central
        p.setPen(QColor(10, 25, 35, 230))
        grande = R > 90
        p.setFont(QFont("Segoe UI", 13 if grande else max(9, int(R / 3)), QFont.Bold))
        p.drawText(QRectF(cx - rn, cy - rn, 2 * rn, 2 * rn), Qt.AlignCenter, "J.A.R.V.I.S." if grande else "J")
        p.end()


ESTILO_DIALOGO = """
    QDialog { background: #07131c; }
    QLabel { color: #bfefff; font: 10pt 'Segoe UI'; }
    QLineEdit, QComboBox { background: #0b2533; color: #e6fbff; border: 1px solid #1d6f8f;
                           border-radius: 8px; padding: 6px 8px; font: 10pt 'Segoe UI'; }
    QPushButton { background: #0d3a50; color: #e6fbff; border: 1px solid #1d8fb8; border-radius: 8px;
                  padding: 7px 14px; font: 10pt 'Segoe UI'; }
    QPushButton:hover { background: #125575; }
    QTableWidget { background: #0b1d28; color: #dff7ff; gridline-color: #12384a; border: 1px solid #1d6f8f;
                   font: 10pt 'Segoe UI'; selection-background-color: #125575; }
    QHeaderView::section { background: #0d2a3a; color: #7fe3ff; border: none; padding: 6px; }
"""
if config.ES_MAC:
    ACCESO_INICIO = Path.home() / "Library/LaunchAgents/com.jarvis.asistente.plist"
elif config.ES_WIN:
    ACCESO_INICIO = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Jarvis.lnk"
else:  # Linux: autoarranque del escritorio (KDE)
    ACCESO_INICIO = Path.home() / ".config/autostart/jarvis.desktop"


class DialogoFrase(QDialog):
    """Crear o desbloquear la bóveda compartida con la frase maestra."""

    def __init__(self, padre, crear):
        super().__init__(padre)
        self.crear = crear
        self.setWindowTitle("Crear la bóveda" if crear else "Desbloquear la bóveda")
        self.setStyleSheet(ESTILO_DIALOGO)
        self.setMinimumWidth(460)
        capa = QVBoxLayout(self)
        if crear:
            texto = ("Elige una frase maestra para cifrar tus claves. La bóveda se comparte entre tus "
                     "equipos por Syncthing, y cada equipo te pedirá esta frase una sola vez.\n\n"
                     "Apúntala en un sitio seguro: si la olvidas, habrá que volver a meter las claves.")
        else:
            texto = (f"Escribe la frase maestra para usar la bóveda en este equipo ({config.EQUIPO}). "
                     "Solo se pide una vez; después se recuerda en el llavero del sistema.")
        aviso = QLabel(texto)
        aviso.setWordWrap(True)
        capa.addWidget(aviso)
        self.frase = QLineEdit()
        self.frase.setEchoMode(QLineEdit.Password)
        self.frase.setPlaceholderText("Frase maestra (mínimo 8 caracteres)")
        capa.addWidget(self.frase)
        self.repetir = QLineEdit()
        self.repetir.setEchoMode(QLineEdit.Password)
        self.repetir.setPlaceholderText("Repite la frase")
        if crear:
            capa.addWidget(self.repetir)
        self.error = QLabel()
        self.error.setStyleSheet("color:#ffb070;")
        capa.addWidget(self.error)
        boton = QPushButton("Crear bóveda" if crear else "Desbloquear")
        boton.clicked.connect(self._aceptar)
        self.frase.returnPressed.connect(self._aceptar)
        capa.addWidget(boton, alignment=Qt.AlignRight)

    def _aceptar(self):
        f = self.frase.text()
        try:
            if self.crear:
                if f != self.repetir.text():
                    self.error.setText("Las frases no coinciden.")
                    return
                boveda.crear(f)
                self.accept()
            elif boveda.desbloquear(f):
                self.accept()
            else:
                self.error.setText("Frase incorrecta.")
        except Exception as e:
            self.error.setText(str(e))


class DialogoClave(QDialog):
    def __init__(self, padre, prefill=None):
        super().__init__(padre)
        prefill = prefill or {}
        self.setWindowTitle("Nueva clave")
        self.setStyleSheet(ESTILO_DIALOGO)
        self.setMinimumWidth(420)
        self.nombre = QLineEdit(prefill.get("nombre", ""))
        self.tipo = QComboBox()
        self.tipo.addItems(boveda.TIPOS)
        if prefill.get("tipo") in boveda.TIPOS:
            self.tipo.setCurrentText(prefill["tipo"])
        self.usuario = QLineEdit(prefill.get("usuario", ""))
        self.usuario.setPlaceholderText("SSID, usuario o email (opcional)")
        self.valor = QLineEdit()
        self.valor.setEchoMode(QLineEdit.Password)
        self.valor.setPlaceholderText("La clave, contraseña o token")
        ver = QPushButton("👁")
        ver.setFixedWidth(40)
        ver.setCheckable(True)
        ver.toggled.connect(lambda v: self.valor.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        fila = QHBoxLayout()
        fila.addWidget(self.valor)
        fila.addWidget(ver)
        self.descripcion = QLineEdit(prefill.get("descripcion", ""))
        self.descripcion.setPlaceholderText("Para qué sirve (lo ve Jarvis)")
        form = QFormLayout()
        form.addRow("Nombre", self.nombre)
        form.addRow("Tipo", self.tipo)
        form.addRow("Usuario", self.usuario)
        form.addRow("Valor", fila)
        form.addRow("Descripción", self.descripcion)
        guardar = QPushButton("Guardar")
        guardar.clicked.connect(self._guardar)
        capa = QVBoxLayout(self)
        capa.addLayout(form)
        capa.addWidget(guardar, alignment=Qt.AlignRight)

    def _guardar(self):
        try:
            boveda.guardar(self.nombre.text(), self.valor.text(), self.tipo.currentText(),
                           self.descripcion.text(), self.usuario.text())
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Bóveda", f"No se pudo guardar: {e}")


class DialogoBoveda(QDialog):
    def __init__(self, padre, prefill=None):
        super().__init__(padre)
        self.setWindowTitle("Bóveda de claves de Jarvis")
        self.setStyleSheet(ESTILO_DIALOGO)
        self.resize(640, 420)
        aviso = QLabel("Los valores se guardan cifrados en el Administrador de credenciales de Windows.\n"
                       "Jarvis y DeepSeek solo ven los nombres y descripciones.")
        aviso.setStyleSheet("color:#5fb8d6; font: 9pt 'Segoe UI';")
        self.tabla = QTableWidget(0, 3)
        self.tabla.setHorizontalHeaderLabels(["Nombre", "Tipo", "Descripción"])
        self.tabla.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        botones = QHBoxLayout()
        for texto, fn in (("Añadir", self._anadir), ("Importar WiFi actual", self._wifi),
                          ("Copiar", self._copiar), ("Borrar", self._borrar)):
            b = QPushButton(texto)
            b.clicked.connect(fn)
            botones.addWidget(b)
        capa = QVBoxLayout(self)
        capa.addWidget(aviso)
        capa.addWidget(self.tabla)
        capa.addLayout(botones)
        self._cargar()
        if prefill is not None:
            QTimer.singleShot(100, lambda: self._anadir(prefill))

    def _cargar(self):
        datos = boveda.listar()
        self.tabla.setRowCount(len(datos))
        for i, (n, d) in enumerate(sorted(datos.items())):
            for j, v in enumerate((n, d.get("tipo", ""), d.get("descripcion", ""))):
                self.tabla.setItem(i, j, QTableWidgetItem(v))

    def _seleccion(self):
        fila = self.tabla.currentRow()
        return self.tabla.item(fila, 0).text() if fila >= 0 else None

    def _anadir(self, prefill=None):
        if DialogoClave(self, prefill if isinstance(prefill, dict) else None).exec():
            self._cargar()

    def _wifi(self):
        ssid, clave = boveda.wifi_clave()
        if not clave:
            QMessageBox.information(self, "WiFi", "No he podido leer la contraseña de la red actual.")
            return
        boveda.guardar(ssid, clave, "wifi", "Red WiFi", usuario=ssid)
        self._cargar()

    def _copiar(self):
        nombre = self._seleccion()
        if nombre:
            _, valor = boveda.obtener(nombre)
            boveda.copiar_temporal(valor)

    def _borrar(self):
        nombre = self._seleccion()
        if nombre and QMessageBox.question(self, "Borrar", f"¿Borrar «{nombre}»?") == QMessageBox.Yes:
            boveda.borrar(nombre)
            self._cargar()


class DialogoWhatsApp(QDialog):
    """Estado del WhatsApp de Jarvis y datos para copiar en Meta."""

    def __init__(self, padre, wa):
        super().__init__(padre)
        self.setWindowTitle("WhatsApp de Jarvis")
        self.setStyleSheet(ESTILO_DIALOGO)
        self.setMinimumWidth(560)
        capa = QVBoxLayout(self)

        def ok(v):
            return "✅" if v else "⬜"
        b = boveda.listar()
        pasos = [
            (ok(boveda.buscar("WhatsApp Cloud token")), "Bóveda: «WhatsApp Cloud token» (usuario = Phone number ID)"),
            (ok(boveda.buscar("WhatsApp app secret")), "Bóveda: «WhatsApp app secret»"),
            (ok(boveda.buscar("ngrok")), "Bóveda: «ngrok» (usuario = tu dominio .ngrok-free.app)"),
            (ok(wa.url_publica), "Túnel abierto" + (f": {wa.url_publica}" if wa.url_publica else " (reinicia Jarvis tras guardar las claves)")),
            (ok(wa.ventana_abierta()), "Ventana de 24 h abierta (escríbele a Jarvis desde tu WhatsApp)"),
        ]
        for marca, texto in pasos:
            capa.addWidget(QLabel(f"{marca}  {texto}"))
        if wa.error:
            err = QLabel(f"⚠ {wa.error}")
            err.setStyleSheet("color:#ffb070;")
            err.setWordWrap(True)
            capa.addWidget(err)
        capa.addWidget(QLabel("\nDatos para pegar en Meta (WhatsApp > Configuración > Webhook):"))
        for etiqueta, valor in (("URL de devolución de llamada", wa.url_webhook or "(se rellena al abrir el túnel)"),
                                ("Token de verificación", wa.estado.get("verify_token", "")),
                                ("Tu número autorizado", "+" + config.WA_USUARIO)):
            fila = QHBoxLayout()
            campo = QLineEdit(valor)
            campo.setReadOnly(True)
            copiar = QPushButton("Copiar")
            copiar.clicked.connect(lambda _=False, v=valor: QApplication.clipboard().setText(v))
            fila.addWidget(QLabel(etiqueta), 1)
            fila.addWidget(campo, 3)
            fila.addWidget(copiar)
            capa.addLayout(fila)
        cerrar = QPushButton("Cerrar")
        cerrar.clicked.connect(self.accept)
        capa.addWidget(cerrar, alignment=Qt.AlignRight)


class DialogoWhatsAppWeb(QDialog):
    """Vincular a Jarvis con el WhatsApp del 628 escaneando un QR, como WhatsApp Web."""

    def __init__(self, padre, wa):
        super().__init__(padre)
        self.wa = wa
        self.setWindowTitle("WhatsApp de Jarvis")
        self.setStyleSheet(ESTILO_DIALOGO)
        self.setMinimumWidth(460)
        capa = QVBoxLayout(self)
        self.estado = QLabel()
        self.estado.setWordWrap(True)
        capa.addWidget(self.estado)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.hide()
        capa.addWidget(self.qr)
        self.ayuda = QLabel(
            "En el móvil que tiene el WhatsApp de Jarvis (el 628):\n"
            "WhatsApp › Ajustes › Dispositivos vinculados › Vincular un dispositivo,\n"
            "y escanea este código.")
        self.ayuda.hide()
        capa.addWidget(self.ayuda)
        botones = QHBoxLayout()
        self.b_vincular = QPushButton("Vincular")
        self.b_vincular.clicked.connect(self._vincular)
        self.b_prueba = QPushButton("Enviar prueba")
        self.b_prueba.clicked.connect(lambda: self.wa.enviar(f"Hola, soy Jarvis desde el {config.EQUIPO}. Ya me puedes escribir por aquí."))
        self.b_desvincular = QPushButton("Desvincular")
        self.b_desvincular.clicked.connect(self._desvincular)
        cerrar = QPushButton("Cerrar")
        cerrar.clicked.connect(self.accept)
        for b in (self.b_vincular, self.b_prueba, self.b_desvincular):
            botones.addWidget(b)
        botones.addStretch(1)
        botones.addWidget(cerrar)
        capa.addLayout(botones)
        self._qr_pintado = None
        self._temporizador = QTimer(self)
        self._temporizador.timeout.connect(self._refrescar)
        self._temporizador.start(1000)
        self._refrescar()

    def _vincular(self):
        self.wa.vincular()
        self._refrescar()

    def _desvincular(self):
        if QMessageBox.question(self, "WhatsApp de Jarvis",
                                "¿Desvincular a Jarvis del WhatsApp del 628?") == QMessageBox.Yes:
            self.wa.desvincular()
            self._refrescar()

    def _refrescar(self):
        wa = self.wa
        vinculado = wa.vinculado()
        if wa.conectado:
            texto = f"✅ Conectado. Escríbele a Jarvis desde tu WhatsApp (+{config.WA_USUARIO})."
        elif wa.qr:
            texto = "📷 Escanea el código con el WhatsApp de Jarvis."
        elif vinculado:
            texto = "⏳ Vinculado. Conectando…" if (wa._hilo and wa._hilo.is_alive()) else \
                    "Vinculado. Se conecta cuando este equipo es el principal (★)."
        elif wa.emparejando:
            texto = "⏳ Generando el código QR…"
        else:
            texto = "Jarvis aún no está vinculado a WhatsApp. Pulsa «Vincular»."
        if wa.error and not wa.conectado:
            texto += f"\n⚠ {wa.error}"
        self.estado.setText(texto)
        if wa.qr and not wa.conectado:
            if wa.qr != self._qr_pintado:
                self.qr.setPixmap(_pixmap_qr(wa.qr))
                self._qr_pintado = wa.qr
            self.qr.show()
            self.ayuda.show()
        else:
            self.qr.hide()
            self.ayuda.hide()
        self.b_vincular.setEnabled(not vinculado and not wa.qr)
        self.b_prueba.setEnabled(wa.conectado)
        self.b_desvincular.setEnabled(vinculado)


ARCHIVO_MODO = config.LOCAL / "interfaz.json"


def _leer_modo():
    try:
        import json
        m = json.loads(ARCHIVO_MODO.read_text(encoding="utf-8")).get("modo")
        if m in ("normal", "mini", "oculta"):
            return m
    except Exception:
        pass
    return os.getenv("INTERFAZ", "mini" if config.ES_MAC else "normal").lower()


def _guardar_modo(modo):
    try:
        import json
        ARCHIVO_MODO.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVO_MODO.write_text(json.dumps({"modo": modo}), encoding="utf-8")
    except Exception:
        pass


def _pixmap_qr(texto):
    import io

    import segno
    from PySide6.QtGui import QPixmap
    buf = io.BytesIO()
    segno.make_qr(texto, error="l").save(buf, kind="png", scale=7, border=3, dark="#000000", light="#ffffff")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm


class Ventana(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S.")
        banderas = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if not config.ES_MAC:  # en el Mac una ventana "Tool" se esconde al tocar otra app o el escritorio
            banderas |= Qt.Tool
        self.setWindowFlags(banderas)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(460, 680)
        self._arrastre = None

        self.reactor = Reactor()
        self.reactor.setCursor(Qt.PointingHandCursor)
        self.reactor.setToolTip(f"Clic o {config.ATAJO.upper()} para hablar sin decir «Jarvis»")
        self.reloj = QLabel()
        self.estado = QLabel(ROTULOS["reposo"])
        self.usuario = QLabel()
        self.jarvis = QLabel()
        self.secreto = QLabel()
        self.secreto.hide()
        self.secreto.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.entrada = QLineEdit()
        self.entrada.setPlaceholderText("Escriba una orden y pulse Enter…")
        self.entrada.returnPressed.connect(self._escrito)
        self.pie = QLabel()

        for etiqueta in (self.usuario, self.jarvis, self.secreto):
            etiqueta.setWordWrap(True)
            etiqueta.setAlignment(Qt.AlignCenter)
        for etiqueta in (self.reloj, self.estado, self.pie):
            etiqueta.setAlignment(Qt.AlignCenter)

        self.setStyleSheet("""
            QWidget#fondo { background: rgba(5, 14, 22, 215); border: 1px solid rgba(0,200,255,90);
                            border-radius: 22px; }
            QLabel { color: #bfefff; font-family: 'Segoe UI'; background: transparent; }
            QLineEdit { background: rgba(0,40,60,160); color: #e6fbff; border: 1px solid rgba(0,200,255,120);
                        border-radius: 12px; padding: 8px 12px; font: 11pt 'Segoe UI'; }
        """)
        self.reloj.setStyleSheet("font: 300 22pt 'Segoe UI'; color: #7fe3ff; letter-spacing: 2px;")
        self.estado.setStyleSheet("font: 10pt 'Segoe UI'; color: #5fb8d6; letter-spacing: 3px;")
        self.usuario.setStyleSheet("font: italic 10pt 'Segoe UI'; color: #8fb3c2;")
        self.jarvis.setStyleSheet("font: 12pt 'Segoe UI'; color: #e6fbff;")
        self.secreto.setStyleSheet("font: 12pt 'Consolas'; color: #ffd27f; background: rgba(60,40,0,140);"
                                   "border: 1px solid rgba(255,190,80,150); border-radius: 10px; padding: 8px;")
        self.pie.setStyleSheet("font: 9pt 'Segoe UI'; color: #4f8fa6;")

        fondo = QWidget(self)
        fondo.setObjectName("fondo")
        capa = QVBoxLayout(fondo)
        capa.setContentsMargins(20, 18, 20, 14)
        self.boton_menu = QPushButton("⚙")
        self.boton_menu.setToolTip("Opciones de Jarvis")
        self.boton_menu.setCursor(Qt.PointingHandCursor)
        self.boton_menu.setFixedSize(34, 34)
        self.boton_menu.setStyleSheet("QPushButton{background:transparent;color:#7fe3ff;border:1px solid #1d6f8f;"
                                      "border-radius:17px;font-size:16pt;}QPushButton:hover{background:#125575;}")
        self.boton_menu.clicked.connect(lambda: self._mostrar_menu(
            self.boton_menu.mapToGlobal(self.boton_menu.rect().bottomLeft())))
        fila = QHBoxLayout()
        fila.addSpacing(34)
        fila.addWidget(self.reloj, 1)
        fila.addWidget(self.boton_menu, 0, Qt.AlignTop)
        capa.addLayout(fila)
        for w in (self.reactor, self.estado, self.usuario, self.jarvis,
                  self.secreto, self.entrada, self.pie):
            capa.addWidget(w)
        self.panel = None
        if not config.ES_MAC:  # panel de estado, solo en el PC (Windows y Linux)
            try:
                import panel
                self.panel = panel.PanelEstado(self)
                capa.addWidget(self.panel)
            except Exception as e:
                print(f"[panel] no se pudo cargar: {e}")
        capa.setStretch(1, 1)
        externa = QVBoxLayout(self)
        externa.setContentsMargins(0, 0, 0, 0)
        externa.addWidget(fondo)

        t = QTimer(self)
        t.timeout.connect(self._tic)
        t.start(1000)

        self.puente = Puente()
        self.puente.cambio.connect(self._aplicar)
        self.puente.atajo.connect(lambda: self.nucleo.activar_escucha())
        self.nucleo, _ = nucleo.en_segundo_plano(lambda **kw: self.puente.cambio.emit(kw))
        self._tic()
        self._atajo_global()
        self._bandeja()
        self.modo = _leer_modo()
        self._poner_modo(self.modo, inicio=True)
        QTimer.singleShot(4000, self._avisar_boveda_bloqueada)

    # --------------------------------------------------- tamaño ventana
    def _poner_modo(self, modo, inicio=False):
        """normal = HUD completo · mini = solo el círculo pequeño · oculta = solo el icono de la barra."""
        if modo == "oculta" and not getattr(self, "_icono_bandeja", None):
            modo = "mini"  # sin icono en la barra no habría forma de volver a abrirlo
        self.modo = modo
        _guardar_modo(modo)
        mini = modo != "normal"
        for w in (self.reloj, self.usuario, self.jarvis, self.entrada, self.pie):
            w.setVisible(not mini)
        if mini:
            self.reactor.setMinimumSize(120, 120)
            self.estado.setStyleSheet("font: 8pt 'Segoe UI'; color: #5fb8d6; letter-spacing: 2px;")
            self.secreto.setMaximumWidth(220)
            self.setMinimumSize(0, 0)
            self.resize(170, 205)
        else:
            self.reactor.setMinimumSize(380, 380)
            self.estado.setStyleSheet("font: 10pt 'Segoe UI'; color: #5fb8d6; letter-spacing: 3px;")
            self.secreto.setMaximumWidth(16777215)
            self.resize(460, 680)
        self._colocar()
        if modo == "oculta":
            self.hide()
        elif not inicio:
            self._traer()

    def _colocar(self):
        pantalla = QApplication.primaryScreen().availableGeometry()
        self.move(pantalla.right() - self.width() - 20, pantalla.top() + 30)

    def _ajustar_mini(self):
        if getattr(self, "modo", "normal") != "normal":
            self.adjustSize()
            self.resize(max(170, self.width()), self.height())

    # --------------------------------------------------------------- UI
    def _atajo_global(self):
        try:
            if config.ES_MAC:  # requiere permiso de Accesibilidad / Monitorización de entrada
                from pynput import keyboard as kb
                combo = "+".join(f"<{p}>" if len(p) > 1 else p for p in config.ATAJO.lower().split("+"))
                self._oyente_atajo = kb.GlobalHotKeys({combo: self.puente.atajo.emit})
                self._oyente_atajo.start()
            else:
                import keyboard
                keyboard.add_hotkey(config.ATAJO, self.puente.atajo.emit)
        except Exception as e:
            print(f"[atajo] No se pudo registrar {config.ATAJO}: {e}")

    def _avisar_boveda_bloqueada(self):
        if boveda.existe() and not boveda.desbloqueada():
            self.secreto.setText("La bóveda está bloqueada en este equipo.\nBotón ⚙ (o clic derecho) > Bóveda de claves para desbloquearla.")
            self.secreto.show()
            QTimer.singleShot(30000, lambda: (self.secreto.clear(), self.secreto.hide()))

    def _tic(self):
        self.reloj.setText(datetime.now().strftime("%H:%M:%S"))
        if int(time.time()) % 5 == 0 or not self.pie.text():
            mqtt_ok = getattr(self.nucleo.riego, "conectado", False)
            punto = "<span style='color:#3ddc84'>●</span>" if mqtt_ok else "<span style='color:#a05050'>●</span>"
            tg = getattr(self.nucleo, "telegram", None)
            movil = "  ·  📱" if tg is not None and getattr(tg, "emparejado", False) else ""
            if getattr(getattr(self.nucleo, "whatsapp", None), "conectado", False):
                movil += "  ·  💬"
            principal = " ★" if getattr(self.nucleo, "es_principal", False) else ""
            self.pie.setText(f"{config.EQUIPO}{principal}  ·  {punto} riego{movil}  ·  hoy {gasto.hoy() * 100:.2f} ¢"
                             f"  ·  {config.ATAJO.upper()} para hablar")

    def _aplicar(self, d):
        if "estado" in d:
            est = d["estado"]
            self.reactor.poner_estado(est)
            self.estado.setText(ROTULOS.get(est, "").upper())
            if est == "apagado":
                QTimer.singleShot(1500, QApplication.quit)
        if "usuario" in d:
            self.usuario.setText(f"« {d['usuario']} »")
            self.jarvis.setText("")
        if "jarvis" in d:
            self.jarvis.setText(d["jarvis"])
        if "secreto" in d:
            nombre, valor = d["secreto"]
            self.secreto.setText(f"{nombre}\n{valor}")
            self.secreto.show()
            if getattr(self, "modo", "normal") == "oculta":
                self.show()
            self._ajustar_mini()
            QTimer.singleShot(20000, lambda: (self.secreto.clear(), self.secreto.hide()))
        if "codigo" in d:
            bot = f"@{d.get('bot')}" if d.get("bot") else "tu bot"
            self.secreto.setText(f"Envía este código a {bot} en Telegram:\n{d['codigo']}")
            self.secreto.show()
            if getattr(self, "modo", "normal") == "oculta":
                self.show()
            self._ajustar_mini()
            QTimer.singleShot(300000, lambda: (self.secreto.clear(), self.secreto.hide()))
        if d.get("whatsapp") == "vinculado":
            self.secreto.setText("WhatsApp vinculado.\nYa puedes escribirle a Jarvis desde tu WhatsApp.")
            self.secreto.show()
            QTimer.singleShot(15000, lambda: (self.secreto.clear(), self.secreto.hide()))
        if d.get("abrir") == "boveda":
            self._abrir_boveda(d.get("prefill"))
        if "principal" in d:
            self.pie.clear()
            self._tic()

    def _escrito(self):
        texto = self.entrada.text().strip()
        if texto:
            self.entrada.clear()
            threading.Thread(target=self.nucleo.atender, args=(texto,), daemon=True).start()

    def _abrir_boveda(self, prefill=None):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.show()
        if not boveda.desbloqueada():
            if not DialogoFrase(self, crear=not boveda.existe()).exec():
                self.setWindowFlag(Qt.WindowStaysOnTopHint, self._fijada)
                self.show()
                return
            self.secreto.hide()
        DialogoBoveda(self, prefill).exec()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self._fijada)
        self.show()

    _fijada = True

    def _llenar_menu(self, menu):
        menu.clear()
        menu.setStyleSheet("QMenu{background:#07131c;color:#dff7ff;border:1px solid #1d6f8f;}"
                           "QMenu::item:selected{background:#125575;}")
        acciones = [
            ("Mostrar Jarvis", self._traer),
            ("Bóveda de claves…", lambda: self._abrir_boveda()),
            ("Editar memoria", lambda: self._abrir(config.DATOS / "memoria.md")),
            ("Ver conversaciones", lambda: self._abrir(config.DATOS / "conversaciones")),
            ("Abrir carpeta de proyectos", lambda: self._abrir(Path(config.PROYECTOS[0]))),
            ("Emparejar móvil (Telegram)", self._emparejar),
            ("WhatsApp de Jarvis…", self._dialogo_whatsapp),
            ("Probar aviso al móvil", lambda: self.nucleo.al_movil(f"Prueba de aviso desde el {config.EQUIPO}.")),
        ]
        for texto, fn in acciones:
            a = QAction(texto, menu)
            a.triggered.connect(fn)
            menu.addAction(a)
        menu.addSeparator()
        tam = menu.addMenu("Tamaño de la ventana")
        for clave, texto in (("normal", "Normal"), ("mini", "Pequeña (solo el círculo)"),
                             ("oculta", "Oculta (solo el icono de la barra)")):
            a = QAction(texto, tam, checkable=True, checked=getattr(self, "modo", "normal") == clave)
            a.triggered.connect(lambda _=False, c=clave: self._poner_modo(c))
            tam.addAction(a)
        fijar = QAction("Siempre visible", menu, checkable=True, checked=self._fijada)
        fijar.toggled.connect(self._fijar)
        if getattr(self, "panel", None):
            ver_panel = QAction("Panel de estado", menu, checkable=True, checked=self.panel.activo)
            ver_panel.toggled.connect(self.panel.mostrar)
            menu.addAction(ver_panel)
        inicio = QAction("Iniciar con " + ("el Mac" if config.ES_MAC else "Windows" if config.ES_WIN else "la sesión"), menu,
                         checkable=True, checked=ACCESO_INICIO.exists())
        inicio.toggled.connect(self._inicio_sistema)
        salir = QAction("Salir", menu)
        salir.triggered.connect(QApplication.quit)
        for a in (fijar, inicio):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(salir)

    def _mostrar_menu(self, pos):
        menu = QMenu(self)
        self._llenar_menu(menu)
        menu.exec(pos)

    def contextMenuEvent(self, e):
        self._mostrar_menu(e.globalPos())

    def _traer(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _bandeja(self):
        """Icono en la barra de menús (Mac) o junto al reloj (Windows) con las opciones."""
        from PySide6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        from PySide6.QtGui import QIcon, QPixmap
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(0, 200, 255), 7))
        p.drawEllipse(8, 8, 48, 48)
        p.setBrush(QColor(0, 200, 255))
        p.drawEllipse(24, 24, 16, 16)
        p.end()
        self._icono_bandeja = QSystemTrayIcon(QIcon(pm), self)
        self._icono_bandeja.setToolTip("J.A.R.V.I.S.")
        self._menu_bandeja = QMenu()
        self._menu_bandeja.aboutToShow.connect(lambda: self._llenar_menu(self._menu_bandeja))
        self._llenar_menu(self._menu_bandeja)
        self._icono_bandeja.setContextMenu(self._menu_bandeja)
        self._icono_bandeja.activated.connect(
            lambda motivo: self._traer() if motivo == QSystemTrayIcon.Trigger and not config.ES_MAC else None)
        self._icono_bandeja.show()

    def _dialogo_whatsapp(self):
        wa = self.nucleo.whatsapp
        (DialogoWhatsApp if config.WA_MODO == "cloud" else DialogoWhatsAppWeb)(self, wa).exec()

    def _emparejar(self):
        tg = self.nucleo.telegram
        if not tg.activo:
            QMessageBox.information(self, "Telegram",
                "1. En Telegram, habla con @BotFather y crea un bot con /newbot.\n"
                "2. Copia el token que te da.\n"
                "3. Guárdalo en la Bóveda con el nombre «Telegram bot» (tipo token).\n"
                "4. Espera unos segundos y vuelve a pulsar «Emparejar móvil».")
            return
        self._aplicar({"codigo": tg.nuevo_codigo(), "bot": tg.estado.get("bot", "")})

    def _abrir(self, ruta):
        ruta = Path(ruta)
        if ruta.suffix and not ruta.exists():
            ruta.write_text("", encoding="utf-8")
        ruta.mkdir(exist_ok=True) if not ruta.suffix else None
        sistema.abrir(ruta)

    def _fijar(self, valor):
        self._fijada = valor
        self.setWindowFlag(Qt.WindowStaysOnTopHint, valor)
        self.show()

    def _inicio_sistema(self, activar):
        if not activar:
            ACCESO_INICIO.unlink(missing_ok=True)
            return
        if config.ES_MAC:
            import plistlib
            ACCESO_INICIO.parent.mkdir(parents=True, exist_ok=True)
            with open(ACCESO_INICIO, "wb") as f:
                plistlib.dump({"Label": "com.jarvis.asistente",
                               "ProgramArguments": [sys.executable, str(config.CARPETA / "interfaz.py")],
                               "WorkingDirectory": str(config.CARPETA), "RunAtLoad": True}, f)
            return
        if not config.ES_WIN:  # Linux: lanzador en ~/.config/autostart
            ACCESO_INICIO.parent.mkdir(parents=True, exist_ok=True)
            ACCESO_INICIO.write_text("[Desktop Entry]\nType=Application\nName=Jarvis\nComment=Asistente de voz\n"
                                     f'Exec=bash "{config.CARPETA / "iniciar_linux.sh"}"\n'
                                     "Icon=audio-input-microphone\nTerminal=false\n", encoding="utf-8")
            return
        pyw = Path(sys.executable).with_name("pythonw.exe")
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{ACCESO_INICIO}');"
                  f"$s.TargetPath='{pyw}';$s.Arguments='\"{config.CARPETA / 'interfaz.py'}\"';"
                  f"$s.WorkingDirectory='{config.CARPETA}';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.reactor.geometry().contains(self.reactor.parentWidget().mapFrom(self, e.position().toPoint())):
                self.nucleo.activar_escucha()
                return
            self._arrastre = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._arrastre is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._arrastre)

    def mouseReleaseEvent(self, _):
        self._arrastre = None


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # con la ventana oculta, Jarvis sigue en la barra
    v = Ventana()
    if v.modo != "oculta":
        v.show()
    codigo = app.exec()
    try:
        v.nucleo.whatsapp.detener()  # cierra la conexión de WhatsApp para que el programa termine
    except Exception:
        pass
    os._exit(codigo)


if __name__ == "__main__":
    main()
