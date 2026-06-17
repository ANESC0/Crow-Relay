#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

"""Crow-Relay - service de transfert de fichiers local, multi-plateforme.

Lance un petit serveur web accessible depuis n'importe quel appareil du meme
reseau (telephone, autre ordinateur...). Depuis une page web on peut :
  - envoyer un fichier vers cet ordinateur ("Envoyer")
  - recuperer un fichier deja present sur cet ordinateur ("Recevoir")

L'ordinateur sert donc de relais/point de depot partage sur le LAN.

Securite (deux couches independantes) :
  1. Code PIN (active par defaut) : necessaire pour acceder au service. Le QR
     code l'embarque, donc le scanner connecte le telephone automatiquement.
  2. Autorisation par appareil (active par defaut) : chaque appareil qui se
     connecte apparait dans un panneau admin (protege par une cle admin) ou
     l'hote l'autorise a "envoyer" et/ou "recevoir". Tant qu'il n'est pas
     autorise, l'appareil voit un ecran "en attente".
"""

import argparse
import io
import json
import logging
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from cheroot.wsgi import Server as WSGIServer
from flask_limiter import Limiter
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Dossier ou les fichiers transferes sont stockes.
SHARE_DIR = os.environ.get("CROW_RELAY_SHARE_DIR", os.path.join(BASE_DIR, "shared"))

# Registre persistant des appareils connus (autorisations).
DEVICES_FILE = os.environ.get(
    "CROW_RELAY_DEVICES_FILE", os.path.join(BASE_DIR, "devices.json")
)

# Taille maximale d'un upload (par defaut illimite). En octets.
_max_mb = os.environ.get("CROW_RELAY_MAX_MB")

# Etat de securite, renseigne dans main().
PIN = None
AUTH_ENABLED = True
APPROVAL_ENABLED = True
ADMIN_KEY = None
LAN_URL = "http://localhost"
TUNNEL_MODE = False
LOCAL_IP = ""

# Protection brute-force sur le PIN.
_login_attempts: dict = {}
_login_lock = threading.Lock()
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 600  # 10 min

# Processus cloudflared actif (mode --tunnel).
_cloudflared_proc = None
# URL publique du tunnel (renseignee une fois cloudflared connecte).
TUNNEL_URL = None
MAX_DEVICES = 500  # cap du registre pour eviter l'epuisement memoire/disque

# Endpoints accessibles sans PIN.
OPEN_ENDPOINTS = {"login", "static", "api_network_info"}
# Endpoints d'administration : gerent eux-memes leur authentification (cle admin).
ADMIN_ENDPOINTS = {
    "admin",
    "admin_boot",
    "admin_login",
    "admin_logout",
    "api_admin_devices",
    "api_tunnel_url",
    "api_admin_set",
    "api_admin_clear_files",
    "api_admin_clear_devices",
}

app = Flask(__name__)
# Cle de session regeneree a chaque lancement (invalide les anciennes sessions).
app.secret_key = secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


@app.errorhandler(413)
def too_large(e):
    limit_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
    msg = f"Fichier trop volumineux (limite : {limit_mb} Mo)" if limit_mb else "Fichier trop volumineux"
    return jsonify({"error": msg}), 413


@app.errorhandler(429)
def too_many_requests(e):
    return jsonify({"error": "Trop de requêtes. Réessaie dans quelques instants."}), 429


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    response.headers.pop("Server", None)
    if app.config.get("SESSION_COOKIE_SECURE"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# --------------------------------------------------------------------------- #
# Registre des appareils
# --------------------------------------------------------------------------- #
_devices_lock = threading.Lock()
_devices: dict = {}
_upload_lock = threading.Lock()
# Token à usage unique généré au démarrage pour ouvrir le panneau admin sans
# mettre la clé admin dans l'URL (et donc dans l'historique du navigateur).
_bootstrap_token: str | None = None


def load_devices() -> None:
    global _devices
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            _devices = json.load(f)
    except (OSError, ValueError):
        _devices = {}


def save_devices() -> None:
    """Ecriture atomique du registre. Doit etre appelee avec _devices_lock acquis."""
    tmp = DEVICES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_devices, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DEVICES_FILE)


