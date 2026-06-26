#!/bin/bash
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OS="$(uname -s)"

echo ""
echo "  Désinstallation de Crow-Relay..."
echo ""

if [[ "$OS" == "Darwin" ]]; then
    # macOS
    rm -f "$HOME/Desktop/Crow-Relay.command" && echo "  Raccourci bureau supprimé." || true
else
    # Linux
    rm -f "$HOME/Desktop/crow-relay.desktop"               && echo "  Raccourci bureau supprimé."      || true
    rm -f "$HOME/.local/share/applications/crow-relay.desktop" && echo "  Entrée applications supprimée." || true
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo ""
read -rp "  Supprimer aussi le venv (.venv) ? [o/N] " rep
if [[ "$rep" =~ ^[oO]$ ]]; then
    rm -rf "$DIR/.venv" && echo "  Environnement virtuel supprimé."
fi

echo ""
echo "  Désinstallation terminée. Le dossier du projet est conservé."
echo ""
