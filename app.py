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
import ipaddress
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
# Schema/port retenus au demarrage pour reconstruire l'URL LAN a la volee.
LAN_SCHEME = "http"
LAN_PORT = 8000
# Si --host vise une IP concrete, on annonce exactement cette IP (interface
# choisie par l'utilisateur) et on ne rafraichit pas dynamiquement.
HOST_PIN: str | None = None

# Cache court de la detection d'IP : evite de rouvrir un socket a chaque requete
# tout en laissant l'URL/QR se corriger si l'IP change (DHCP, VPN, changement de
# reseau) sans relancer le service.
_ip_cache: dict = {"primary": "", "ips": [], "ts": 0.0}
_ip_cache_lock = threading.Lock()
_IP_CACHE_TTL = 30  # secondes

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
_activity_log: list = []
_activity_lock = threading.Lock()
# Token à usage unique généré au démarrage pour ouvrir le panneau admin sans
# mettre la clé admin dans l'URL (et donc dans l'historique du navigateur).
_bootstrap_token: str | None = None



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


def _arp_lookup(ip: str) -> str | None:
    """Lookup MAC dans le cache ARP du système. Multi-plateforme.

    Linux  : /proc/net/arp (lecture directe, sans fork).
    macOS  : arp -n <ip>
    Windows: arp -a <ip>
    Normalise le résultat en XX:XX:XX:XX:XX:XX majuscules.
    """
    # Linux — lecture directe, la plus rapide
    try:
        with open("/proc/net/arp", encoding="ascii") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip:
                    mac = parts[3].upper()
                    if mac != "00:00:00:00:00:00":
                        return mac
        return None  # Linux mais IP absente du cache
    except OSError:
        pass  # pas Linux → commande système

    # macOS / Windows
    try:
        cmd = ["arp", "-n", ip] if sys.platform == "darwin" else ["arp", "-a", ip]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=2)
        text = out.decode(errors="replace")
        # Accepte les deux séparateurs (: et -) et les octets à 1 ou 2 chiffres hex
        m = re.search(r"(?:[\da-fA-F]{1,2}[:\-]){5}[\da-fA-F]{1,2}", text)
        if m:
            raw = m.group(0).upper().replace("-", ":")
            mac = ":".join(o.zfill(2) for o in raw.split(":"))
            if mac != "00:00:00:00:00:00":
                return mac
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def get_client_mac() -> str | None:
    """Retourne l'adresse MAC du client via le cache ARP (LAN uniquement).
    Indisponible en mode tunnel (l'IP vue est celle de Cloudflare)."""
    if TUNNEL_MODE:
        return None
    ip = request.remote_addr or ""
    if ip in ("127.0.0.1", "::1", ""):
        return None
    return _arp_lookup(ip)


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
    if not client:
        return False
    def _prefix(ip: str) -> str:
        parts = ip.split(".")
        return ".".join(parts[:3]) if len(parts) == 4 else ip
    # Compare le client au prefixe /24 de chacune des cartes LAN de l'hote
    # (et non d'une seule), pour rester correct en multi-cartes.
    host_ips = current_lan_ips() or ([LOCAL_IP] if LOCAL_IP else [])
    cp = _prefix(client)
    return any(cp == _prefix(h) for h in host_ips)


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
            session.clear()
            session["auth"] = True
            # On nettoie l'URL pour ne pas laisser le token visible/partageable.
            return redirect(request.path)
        if not _is_blocked(ip):
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
    return render_template("admin.html", lan_url=current_lan_url(), pin=PIN, auth=AUTH_ENABLED, tunnel_mode=TUNNEL_MODE)


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
            rec["can_send"] = bool(data.get("can_send", False))
            rec["can_receive"] = bool(data.get("can_receive", False))
        elif action == "update":
            if rec.get("status") != "approved":
                abort(400)
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
@limiter.limit("30 per minute")
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
        if now - rec.get("last_seen", 0) > 300:
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
    ip = _client_ip()
    with _devices_lock:
        rec = _devices.get(did)  # lecture fraîche sous le verrou
        if rec is None and len(_devices) >= MAX_DEVICES:
            return jsonify({"error": "Capacite maximale atteinte"}), 503
        if rec is None:
            pending_from_ip = sum(
                1 for d in _devices.values()
                if d.get("status") == "pending" and d.get("ip") == ip
            )
            if pending_from_ip >= 3:
                return jsonify({"error": "Trop de demandes en attente depuis cette IP"}), 429
            # Le MAC sert uniquement à pré-remplir le nom (ergonomie) — jamais à auto-approuver.
            # L'auto-approbation par MAC permettrait à n'importe quel appareil du LAN de
            # contourner l'approbation en usurpant l'adresse MAC d'un appareil déjà approuvé.
            existing = find_approved_by_mac(mac) if mac else None
            _devices[did] = {
                "name": name or (existing["name"] if existing else ""),
                "status": "pending",
                "can_send": False,
                "can_receive": False,
                "mac": mac or "",
                "ip": ip,
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


def _add_activity(action: str, filename: str) -> None:
    if session.get("is_admin"):
        actor = "Admin"
    else:
        _, rec = get_device()
        actor = (rec.get("name") or "Appareil").strip() if rec else "Appareil"
    entry = {"action": action, "filename": filename, "actor": actor, "ts": time.time()}
    with _activity_lock:
        _activity_log.append(entry)
        if len(_activity_log) > 50:
            del _activity_log[:-50]


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
            lan_url=current_lan_url(),
            lan_ips=current_lan_ips(),
            lan_scheme=LAN_SCHEME,
            lan_port=LAN_PORT,
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
        base = current_lan_url()
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
        "lan_url": (current_lan_url() if not TUNNEL_MODE or authed else None),
    })