def get_device():
    """Retourne (device_id, record|None) pour la requete courante."""
    did = request.cookies.get("crow_relay_device")
    # Rejects empty values, control chars, path separators, and excessively long values.
    # Production IDs are uuid4().hex (32 hex chars); limit is intentionally generous for flexibility.
    if not did or not re.fullmatch(r"[\w\-]{1,128}", did):
        return None, None
    return did, _devices.get(did)


def get_client_mac() -> str | None:
    """Retourne l'adresse MAC du client via le cache ARP (LAN uniquement).
    Indisponible en mode tunnel (l'IP vue est celle de Cloudflare)."""
    if TUNNEL_MODE:
        return None
    ip = request.remote_addr or ""
    if ip in ("127.0.0.1", "::1", ""):
        return None
    try:
        with open("/proc/net/arp", encoding="ascii") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip:
                    mac = parts[3].upper()
                    if mac != "00:00:00:00:00:00":
                        return mac
    except OSError:
        pass
    return None


def find_approved_by_mac(mac: str) -> dict | None:
    """Retourne l'enregistrement approuvé dont le MAC correspond, ou None."""
    if not mac:
        return None
    for rec in _devices.values():
        if rec.get("mac") == mac and rec.get("status") == "approved":
            return rec
    return None


def device_allowed(perm: str) -> bool:
    """Indique si l'appareil courant a la permission demandee (can_send/can_receive)."""
    if not APPROVAL_ENABLED:
        return True
    if session.get("is_admin"):
        return True
    _, rec = get_device()
    if not rec or rec.get("status") != "approved":
        return False
    return bool(rec.get(perm, False))


# --------------------------------------------------------------------------- #
# Protection brute-force
# --------------------------------------------------------------------------- #
def _client_ip() -> str:
    """IP reelle du client (Cloudflare envoie CF-Connecting-IP en mode tunnel)."""
    if TUNNEL_MODE:
        return request.headers.get("CF-Connecting-IP") or request.remote_addr or ""
    return request.remote_addr or ""


limiter = Limiter(
    key_func=_client_ip,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)


def _is_same_network() -> bool:
    """True si le client est sur le meme LAN que le serveur."""
    if TUNNEL_MODE:
        return False
    client = request.remote_addr or ""
    if client in ("127.0.0.1", "::1"):
        return True
    if not LOCAL_IP or not client:
        return True
    def _prefix(ip: str) -> str:
        parts = ip.split(".")
        return ".".join(parts[:3]) if len(parts) == 4 else ip
    return _prefix(client) == _prefix(LOCAL_IP)


def _is_blocked(ip: str) -> bool:
    with _login_lock:
        rec = _login_attempts.get(ip)
        if not rec:
            return False
        blocked_until = rec.get("blocked_until")
        if blocked_until is None:
            return False
        if blocked_until > time.time():
            return True
        _login_attempts.pop(ip, None)  # verrou expire : on remet a zero
        return False


def _record_failure(ip: str) -> None:
    with _login_lock:
        rec = _login_attempts.setdefault(ip, {"count": 0})
        rec["count"] += 1
        if rec["count"] >= _LOGIN_MAX_ATTEMPTS:
            rec["blocked_until"] = time.time() + _LOGIN_LOCKOUT_SECONDS


def _record_success(ip: str) -> None:
    with _login_lock:
        _login_attempts.pop(ip, None)


