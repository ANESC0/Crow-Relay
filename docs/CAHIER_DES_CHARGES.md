# Cahier des charges — Crow-Relay

## 1. Contexte et objectif

**Crow-Relay** est un service de transfert de fichiers sur réseau local,
**multi-plateforme** et **sans installation côté client**.

L'ordinateur héberge le service et joue le rôle de **point de dépôt partagé**.
Tout appareil du même réseau (téléphone, tablette, autre ordinateur) accède à
une **page web** pour échanger des fichiers dans les deux sens.

**Problème résolu :** transférer rapidement un fichier entre téléphone et PC
sans câble, sans cloud, sans compte, sans application dédiée.

## 2. Périmètre

### Inclus
- Serveur web léger lancé sur l'ordinateur hôte.
- Page web responsive accessible depuis n'importe quel navigateur du réseau.
- Envoi de fichiers (appareil → ordinateur).
- Récupération de fichiers (ordinateur → appareil).
- Découverte simplifiée via QR code + code PIN.
- Autorisation par appareil via un panneau d'administration (clé admin).
- Accès depuis l'extérieur du réseau local via Cloudflare Tunnel (`--tunnel`).
- Chiffrement TLS optionnel via certificat auto-signé (`--https`).

### Exclu (hors périmètre actuel)
- Transfert direct appareil ↔ appareil sans passer par l'hôte (P2P).
- Synchronisation automatique / dossiers surveillés.
- Chiffrement de bout en bout (le tunnel et `--https` chiffrent le transport,
  mais l'hôte déchiffre et stocke les fichiers en clair).

## 3. Acteurs

| Acteur            | Rôle                                                        |
| ----------------- | ----------------------------------------------------------- |
| Hôte              | Ordinateur qui exécute le service et stocke les fichiers.   |
| Client mobile/web | Appareil qui se connecte via navigateur pour échanger.      |

## 4. Exigences fonctionnelles

| ID    | Exigence                                                                       | Priorité |
| ----- | ------------------------------------------------------------------------------ | -------- |
| EF-1  | Le service démarre via une seule commande et affiche son adresse d'accès.      | Haute    |
| EF-2  | Le service affiche un QR code (terminal + page) pour se connecter rapidement.  | Haute    |
| EF-3  | Un client peut envoyer un ou plusieurs fichiers vers l'hôte.                   | Haute    |
| EF-4  | Un client peut lister les fichiers disponibles sur l'hôte.                     | Haute    |
| EF-5  | Un client peut télécharger un fichier depuis l'hôte.                           | Haute    |
| EF-6  | Un client peut supprimer un fichier de l'hôte.                                 | Moyenne  |
| EF-7  | L'upload supporte le glisser-déposer et le sélecteur de fichiers.             | Moyenne  |
| EF-8  | Une barre de progression indique l'avancement d'un envoi.                      | Moyenne  |
| EF-9  | Les fichiers de même nom ne s'écrasent pas (renommage automatique).            | Moyenne  |
| EF-10 | L'interface est en français et utilisable sur petit écran (mobile-first).      | Haute    |

## 5. Exigences non fonctionnelles

| ID     | Exigence                                                                      |
| ------ | ---------------------------------------------------------------------------- |
| ENF-1  | **Simplicité** : dépendances minimales (Flask + qrcode/Pillow ; `cryptography` requis uniquement pour `--https`). |
| ENF-2  | **Portabilité** : fonctionne sur Windows, macOS et Linux.                     |
| ENF-3  | **Performance** : transferts en parallèle (serveur multi-thread).            |
| ENF-4  | **Réactivité** : page utilisable sans rechargement (API JSON + fetch/XHR).   |
| ENF-5  | **Robustesse** : noms de fichiers assainis (`secure_filename`).              |

## 6. Exigences de sécurité

| ID     | Exigence                                                                                  | Statut   |
| ------ | ----------------------------------------------------------------------------------------- | -------- |
| SEC-1  | Accès protégé par un **code PIN**, activé par défaut.                                      | Fait     |
| SEC-2  | Le code PIN est généré aléatoirement à chaque démarrage (ou fourni explicitement).        | Fait     |
| SEC-3  | Le QR code embarque un token à usage de connexion ; le token est retiré de l'URL ensuite. | Fait     |
| SEC-4  | Toutes les routes (sauf la page de connexion) exigent une session authentifiée.           | Fait     |
| SEC-5  | La clé de session est régénérée à chaque lancement.                                        | Fait     |
| SEC-6  | Comparaison du PIN en temps constant (`secrets.compare_digest`).                           | Fait     |
| SEC-7  | Par défaut **réseau de confiance** ; l'exposition Internet (`--tunnel`) impose PIN + autorisation. | Fait     |
| SEC-8  | Chiffrement TLS (HTTPS) optionnel via `--https` (certificat auto-signé persistant).        | Fait     |
| SEC-9  | Verrouillage de l'IP (10 min) après 5 tentatives de PIN/clé admin erronées.                | Fait     |
| SEC-10 | Comparaison du token QR en temps constant, puis retrait du token de l'URL.                 | Fait     |
| SEC-11 | En mode tunnel, `--no-pin` et `--no-approval` sont refusés au démarrage.                    | Fait     |
| SEC-12 | **À venir** : expiration automatique des fichiers déposés.                                 | Évolution |

## 7. Architecture technique

```
┌─────────────────────────┐         réseau local (Wi-Fi)        ┌──────────────────────┐
│  Client (navigateur)    │ ◀─────────── HTTP ───────────────▶ │  Hôte : service Flask │
│  - page Envoyer/Recevoir│                                     │  - routes API        │
│  - QR / PIN             │                                     │  - dossier shared/   │
└─────────────────────────┘                                     └──────────────────────┘
```

- **Backend** : Python 3.8+, Flask (`app.py`), serveur multi-thread.
- **Frontend** : HTML/CSS/JS sans framework (`templates/index.html`, `login.html`).
- **Stockage** : dossier local `shared/` (configurable via `CROW_RELAY_SHARE_DIR`).
- **QR code** : librairie `qrcode` (ASCII pour le terminal, SVG pour la page).

### Points d'API

| Méthode | Route                          | Rôle                                          |
| ------- | ------------------------------ | --------------------------------------------- |
| GET     | `/`                            | Page principale (Envoyer / Recevoir).         |
| GET     | `/login`, POST `/login`        | Connexion par code PIN.                       |
| GET     | `/logout`                      | Déconnexion.                                  |
| GET     | `/qr.svg`                      | QR code de connexion (SVG).                   |
| GET     | `/api/network-info`            | État réseau (même LAN, tunnel) du client.     |
| GET     | `/api/files`                   | Liste JSON des fichiers.                      |
| POST    | `/api/upload`                  | Envoi de fichier(s).                          |
| GET     | `/download/<nom>`              | Téléchargement d'un fichier.                  |
| POST    | `/api/delete/<nom>`            | Suppression d'un fichier.                     |
| GET     | `/api/access-status`           | État d'autorisation de l'appareil courant.    |
| POST    | `/api/request-access`          | Demande d'accès (avec nom d'appareil).        |
| GET     | `/admin`, `/admin/login`       | Panneau d'autorisation (clé admin).           |
| GET     | `/api/admin/devices`           | Liste des appareils connus (admin).           |
| POST    | `/api/admin/devices/<id>`      | Approuver / refuser / révoquer un appareil.   |
| POST    | `/api/admin/clear-files`       | Vider le dossier partagé (admin).             |
| POST    | `/api/admin/clear-devices`     | Vider la liste des appareils (admin).         |
| GET     | `/api/tunnel-url`              | URL publique du tunnel Cloudflare (admin).    |

