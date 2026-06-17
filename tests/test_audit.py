"""
Cas de test issus de l'audit de sécurité — couverture des angles morts
identifiés lors de la revue du projet.
"""

import io
import os
import time

import app as crow


# ---------------------------------------------------------------------------
# BUG #1 — /logout ne vide pas la session admin (HIGH)
# ---------------------------------------------------------------------------

class TestLogoutClearsAdminSession:
    """
    Bug : /logout (bouton "Se déconnecter" sur index.html) ne supprime que
    session["auth"] mais laisse session["is_admin"] intact.
    Un admin qui clique sur ce bouton reste donc admin sans le savoir.
    """

    def setup_method(self):
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "adminkey"

    def test_logout_also_clears_is_admin(self, client):
        """Après /logout, la session admin doit être révoquée."""
        # L'admin s'authentifie par PIN puis par clé admin
        client.post("/login", data={"pin": "123456"})
        client.post("/admin/login", data={"key": "adminkey"})

        # Vérification : accès admin actif
        assert client.get("/api/admin/devices").status_code == 200

        # Déconnexion via le bouton de la page principale
        client.post("/logout")

        # L'accès admin doit être révoqué
        assert client.get("/api/admin/devices").status_code == 403

    def test_admin_logout_route_clears_admin_session(self, client):
        """/admin/logout (bouton du panneau admin) : comportement correct."""
        client.post("/admin/login", data={"key": "adminkey"})
        assert client.get("/api/admin/devices").status_code == 200

        client.post("/admin/logout")
        assert client.get("/api/admin/devices").status_code == 403

    def test_logout_without_admin_session_is_safe(self, client):
        """Un utilisateur non-admin qui se déconnecte ne lève pas d'erreur."""
        client.post("/login", data={"pin": "123456"})
        r = client.post("/logout")
        assert r.status_code == 302  # redirigé vers /login


# ---------------------------------------------------------------------------
# BUG #2 — TUNNEL_URL exposée sans authentification via /api/network-info (MEDIUM)
# ---------------------------------------------------------------------------

class TestTunnelUrlDisclosure:
    """
    /api/network-info est dans OPEN_ENDPOINTS (pas de PIN requis).
    En mode tunnel, il retourne TUNNEL_URL à n'importe quel visiteur
    non authentifié, même si un PIN est actif.
    """

    def setup_method(self):
        crow.AUTH_ENABLED = True
        crow.PIN = "secret"
        crow.TUNNEL_MODE = True
        crow.TUNNEL_URL = "https://abc123.trycloudflare.com"
        crow.APPROVAL_ENABLED = False

    def test_tunnel_url_hidden_from_unauthenticated(self, client):
        """Visiteur sans PIN ne doit pas voir l'URL du tunnel."""
        r = client.get("/api/network-info")
        assert r.status_code == 200
        assert r.json["tunnel_url"] is None

    def test_tunnel_url_visible_after_auth(self, client):
        """Utilisateur authentifié par PIN peut voir l'URL du tunnel."""
        client.post("/login", data={"pin": "secret"})
        r = client.get("/api/network-info")
        assert r.json["tunnel_url"] == crow.TUNNEL_URL

    def test_tunnel_url_hidden_when_tunnel_mode_off(self, client):
        """Sans --tunnel, tunnel_url est None même pour un admin."""
        crow.TUNNEL_MODE = False
        crow.TUNNEL_URL = None
        r = client.get("/api/network-info")
        assert r.json["tunnel_url"] is None


# ---------------------------------------------------------------------------
# Couverture manquante — transitions d'état des appareils (MEDIUM)
# ---------------------------------------------------------------------------