@app.route("/api/files")
def api_files():
    if not device_allowed("can_receive"):
        return jsonify({"error": "Acces non autorise"}), 403
    return jsonify(list_files())


@app.route("/api/activity")
def api_activity():
    if APPROVAL_ENABLED and not session.get("is_admin"):
        _, rec = get_device()
        if not rec or rec.get("status") != "approved":
            return jsonify([])
        if not rec.get("can_send") and not rec.get("can_receive"):
            return jsonify([])
    with _activity_lock:
        return jsonify(list(reversed(_activity_log[-20:])))


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
        _add_activity("upload", os.path.basename(dest))

    if not saved:
        return jsonify({"error": "Aucun fichier valide"}), 400
    return jsonify({"saved": saved})


@app.route("/download/<path:filename>")
def download(filename):
    if not device_allowed("can_receive"):
        abort(403)
    safe = secure_filename(filename)
    if not safe:
        abort(404)
    if os.path.islink(os.path.join(SHARE_DIR, safe)):
        abort(404)
    app.logger.info("DOWNLOAD  %s  by %s", safe, _client_ip())
    return send_from_directory(SHARE_DIR, safe, as_attachment=True)


@app.route("/api/admin/clear-files", methods=["POST"])
def api_admin_clear_files():
    _require_admin()
    deleted = 0
    with _upload_lock:
        for name in os.listdir(SHARE_DIR):
            path = os.path.join(SHARE_DIR, name)
            try:
                if os.path.islink(path):
                    os.remove(path)
                    deleted += 1
                elif os.path.isfile(path):
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
        if os.path.islink(path) or not os.path.isfile(path):
            abort(404)
        try:
            os.remove(path)
        except FileNotFoundError:
            abort(404)
    _add_activity("delete", safe)
    return jsonify({"deleted": safe})


