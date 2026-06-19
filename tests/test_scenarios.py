"""
Scénarios d'usage réalistes — simulent des sessions utilisateur complètes.
Chaque classe représente un cas d'usage distinct tel qu'un vrai utilisateur
le vivrait.

Convention : `client` = l'appareil de l'utilisateur lambda.
             `crow.app.test_client()` créé inline = session admin séparée.
"""

import io
import os

import app as crow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(client, pin):
    return client.post("/login", data={"pin": pin}, follow_redirects=True)


def _admin_login(client, key):
    return client.post("/admin/login", data={"key": key}, follow_redirects=True)


def _request_access(client, name="Téléphone de test"):
    did = "device-" + name.replace(" ", "-").lower()
    client.set_cookie("crow_relay_device", did)
    client.post("/api/request-access", json={"name": name})
    return did


def _approve(admin_client, device_id, can_send=True, can_receive=True):
    return admin_client.post(
        f"/api/admin/devices/{device_id}",
        json={"action": "approve", "can_send": can_send, "can_receive": can_receive},
    )


def _upload(client, filename="test.txt", content=b"hello"):
    return client.post(
        "/api/upload",
        content_type="multipart/form-data",
        data={"files": (io.BytesIO(content), filename)},
    )


# ---------------------------------------------------------------------------
# Scénario 1 — Parcours LAN complet (cas nominal)
# ---------------------------------------------------------------------------

class TestLANHappyPath:
    """
    Alice héberge Crow-Relay sur son PC.
    Bob arrive avec son téléphone sur le même Wi-Fi.
    """

    def setup_method(self):
        crow.AUTH_ENABLED = True
        crow.PIN = "555000"
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "adminkey"

    def test_full_flow_pin_approval_upload_download(self, client):
        # Bob entre le PIN
        _login(client, "555000")
        did = _request_access(client, "Téléphone Bob")

        assert client.get("/api/access-status").json["status"] == "pending"

        # Alice approuve dans une session admin séparée
        with crow.app.test_client() as admin:
            _admin_login(admin, "adminkey")
            _approve(admin, did)

        # Bob peut uploader
        r = _upload(client, "photo.jpg", b"imagedata")
        assert r.status_code == 200
        assert "photo.jpg" in r.json["saved"]

        # Bob peut lister et télécharger
        files = client.get("/api/files").json
        assert any(f["name"] == "photo.jpg" for f in files)

        r = client.get("/download/photo.jpg")
        assert r.status_code == 200
        assert r.data == b"imagedata"

    def test_pending_device_blocked(self, client):
        _login(client, "555000")
        _request_access(client, "Inconnu")

        assert client.get("/api/access-status").json["status"] == "pending"
        assert _upload(client).status_code == 403
        assert client.get("/api/files").status_code == 403

    def test_denied_device_cannot_act(self, client):
        _login(client, "555000")
        did = _request_access(client, "Indésirable")

        with crow.app.test_client() as admin:
            _admin_login(admin, "adminkey")
            admin.post(f"/api/admin/devices/{did}", json={"action": "deny"})

        assert _upload(client).status_code == 403

    def test_revoked_device_loses_access(self, client):
        _login(client, "555000")
        did = _request_access(client, "Bob")

        with crow.app.test_client() as admin:
            _admin_login(admin, "adminkey")
            _approve(admin, did)

        assert _upload(client).status_code == 200

        with crow.app.test_client() as admin:
            _admin_login(admin, "adminkey")
            admin.post(f"/api/admin/devices/{did}", json={"action": "revoke"})

        assert _upload(client).status_code == 403

    def test_receive_only_device_cannot_send(self, client):
        _login(client, "555000")
        did = _request_access(client, "Lecture seule")

        with crow.app.test_client() as admin:
            _admin_login(admin, "adminkey")
            _approve(admin, did, can_send=False, can_receive=True)

        assert _upload(client).status_code == 403
        assert client.get("/api/files").status_code == 200

    def test_send_only_device_cannot_download(self, client):
        _login(client, "555000")
        did = _request_access(client, "Envoi seul")

        with crow.app.test_client() as admin:
            _admin_login(admin, "adminkey")
            _approve(admin, did, can_send=True, can_receive=False)

        assert _upload(client).status_code == 200
        assert client.get("/api/files").status_code == 403
        assert client.get("/download/anything.txt").status_code == 403

    def test_admin_session_has_full_access(self, client):
        """Le panneau admin a toujours accès, sans passer par l'approbation."""
        _admin_login(client, "adminkey")
        assert _upload(client).status_code == 200
        assert client.get("/api/files").status_code == 200