class TestDeviceStateTransitions:
    """Machine à états : pending → approved → denied → pending → approved."""

    def setup_method(self):
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "key"

    def _setup_device(self, client, name="device"):
        did = f"device-{name}"
        client.set_cookie("crow_relay_device", did)
        client.post("/api/request-access", json={"name": name})
        return did

    def _admin_action(self, action, did, **kwargs):
        with crow.app.test_client() as admin:
            admin.post("/admin/login", data={"key": "key"})
            admin.post(f"/api/admin/devices/{did}", json={"action": action, **kwargs})

    def test_pending_to_approved_to_denied_to_pending(self, client):
        did = self._setup_device(client)

        # pending → approved
        self._admin_action("approve", did, can_send=True, can_receive=True)
        assert crow._devices[did]["status"] == "approved"

        # approved → denied
        self._admin_action("deny", did)
        assert crow._devices[did]["status"] == "denied"
        assert not crow._devices[did]["can_send"]
        assert not crow._devices[did]["can_receive"]

        # denied → pending (via "Redemander l'accès")
        client.post("/api/request-access", json={"name": "device"})
        assert crow._devices[did]["status"] == "pending"

        # pending → approved à nouveau
        self._admin_action("approve", did, can_send=True, can_receive=True)
        assert crow._devices[did]["status"] == "approved"

    def test_revoke_resets_permissions(self, client):
        did = self._setup_device(client, "charlie")
        self._admin_action("approve", did, can_send=True, can_receive=True)

        self._admin_action("revoke", did)
        rec = crow._devices[did]
        assert rec["status"] == "pending"
        assert not rec["can_send"]
        assert not rec["can_receive"]

    def test_update_partial_permissions(self, client):
        did = self._setup_device(client, "partial")
        self._admin_action("approve", did, can_send=True, can_receive=True)

        # Retire can_send uniquement
        with crow.app.test_client() as admin:
            admin.post("/admin/login", data={"key": "key"})
            admin.post(
                f"/api/admin/devices/{did}",
                json={"action": "update", "can_send": False},
            )

        assert not crow._devices[did]["can_send"]
        assert crow._devices[did]["can_receive"]  # inchangé

    def test_delete_nonexistent_device_is_idempotent(self, client):
        with crow.app.test_client() as admin:
            admin.post("/admin/login", data={"key": "key"})
            r = admin.post("/api/admin/devices/ghost-device", json={"action": "delete"})
            assert r.status_code == 200  # delete est idempotent : suppression d'une clé absente

    def test_unknown_action_returns_400(self, client):
        did = self._setup_device(client, "bad-action")
        with crow.app.test_client() as admin:
            admin.post("/admin/login", data={"key": "key"})
            r = admin.post(f"/api/admin/devices/{did}", json={"action": "fly"})
            assert r.status_code == 400


# ---------------------------------------------------------------------------
# Couverture manquante — /api/access-status pour session admin (MEDIUM)
# ---------------------------------------------------------------------------

class TestAccessStatusAdmin:

    def setup_method(self):
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "key"

    def test_admin_session_returns_approved_status(self, client):
        """La session admin est toujours 'approved' avec tous les droits."""
        client.post("/admin/login", data={"key": "key"})
        r = client.get("/api/access-status")
        assert r.status_code == 200
        data = r.json
        assert data["status"] == "approved"
        assert data["can_send"]
        assert data["can_receive"]
        assert data["admin"]

    def test_unknown_device_returns_unknown_status(self, client):
        """Appareil sans cookie → status 'unknown'."""
        r = client.get("/api/access-status")
        assert r.json["status"] == "unknown"

    def test_no_approval_mode_returns_approved_for_everyone(self, client):
        """--no-approval : tout le monde est 'approved'."""
        crow.APPROVAL_ENABLED = False
        r = client.get("/api/access-status")
        data = r.json
        assert data["status"] == "approved"
        assert data["can_send"]
        assert data["can_receive"]


# ---------------------------------------------------------------------------
# Couverture manquante — /api/network-info (MEDIUM)
# ---------------------------------------------------------------------------

class TestNetworkInfo:

    def test_same_network_detected_for_localhost(self, client):
        crow.LOCAL_IP = "192.168.1.100"
        r = client.get("/api/network-info")
        assert r.status_code == 200
        assert r.json["same_network"]  # test client = 127.0.0.1

    def test_tunnel_mode_flag_reflected(self, client):
        crow.TUNNEL_MODE = True
        assert client.get("/api/network-info").json["tunnel_mode"]

    def test_is_admin_reflected_in_network_info(self, client):
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "key"
        client.post("/admin/login", data={"key": "key"})
        assert client.get("/api/network-info").json["is_admin"]


# ---------------------------------------------------------------------------
# Couverture manquante — protection brute-force (vecteurs complets)
# ---------------------------------------------------------------------------