# --------------------------------------------------------------------------- #
# Reseau / demarrage
# --------------------------------------------------------------------------- #
def _route_ip() -> str | None:
    """IP de l'interface qui sort vers internet (route par defaut), ou None.

    N'envoie aucun paquet : connect() en UDP ne fait que choisir l'interface
    selon la table de routage. Avec plusieurs cartes, c'est celle qui porte la
    route par defaut (souvent un VPN si un VPN est actif)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _is_lan_ipv4(ip: str) -> bool:
    """True si l'IP est une adresse LAN privee utilisable (exclut loopback,
    link-local/APIPA et les IP publiques/VPN hors plages privees)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local


def _discover_ips() -> tuple[str, list]:
    """Retourne (ip_principale, liste des IP LAN privees). Best-effort.

    - L'IP principale est l'IP de la route internet si elle est privee ; sinon
      (route via VPN/IP publique, ou hors-ligne) on bascule sur une vraie IP LAN.
    - L'enumeration via getaddrinfo(hostname) est "au mieux" : si elle echoue ou
      ne renvoie rien d'exploitable, on retombe proprement sur l'IP principale.
    """
    route = _route_ip()
    candidates: list = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass

    lan: list = []
    for ip in ([route] if route else []) + candidates:
        if ip and _is_lan_ipv4(ip) and ip not in lan:
            lan.append(ip)

    if route and _is_lan_ipv4(route):
        primary = route                 # cas normal : IP vers internet, privee
    elif lan:
        primary = lan[0]                # route = VPN/publique -> vraie IP LAN
    elif route:
        primary = route                 # rien de prive : mieux que loopback
    else:
        primary = "127.0.0.1"           # hors-ligne total
    if _is_lan_ipv4(primary) and primary not in lan:
        lan.insert(0, primary)
    return primary, lan


def _ip_snapshot() -> tuple[str, list]:
    """(ip_principale, IP LAN) avec cache court (_IP_CACHE_TTL)."""
    now = time.time()
    with _ip_cache_lock:
        if _ip_cache["primary"] and now - _ip_cache["ts"] < _IP_CACHE_TTL:
            return _ip_cache["primary"], list(_ip_cache["ips"])
    primary, ips = _discover_ips()
    with _ip_cache_lock:
        _ip_cache["primary"] = primary
        _ip_cache["ips"] = ips
        _ip_cache["ts"] = now
    return primary, ips


def get_local_ip() -> str:
    """IP principale a annoncer (frais, sans cache : usage demarrage/cert)."""
    return _discover_ips()[0]


def current_lan_ips() -> list:
    """IP LAN a afficher. Si --host vise une IP concrete, on n'annonce qu'elle."""
    if HOST_PIN:
        return [HOST_PIN]
    return _ip_snapshot()[1]


def current_lan_url() -> str:
    """URL LAN courante (rafraichie via cache court, sauf --host fige)."""
    if HOST_PIN:
        return f"{LAN_SCHEME}://{HOST_PIN}:{LAN_PORT}"
    return f"{LAN_SCHEME}://{_ip_snapshot()[0]}:{LAN_PORT}"


