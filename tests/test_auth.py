import app as crow


class TestPIN:
    def test_unauthenticated_redirects_to_login(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        r = client.get("/")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_wrong_pin_returns_error(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        r = client.post("/login", data={"pin": "000000"})
        assert b"incorrect" in r.data.lower()

    def test_correct_pin_grants_access(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        r = client.post("/login", data={"pin": "123456"}, follow_redirects=True)
        assert r.status_code == 200

    def test_brute_force_lockout_after_5_attempts(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        for _ in range(5):
            client.post("/login", data={"pin": "000000"})
        r = client.post("/login", data={"pin": "000000"})
        assert b"10 minutes" in r.data

    def test_correct_pin_after_lockout_still_blocked(self, client):
        crow.AUTH_ENABLED = True
        crow.PIN = "123456"
        for _ in range(5):
            client.post("/login", data={"pin": "000000"})
        r = client.post("/login", data={"pin": "123456"})
        assert b"10 minutes" in r.data

    def test_no_auth_mode_skips_login(self, client):
        crow.AUTH_ENABLED = False
        r = client.get("/")
        assert r.status_code == 200


class TestAdmin:
    def test_wrong_admin_key_returns_error(self, client):
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "secretkey"
        r = client.post("/admin/login", data={"key": "wrongkey"})
        assert b"incorrecte" in r.data

    def test_correct_admin_key_grants_access(self, client):
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "secretkey"
        r = client.post("/admin/login", data={"key": "secretkey"}, follow_redirects=True)
        assert r.status_code == 200

    def test_admin_api_requires_auth(self, client):
        crow.APPROVAL_ENABLED = True
        r = client.get("/api/admin/devices")
        assert r.status_code == 403

    def test_bootstrap_token_single_use(self, client):
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "key"
        import secrets
        crow._bootstrap_token = token = secrets.token_urlsafe(16)
        client.get(f"/admin/boot/{token}")
        r = client.get(f"/admin/boot/{token}", follow_redirects=False)
        assert r.status_code == 302
        assert "/admin/login" in r.headers["Location"]