class TestBruteForceEdgeCases:

    def test_lockout_resets_after_expiry(self, client):
        """Après expiration du blocage, de nouvelles tentatives sont acceptées."""
        crow.AUTH_ENABLED = True
        crow.PIN = "111111"

        for _ in range(5):
            client.post("/login", data={"pin": "000000"})

        # Simuler l'expiration du blocage
        ip = "127.0.0.1"
        crow._login_attempts[ip]["blocked_until"] = time.time() - 1

        r = client.post("/login", data={"pin": "000000"})
        assert b"10 minutes" not in r.data  # blocage expiré

    def test_pin_lockout_bleeds_across_endpoints(self, client):
        """
        Le compteur de tentatives est partagé par IP : un blocage sur /login
        bloque aussi /admin/login (comportement intentionnel — l'attaquant ne
        peut pas pivoter vers l'endpoint admin après avoir été bloqué sur le PIN).
        """
        crow.AUTH_ENABLED = True
        crow.PIN = "111111"
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "realkey"

        for _ in range(5):
            client.post("/login", data={"pin": "000000"})

        # Le même compteur IP bloque aussi /admin/login
        r = client.post("/admin/login", data={"key": "realkey"})
        assert b"10 minutes" in r.data


# ---------------------------------------------------------------------------
# Couverture manquante — opérations fichiers (edge cases)
# ---------------------------------------------------------------------------

