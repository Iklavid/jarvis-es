#!/bin/bash
# Instalador de J.A.R.V.I.S. para macOS. Ejecútalo con:  bash instalar_mac.command
cd "$(dirname "$0")"
CARPETA="$(pwd)"
LOCAL="$HOME/Library/Application Support/Jarvis"
VENV="$LOCAL/venv"
mkdir -p "$LOCAL"
echo "== J.A.R.V.I.S. para Mac =="

PY=""
for c in /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3.12; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "No encuentro Python 3.12. Descargo el instalador oficial de python.org..."
  curl -L -o /tmp/python312.pkg "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg" || exit 1
  open /tmp/python312.pkg
  echo
  echo "Completa el instalador de Python que se ha abierto y luego vuelve a ejecutar:"
  echo "   bash \"$CARPETA/instalar_mac.command\""
  exit 0
fi
# Certificados SSL para el Python de python.org (necesarios para el reconocimiento de voz)
[ -f "/Applications/Python 3.12/Install Certificates.command" ] && bash "/Applications/Python 3.12/Install Certificates.command" >/dev/null 2>&1

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creando entorno de Python en $VENV ..."
  "$PY" -m venv "$VENV" || exit 1
fi
"$VENV/bin/python" -m pip install --upgrade pip -q
"$VENV/bin/python" -m pip install -r requirements.txt || { echo "Fallo instalando dependencias"; exit 1; }
cp requirements.txt "$VENV/requisitos_instalados.txt"

# Ajustes solo de este Mac (no se comparten)
if [ ! -f "$LOCAL/local.env" ]; then
  echo
  echo "Pega tu clave de DeepSeek (la misma que usa el PC) y pulsa Enter."
  echo "No se verá mientras la escribes:"
  read -rs CLAVE
  cat > "$LOCAL/local.env" <<CONF
# Ajustes solo de este Mac
EQUIPO=mac
PRIORIDAD=1
DEEPSEEK_API_KEY=$CLAVE
# Este Mac es el broker MQTT del riego
MQTT_HOST=127.0.0.1
CONF
  chmod 600 "$LOCAL/local.env"
fi

# App "Jarvis" en Aplicaciones del usuario para abrirlo con doble clic o desde Spotlight
mkdir -p "$HOME/Applications"
osacompile -o "$HOME/Applications/Jarvis.app" -e "do shell script \"cd '$CARPETA' && bash ./iniciar_mac.command > /dev/null 2>&1 &\"" 2>/dev/null

echo
echo "=========================================="
echo " Instalación terminada."
echo " Abre Jarvis desde Spotlight (escribe Jarvis) o con:"
echo "   bash \"$CARPETA/iniciar_mac.command\""
echo " La primera vez macOS pedirá permiso para el micrófono"
echo " (y para Accesibilidad si quieres el atajo de teclado)."
echo "=========================================="
