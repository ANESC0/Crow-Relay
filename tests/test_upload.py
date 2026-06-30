import io
import os
import app as crow


class TestUpload:
    def test_upload_saves_file(self, approved_device):
        data = {"files": (io.BytesIO(b"hello world"), "hello.txt")}
        r = approved_device.post("/api/upload", content_type="multipart/form-data", data=data)
        assert r.status_code == 200
        assert r.json["saved"] == ["hello.txt"]
        assert os.path.isfile(os.path.join(crow.SHARE_DIR, "hello.txt"))

    def test_upload_deduplicates_filename(self, approved_device):
        open(os.path.join(crow.SHARE_DIR, "file.txt"), "w").close()
        data = {"files": (io.BytesIO(b"data"), "file.txt")}
        r = approved_device.post("/api/upload", content_type="multipart/form-data", data=data)
        assert r.status_code == 200
        assert r.json["saved"] == ["file (1).txt"]

    def test_path_traversal_sanitized(self, approved_device):
        data = {"files": (io.BytesIO(b"evil"), "../evil.txt")}
        r = approved_device.post("/api/upload", content_type="multipart/form-data", data=data)
        assert r.status_code == 200
        assert not os.path.exists(os.path.join(crow.SHARE_DIR, "../evil.txt"))
        assert os.path.isfile(os.path.join(crow.SHARE_DIR, "evil.txt"))

    def test_upload_without_approval_blocked(self, client):
        crow.APPROVAL_ENABLED = True
        data = {"files": (io.BytesIO(b"data"), "test.txt")}
        r = client.post("/api/upload", content_type="multipart/form-data", data=data)
        assert r.status_code == 403


class TestDownloadAndList:
    def test_list_files(self, approved_device):
        open(os.path.join(crow.SHARE_DIR, "sample.txt"), "w").close()
        r = approved_device.get("/api/files")
        assert r.status_code == 200
        names = [f["name"] for f in r.json]
        assert "sample.txt" in names

    def test_download_existing_file(self, approved_device):
        path = os.path.join(crow.SHARE_DIR, "download_me.txt")
        with open(path, "w") as f:
            f.write("content")
        r = approved_device.get("/download/download_me.txt")
        assert r.status_code == 200
        assert r.data == b"content"

    def test_download_nonexistent_returns_404(self, approved_device):
        r = approved_device.get("/download/ghost.txt")
        assert r.status_code == 404


class TestDelete:
    def test_delete_file(self, approved_device):
        """L'uploader peut supprimer son propre fichier."""
        approved_device.post(
            "/api/upload",
            content_type="multipart/form-data",
            data={"files": (io.BytesIO(b"data"), "todelete.txt")},
        )
        path = os.path.join(crow.SHARE_DIR, "todelete.txt")
        r = approved_device.post("/api/delete/todelete.txt")
        assert r.status_code == 200
        assert not os.path.exists(path)

    def test_delete_nonexistent_returns_404(self, approved_device):
        r = approved_device.post("/api/delete/ghost.txt")
        assert r.status_code == 404

    def test_delete_other_user_file_forbidden(self, approved_device):
        """Un device ne peut pas supprimer un fichier uploadé par un autre."""
        crow.APPROVAL_ENABLED = True
        other_id = "otherdevice999"
        crow._devices[other_id] = {
            "name": "other",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        with crow.app.test_client() as other:
            other.set_cookie("crow_relay_device", other_id)
            other.post(
                "/api/upload",
                content_type="multipart/form-data",
                data={"files": (io.BytesIO(b"secret"), "others_file.txt")},
            )
        r = approved_device.post("/api/delete/others_file.txt")
        assert r.status_code == 403

    def test_can_delete_true_for_owner_in_file_list(self, approved_device):
        """can_delete=True pour le fichier uploadé par le device courant."""
        approved_device.post(
            "/api/upload",
            content_type="multipart/form-data",
            data={"files": (io.BytesIO(b"data"), "mine.txt")},
        )
        files = approved_device.get("/api/files").json
        f = next(x for x in files if x["name"] == "mine.txt")
        assert f["can_delete"] is True

    def test_can_delete_false_for_other_owners_file(self, approved_device):
        """can_delete=False quand le fichier appartient à un autre device."""
        crow.APPROVAL_ENABLED = True
        other_id = "stranger111"
        crow._devices[other_id] = {
            "name": "stranger",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        with crow.app.test_client() as other:
            other.set_cookie("crow_relay_device", other_id)
            other.post(
                "/api/upload",
                content_type="multipart/form-data",
                data={"files": (io.BytesIO(b"nope"), "theirs.txt")},
            )
        files = approved_device.get("/api/files").json
        f = next(x for x in files if x["name"] == "theirs.txt")
        assert f["can_delete"] is False

    def test_admin_can_delete_any_file(self, client):
        """L'admin peut supprimer n'importe quel fichier."""
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "adminkey"
        # Upload par un user normal
        did = "normaluser"
        crow._devices[did] = {
            "name": "user",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        user = client
        user.set_cookie("crow_relay_device", did)
        user.post(
            "/api/upload",
            content_type="multipart/form-data",
            data={"files": (io.BytesIO(b"content"), "user_file.txt")},
        )
        # L'admin supprime via son propre client
        with crow.app.test_client() as admin:
            admin.post("/admin/login", data={"key": "adminkey"})
            r = admin.post("/api/delete/user_file.txt")
            assert r.status_code == 200

    def test_admin_can_delete_all_in_file_list(self, client):
        """can_delete=True pour l'admin sur tous les fichiers."""
        crow.APPROVAL_ENABLED = True
        crow.ADMIN_KEY = "adminkey"
        did = "uploader"
        crow._devices[did] = {
            "name": "up",
            "status": "approved",
            "can_send": True,
            "can_receive": True,
            "mac": "",
            "first_seen": 0.0,
            "last_seen": 0.0,
        }
        user = client
        user.set_cookie("crow_relay_device", did)
        user.post(
            "/api/upload",
            content_type="multipart/form-data",
            data={"files": (io.BytesIO(b"x"), "admin_sees.txt")},
        )
        with crow.app.test_client() as admin:
            admin.post("/admin/login", data={"key": "adminkey"})
            files = admin.get("/api/files").json
            f = next(x for x in files if x["name"] == "admin_sees.txt")
            assert f["can_delete"] is True