class TestFileEdgeCases:

    def test_upload_empty_filename_fallback(self, approved_device):
        """Un fichier sans nom est rejeté : Flask ne transmet pas les champs vides."""
        import io
        data = {"files": (io.BytesIO(b"data"), "")}
        r = approved_device.post("/api/upload", content_type="multipart/form-data", data=data)
        assert r.status_code == 400

    def test_upload_no_files_field_returns_400(self, approved_device):
        """POST /api/upload sans champ 'files' → 400."""
        r = approved_device.post("/api/upload", content_type="multipart/form-data", data={})
        assert r.status_code == 400

    def test_list_files_excludes_directories(self, approved_device):
        """Les sous-dossiers dans SHARE_DIR n'apparaissent pas dans la liste."""
        subdir = os.path.join(crow.SHARE_DIR, "subdir")
        os.makedirs(subdir, exist_ok=True)
        r = approved_device.get("/api/files")
        names = [f["name"] for f in r.json]
        assert "subdir" not in names

    def test_file_size_and_human_size_consistent(self, approved_device):
        """Le champ size (octets) est cohérent avec size_human."""
        content = b"x" * 1024
        approved_device.post(
            "/api/upload",
            content_type="multipart/form-data",
            data={"files": (io.BytesIO(content), "size_test.txt")},
        )
        files = approved_device.get("/api/files").json
        f = next(x for x in files if x["name"] == "size_test.txt")
        assert f["size"] == 1024
        assert "Ko" in f["size_human"]

    def test_delete_requires_can_send_permission(self, client):
        """Seuls les appareils avec can_send peuvent supprimer des fichiers."""
        crow.APPROVAL_ENABLED = True
        did = "recv-only"
        crow._devices[did] = {
            "name": "recv",
            "status": "approved",
            "can_send": False,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.set_cookie("crow_relay_device", did)
        assert client.post("/api/delete/anything.txt").status_code == 403

    def test_unique_path_never_overwrites_existing_file(self):
        """unique_path() génère toujours un chemin distinct si le fichier existe."""
        base = crow.SHARE_DIR
        # Crée le fichier original
        open(os.path.join(base, "shared.txt"), "w").close()

        p1 = crow.unique_path(base, "shared.txt")
        assert p1 != os.path.join(base, "shared.txt")
        assert "shared (1).txt" in p1

        # Crée le (1) pour forcer le (2)
        open(p1, "w").close()
        p2 = crow.unique_path(base, "shared.txt")
        assert "shared (2).txt" in p2


# ---------------------------------------------------------------------------
# Couverture manquante — endpoint /api/request-access (MEDIUM)
# ---------------------------------------------------------------------------

class TestRequestAccess:

    def setup_method(self):
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True

    def test_request_access_without_cookie_returns_400(self, client):
        """Sans cookie crow_relay_device, /api/request-access → 400."""
        r = client.post("/api/request-access", json={"name": "test"})
        assert r.status_code == 400

    def test_request_access_creates_pending_device(self, client):
        """Un nouvel appareil est créé avec status='pending'."""
        did = "newdevice123"
        client.set_cookie("crow_relay_device", did)
        r = client.post("/api/request-access", json={"name": "Mon iPhone"})
        assert r.status_code == 200
        assert crow._devices[did]["status"] == "pending"
        assert crow._devices[did]["name"] == "Mon iPhone"

    def test_request_access_name_truncated_at_40_chars(self, client):
        """Le nom est limité à 40 caractères."""
        did = "longname-device"
        client.set_cookie("crow_relay_device", did)
        long_name = "A" * 100
        client.post("/api/request-access", json={"name": long_name})
        assert len(crow._devices[did]["name"]) <= 40

    def test_denied_device_becomes_pending_on_retry(self, client):
        """Un appareil refusé repasse en 'pending' quand il redemande l'accès."""
        did = "denied-device"
        crow._devices[did] = {
            "name": "denied",
            "status": "denied",
            "can_send": False,
            "can_receive": False,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.set_cookie("crow_relay_device", did)
        client.post("/api/request-access", json={"name": "denied"})
        assert crow._devices[did]["status"] == "pending"

    def test_approved_device_stays_approved_on_reaccess(self, client):
        """Un appareil déjà approuvé qui refait /request-access reste approuvé."""
        did = "already-approved"
        crow._devices[did] = {
            "name": "approved",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.set_cookie("crow_relay_device", did)
        client.post("/api/request-access", json={"name": "approved"})
        assert crow._devices[did]["status"] == "approved"


# ---------------------------------------------------------------------------
# FIX M1 — Bypass MAC : usurpation d'adresse MAC ne doit pas auto-approuver
# ---------------------------------------------------------------------------

class TestMacBypassFixed:
    """
    Régression pour M1 : avant le fix, un attaquant pouvait usurper le MAC
    d'un appareil approuvé pour obtenir l'auto-approbation sans intervention admin.
    Après fix : le MAC sert uniquement à pré-remplir le nom de l'appareil.
    """

    def setup_method(self):
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True
        crow.TUNNEL_MODE = False

    def test_mac_spoof_does_not_auto_approve(self, client, monkeypatch):
        """Un nouveau cookie avec le MAC d'un appareil approuvé reste pending."""
        approved_did = "legit-device-001"
        target_mac = "AA:BB:CC:DD:EE:FF"
        crow._devices[approved_did] = {
            "name": "Téléphone Alice",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": target_mac,
            "first_seen": 0.0,
            "last_seen": 0.0,
        }

        # Simule un attaquant qui usurpe le MAC de l'appareil approuvé
        monkeypatch.setattr(crow, "get_client_mac", lambda: target_mac)

        attacker_did = "attacker-device-999"
        client.set_cookie("crow_relay_device", attacker_did)
        r = client.post("/api/request-access", json={"name": "Attaquant"})
        assert r.status_code == 200

        rec = crow._devices[attacker_did]
        assert rec["status"] == "pending", "MAC spoofing ne doit pas auto-approuver"
        assert not rec["can_send"]
        assert not rec["can_receive"]

    def test_mac_match_still_prefills_name(self, client, monkeypatch):
        """Le MAC peut toujours pré-remplir le nom (ergonomie préservée)."""
        approved_did = "named-device"
        target_mac = "11:22:33:44:55:66"
        crow._devices[approved_did] = {
            "name": "Téléphone Alice",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": target_mac,
            "first_seen": 0.0,
            "last_seen": 0.0,
        }

        monkeypatch.setattr(crow, "get_client_mac", lambda: target_mac)

        new_did = "new-device-no-name"
        client.set_cookie("crow_relay_device", new_did)
        client.post("/api/request-access", json={"name": ""})

        rec = crow._devices[new_did]
        assert rec["status"] == "pending"
        assert rec["name"] == "Téléphone Alice"  # nom pré-rempli depuis l'ancien appareil

    def test_no_mac_match_creates_pending_device(self, client, monkeypatch):
        """Sans MAC connu, le comportement normal s'applique : pending."""
        monkeypatch.setattr(crow, "get_client_mac", lambda: "FF:FF:FF:FF:FF:FF")

        did = "unknown-mac-device"
        client.set_cookie("crow_relay_device", did)
        client.post("/api/request-access", json={"name": "Inconnu"})

        assert crow._devices[did]["status"] == "pending"

    def test_mac_bypass_blocked_in_tunnel_mode(self, client):
        """En mode tunnel, get_client_mac() retourne None : aucune auto-approbation possible."""
        crow.TUNNEL_MODE = True
        approved_did = "tunnel-legit"
        crow._devices[approved_did] = {
            "name": "Appareil approuvé",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": "AA:BB:CC:DD:EE:FF",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }

        new_did = "tunnel-attacker"
        client.set_cookie("crow_relay_device", new_did)
        client.post("/api/request-access", json={"name": "Tunnel Attaquant"})

        assert crow._devices[new_did]["status"] == "pending"


# ---------------------------------------------------------------------------
# FIX M2 — Download : symlink check et serve utilisent le même nom sécurisé
# ---------------------------------------------------------------------------

class TestDownloadSymlinkFixed:
    """
    Régression pour M2 : avant le fix, le check symlink utilisait secure_filename()
    mais send_from_directory utilisait le nom brut. Un symlink avec espace dans le nom
    (ex: 'my file.txt') passait le check (qui vérifiait 'my_file.txt') et était servi.
    """

    def setup_method(self):
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True

    def test_download_uses_secure_filename(self, approved_device):
        """Le fichier servi correspond au nom sécurisé, pas au nom brut."""
        import io
        approved_device.post(
            "/api/upload",
            content_type="multipart/form-data",
            data={"files": (io.BytesIO(b"contenu"), "test.txt")},
        )
        r = approved_device.get("/download/test.txt")
        assert r.status_code == 200
        assert r.data == b"contenu"

    def test_download_empty_secure_filename_returns_404(self, approved_device):
        """Un nom qui devient vide après secure_filename (ex: '../..') → 404."""
        r = approved_device.get("/download/../..")
        assert r.status_code == 404

    def test_symlink_in_sharedir_blocked(self, approved_device):
        """Un lien symbolique direct dans SHARE_DIR est bloqué au téléchargement."""
        target = os.path.join(crow.SHARE_DIR, "real.txt")
        link = os.path.join(crow.SHARE_DIR, "link.txt")
        with open(target, "w") as f:
            f.write("réel")
        os.symlink(target, link)

        r = approved_device.get("/download/link.txt")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# FIX L2 — action "update" refusée sur un appareil non approuvé
# ---------------------------------------------------------------------------

class TestUpdateActionGuard:
    """
    Régression pour L2 : avant le fix, l'action "update" acceptait de modifier
    can_send/can_receive sur un appareil pending ou denied. Sans effet de sécurité
    immédiat (device_allowed vérifie le statut), mais comportement incohérent.
    """

    def setup_method(self):
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "key"

    def test_update_on_pending_device_returns_400(self, client):
        """action='update' sur un appareil pending → 400."""
        did = "pending-device"
        crow._devices[did] = {
            "name": "pending",
            "status": "pending",
            "can_send": False,
            "can_receive": False,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.post("/admin/login", data={"key": "key"})
        r = client.post(f"/api/admin/devices/{did}", json={"action": "update", "can_send": True})
        assert r.status_code == 400
        assert not crow._devices[did]["can_send"]

    def test_update_on_denied_device_returns_400(self, client):
        """action='update' sur un appareil denied → 400."""
        did = "denied-device"
        crow._devices[did] = {
            "name": "denied",
            "status": "denied",
            "can_send": False,
            "can_receive": False,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.post("/admin/login", data={"key": "key"})
        r = client.post(f"/api/admin/devices/{did}", json={"action": "update", "can_send": True})
        assert r.status_code == 400

    def test_update_on_approved_device_succeeds(self, client):
        """action='update' sur un appareil approved → 200, permissions modifiées."""
        did = "approved-device"
        crow._devices[did] = {
            "name": "approved",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.post("/admin/login", data={"key": "key"})
        r = client.post(f"/api/admin/devices/{did}", json={"action": "update", "can_send": False})
        assert r.status_code == 200
        assert not crow._devices[did]["can_send"]
        assert crow._devices[did]["can_receive"]


# ---------------------------------------------------------------------------
# Couverture manquante — sécurité des headers (complément)
# ---------------------------------------------------------------------------

class TestSecurityHeadersComplement:

    def test_referrer_policy_present(self, client):
        r = client.get("/login")
        assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy_present(self, client):
        r = client.get("/login")
        assert "Permissions-Policy" in r.headers

    def test_x_robots_tag_noindex(self, client):
        r = client.get("/login")
        assert "noindex" in r.headers.get("X-Robots-Tag", "")

    def test_csp_disallows_external_scripts(self, client):
        r = client.get("/login")
        csp = r.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp

    def test_upload_endpoint_has_security_headers(self, approved_device):
        """Les headers de sécurité sont présents sur toutes les routes."""
        r = approved_device.get("/api/files")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