# --------------------------------------------------------------------------- #
# Securite : PIN (acces au service)
# --------------------------------------------------------------------------- #
@app.before_request
def require_auth():
    """Protege les routes par le code PIN, sauf connexion et pages admin."""
    if not AUTH_ENABLED:
        return None
    if request.endpoint in OPEN_ENDPOINTS or request.endpoint in ADMIN_ENDPOINTS:
        return None
    if session.get("is_admin"):
        return None
    if session.get("auth"):
        return None
    # Un token valide dans l'URL (via le QR code) connecte automatiquement.
    token = request.args.get("token")
    if token and PIN:
        ip = _client_ip()
        if not _is_blocked(ip) and secrets.compare_digest(token, PIN):
            _record_success(ip)
            session["auth"] = True
            # On nettoie l'URL pour ne pas laisser le token visible/partageable.
            return redirect(request.path)
        _record_failure(ip)
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        ip = _client_ip()
        if _is_blocked(ip):
            error = "Trop de tentatives. Réessaie dans 10 minutes."
        elif PIN and secrets.compare_digest(request.form.get("pin", ""), PIN):
            _record_success(ip)
            session.clear()
            session["auth"] = True
            next_url = request.args.get("next", "")
            parsed = urlparse(next_url)
            safe = not parsed.scheme and not parsed.netloc and next_url.startswith("/")
            return redirect(next_url if safe else url_for("index"))
        else:
            _record_failure(ip)
            error = "Code incorrect"
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("auth", None)
    session.pop("is_admin", None)
    return redirect(url_for("login"))


# --------------------------------------------------------------------------- #
# Securite : administration (cle admin)
# --------------------------------------------------------------------------- #
@app.route("/admin")
def admin():
    if not APPROVAL_ENABLED:
        return "Les autorisations par appareil sont desactivees (--no-approval).", 200
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin.html", lan_url=LAN_URL, pin=PIN, auth=AUTH_ENABLED, tunnel_mode=TUNNEL_MODE)


@app.route("/admin/boot/<token>")
def admin_boot(token: str):
    """Connexion admin automatique via token à usage unique (ouverture locale au démarrage)."""
    global _bootstrap_token
    if not APPROVAL_ENABLED or not _bootstrap_token:
        return redirect(url_for("admin_login"))
    if not secrets.compare_digest(token, _bootstrap_token):
        return redirect(url_for("admin_login"))
    _bootstrap_token = None  # invalidé après premier usage
    session.clear()
    session["is_admin"] = True
    return redirect(url_for("admin"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not APPROVAL_ENABLED:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        ip = _client_ip()
        if _is_blocked(ip):
            error = "Trop de tentatives. Réessaie dans 10 minutes."
        elif ADMIN_KEY and secrets.compare_digest(request.form.get("key", ""), ADMIN_KEY):
            _record_success(ip)
            was_authed = session.get("auth")
            session.clear()
            session["is_admin"] = True
            if was_authed:
                session["auth"] = True
            return redirect(url_for("admin"))
        else:
            _record_failure(ip)
            error = "Clé admin incorrecte"
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))


def _require_admin():
    if not session.get("is_admin"):
        abort(403)


@app.route("/api/admin/devices")
def api_admin_devices():
    _require_admin()
    with _devices_lock:
        items = []
        for did, rec in _devices.items():
            item = dict(rec)
            item["id"] = did
            item["last_seen_human"] = datetime.fromtimestamp(
                rec.get("last_seen", 0)
            ).strftime("%d/%m/%Y %H:%M")
            items.append(item)
    items.sort(key=lambda d: d.get("last_seen", 0), reverse=True)
    return jsonify(items)


