#!/bin/bash
echo "Instalando Claude Code (instalador oficial de Anthropic)..."
curl -fsSL https://claude.ai/install.sh | bash
echo
echo "Ahora se abrirá Claude Code para iniciar sesión con tu cuenta de Claude."
echo "Cuando termines, escribe /exit y ya podrás usarlo desde Jarvis."
cd "$(dirname "$0")/.."
"$HOME/.local/bin/claude"
