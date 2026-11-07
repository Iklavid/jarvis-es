#!/bin/bash
cd "$(dirname "$0")"
VENV="$HOME/Library/Application Support/Jarvis/venv"
[ -x "$VENV/bin/python" ] || bash ./instalar_mac.command
if ! cmp -s requirements.txt "$VENV/requisitos_instalados.txt"; then
  "$VENV/bin/python" -m pip install -q -r requirements.txt && cp requirements.txt "$VENV/requisitos_instalados.txt"
fi
exec "$VENV/bin/python" interfaz.py