# ---------------------------------------------------------------------------
# Scénario 2 — Modes sans PIN et/ou sans approbation
# ---------------------------------------------------------------------------

class TestOpenModes:

    def test_no_approval_mode_immediate_access(self, client):
        """--no-approval : upload immédiat sans attendre l'hôte."""
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        crow.APPROVAL_ENABLED = False

        _login(client, "123456")
        assert _upload(client, "doc.pdf", b"pdfdata").status_code == 200

    def test_no_pin_mode_direct_access(self, client):
        """--no-pin : page accessible directement, pas de formulaire."""
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = False

        assert client.get("/").status_code == 200
        assert _upload(client, "file.txt", b"data").status_code == 200

    def test_fully_open_mode(self, client):
        """--no-pin --no-approval : accès complet sans friction."""
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = False

        assert client.get("/api/files").status_code == 200
        assert _upload(client, "open.txt", b"x").status_code == 200

    def test_no_pin_still_requires_approval(self, client):
        """Sans PIN mais avec approbation : l'appareil doit quand même être validé."""
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "key"

        assert _upload(client).status_code == 403

        did = _request_access(client)
        with crow.app.test_client() as admin:
            _admin_login(admin, "key")
            _approve(admin, did)

        assert _upload(client).status_code == 200


# ---------------------------------------------------------------------------
# Scénario 3 — Mode tunnel (régression du bug SESSION_COOKIE_SECURE)
# ---------------------------------------------------------------------------

class TestTunnelMode:

    def test_local_url_login_works_in_tunnel_mode(self, client):
        """
        Régression : avant le fix, SESSION_COOKIE_SECURE=True en mode tunnel
        empêchait le navigateur de stocker le cookie sur HTTP local.
        L'URL locale devenait inutilisable (PIN accepté mais session perdue).
        """
        crow.TUNNEL_MODE = True
        crow.AUTH_ENABLED = True
        crow.PIN = "999999"
        crow.APPROVAL_ENABLED = False

        r = _login(client, "999999")
        assert r.status_code == 200
        assert client.get("/api/files").status_code == 200

    def test_session_cookie_not_secure_in_tunnel_mode(self, client):
        """
        En mode tunnel sans --https, SESSION_COOKIE_SECURE doit rester False.
        Sinon les cookies ne sont pas envoyés sur l'URL locale HTTP.
        """
        crow.TUNNEL_MODE = True
        assert not crow.app.config.get("SESSION_COOKIE_SECURE")

    def test_hsts_header_absent_in_tunnel_mode(self, client):
        """HSTS ne nous appartient pas en mode tunnel : Cloudflare le gère."""
        crow.TUNNEL_MODE = True
        r = client.get("/login")
        assert "Strict-Transport-Security" not in r.headers

    def test_https_mode_sets_secure_cookie_and_hsts(self, client):
        """--https : SESSION_COOKIE_SECURE=True et HSTS sont actifs."""
        crow.app.config["SESSION_COOKIE_SECURE"] = True

        r = client.get("/login")
        assert "Strict-Transport-Security" in r.headers

        crow.app.config["SESSION_COOKIE_SECURE"] = False  # restaurer pour les tests suivants

    def test_lan_and_tunnel_users_independent_sessions(self):
        """
        Un utilisateur LAN et un utilisateur tunnel peuvent se connecter
        indépendamment avec le même PIN.
        """
        crow.TUNNEL_MODE = True
        crow.AUTH_ENABLED = True
        crow.PIN = "777777"
        crow.APPROVAL_ENABLED = False

        with crow.app.test_client() as lan_client:
            assert _login(lan_client, "777777").status_code == 200
            assert lan_client.get("/api/files").status_code == 200

        with crow.app.test_client() as tunnel_client:
            assert _login(tunnel_client, "777777").status_code == 200
            assert tunnel_client.get("/api/files").status_code == 200


