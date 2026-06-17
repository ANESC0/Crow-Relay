#!/bin/bash
# Lanceur macOS — double-cliquable depuis le Finder
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

if [ -f ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -f "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY="python3"
fi

if ! "$PY" -c "import flask" 2>/dev/null; then
    echo ""
    echo "  Les dependances ne sont pas installees."
    echo "  Lance setup.sh en premier : bash scripts/setup.sh"
    echo ""
    read -rp "  Appuie sur Entrée pour fermer..."
    exit 1
fi

# Si des arguments sont passes directement, on les utilise tels quels
if [ $# -gt 0 ]; then
    "$PY" app.py "$@"
    exit $?
fi

# ══════════════════════════════════════════
#  Etape 1 — Mode de connexion
# ══════════════════════════════════════════
echo ""
echo "  =========================================="
echo "   Crow-Relay"
echo "  =========================================="
echo ""
echo "  Etape 1 — Mode de connexion"
echo ""
echo "    1) Local     (meme Wi-Fi — connexion directe)"
echo "    2) Internet  (Cloudflare Tunnel — accessible de partout)"
echo ""
read -rp "  Ton choix [1/2, defaut : 1] : " CROW_MODE

CROW_ARGS=()

if [ "$CROW_MODE" = "2" ]; then
    # ══════════════════════════════════════════
    #  Etape 2/2 — Limite par fichier (TUNNEL)
    # ══════════════════════════════════════════
    echo ""
    echo "  Etape 2/2 — Limite de taille par fichier"
    echo ""
    echo "    1) 500 Mo           [recommande en tunnel]"
    echo "    2) 1 Go"
    echo "    3) 2 Go"
    echo "    4) Personnalisee"
    echo ""
    read -rp "  Ton choix [1-4, defaut : 1] : " CROW_SIZE
    CROW_ARGS+=(--tunnel --host 127.0.0.1)
    case "$CROW_SIZE" in
        2) CROW_ARGS+=(--max-mb 1000) ;;
        3) CROW_ARGS+=(--max-mb 2000) ;;
        4)
            read -rp "  Limite en Mo (ex: 200) : " CROW_MB
            [ -n "$CROW_MB" ] && CROW_ARGS+=(--max-mb "$CROW_MB")
            ;;
    esac
else
    # ══════════════════════════════════════════
    #  Etape 2/3 — Chiffrement (LOCAL)
    # ══════════════════════════════════════════
    echo ""
    echo "  Etape 2/3 — Chiffrement du trafic"
    echo ""
    echo "    1) HTTPS  [recommande — trafic chiffre, avertissement navigateur normal]"
    echo "    2) HTTP   [sans chiffrement]"
    echo ""
    read -rp "  Ton choix [1/2, defaut : 1] : " CROW_HTTPS
    [ "$CROW_HTTPS" != "2" ] && CROW_ARGS+=(--https)

    # ══════════════════════════════════════════
    #  Etape 3/3 — Limite par fichier (LOCAL)
    # ══════════════════════════════════════════
    echo ""
    echo "  Etape 3/3 — Limite de taille par fichier"
    echo ""
    echo "    1) Illimitee        [recommande en local]"
    echo "    2) 500 Mo"
    echo "    3) 1 Go"
    echo "    4) 2 Go"
    echo "    5) Personnalisee"
    echo ""
    read -rp "  Ton choix [1-5, defaut : 1] : " CROW_SIZE
    case "$CROW_SIZE" in
        2) CROW_ARGS+=(--max-mb 500) ;;
        3) CROW_ARGS+=(--max-mb 1000) ;;
        4) CROW_ARGS+=(--max-mb 2000) ;;
        5)
            read -rp "  Limite en Mo (ex: 200) : " CROW_MB
            [ -n "$CROW_MB" ] && CROW_ARGS+=(--max-mb "$CROW_MB")
            ;;
    esac
fi

echo ""
echo "  Lancement de Crow-Relay..."
echo "  Appuie sur Ctrl+C pour arreter."
echo ""
"$PY" app.py "${CROW_ARGS[@]}"

echo ""
read -rp "  Crow-Relay arrete. Appuie sur Entrée pour fermer..."
