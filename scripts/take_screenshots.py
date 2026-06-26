#!/usr/bin/env python3
"""
Prend toutes les captures d'écran de la doc via Playwright.
Lance les serveurs Flask en subprocess, prend les screenshots, nettoie.
Usage : python3 scripts/take_screenshots.py
"""
import os
import sys
import time
import subprocess
import signal
import tempfile
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT    = Path(__file__).parent.parent
OUT     = ROOT / "docs" / "screenshots"
APP     = str(ROOT / "app.py")
PY      = sys.executable
MOBILE  = {"width": 390, "height": 844}
DESKTOP = {"width": 1400, "height": 860}

# Ports suffisamment hauts pour éviter les conflits avec une instance en cours
PORTS = {"home": 19701, "login": 19702, "admin": 19703, "recv": 19704, "gate": 19705}


def wait_server(port, timeout=12):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"Serveur sur port {port} n'a pas démarré")


def start(port, args, share_dir, devices_dir):
    env = {**os.environ,
           "CROW_RELAY_SHARE_DIR": str(share_dir),
           "CROW_RELAY_DEVICES_FILE": str(devices_dir / f"devices_{port}.json")}
    proc = subprocess.Popen(
        [PY, APP, "--port", str(port), "--no-open", "--no-qr"] + args,
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_server(port)
    time.sleep(0.2)
    return proc


def stop(proc):
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    time.sleep(0.3)


def shot(page, path, *, full=False):
    time.sleep(0.3)
    page.screenshot(path=str(path), full_page=full)
    print(f"  ✓ {path.name}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    share   = Path(tempfile.mkdtemp(prefix="crow_share_"))
    devdir  = Path(tempfile.mkdtemp(prefix="crow_dev_"))

    # Fichiers factices pour la vue "Recevoir"
    (share / "photo-vacances.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 2000)
    (share / "rapport-2026.pdf").write_bytes(b"%PDF-1.4" + b"\x00" * 5000)
    (share / "archive.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 3000)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()

            # ── 01 Accueil mobile (onglet Envoyer) ───────────────────────
            print("→ 01 Accueil mobile")
            proc = start(PORTS["home"], ["--no-pin", "--no-approval"], share, devdir)
            ctx  = browser.new_context(viewport=MOBILE, device_scale_factor=2)
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{PORTS['home']}/")
            page.wait_for_selector(".tabs")
            page.locator('.tab[data-panel="send"]').click()
            shot(page, OUT / "01-accueil-mobile.png")
            ctx.close()
            stop(proc)

            # ── 02 Connexion PIN mobile ───────────────────────────────────
            print("→ 02 Connexion PIN mobile")
            proc = start(PORTS["login"], ["--pin", "471006"], share, devdir)
            ctx  = browser.new_context(viewport=MOBILE, device_scale_factor=2)
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{PORTS['login']}/login")
            page.wait_for_selector(".pin-input")
            shot(page, OUT / "02-connexion-mobile.png")
            ctx.close()
            stop(proc)

            # ── 03 Panneau admin desktop ──────────────────────────────────
            print("→ 03 Admin desktop")
            proc = start(PORTS["admin"],
                         ["--no-pin", "--admin-key", "demo-admin-key"], share, devdir)
            port_a = PORTS["admin"]
            for name in ["iPhone de Marie", "Laptop de Paul", "iPad Pro"]:
                c = browser.new_context(viewport=MOBILE)
                pg = c.new_page()
                pg.goto(f"http://127.0.0.1:{port_a}/")
                pg.wait_for_selector("#gatePending", state="visible", timeout=8000)
                pg.fill("#deviceName", name)
                pg.click("#requestBtn")
                pg.wait_for_timeout(400)
                c.close()
            ctx_a = browser.new_context(viewport=DESKTOP)
            page_a = ctx_a.new_page()
            page_a.goto(f"http://127.0.0.1:{port_a}/admin/login")
            page_a.fill('input[name="key"]', "demo-admin-key")
            page_a.click('button[type="submit"]')
            page_a.wait_for_selector(".sections-grid")
            page_a.wait_for_timeout(800)
            page_a.locator('button[data-act="approve"]').first.click()
            page_a.wait_for_timeout(700)
            shot(page_a, OUT / "03-admin-desktop.png", full=True)
            ctx_a.close()
            stop(proc)

            # ── 04 Recevoir mobile ────────────────────────────────────────
            print("→ 04 Recevoir mobile")
            proc = start(PORTS["recv"], ["--no-pin", "--no-approval"], share, devdir)
            ctx  = browser.new_context(viewport=MOBILE, device_scale_factor=2)
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{PORTS['recv']}/")
            page.wait_for_selector(".tabs")
            page.locator('.tab[data-panel="receive"]').click()
            page.wait_for_selector(".item, .empty", timeout=5000)
            shot(page, OUT / "04-recevoir-mobile.png")
            ctx.close()
            stop(proc)

            # ── 05 Gate — demande d'accès mobile ─────────────────────────
            print("→ 05 Gate demande d'accès mobile")
            proc = start(PORTS["gate"], ["--no-pin"], share, devdir)
            ctx  = browser.new_context(viewport=MOBILE, device_scale_factor=2)
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{PORTS['gate']}/")
            page.wait_for_selector("#gatePending", state="visible", timeout=8000)
            shot(page, OUT / "05-autorisation-mobile.png")
            ctx.close()
            stop(proc)

            browser.close()
    finally:
        shutil.rmtree(share, ignore_errors=True)
        shutil.rmtree(devdir, ignore_errors=True)

    print("\nDone — captures dans docs/screenshots/")


if __name__ == "__main__":
    main()
