import app as crow


class TestSecurityHeaders:
    def test_x_content_type_options(self, client):
        r = client.get("/login")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, client):
        r = client.get("/login")
        assert r.headers.get("X-Frame-Options") == "DENY"

    def test_csp_present(self, client):
        r = client.get("/login")
        assert "Content-Security-Policy" in r.headers

    def test_server_header_removed(self, client):
        r = client.get("/login")
        assert "Server" not in r.headers

    def test_hsts_absent_on_plain_http(self, client):
        r = client.get("/login")
        assert "Strict-Transport-Security" not in r.headers


class TestDeviceApproval:
    def test_pending_device_cannot_upload(self, client):
        crow.APPROVAL_ENABLED = True
        device_id = "pendingdevice"
        crow._devices[device_id] = {
            "name": "test",
            "status": "pending",
            "can_send": False,
            "can_receive": False,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.set_cookie("crow_relay_device", device_id)
        r = client.post("/api/upload")
        assert r.status_code == 403

    def test_denied_device_cannot_download(self, client):
        crow.APPROVAL_ENABLED = True
        device_id = "denieddevice"
        crow._devices[device_id] = {
            "name": "test",
            "status": "denied",
            "can_send": False,
            "can_receive": False,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        client.set_cookie("crow_relay_device", device_id)
        r = client.get("/download/anything.txt")
        assert r.status_code == 403

    def test_unknown_device_cannot_list_files(self, client):
        crow.APPROVAL_ENABLED = True
        r = client.get("/api/files")
        assert r.status_code == 403