@app.route("/api/admin/devices/<device_id>", methods=["POST"])
def api_admin_set(device_id):
    _require_admin()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    with _devices_lock:
        rec = _devices.get(device_id)
        if rec is None and action != "delete":
            abort(404)
        if action == "approve":
            rec["status"] = "approved"
            rec["can_send"] = bool(data.get("can_send", True))
            rec["can_receive"] = bool(data.get("can_receive", True))
        elif action == "update":
            if "can_send" in data:
                rec["can_send"] = bool(data["can_send"])
            if "can_receive" in data:
                rec["can_receive"] = bool(data["can_receive"])
        elif action == "deny":
            rec["status"] = "denied"
            rec["can_send"] = rec["can_receive"] = False
        elif action == "revoke":
            rec["status"] = "pending"
            rec["can_send"] = rec["can_receive"] = False
        elif action == "delete":
            _devices.pop(device_id, None)
        else:
            abort(400)
        save_devices()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Etat d'acces de l'appareil courant
# --------------------------------------------------------------------------- #
@app.route("/api/access-status")
def access_status():
    if not APPROVAL_ENABLED or session.get("is_admin"):
        return jsonify(
            {
                "status": "approved",
                "can_send": True,
                "can_receive": True,
                "admin": bool(session.get("is_admin")),
                "name": "",
            }
        )
    did, _ = get_device()
    if not did:
        return jsonify({"status": "unknown"})
    now = time.time()
    with _devices_lock:
        rec = _devices.get(did)
        if rec is None:
            return jsonify({"status": "unknown"})
        if now - rec.get("last_seen", 0) > 60:
            rec["last_seen"] = now
            save_devices()
        snapshot = dict(rec)
    return jsonify(
        {
            "status": snapshot["status"],
            "can_send": snapshot.get("can_send", False),
            "can_receive": snapshot.get("can_receive", False),
            "admin": False,
            "name": snapshot.get("name", ""),
        }
    )


@app.route("/api/request-access", methods=["POST"])
@limiter.limit("10 per minute")
def request_access():
    if not APPROVAL_ENABLED:
        return jsonify({"ok": True})
    did, _ = get_device()
    if not did:
        return jsonify({"error": "Cookie manquant"}), 400
    name = (request.get_json(silent=True) or {}).get("name", "").strip()[:40]
    mac = get_client_mac()
    with _devices_lock:
        rec = _devices.get(did)  # lecture fraîche sous le verrou
        if rec is None and len(_devices) >= MAX_DEVICES:
            return jsonify({"error": "Capacite maximale atteinte"}), 503
        if rec is None:
            # Vérifie si ce MAC appartient déjà à un appareil approuvé
            existing = find_approved_by_mac(mac) if mac else None
            _devices[did] = {
                "name": name or (existing["name"] if existing else ""),
                "status": "approved" if existing else "pending",
                "can_send": existing["can_send"] if existing else False,
                "can_receive": existing["can_receive"] if existing else False,
                "mac": mac or "",
                "first_seen": time.time(),
                "last_seen": time.time(),
            }
        else:
            if name:
                rec["name"] = name
            if not rec.get("mac") and mac:
                rec["mac"] = mac
            if rec["status"] == "denied":
                rec["status"] = "pending"
            rec["last_seen"] = time.time()
        save_devices()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Utilitaires fichiers
# --------------------------------------------------------------------------- #
def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024.0:
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} Po"


def list_files() -> list:
    files = []
    try:
        entries = os.listdir(SHARE_DIR)
    except OSError:
        return []
    for name in entries:
        path = os.path.join(SHARE_DIR, name)
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            continue
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        files.append(
            {
                "name": name,
                "size": stat.st_size,
                "size_human": human_size(stat.st_size),
                "modified": stat.st_mtime,
                "modified_human": datetime.fromtimestamp(
                    stat.st_mtime
                ).strftime("%d/%m/%Y %H:%M"),
            }
        )
    files.sort(key=lambda f: f["modified"], reverse=True)
    return files


