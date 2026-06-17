# Sécurité de Crow-Relay

## Usage prévu

Crow-Relay est conçu pour un usage **personnel ou familial sur réseau local**. C'est un outil de
partage de fichiers entre tes propres appareils ou ceux de personnes en qui tu as confiance — pas
un service public exposé à des inconnus.

---

## Ce qui est protégé

### Couche 1 — Code PIN
Personne ne peut accéder au service sans connaître le code PIN affiché sur l'ordinateur hôte.
Le PIN est intégré dans le QR code pour éviter de le saisir manuellement.

- Verrouillage automatique après 5 tentatives incorrectes (10 minutes)
- Comparaison à temps constant (résistant aux attaques par timing)

### Couche 2 — Approbation par appareil
Chaque appareil qui se connecte doit être explicitement validé par l'admin depuis le panneau
d'autorisation. Un appareil approuvé peut se voir accorder des permissions distinctes :
envoyer uniquement, recevoir uniquement, ou les deux.

- L'identité de l'appareil repose sur un cookie UUID persistant (128 bits)
- Un appareil refusé ou révoqué perd immédiatement tout accès
- Registre des appareils persistant entre les redémarrages

### Protection générale
- Headers de sécurité HTTP sur toutes les réponses (CSP, X-Frame-Options, X-Content-Type-Options…)
- Rate limiting global (200 req/min) et spécifique sur les endpoints sensibles
- Écriture atomique du registre des appareils (pas de corruption en cas de coupure)
- Protection contre le path traversal dans upload/download/suppression
- Liens symboliques bloqués dans le dossier partagé
- Session invalidée à chaque démarrage (nouvelle clé secrète)

---

## Limites connues

### Le LAN n'est pas chiffré par défaut
Sans `--https`, les fichiers transitent en clair sur le réseau Wi-Fi. N'importe quel appareil sur
le même réseau peut intercepter le trafic (Wireshark, etc.).

**Si tu partages des fichiers sensibles sur le LAN, utilise `--https`.**

### L'adresse MAC n'est pas une authentification forte
En mode LAN, le serveur lit l'adresse MAC pour pré-remplir le nom de l'appareil. Ce n'est pas
utilisé pour accorder des droits. Mais les adresses MAC circulent en clair sur le réseau Wi-Fi
et peuvent être usurpées par n'importe quel appareil du LAN.

**Conclusion : l'approbation par appareil protège contre les inconnus qui rejoignent le réseau,
pas contre quelqu'un qui partage déjà ton Wi-Fi et est déterminé à contourner le système.**

### Le QR code contient le PIN

Le QR code affiché au démarrage embarque le PIN dans son URL. Partager une photo du QR code ou de l'écran revient à partager le PIN. Ne montre pas ton écran (ni ne prends de capture) pendant que le QR code est affiché.

### Le cookie d'appareil peut être copié
L'identité d'un appareil est stockée dans un cookie navigateur. Si quelqu'un accède à ce cookie
(session partagée, synchronisation de navigateur, accès physique à l'appareil), il peut se faire
passer pour cet appareil.

### Cloudflare voit le contenu en mode tunnel
En mode `--tunnel`, Cloudflare termine la connexion TLS. Le trafic est chiffré entre l'utilisateur
et Cloudflare, mais Cloudflare déchiffre les données avant de les transmettre au serveur local.
Pour des fichiers que tu ne confierais pas à un tiers, chiffre-les avant de les uploader.

### `--no-pin` supprime la première couche de sécurité
Sans PIN, seule l'approbation par appareil protège le service. À n'utiliser que sur un réseau
totalement fermé (pas d'invités, pas d'appareils inconnus).

### `--no-pin --no-approval` = aucune sécurité
En mode complètement ouvert, tout appareil sur le réseau peut envoyer et recevoir des fichiers
sans aucune restriction. Réservé aux réseaux 100 % contrôlés et de confiance.

---

## Recommandations par cas d'usage

| Situation | Configuration recommandée |
|-----------|--------------------------|
| Famille à la maison, Wi-Fi avec mot de passe fort | Mode par défaut (PIN + approbation) |
| Partage de fichiers sensibles en local | `--https` obligatoire |
| Accès depuis internet (réseau distant) | `--tunnel` (PIN + approbation imposés) |
| Fichiers confidentiels via tunnel | Chiffrer les fichiers avant upload (GPG, zip chiffré) |
| Réseau 100 % contrôlé, zéro invité | `--no-approval` acceptable |
| Données médicales, légales, credentials | Utiliser un outil E2E dédié (Signal, ProtonDrive) |

---

## Ce que Crow-Relay n'est pas

- ❌ Un service de partage public (pas de gestion multi-utilisateurs, pas d'isolation des données entre utilisateurs)
- ❌ Un outil de chiffrement de bout en bout (le serveur voit les fichiers en clair)
- ❌ Un remplaçant de Signal ou d'un outil E2E pour des données vraiment confidentielles
- ✅ Un outil de partage pratique et sécurisé pour un usage personnel/familial sur réseau de confiance

---

## Signaler une vulnérabilité

Si tu découvres une faille de sécurité, ouvre une **issue privée** sur GitHub ou contacte le
mainteneur directement avant toute publication publique. Merci de laisser le temps de corriger
avant de divulguer.