# ---------------------------------------------------------------------------
# Scénario 4 — QR code / token dans l'URL
# ---------------------------------------------------------------------------

class TestQRCodeToken:

    def test_valid_token_auto_authenticates(self, client):
        """Scanner le QR code connecte directement sans saisir le PIN."""
        crow.AUTH_ENABLED = True
        crow.PIN = "654321"
        crow.APPROVAL_ENABLED = False

        r = client.get(f"/?token={crow.PIN}", follow_redirects=True)
        assert r.status_code == 200
        assert client.get("/api/files").status_code == 200

    def test_wrong_token_does_not_authenticate(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "654321"

        client.get("/?token=000000", follow_redirects=True)
        r = client.get("/api/files")
        assert r.status_code == 302  # redirigé vers /login

    def test_token_blocked_after_brute_force(self, client):
        """Le brute-force via token dans l'URL est aussi protégé."""
        crow.AUTH_ENABLED = True
        crow.PIN = "654321"

        for _ in range(5):
            client.get("/?token=000000")

        # Le bon token est bloqué pendant le lockout
        r = client.get(f"/?token={crow.PIN}", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]


# ---------------------------------------------------------------------------
# Scénario 5 — Gestion admin complète
# ---------------------------------------------------------------------------

class TestAdminManagement:

    def setup_method(self):
        crow.AUTH_ENABLED = False
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "superkey"

    def test_admin_sees_all_devices(self, client):
        with crow.app.test_client() as user1:
            _request_access(user1, "Appareil 1")
        with crow.app.test_client() as user2:
            _request_access(user2, "Appareil 2")

        _admin_login(client, "superkey")
        r = client.get("/api/admin/devices")
        assert r.status_code == 200
        names = [d["name"] for d in r.json]
        assert "Appareil 1" in names
        assert "Appareil 2" in names

    def test_admin_can_delete_device(self, client):
        with crow.app.test_client() as user:
            did = _request_access(user, "À supprimer")

        _admin_login(client, "superkey")
        client.post(f"/api/admin/devices/{did}", json={"action": "delete"})

        ids = [d["id"] for d in client.get("/api/admin/devices").json]
        assert did not in ids

    def test_admin_can_update_permissions(self, client):
        with crow.app.test_client() as user:
            did = _request_access(user, "Bob")

        _admin_login(client, "superkey")
        _approve(client, did, can_send=True, can_receive=True)
        client.post(f"/api/admin/devices/{did}", json={"action": "update", "can_send": False})

        device = next(d for d in client.get("/api/admin/devices").json if d["id"] == did)
        assert not device["can_send"]
        assert device["can_receive"]

    def test_admin_clear_files(self, client):
        crow.APPROVAL_ENABLED = False
        crow.AUTH_ENABLED = False

        with crow.app.test_client() as user:
            _upload(user, "a.txt", b"a")
            _upload(user, "b.txt", b"b")

        crow.APPROVAL_ENABLED = True
        _admin_login(client, "superkey")
        r = client.post("/api/admin/clear-files")
        assert r.json["deleted"] == 2
        assert client.get("/api/files").json == []

    def test_admin_clear_devices(self, client):
        with crow.app.test_client() as u1:
            _request_access(u1, "A")
        with crow.app.test_client() as u2:
            _request_access(u2, "B")

        _admin_login(client, "superkey")
        client.post("/api/admin/clear-devices")
        assert client.get("/api/admin/devices").json == []

    def test_non_admin_cannot_access_admin_api(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        _login(client, "123456")

        assert client.get("/api/admin/devices").status_code == 403
        assert client.post("/api/admin/clear-files").status_code == 403
        assert client.post("/api/admin/clear-devices").status_code == 403


# ---------------------------------------------------------------------------
# Scénario 6 — Brute-force (tous les vecteurs)
# ---------------------------------------------------------------------------

class TestBruteForce:

    def test_pin_lockout_after_5_attempts(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "111111"

        for _ in range(5):
            client.post("/login", data={"pin": "000000"})

        r = client.post("/login", data={"pin": "000000"})
        assert b"10 minutes" in r.data

    def test_correct_pin_still_blocked_during_lockout(self, client):
        """Même le bon PIN est rejeté pendant le blocage."""
        crow.AUTH_ENABLED = True
        crow.PIN = "111111"

        for _ in range(5):
            client.post("/login", data={"pin": "000000"})

        r = client.post("/login", data={"pin": "111111"})
        assert b"10 minutes" in r.data

    def test_admin_key_lockout_after_5_attempts(self, client):
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "realkey"

        for _ in range(5):
            client.post("/admin/login", data={"key": "wrongkey"})

        r = client.post("/admin/login", data={"key": "wrongkey"})
        assert b"10 minutes" in r.data

    def test_lockout_is_per_ip(self, client):
        """Bloquer une IP n'affecte pas les autres."""
        crow.AUTH_ENABLED = True
        crow.PIN = "111111"

        for _ in range(5):
            client.post("/login", data={"pin": "000000"})

        assert crow._is_blocked("127.0.0.1")
        assert not crow._is_blocked("10.0.0.99")

    def test_success_resets_counter(self, client):
        """Une connexion réussie remet le compteur à zéro."""
        crow.AUTH_ENABLED = True
        crow.PIN = "111111"
        crow.APPROVAL_ENABLED = False

        for _ in range(4):
            client.post("/login", data={"pin": "000000"})

        client.post("/login", data={"pin": "111111"})  # succès

        r = client.post("/login", data={"pin": "000000"})
        assert b"10 minutes" not in r.data  # compteur remis à 0, pas encore bloqué


# ---------------------------------------------------------------------------
# Scénario 7 — Opérations fichiers (cas limites)
# ---------------------------------------------------------------------------

class TestFileOperations:

    def test_upload_multiple_files_at_once(self, approved_device):
        data = {
            "files": [
                (io.BytesIO(b"data1"), "file1.txt"),
                (io.BytesIO(b"data2"), "file2.txt"),
            ]
        }
        r = approved_device.post(
            "/api/upload", content_type="multipart/form-data", data=data
        )
        assert r.status_code == 200
        assert set(r.json["saved"]) == {"file1.txt", "file2.txt"}

    def test_upload_deduplicates_collisions(self, approved_device):
        _upload(approved_device, "report.pdf", b"v1")
        _upload(approved_device, "report.pdf", b"v2")
        _upload(approved_device, "report.pdf", b"v3")

        names = {f["name"] for f in approved_device.get("/api/files").json}
        assert {"report.pdf", "report (1).pdf", "report (2).pdf"}.issubset(names)

    def test_path_traversal_in_upload_sanitized(self, approved_device):
        r = _upload(approved_device, "../../../etc/passwd", b"evil")
        assert r.status_code == 200
        saved = r.json["saved"][0]
        assert ".." not in saved
        assert "/" not in saved
        assert not os.path.exists(os.path.join(crow.SHARE_DIR, "../etc/passwd"))

    def test_download_nonexistent_returns_404(self, approved_device):
        assert approved_device.get("/download/ghost.pdf").status_code == 404

    def test_delete_then_reupload_without_suffix(self, approved_device):
        _upload(approved_device, "temp.txt", b"original")
        approved_device.post("/api/delete/temp.txt")

        r = _upload(approved_device, "temp.txt", b"nouveau")
        assert r.json["saved"] == ["temp.txt"]  # pas de "(1)" car le fichier est parti

    def test_file_metadata_in_listing(self, approved_device):
        _upload(approved_device, "meta.txt", b"content")
        f = next(x for x in approved_device.get("/api/files").json if x["name"] == "meta.txt")
        assert f["size"] == len(b"content")
        assert "size_human" in f
        assert "modified_human" in f

    def test_delete_nonexistent_returns_404(self, approved_device):
        assert approved_device.post("/api/delete/ghost.txt").status_code == 404

    def test_readonly_device_cannot_delete(self, client):
        """La suppression nécessite can_send."""
        crow.APPROVAL_ENABLED = True
        did = "readonly-device"
        crow._devices[did] = {
            "name": "readonly",
            "status": "approved",
            "can_send": False,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.set_cookie("crow_relay_device", did)
        assert client.post("/api/delete/anything.txt").status_code == 403