def unique_path(directory: str, filename: str) -> str:
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    base, ext = os.path.splitext(filename)
    i = 1
    while True:
        candidate = os.path.join(directory, f"{base} ({i}){ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


# --------------------------------------------------------------------------- #
# Pages et API de transfert
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    # Pose un cookie stable mais n'enregistre PAS le device tant que
    # l'utilisateur n'a pas explicitement cliqué "Demander l'accès".
    new_cookie = None
    if APPROVAL_ENABLED and not session.get("is_admin"):
        did = request.cookies.get("crow_relay_device")
        if not did:
            did = uuid.uuid4().hex
            new_cookie = did
        else:
            with _devices_lock:
                rec = _devices.get(did)
                if rec:
                    rec["last_seen"] = time.time()
                    save_devices()

    resp = make_response(
        render_template(
            "index.html",
            lan_url=LAN_URL,
            pin=PIN,
            auth=AUTH_ENABLED,
            approval=APPROVAL_ENABLED,
            is_admin=bool(session.get("is_admin")),
            tunnel_mode=TUNNEL_MODE,
        )
    )
    if new_cookie:
        resp.set_cookie(
            "crow_relay_device",
            new_cookie,
            max_age=60 * 60 * 24 * 365,
            samesite="Lax",
            httponly=True,
            secure=bool(app.config.get("SESSION_COOKIE_SECURE")),
        )
    return resp


@app.route("/qr.svg")
def qr_code():
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        abort(503)
    if request.args.get("tunnel"):
        if not TUNNEL_URL:
            abort(503)
        base = TUNNEL_URL
    else:
        base = LAN_URL
    data = base + (f"/?token={PIN}" if AUTH_ENABLED and PIN else "/")
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), mimetype="image/svg+xml")


@app.route("/api/tunnel-url")
def api_tunnel_url():
    _require_admin()
    return jsonify({"url": TUNNEL_URL, "tunnel_mode": TUNNEL_MODE})


@app.route("/api/network-info")
def api_network_info():
    authed = session.get("auth") or session.get("is_admin")
    return jsonify({
        "same_network": _is_same_network(),
        "tunnel_mode": TUNNEL_MODE,
        "tunnel_url": (TUNNEL_URL if TUNNEL_MODE and authed else None),
        "is_admin": bool(session.get("is_admin")),
    })


@app.route("/api/files")
def api_files():
    if not device_allowed("can_receive"):
        return jsonify({"error": "Acces non autorise"}), 403
    return jsonify(list_files())


@app.route("/api/upload", methods=["POST"])
@limiter.limit("30 per minute")
def api_upload():
    if not device_allowed("can_send"):
        return jsonify({"error": "Acces non autorise"}), 403
    if "files" not in request.files:
        return jsonify({"error": "Aucun fichier recu"}), 400

    saved = []
    for storage in request.files.getlist("files"):
        if not storage or storage.filename == "":
            continue
        filename = secure_filename(storage.filename) or "fichier"
        with _upload_lock:
            dest = unique_path(SHARE_DIR, filename)
            storage.save(dest)
        saved.append(os.path.basename(dest))
        app.logger.info("UPLOAD  %s  from %s", os.path.basename(dest), _client_ip())

    if not saved:
        return jsonify({"error": "Aucun fichier valide"}), 400
    return jsonify({"saved": saved})


@app.route("/download/<path:filename>")
def download(filename):
    if not device_allowed("can_receive"):
        abort(403)
    if os.path.islink(os.path.join(SHARE_DIR, secure_filename(filename))):
        abort(404)
    app.logger.info("DOWNLOAD  %s  by %s", filename, _client_ip())
    return send_from_directory(SHARE_DIR, filename, as_attachment=True)


