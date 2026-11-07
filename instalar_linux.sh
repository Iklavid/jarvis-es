#!/bin/bash
# Instalador de J.A.R.V.I.S. para Linux (probado en CachyOS / KDE). Ejecútalo con:  bash instalar_linux.sh
cd "$(dirname "$0")"
CARPETA="$(pwd)"
LOCAL="$HOME/.jarvis"
VENV="$LOCAL/venv"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
mkdir -p "$LOCAL"
echo "== J.A.R.V.I.S. para Linux =="
[ -x "$UV" ] || { echo "Falta uv. Instálalo con: sudo pacman -S uv"; exit 1; }
if [ ! -x "$VENV/bin/python" ]; then
  echo "Creando entorno de Python 3.12 en $VENV ..."
  "$UV" venv --python 3.12 "$VENV" || exit 1
fi
"$UV" pip install --python "$VENV/bin/python" -r requirements.txt || { echo "Fallo instalando dependencias"; exit 1; }
cp requirements.txt "$VENV/requisitos_instalados.txt"

# Ajustes solo de este equipo (no se comparten)
if [ ! -f "$LOCAL/local.env" ]; then
  echo
  echo "Pega tu clave de DeepSeek y pulsa Enter."
  echo "No se verá mientras la escribes:"
  read -rs CLAVE
  cat > "$LOCAL/local.env" <<CONF
# Ajustes solo de este equipo 
EQUIPO=$(hostname | tr 'A-Z' 'a-z')
PRIORIDAD=1
DEEPSEEK_API_KEY=$CLAVE
JARVIS_COMPARTIDA=$(dirname "$CARPETA")
CLAUDE=$HOME/.local/bin/claude
CONF
  chmod 600 "$LOCAL/local.env"
fi

# Lanzador en el menú de aplicaciones de KDE
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/jarvis.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Jarvis
Comment=Asistente de voz
Exec=bash "$CARPETA/iniciar_linux.sh"
Icon=audio-input-microphone
Terminal=false
Categories=Utility;
DESK

echo
echo "Instalación terminada. Abre Jarvis desde el menú de aplicaciones o con:"
echo "   bash \"$CARPETA/iniciar_linux.sh\""