def _choose_listen_ip(ips: list) -> str | None:
    """Menu interactif : sur quelle IP ecouter (option --pick-host).

    Retourne l'IP choisie, ou None pour "toutes les cartes". Si moins de deux
    IP LAN sont disponibles, ne demande rien (None) : aucun friction sur un PC
    a une seule carte."""
    lan = [ip for ip in ips if _is_lan_ipv4(ip)]
    if len(lan) < 2:
        return None
    print("\n  Plusieurs cartes reseau detectees. Sur quelle adresse ecouter ?")
    print("    0) Toutes les cartes  [defaut]")
    for i, ip in enumerate(lan, 1):
        print(f"    {i}) {ip}")
    try:
        choice = input(f"  Ton choix [0-{len(lan)}, defaut 0] : ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if choice.isdigit():
        n = int(choice)
        if 1 <= n <= len(lan):
            return lan[n - 1]
    return None


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
    if sys.platform != "win32":
        os.chmod(key_path, 0o600)
    else:
        _win_user = os.environ.get("USERNAME") or os.environ.get("USER", "")
        if _win_user:
            subprocess.run(
                ["icacls", key_path, "/inheritance:r",
                 "/grant:r", f"{_win_user}:(R,W)"],
                check=False, capture_output=True,
            )
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


def print_banner(url: str, https: bool, port: int, no_qr: bool = False) -> None:
    scheme = "https" if https else "http"
    # Si l'ecoute est bridee sur une carte precise, 127.0.0.1 ne repond pas :
    # l'acces local passe alors par cette IP.
    local_host = HOST_PIN if (HOST_PIN and HOST_PIN != "127.0.0.1") else "127.0.0.1"
    local_url = f"{scheme}://{local_host}:{port}"

    print("\n" + "=" * 50)
    print("  CROW-RELAY - partage de fichiers")
    print("=" * 50)
    print(f"\n  Dossier partage  : {SHARE_DIR}")
    if AUTH_ENABLED:
        print(f"\n  Code PIN         : {PIN}")
    else:
        print("\n  Code PIN         : DESACTIVE (--no-pin)")
    if APPROVAL_ENABLED:
        print(f"  Cle admin        : {ADMIN_KEY}")
    else:
        print("  Autorisations    : DESACTIVEES (--no-approval)")

    if TUNNEL_MODE:
        print(f"\n  Acces admin      : {local_url}/admin")
        print("\n  En attente de l'URL Cloudflare Tunnel...")
        print("  (L'adresse de partage s'affichera dans quelques secondes)\n")
    else:
        print(f"\n  Acces reseau     : {url}")
        # Plusieurs cartes (Ethernet + Wi-Fi...) : liste les autres adresses LAN
        # pour que l'utilisateur sache sur quoi le partage est joignable.
        others = [
            f"{scheme}://{ip}:{port}"
            for ip in current_lan_ips()
            if f"://{ip}:" not in url
        ]
        if others:
            print(f"  Autres adresses  : {', '.join(others)}")
        print(f"  Acces local      : {local_url}")
        if APPROVAL_ENABLED:
            print(f"  Panneau admin    : {url}/admin")
        if https:
            print("  Chiffrement      : HTTPS (certificat auto-signe)")
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
    global LAN_SCHEME, LAN_PORT, HOST_PIN

    parser = argparse.ArgumentParser(description="Crow-Relay file transfer service")
    parser.add_argument("--host", default="0.0.0.0", help="Interface d'ecoute")
    parser.add_argument("--port", type=int, default=8000, help="Port d'ecoute")
    parser.add_argument(
        "--pick-host",
        action="store_true",
        help="Si plusieurs cartes reseau : demande sur quelle IP ecouter (LAN)",
    )
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
        if args.host != "127.0.0.1":
            print(f"  [!] --host {args.host!r} ignoré en mode --tunnel : écoute forcée sur 127.0.0.1", flush=True)
            args.host = "127.0.0.1"

    # --max-mb a priorité sur CROW_RELAY_MAX_MB, qui a priorité sur les défauts.
    if _max_mb and not _max_mb.isdigit():
        parser.error(f"CROW_RELAY_MAX_MB='{_max_mb}' doit être un entier positif")
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
    with _devices_lock:
        save_devices()

    # --host avec une IP concrete (ex: --host 192.168.1.50) : l'utilisateur
    # choisit la carte ; on annonce exactement cette IP (et on ne la rafraichit
    # pas). Avec 0.0.0.0 (defaut), on ecoute sur toutes les cartes et on detecte
    # l'IP a annoncer dynamiquement.
    if args.host not in ("", "0.0.0.0", "::"):
        HOST_PIN = args.host
    # --pick-host : si plusieurs cartes et qu'aucune n'est deja imposee, on
    # demande sur laquelle ecouter. Le choix BORNE l'ecoute a cette carte
    # (bind) et l'annonce. Ignore en mode tunnel.
    if args.pick_host and not HOST_PIN and not TUNNEL_MODE:
        chosen = _choose_listen_ip(current_lan_ips())
        if chosen:
            args.host = chosen
            HOST_PIN = chosen
    local_ip = HOST_PIN or get_local_ip()
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

    LAN_SCHEME = scheme
    LAN_PORT = args.port
    LAN_URL = current_lan_url()
    print_banner(LAN_URL, args.https, args.port, no_qr=args.no_qr or args.tunnel)

    if not args.no_open:
        # Ouvre le panneau admin via un token à usage unique (la clé admin ne passe jamais par l'URL).
        # Si on est bridé sur une carte precise, 127.0.0.1 n'ecoute pas : on
        # ouvre alors sur l'IP bindee.
        _scheme = "https" if args.https else "http"
        _open_host = HOST_PIN if (HOST_PIN and HOST_PIN != "127.0.0.1") else "127.0.0.1"
        if APPROVAL_ENABLED:
            open_url = f"{_scheme}://{_open_host}:{args.port}/admin/boot/{_bootstrap_token}"
        else:
            token_suffix = f"?token={PIN}" if AUTH_ENABLED and PIN else ""
            open_url = f"{_scheme}://{_open_host}:{args.port}/{token_suffix}"
        threading.Timer(0.8, webbrowser.open_new_tab, args=[open_url]).start()

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
                _scheme = "https" if args.https else "http"
                _local = f"{_scheme}://127.0.0.1:{port}"
                print(f"\n{'='*50}")
                print("  Tunnel actif — partage cette adresse :\n")
                print(f"      {url}\n")
                if AUTH_ENABLED:
                    print(f"  Code PIN     : {PIN}")
                if APPROVAL_ENABLED:
                    print(f"  Panneau      : {url}/admin")
                    print(f"  Admin local  : {_local}/admin")
                else:
                    print(f"  Acces local  : {_local}")
                if ttl:
                    print(f"  Expiration   : dans {ttl} min")
                print(f"{'='*50}\n")
            else:
                print("\n  [Crow-Relay] Tunnel Cloudflare interrompu.\n")

        threading.Thread(target=_launch_tunnel, daemon=True).start()
        if ttl:
            _auto_shutdown(ttl)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    class _NoSSLNoise(logging.Filter):
        _drop = ("TLS connection", "SSLV3", "CERTIFICATE", "HTTP_REQUEST")
        def filter(self, record):
            m = record.getMessage()
            return not any(p in m for p in self._drop)
    logging.getLogger("cheroot.error").addFilter(_NoSSLNoise())

    # Les tracebacks "Exception ignored … Bad file descriptor" lors du nettoyage
    # des sockets TLS fermés arrivent directement sur sys.stderr (hors logging).
    class _FilteredStderr:
        _drop = ("Bad file descriptor", "Exception ignored while calling deallocator",
                 "cheroot/makefile", "_flush_unlocked", "IOBase.__del__")
        def __init__(self, orig):
            self._orig = orig
            self._buf = ""
        def write(self, s):
            self._buf += s
            if "\n" in self._buf:
                lines = self._buf.split("\n")
                self._buf = lines[-1]
                for line in lines[:-1]:
                    if not any(p in line for p in self._drop):
                        self._orig.write(line + "\n")
        def flush(self): self._orig.flush()
        def __getattr__(self, n): return getattr(self._orig, n)
    sys.stderr = _FilteredStderr(sys.stderr)

    server = WSGIServer((args.host, args.port), app, numthreads=8)
    if ssl_context:
        from cheroot.ssl.builtin import BuiltinSSLAdapter
        server.ssl_adapter = BuiltinSSLAdapter(*ssl_context)

    def _shutdown(sig, frame):  # noqa: ARG001
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _shutdown)

    try:
        server.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.stop()
        if _cloudflared_proc:
            _cloudflared_proc.terminate()


if __name__ == "__main__":
    main()
