#!/bin/bash
cd "$(dirname "$0")"
VENV="$HOME/.jarvis/venv"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
[ -x "$VENV/bin/python" ] || bash ./instalar_linux.sh
if ! cmp -s requirements.txt "$VENV/requisitos_instalados.txt"; then
  "$UV" pip install -q --python "$VENV/bin/python" -r requirements.txt && cp requirements.txt "$VENV/requisitos_instalados.txt"
fi
# En Wayland los programas no pueden colocar sus ventanas ni sus menús; con XWayland sí
export QT_QPA_PLATFORM=xcb
exec "$VENV/bin/python" interfaz.py
