"""Tests de la detection/affichage d'IP (multi-cartes, VPN, rafraichissement)."""
import app as crow


def _fake_getaddrinfo(ips):
    """Fabrique un faux socket.getaddrinfo renvoyant ces IP."""
    def _inner(*_a, **_k):
        return [(None, None, None, None, (ip, 0)) for ip in ips]
    return _inner


# --------------------------------------------------------------------------- #
# Classification des IP
# --------------------------------------------------------------------------- #
def test_is_lan_ipv4_accepts_private():
    assert crow._is_lan_ipv4("192.168.1.50")
    assert crow._is_lan_ipv4("10.0.0.5")
    assert crow._is_lan_ipv4("172.16.4.2")


def test_is_lan_ipv4_rejects_public_loopback_linklocal():
    assert not crow._is_lan_ipv4("8.8.8.8")          # publique
    assert not crow._is_lan_ipv4("127.0.0.1")        # loopback
    assert not crow._is_lan_ipv4("169.254.1.1")      # link-local / APIPA
    assert not crow._is_lan_ipv4("pas-une-ip")       # invalide


# --------------------------------------------------------------------------- #
# Choix de l'IP principale
# --------------------------------------------------------------------------- #
def test_route_ip_private_is_used(monkeypatch):
    """Cas normal : la route internet est une IP LAN -> on l'annonce."""
    monkeypatch.setattr(crow, "_route_ip", lambda: "192.168.1.50")
    monkeypatch.setattr(crow.socket, "getaddrinfo", _fake_getaddrinfo(["192.168.1.50"]))
    primary, ips = crow._discover_ips()
    assert primary == "192.168.1.50"
    assert ips == ["192.168.1.50"]


def test_vpn_route_falls_back_to_private(monkeypatch):
    """Route via VPN/IP publique -> on bascule sur une vraie IP LAN."""
    monkeypatch.setattr(crow, "_route_ip", lambda: "100.100.100.100")  # non privee
    monkeypatch.setattr(crow.socket, "getaddrinfo", _fake_getaddrinfo(["192.168.1.50"]))
    primary, ips = crow._discover_ips()
    assert primary == "192.168.1.50"
    assert "100.100.100.100" not in ips


def test_offline_falls_back_to_loopback(monkeypatch):
    """Aucune route, aucune IP privee -> repli propre sur loopback."""
    monkeypatch.setattr(crow, "_route_ip", lambda: None)
    monkeypatch.setattr(crow.socket, "getaddrinfo", _fake_getaddrinfo([]))
    primary, ips = crow._discover_ips()
    assert primary == "127.0.0.1"
    assert ips == []


def test_multi_nic_lists_all_private(monkeypatch):
    """Plusieurs cartes : toutes les IP LAN sont listees, route en tete."""
    monkeypatch.setattr(crow, "_route_ip", lambda: "192.168.1.50")
    monkeypatch.setattr(
        crow.socket, "getaddrinfo",
        _fake_getaddrinfo(["192.168.1.50", "10.0.0.7", "8.8.8.8"]),
    )
    primary, ips = crow._discover_ips()
    assert primary == "192.168.1.50"
    assert ips == ["192.168.1.50", "10.0.0.7"]   # publique exclue


# --------------------------------------------------------------------------- #
# --host fige l'IP annoncee
# --------------------------------------------------------------------------- #
def test_host_pin_overrides_detection(monkeypatch):
    monkeypatch.setattr(crow, "_route_ip", lambda: "192.168.1.50")
    crow.HOST_PIN = "10.0.0.99"
    crow.LAN_SCHEME = "http"
    crow.LAN_PORT = 8000
    assert crow.current_lan_ips() == ["10.0.0.99"]
    assert crow.current_lan_url() == "http://10.0.0.99:8000"


# --------------------------------------------------------------------------- #
# Cache court + rafraichissement
# --------------------------------------------------------------------------- #
def test_url_refreshes_when_ip_changes(monkeypatch):
    """Apres expiration du cache, l'URL reflete la nouvelle IP (DHCP/VPN)."""
    crow.LAN_SCHEME = "http"
    crow.LAN_PORT = 8000
    monkeypatch.setattr(crow.socket, "getaddrinfo", _fake_getaddrinfo([]))

    monkeypatch.setattr(crow, "_route_ip", lambda: "192.168.1.50")
    assert crow.current_lan_url() == "http://192.168.1.50:8000"

    # l'IP change ; on simule l'expiration du cache
    monkeypatch.setattr(crow, "_route_ip", lambda: "192.168.1.77")
    crow._ip_cache.update({"primary": "", "ips": [], "ts": 0.0})
    assert crow.current_lan_url() == "http://192.168.1.77:8000"


def test_cache_avoids_recompute(monkeypatch):
    """Tant que le cache est chaud, on ne recalcule pas (pas de socket)."""
    crow.LAN_SCHEME = "http"
    crow.LAN_PORT = 8000
    monkeypatch.setattr(crow.socket, "getaddrinfo", _fake_getaddrinfo([]))
    monkeypatch.setattr(crow, "_route_ip", lambda: "192.168.1.50")
    assert crow.current_lan_url() == "http://192.168.1.50:8000"

    calls = {"n": 0}
    def _boom():
        calls["n"] += 1
        return "192.168.9.9"
    monkeypatch.setattr(crow, "_route_ip", _boom)
    # cache encore chaud -> ne doit pas appeler _route_ip
    assert crow.current_lan_url() == "http://192.168.1.50:8000"
    assert calls["n"] == 0


# --------------------------------------------------------------------------- #
# Meme reseau en multi-cartes
# --------------------------------------------------------------------------- #
def test_same_network_matches_any_nic(monkeypatch):
    crow.TUNNEL_MODE = False
    monkeypatch.setattr(crow, "current_lan_ips", lambda: ["192.168.1.50", "192.168.2.50"])
    with crow.app.test_request_context(environ_base={"REMOTE_ADDR": "192.168.2.10"}):
        assert crow._is_same_network() is True
    with crow.app.test_request_context(environ_base={"REMOTE_ADDR": "10.5.5.5"}):
        assert crow._is_same_network() is False


# --------------------------------------------------------------------------- #
# Choix interactif de la carte (--pick-host)
# --------------------------------------------------------------------------- #
def test_pick_host_returns_chosen_ip(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "2")
    assert crow._choose_listen_ip(["192.168.1.50", "10.0.0.7"]) == "10.0.0.7"


def test_pick_host_zero_means_all(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "0")
    assert crow._choose_listen_ip(["192.168.1.50", "10.0.0.7"]) is None


def test_pick_host_empty_means_all(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "")
    assert crow._choose_listen_ip(["192.168.1.50", "10.0.0.7"]) is None


def test_pick_host_invalid_means_all(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "abc")
    assert crow._choose_listen_ip(["192.168.1.50", "10.0.0.7"]) is None
    monkeypatch.setattr("builtins.input", lambda *_: "9")  # hors borne
    assert crow._choose_listen_ip(["192.168.1.50", "10.0.0.7"]) is None


def test_pick_host_single_card_no_prompt(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("input ne doit pas etre appele avec une seule carte")
    monkeypatch.setattr("builtins.input", _boom)
    assert crow._choose_listen_ip(["192.168.1.50"]) is None
    # une seule IP LAN parmi du bruit -> pas de question non plus
    assert crow._choose_listen_ip(["192.168.1.50", "8.8.8.8"]) is None