@app.route("/api/admin/clear-files", methods=["POST"])
def api_admin_clear_files():
    _require_admin()
    deleted = 0
    with _upload_lock:
        for name in os.listdir(SHARE_DIR):
            path = os.path.join(SHARE_DIR, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    deleted += 1
                elif os.path.isdir(path):
                    shutil.rmtree(path)
                    deleted += 1
            except OSError:
                pass
    return jsonify({"deleted": deleted})


@app.route("/api/admin/clear-devices", methods=["POST"])
def api_admin_clear_devices():
    _require_admin()
    global _devices
    with _devices_lock:
        _devices = {}
        save_devices()
    return jsonify({"ok": True})


@app.route("/api/delete/<path:filename>", methods=["POST"])
def api_delete(filename):
    if not device_allowed("can_send"):
        return jsonify({"error": "Acces non autorise"}), 403
    safe = secure_filename(filename)
    path = os.path.join(SHARE_DIR, safe)
    with _upload_lock:
        if not os.path.isfile(path):
            abort(404)
        try:
            os.remove(path)
        except FileNotFoundError:
            abort(404)
    return jsonify({"deleted": safe})


# --------------------------------------------------------------------------- #
# Reseau / demarrage
# --------------------------------------------------------------------------- #
def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def ensure_cert(cert_path: str, key_path: str, ip: str) -> None:
    """Genere (ou regenere) un certificat auto-signe valide pour l'IP courante."""
    import datetime as dt
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    # Reutilise le cert existant seulement si l'IP courante est dans son SAN et qu'il
    # n'expire pas dans les 7 prochains jours. Sinon, on en genere un nouveau.
    if os.path.exists(cert_path) and os.path.exists(key_path):
        try:
            with open(cert_path, "rb") as f:
                existing = x509.load_pem_x509_certificate(f.read())
            san_ext = existing.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            covered = {str(a) for a in san_ext.value.get_values_for_type(x509.IPAddress)}
            now = dt.datetime.now(dt.timezone.utc)
            # Compatibilite cryptography < 42 et >= 42
            try:
                expiry = existing.not_valid_after_utc
            except AttributeError:
                expiry = existing.not_valid_after.replace(tzinfo=dt.timezone.utc)
            if ip in covered and expiry > now + dt.timedelta(days=7):
                return
        except Exception:
            pass  # cert illisible ou corrompu — on regenere

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # CN = IP pour que les navigateurs qui verifient le CN soient satisfaits
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])

    san = [x509.DNSName("localhost")]
    for addr in {"127.0.0.1", ip}:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(addr)))
        except ValueError:
            pass

    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(seconds=60))
        # 397 jours : iOS/Chrome rejettent tout cert > 398 jours depuis 2020
        .not_valid_after(now + dt.timedelta(days=397))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    os.chmod(key_path, 0o600)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def start_cloudflared(port: int) -> str | None:
    """Lance cloudflared et retourne l'URL publique une fois disponible."""
    global _cloudflared_proc
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        _cloudflared_proc = proc
        for line in proc.stdout:
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                # Drain stdout en arrière-plan pour éviter le deadlock du pipe OS.
                threading.Thread(target=lambda: proc.stdout.read(), daemon=True).start()
                return url
        return None
    except FileNotFoundError:
        return None


def _auto_shutdown(minutes: int) -> None:
    def _stop():
        print(f"\n  [Crow-Relay] Tunnel expire ({minutes} min). Arret automatique.")
        if _cloudflared_proc:
            _cloudflared_proc.terminate()
        os.kill(os.getpid(), signal.SIGINT)
    threading.Timer(minutes * 60, _stop).start()


def print_banner(url: str, https: bool, no_qr: bool = False) -> None:
    print("\n" + "=" * 50)
    print("  CROW-RELAY - partage de fichiers")
    print("=" * 50)
    print(f"\n  Dossier partage : {SHARE_DIR}")
    if AUTH_ENABLED:
        print(f"\n  Code PIN d'acces    : {PIN}")
    else:
        print("\n  Code PIN : DÉSACTIVÉ (--no-pin)")
    if APPROVAL_ENABLED:
        print(f"  Clé admin           : {ADMIN_KEY}")
        print(f"  Panneau d'autorisation : {url}/admin")
    else:
        print("  Autorisations : DÉSACTIVÉES (--no-approval)")
    if https:
        print("  Chiffrement : HTTPS (certificat auto-signe)")
    print(f"\n  Ouvre cette adresse sur ton telephone :\n\n      {url}\n")
    if https:
        print(
            "  (Certificat auto-signe : le navigateur affichera un avertissement,\n"
            "   accepte-le une fois pour ce reseau de confiance.)\n"
        )

    if not no_qr:
        qr_target = url + (f"/?token={PIN}" if AUTH_ENABLED and PIN else "/")
        try:
            import qrcode  # type: ignore

            qr = qrcode.QRCode(border=1)
            qr.add_data(qr_target)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
            if AUTH_ENABLED:
                print("  (Scanner ce QR connecte le telephone automatiquement)\n")
        except ImportError:
            print("  (Installe le paquet 'qrcode' pour afficher un QR code ici)\n")

    print("  Ctrl+C pour arreter.\n")


