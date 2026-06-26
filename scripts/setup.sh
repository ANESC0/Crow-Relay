#!/bin/bash
set -e
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
DIR="$(cd "$SCRIPTS/.." && pwd)"
cd "$DIR"

echo ""
echo "  ===================================="
echo "   Crow-Relay — Installation initiale"
echo "  ===================================="
echo ""

# 1. Verifier Python 3
if ! command -v python3 &>/dev/null; then
    echo "  ERREUR : Python 3 est introuvable."
    echo ""
    echo "  macOS  : brew install python3"
    echo "  Ubuntu : sudo apt install python3 python3-venv python3-pip"
    echo "  Fedora : sudo dnf install python3"
    echo ""
    exit 1
fi
echo "  Python detecte : $(python3 --version)"

# 2. Environnement virtuel
echo ""
echo "  [1/3] Creation de l'environnement virtuel..."
python3 -m venv --clear .venv
echo "         OK"

# 3. Dependances
echo ""
echo "  [2/3] Installation des dependances..."
.venv/bin/pip install -r requirements.txt --quiet --disable-pip-version-check
echo "         OK"

# 4. Raccourci bureau avec icone
echo ""
echo "  [3/3] Configuration du raccourci bureau..."
chmod +x "$DIR/scripts/crow-relay.sh"
chmod +x "$DIR/scripts/crow-relay.command" 2>/dev/null || true

OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
    # ── macOS ──
    TARGET="$HOME/Desktop/Crow-Relay.command"
    # Génère un wrapper avec le chemin absolu du projet — copier le fichier
    # générique causerait un DIR=$HOME au lieu du dossier réel du projet.
    cat > "$TARGET" << CMDEOF
#!/bin/bash
exec "$DIR/scripts/crow-relay.command"
CMDEOF
    chmod +x "$TARGET"
    xattr -d com.apple.quarantine "$TARGET" 2>/dev/null || true
    xattr -d com.apple.quarantine "$DIR/scripts/crow-relay.command" 2>/dev/null || true

    # Definir l'icone via AppKit (framework natif macOS, aucune dependance)
    python3 - "$DIR/static/icon.png" "$TARGET" 2>/dev/null << 'PYEOF' || true
import sys
try:
    from AppKit import NSWorkspace, NSImage
    icon_path, target_path = sys.argv[1], sys.argv[2]
    icon = NSImage.alloc().initWithContentsOfFile_(icon_path)
    NSWorkspace.sharedWorkspace().setIcon_forFile_options_(icon, target_path, 0)
except Exception:
    pass
PYEOF

    echo "         Raccourci cree : $TARGET"
    echo ""
    echo "  ===================================="
    echo "   Installation terminee !"
    echo "  ===================================="
    echo ""
    echo "  Pour lancer Crow-Relay :"
    echo "    - Double-clique sur 'Crow-Relay.command' sur le Bureau"
    echo "    - Ou glisse ce fichier sur le Dock pour un acces permanent"
    echo ""

else
    # ── Linux ──
    DESKTOP="$HOME/Desktop"
    mkdir -p "$DESKTOP"
    SHORTCUT="$DESKTOP/crow-relay.desktop"

    # Chemin absolu vers l'icone (PNG avec fond transparent)
    ICON_PATH="$DIR/static/icon.png"

    cat > "$SHORTCUT" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Crow-Relay
Comment=Transfert de fichiers local multi-plateforme
Exec=bash -c 'cd "$DIR" && "$DIR/scripts/crow-relay.sh"; read -rp "Appuie sur Entree pour fermer..."'
Icon=$ICON_PATH
Terminal=true
Categories=Network;FileTransfer;
EOF
    chmod +x "$SHORTCUT"

    # Installer aussi dans le menu applications
    APPS="$HOME/.local/share/applications"
    mkdir -p "$APPS"
    cp "$SHORTCUT" "$APPS/crow-relay.desktop"
    update-desktop-database "$APPS" 2>/dev/null || true

    echo "         Raccourci cree : $SHORTCUT"
    echo ""
    echo "  ===================================="
    echo "   Installation terminee !"
    echo "  ===================================="
    echo ""
    echo "  Pour lancer Crow-Relay :"
    echo "    - Double-clique sur 'Crow-Relay' sur le Bureau"
    echo "    - Ou cherche 'Crow-Relay' dans les applications"
    echo ""
fi
