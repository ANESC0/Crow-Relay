import os
import pytest
import app as crow


@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    """Remet les globaux à zéro entre chaque test."""
    crow.AUTH_ENABLED = False
    crow.APPROVAL_ENABLED = False
    crow.TUNNEL_MODE = False
    crow.PIN = None
    crow.ADMIN_KEY = None
    crow.TUNNEL_URL = None
    crow.LOCAL_IP = ""
    crow._bootstrap_token = None
    crow.SHARE_DIR = str(tmp_path / "shared")
    crow.DEVICES_FILE = str(tmp_path / "devices.json")
    crow._devices.clear()
    crow._login_attempts.clear()
    os.makedirs(crow.SHARE_DIR, exist_ok=True)
    crow.app.config["TESTING"] = True
    crow.app.config["RATELIMIT_ENABLED"] = False
    crow.app.config["SESSION_COOKIE_SECURE"] = False
    crow.app.config["MAX_CONTENT_LENGTH"] = None
    with crow.app.app_context():
        crow.limiter.reset()
    yield
    crow._devices.clear()
    crow._login_attempts.clear()


@pytest.fixture
def client():
    with crow.app.test_client() as c:
        yield c


@pytest.fixture
def authed_client(client):
    """Client déjà authentifié par PIN."""
    crow.AUTH_ENABLED = True
    crow.PIN = "123456"
    client.post("/login", data={"pin": "123456"})
    return client


@pytest.fixture
def approved_device(client):
    """Client avec un appareil approuvé (envoi + réception)."""
    crow.APPROVAL_ENABLED = True
    device_id = "testdevice000"
    crow._devices[device_id] = {
        "name": "test",
        "status": "approved",
        "can_send": True,
        "can_receive": True,
        "mac": "",
        "first_seen": 0.0,
        "last_seen": 0.0,
    }
    client.set_cookie("crow_relay_device", device_id)
    return client