def main() -> None:
    global PIN, AUTH_ENABLED, APPROVAL_ENABLED, ADMIN_KEY, LAN_URL, TUNNEL_MODE, TUNNEL_URL, LOCAL_IP, _bootstrap_token

    parser = argparse.ArgumentParser(description="Crow-Relay file transfer service")
    parser.add_argument("--host", default="0.0.0.0", help="Interface d'ecoute")
    parser.add_argument("--port", type=int, default=8000, help="Port d'ecoute")
    parser.add_argument("--pin", help="Code PIN d'acces (sinon genere aleatoirement)")
    parser.add_argument(
        "--no-pin",
        action="store_true",
        help="Desactive le code PIN (reseau de confiance uniquement)",
    )
    parser.add_argument(
        "--admin-key", help="Cle du panneau d'autorisation (sinon generee)"
    )
    parser.add_argument(
        "--no-approval",
        action="store_true",
        help="Desactive l'autorisation par appareil",
    )
    parser.add_argument(
        "--https",
        action="store_true",
        help="Active HTTPS avec un certificat auto-signe (chiffre les transferts)",
    )
    parser.add_argument(
        "--cert", default=os.path.join(BASE_DIR, "cert.pem"), help="Chemin du certificat"
    )
    parser.add_argument(
        "--key", default=os.path.join(BASE_DIR, "key.pem"), help="Chemin de la cle privee"
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Ne pas ouvrir le navigateur automatiquement au demarrage",
    )
    parser.add_argument(
        "--no-qr",
        action="store_true",
        help="Ne pas afficher le QR code dans le terminal",
    )
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Expose le service via Cloudflare Tunnel (internet, sans meme reseau)",
    )
    parser.add_argument(
        "--tunnel-ttl",
        type=int,
        default=0,
        metavar="MIN",
        help="Ferme automatiquement le tunnel apres N minutes (defaut : desactive, ex: 60)",
    )
    parser.add_argument(
        "--clear-devices",
        action="store_true",
        help="Vide la liste des appareils autorises au demarrage",
    )
    parser.add_argument(
        "--max-mb",
        type=int,
        default=0,
        metavar="MB",
        help="Taille max d'un envoi en Mo (ex: 2000 pour 2 Go). Defaut : illimite en LAN, 500 Mo en tunnel.",
    )
    args = parser.parse_args()

    if args.tunnel:
        if args.no_pin:
            parser.error("--no-pin interdit en mode --tunnel (le PIN est obligatoire).")
        if args.no_approval:
            parser.error("--no-approval interdit en mode --tunnel (l'autorisation par appareil est obligatoire).")

    # --max-mb a priorité sur CROW_RELAY_MAX_MB, qui a priorité sur les défauts.
    effective_max_mb = args.max_mb or (int(_max_mb) if _max_mb else 0)

    TUNNEL_MODE = args.tunnel
    if effective_max_mb:
        app.config["MAX_CONTENT_LENGTH"] = effective_max_mb * 1024 * 1024
    elif TUNNEL_MODE:
        # Limite par défaut en tunnel si rien n'est spécifié.
        app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

    # Activer les logs applicatifs (uploads, downloads) en mode tunnel.
    if TUNNEL_MODE:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s  %(levelname)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        app.logger.setLevel(logging.INFO)

    AUTH_ENABLED = not args.no_pin
    if AUTH_ENABLED:
        PIN = (
            args.pin
            or os.environ.get("CROW_RELAY_PIN")
            or f"{secrets.randbelow(1000000):06d}"
        )

    APPROVAL_ENABLED = not args.no_approval
    if APPROVAL_ENABLED:
        ADMIN_KEY = (
            args.admin_key
            or os.environ.get("CROW_RELAY_ADMIN_KEY")
            or secrets.token_hex(8)
        )
        _bootstrap_token = secrets.token_urlsafe(16)

    os.makedirs(SHARE_DIR, exist_ok=True)
    load_devices()
    if args.clear_devices:
        global _devices
        _devices = {}
        save_devices()
        print("  [Crow-Relay] Liste des appareils videe (--clear-devices).")

    local_ip = get_local_ip()
    LOCAL_IP = local_ip

    ssl_context = None
    scheme = "http"
    if args.https:
        try:
            ensure_cert(args.cert, args.key, local_ip)
        except ImportError:
            parser.error(
                "HTTPS requiert le paquet 'cryptography' (pip install cryptography)"
            )
        ssl_context = (args.cert, args.key)
        scheme = "https"
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # En mode tunnel, on ne force PAS SESSION_COOKIE_SECURE : Cloudflare gère
    # le TLS côté public, mais l'URL locale reste HTTP. Marquer les cookies
    # Secure bloquerait les connexions locales (le navigateur refuse d'envoyer
    # un cookie Secure sur HTTP, même en LAN).

    LAN_URL = f"{scheme}://{local_ip}:{args.port}"
    print_banner(LAN_URL, args.https, no_qr=args.no_qr or args.tunnel)

    if not args.no_open:
        # Ouvre le panneau admin via un token à usage unique (la clé admin ne passe jamais par l'URL).
        if APPROVAL_ENABLED:
            open_url = f"http://127.0.0.1:{args.port}/admin/boot/{_bootstrap_token}"
        else:
            token_suffix = f"?token={PIN}" if AUTH_ENABLED and PIN else ""
            open_url = f"http://127.0.0.1:{args.port}/{token_suffix}"
        threading.Timer(0.8, webbrowser.open, args=[open_url]).start()

    if TUNNEL_MODE:
        if not shutil.which("cloudflared"):
            print(
                "\n  ERREUR : cloudflared introuvable.\n"
                "  Installe-le depuis :\n"
                "  https://developers.cloudflare.com/cloudflare-one/connections/"
                "connect-networks/downloads/\n"
            )
            sys.exit(1)

        port = args.port
        ttl = args.tunnel_ttl

        def _launch_tunnel():
            global TUNNEL_URL
            print("  Tunnel Cloudflare en cours de demarrage...\n")
            url = start_cloudflared(port)
            if url:
                TUNNEL_URL = url
                pin_line = f"\n  Code PIN   : {PIN}" if AUTH_ENABLED else ""
                ttl_line = f"\n  Expiration : dans {ttl} min" if ttl else ""
                print(f"\n{'='*50}")
                print("  Tunnel actif - partage cette adresse :")
                print(f"\n      {url}{pin_line}{ttl_line}")
                print(f"{'='*50}\n")
            else:
                print("\n  [Crow-Relay] Tunnel Cloudflare interrompu.\n")

        threading.Thread(target=_launch_tunnel, daemon=True).start()
        if ttl:
            _auto_shutdown(ttl)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("cheroot.error").setLevel(logging.WARNING)

    server = WSGIServer((args.host, args.port), app, numthreads=8)
    if ssl_context:
        from cheroot.ssl.builtin import BuiltinSSLAdapter
        server.ssl_adapter = BuiltinSSLAdapter(*ssl_context)

    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