## 8. Configuration

| Paramètre            | Type         | Défaut       | Description                              |
| -------------------- | ------------ | ------------ | ---------------------------------------- |
| `--host`             | option CLI   | `0.0.0.0`    | Interface d'écoute.                      |
| `--port`             | option CLI   | `8000`       | Port d'écoute.                           |
| `--pin`              | option CLI   | (généré)     | Code PIN imposé.                         |
| `--no-pin`           | option CLI   | désactivé    | Désactive l'authentification.           |
| `--admin-key`        | option CLI   | (générée)    | Clé du panneau d'autorisation.          |
| `--no-approval`      | option CLI   | désactivé    | Désactive l'autorisation par appareil.  |
| `--https`            | option CLI   | désactivé    | Chiffre les transferts (cert auto-signé).|
| `--tunnel`           | option CLI   | désactivé    | Expose le service via Cloudflare Tunnel. |
| `--tunnel-ttl`       | option CLI   | `0` (off)    | Ferme le tunnel après N minutes.        |
| `CROW_RELAY_PIN`           | env          | (généré)     | Code PIN via variable d'environnement.   |
| `CROW_RELAY_ADMIN_KEY`     | env          | (générée)    | Clé admin via variable d'environnement.  |
| `CROW_RELAY_SHARE_DIR`     | env          | `./shared`   | Dossier de stockage.                     |
| `CROW_RELAY_MAX_MB`        | env          | illimité     | Taille max d'un envoi (Mo).              |

## 9. Critères d'acceptation

- [ ] Le service démarre et affiche adresse + PIN + QR code.
- [ ] Le scan du QR depuis un téléphone ouvre la page et connecte sans saisie.
- [ ] Un accès manuel sans PIN est refusé et redirigé vers la connexion.
- [ ] Envoi d'un fichier depuis le téléphone → fichier présent sur l'hôte.
- [ ] Téléchargement d'un fichier de l'hôte → fichier reçu sur le téléphone.
- [ ] Deux fichiers de même nom coexistent sans écrasement.
- [ ] L'interface est lisible et utilisable sur écran de téléphone.

## 10. Évolutions envisagées

1. Partage de **texte / presse-papier** en plus des fichiers.
2. **Expiration** automatique des fichiers déposés.
3. **Exécutable** packagé (PyInstaller) pour lancement sans Python.
4. Transfert **bidirectionnel direct** entre deux appareils (P2P).
5. Passage à un **serveur WSGI de production** (waitress/gunicorn) en option.
