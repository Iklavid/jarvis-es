"""Oído (reconocimiento de voz en español) y habla (voces neuronales de Edge)."""
import asyncio
import os
import subprocess
import tempfile
import threading
import time

import edge_tts
import speech_recognition as sr

import config

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

pygame.mixer.init()
_lock_habla = threading.Lock()
hablando = threading.Event()


# ----------------------------------------------------------------- HABLA
async def _generar_audio(texto, ruta):
    com = edge_tts.Communicate(texto, config.VOZ, rate=config.VELOCIDAD_VOZ)
    await com.save(ruta)


def decir(texto):
    """Dice el texto en voz alta (bloquea hasta terminar)."""
    texto = (texto or "").strip()
    if not texto:
        return
    print(f"JARVIS: {texto}")
    with _lock_habla:
        hablando.set()
        fd, ruta = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            asyncio.run(_generar_audio(texto, ruta))
            pygame.mixer.music.load(ruta)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
            pygame.mixer.music.unload()
        except Exception as e:
            print(f"[voz] No pude hablar: {e}")
        finally:
            hablando.clear()
            try:
                os.remove(ruta)
            except OSError:
                pass


def pitido():
    if config.ES_MAC:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])
        return
    if not config.ES_WIN:  # Linux: pitido generado con pygame
        try:
            import numpy as np
            frec, _, canales = pygame.mixer.get_init()
            onda = (np.sin(2 * np.pi * 988 * np.arange(int(frec * 0.11)) / frec) * 8000).astype(np.int16)
            pygame.sndarray.make_sound(np.column_stack([onda] * canales) if canales > 1 else onda).play()
        except Exception:
            pass
        return
    try:
        import winsound
        threading.Thread(target=winsound.Beep, args=(988, 110), daemon=True).start()
    except Exception:
        pass


def callar():
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass


# ------------------------------------------------------------------ OÍDO
class MicrofonoSD(sr.AudioSource):
    """Micrófono con sounddevice (no necesita PyAudio/PortAudio instalados aparte: ideal en Mac)."""

    def __init__(self, frecuencia=16000, trozo=1024):
        self.SAMPLE_RATE = frecuencia
        self.SAMPLE_WIDTH = 2
        self.CHUNK = trozo
        self.stream = None

    def __enter__(self):
        import sounddevice as sd
        crudo = sd.RawInputStream(samplerate=self.SAMPLE_RATE, blocksize=self.CHUNK,
                                  channels=1, dtype="int16")
        crudo.start()

        class _Flujo:
            def read(self, n):
                datos, _ = crudo.read(n)
                return bytes(datos)

            def close(self):
                crudo.stop()
                crudo.close()
        self.stream = _Flujo()
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()
        self.stream = None


def _microfono():
    try:
        import pyaudio  # noqa: F401
        return sr.Microphone()
    except Exception:
        return MicrofonoSD()


class Oido:
    def __init__(self):
        self.rec = sr.Recognizer()
        self.rec.pause_threshold = 0.8
        self.rec.dynamic_energy_threshold = True
        self.mic = _microfono()
        with self.mic as fuente:
            print("[oído] Calibrando ruido ambiente…")
            self.rec.adjust_for_ambient_noise(fuente, duration=1.5)

    def escuchar(self, espera=None, max_frase=15):
        """Devuelve el texto reconocido en minúsculas, o '' si no oye nada."""
        while hablando.is_set():  # no escucharse a sí mismo
            time.sleep(0.1)
        try:
            with self.mic as fuente:
                audio = self.rec.listen(fuente, timeout=espera, phrase_time_limit=max_frase)
        except sr.WaitTimeoutError:
            return ""
        try:
            texto = self.rec.recognize_google(audio, language=config.IDIOMA)
            return texto.lower().strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            print(f"[oído] Sin conexión con el reconocimiento de voz: {e}")
            time.sleep(2)
            return ""


class Despertador:
    """Oye «hey Jarvis» en el propio equipo (openWakeWord), sin mandar audio fuera."""
    MODELO = "hey_jarvis_v0.1"
    TROZO = 1280  # 80 ms a 16 kHz

    def __init__(self):
        from openwakeword.model import Model
        from openwakeword.utils import download_models
        carpeta = config.LOCAL / "palabra"
        if not (carpeta / f"{self.MODELO}.onnx").exists():
            print("[oído] Descargando el modelo de la palabra de activación…")
            download_models([self.MODELO], target_directory=str(carpeta))
        self.modelo = Model(wakeword_models=[str(carpeta / f"{self.MODELO}.onnx")], inference_framework="onnx",
                            melspec_model_path=str(carpeta / "melspectrogram.onnx"),
                            embedding_model_path=str(carpeta / "embedding_model.onnx"))

    def esperar(self, seguir):
        """True al oír la palabra; False si seguir() deja de ser cierto antes."""
        import numpy as np
        import sounddevice as sd
        with sd.RawInputStream(samplerate=16000, blocksize=self.TROZO, channels=1, dtype="int16") as flujo:
            while seguir():
                datos, _ = flujo.read(self.TROZO)
                if hablando.is_set():  # no despertarse con su propia voz
                    self.modelo.reset()
                    continue
                if max(self.modelo.predict(np.frombuffer(bytes(datos), dtype=np.int16)).values()) >= config.UMBRAL_PALABRA:
                    self.modelo.reset()
                    return True
        return False
