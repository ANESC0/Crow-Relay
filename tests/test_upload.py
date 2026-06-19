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
        path = os.path.join(crow.SHARE_DIR, "todelete.txt")
        open(path, "w").close()
        r = approved_device.post("/api/delete/todelete.txt")
        assert r.status_code == 200
        assert not os.path.exists(path)

    def test_delete_nonexistent_returns_404(self, approved_device):
        r = approved_device.post("/api/delete/ghost.txt")
        assert r.status_code == 404
