# Guide d'installation — Crow-Relay

---

## Prérequis

| Plateforme | Python requis | Vérifier |
|---|---|---|
| **Windows** | Python 3.10+ | `python --version` |
| **macOS** | Python 3.10+ | `python3 --version` |
| **Linux** | Python 3.10+ | `python3 --version` |

- Télécharger si besoin : <https://www.python.org/downloads/>
- **Windows** : cocher **"Add Python to PATH"** pendant l'installation.
- **macOS** : Python 3 est inclus avec les Xcode Command Line Tools (`xcode-select --install`) ou via Homebrew (`brew install python3`).
- **Linux** : `sudo apt install python3 python3-venv python3-pip` (Debian/Ubuntu) ou `sudo dnf install python3` (Fedora/RHEL).
- L'ordinateur et les appareils doivent être sur le **même réseau Wi-Fi**.

---

## Installation automatique (recommandée)

### Windows

1. Télécharger ou cloner le projet.
2. Ouvrir le dossier et double-cliquer sur **`scripts\setup.bat`**.

Le script :
- vérifie que Python est disponible,
- crée un environnement virtuel (`.venv`),
- installe les dépendances,
- crée un raccourci **Crow-Relay** sur le bureau.

Ensuite, double-cliquer sur le raccourci pour lancer.

---

### macOS

```bash
bash scripts/setup.sh
```

Le script :
- crée un environnement virtuel (`.venv`),
- installe les dépendances,
- dépose **`Crow-Relay.command`** sur le bureau (double-cliquable depuis le Finder).

> **Note macOS :** à la première ouverture, macOS peut bloquer le fichier avec
> un message "développeur non identifié". Faire **Clic droit → Ouvrir → Ouvrir**.
> Pour l'ajouter au Dock : glisser `Crow-Relay.command` sur le Dock.

---

### Linux

```bash
bash scripts/setup.sh
```

Le script :
- crée un environnement virtuel (`.venv`),
- installe les dépendances,
- crée un fichier `crow-relay.desktop` sur le bureau **et** dans `~/.local/share/applications/`.

Double-cliquer sur l'icône Crow-Relay pour lancer (ou chercher "Crow-Relay" dans les applications).

> Sur certaines distributions, il faut faire **Clic droit → "Autoriser l'exécution"** la première fois.

---

## Installation manuelle

Si tu préfères gérer toi-même l'environnement :

```bash
# Cloner le projet
git clone https://github.com/ANESC0/Crow-Relay.git
cd Crow-Relay

# Créer le venv
python3 -m venv .venv          # macOS / Linux
# python -m venv .venv         # Windows

# Activer le venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\Activate.ps1   # Windows PowerShell

# Installer les dépendances
pip install -r requirements.txt

# Lancer
python3 app.py                 # macOS / Linux
# python app.py                # Windows
```

---

## Lancement

### Via le raccourci bureau

Double-cliquer sur le raccourci **Crow-Relay** créé par le script d'installation.

### Via les scripts fournis

| Plateforme | Commande |
|---|---|
| Windows | Double-cliquer sur `scripts\crow-relay.bat` ou l'exécuter depuis cmd |
| macOS | Double-cliquer sur `scripts/crow-relay.command` dans le Finder |
| Linux | `bash scripts/crow-relay.sh` dans un terminal |

### En ligne de commande directe

```bash
# macOS / Linux
python3 app.py                   # lancement standard
python3 app.py --no-pin          # sans code PIN
python3 app.py --no-approval     # sans autorisation par appareil
python3 app.py --https           # avec chiffrement HTTPS
python3 app.py --port 9000       # sur un autre port

# Windows
python app.py
python app.py --https
```

---

## Mode Tunnel — Installer cloudflared

Le mode `--tunnel` permet d'accéder à Crow-Relay depuis internet (téléphone et PC sur des réseaux différents). Il nécessite d'installer `cloudflared` sur le PC hôte **une seule fois**.

> `cloudflared` est un outil en ligne de commande — double-cliquer sur le `.exe` ouvre une fenêtre noire qui se ferme aussitôt, c'est normal. Il faut le placer dans le PATH, pas l'exécuter directement.

### Windows

**Option A — winget (recommandée)**

Ouvrir PowerShell ou l'invite de commandes et taper :

```
winget install --id Cloudflare.cloudflared
```

Fermer et rouvrir le terminal après l'installation.

**Option B — installation manuelle**

1. Télécharger `cloudflared-windows-amd64.exe` depuis [github.com/cloudflare/cloudflared/releases/latest](https://github.com/cloudflare/cloudflared/releases/latest)
2. Renommer le fichier en `cloudflared.exe`
3. Le déplacer dans `C:\Windows\System32\`
4. Fermer et rouvrir le terminal

### macOS

```bash
brew install cloudflared
```

### Linux

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared && sudo mv cloudflared /usr/local/bin/
```

### Vérifier l'installation

```
cloudflared --version
```

Si un numéro de version s'affiche, cloudflared est prêt. Crow-Relay peut alors être lancé avec le choix **2) Internet** dans le lanceur, ou `--tunnel` en ligne de commande.

Aucun compte Cloudflare requis.

---

## Se connecter depuis le téléphone

Au démarrage, le navigateur de l'ordinateur hôte s'ouvre automatiquement sur le panneau d'administration. Dans la console, l'adresse et un QR code s'affichent.

**Depuis le téléphone :**

1. Scanner le **QR code** avec l'appareil photo → la page s'ouvre et te connecte directement (le PIN est embarqué).
2. Ou ouvrir le navigateur, taper l'adresse (`http://192.168.x.x:8000`) et saisir le code PIN.

---

## Autoriser le port dans le pare-feu

Si le téléphone n'arrive pas à joindre l'ordinateur :

**Windows** — à la première exécution, Windows propose d'autoriser Python. Cliquer sur **Autoriser l'accès** en cochant "Réseaux privés".

**macOS** — Réglages Système → Réseau → Coupe-feu → Autoriser les connexions entrantes pour Python.

**Linux (ufw)**
```bash
sudo ufw allow 8000/tcp
```

**Linux (firewalld)**
```bash
sudo firewall-cmd --add-port=8000/tcp --permanent
sudo firewall-cmd --reload
```

---

## Désinstaller

| Plateforme | Commande |
|---|---|
| Windows | Double-cliquer sur `scripts\uninstall.bat` |
| macOS / Linux | `bash scripts/uninstall.sh` |

Les scripts suppriment le raccourci bureau (et l'entrée applications sous Linux) et proposent de supprimer le venv. Le dossier du projet est conservé.

---

## Dépannage

| Problème | Solution |
|---|---|
| `python : command not found` (Linux/macOS) | Utiliser `python3` à la place. |
| `python : command not found` (Windows) | Réinstaller Python en cochant *Add to PATH*. |
| `scripts\setup.bat` ne s'ouvre pas | Clic droit → Exécuter en tant qu'administrateur. |
| Le téléphone n'atteint pas la page | Vérifier le même Wi-Fi + autoriser le port (voir §pare-feu). |
| Avertissement de sécurité macOS | Clic droit → Ouvrir → Ouvrir pour passer outre. |
| Adresse affichée en `127.0.0.1` | L'ordinateur n'est pas connecté au réseau ; vérifier le Wi-Fi. |
| Port déjà utilisé | `python3 app.py --port 9000` (ou tout autre port libre). |
| HTTPS : avertissement navigateur | Normal pour un certificat auto-signé — cliquer "Continuer quand même". |
| HTTPS : iOS bloque complètement | Supprimer `cert.pem` et `key.pem` et relancer (cert expiré ou mauvaise IP). |
| `ModuleNotFoundError: flask` | Relancer `scripts/setup.sh` (ou `scripts\setup.bat`) pour installer les dépendances. |
| `python3 -m venv` échoue (Ubuntu/Debian) | `sudo apt install python3-venv` puis réessayer. |
| Fenêtre noire qui se ferme (cloudflared) | Normal — c'est un outil CLI, pas un installeur GUI. Suivre la section *Mode Tunnel — Installer cloudflared* ci-dessus. |
| `cloudflared : command not found` | cloudflared n'est pas dans le PATH. Fermer et rouvrir le terminal après l'installation, ou vérifier que `C:\Windows\System32\cloudflared.exe` existe (Windows). |
| Le tunnel ne démarre pas | Vérifier que `cloudflared --version` fonctionne dans le terminal avant de lancer Crow-Relay. |

---

## Arrêter le service

**Ctrl + C** dans la fenêtre de terminal, ou fermer la fenêtre.
