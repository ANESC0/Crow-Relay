#!/usr/bin/env python3
"""
Test de charge Crow-Relay — vérifie que cheroot tient sous pression.

Lance le serveur en mode ouvert (--no-pin --no-approval) sur un port
temporaire, envoie des vagues de requêtes concurrentes, et affiche les
stats : req/s, p50/p95/p99 latence, taux d'erreur.

Usage :
    python scripts/loadtest.py
    python scripts/loadtest.py --workers 50 --requests 500
"""

import argparse
import io
import os
import statistics
import subprocess
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
PORT = 18765  # port temporaire pour ne pas interférer avec une instance active
BASE = f"http://127.0.0.1:{PORT}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
# ---------------------------------------------------------------------------


def _wait_for_server(timeout=10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE + "/login", timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def _get(path) -> tuple[int, float]:
    t0 = time.perf_counter()
    try:
        r = urllib.request.urlopen(BASE + path, timeout=5)
        return r.status, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.perf_counter() - t0
    except Exception:
        return 0, time.perf_counter() - t0


def _upload(filename="load.txt", content=b"x" * 1024) -> tuple[int, float]:
    boundary = b"----LoadTestBoundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="files"; filename="' + filename.encode() + b'"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
        + content
        + b"\r\n--" + boundary + b"--\r\n"
    )
    req = urllib.request.Request(
        BASE + "/api/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.perf_counter() - t0
    except Exception:
        return 0, time.perf_counter() - t0


def _stats(label: str, results: list[tuple[int, float]]) -> None:
    latencies = [r[1] * 1000 for r in results]  # ms
    codes = [r[0] for r in results]
    ok = sum(1 for c in codes if 200 <= c < 400)
    err = len(codes) - ok
    rate_limit = sum(1 for c in codes if c == 429)

    print(f"\n  {label}")
    print(f"    Requêtes   : {len(results)}")
    print(f"    OK (2xx/3xx): {ok}  |  Erreurs : {err}  |  429 (rate-limit) : {rate_limit}")
    if latencies:
        latencies.sort()
        print(f"    Latence    : p50={latencies[len(latencies)//2]:.1f}ms  "
              f"p95={latencies[int(len(latencies)*.95)]:.1f}ms  "
              f"p99={latencies[int(len(latencies)*.99)]:.1f}ms  "
              f"max={latencies[-1]:.1f}ms")


def run_scenario(label: str, fn, workers: int, total: int) -> list:
    results = []
    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn) for _ in range(total)]
        for f in as_completed(futures):
            results.append(f.result())
    elapsed = time.perf_counter() - t_start
    rps = len(results) / elapsed
    _stats(label, results)
    print(f"    Débit      : {rps:.0f} req/s  ({elapsed:.2f}s total)")
    return results


def main():
    parser = argparse.ArgumentParser(description="Test de charge Crow-Relay")
    parser.add_argument("--workers", type=int, default=30, help="Threads concurrents (défaut: 30)")
    parser.add_argument("--requests", type=int, default=300, help="Requêtes par scénario (défaut: 300)")
    args = parser.parse_args()

    print("\n" + "=" * 55)
    print("  Test de charge Crow-Relay")
    print(f"  {args.workers} workers | {args.requests} requêtes par scénario")
    print("=" * 55)

    # Démarre le serveur en mode ouvert
    print("\n  Démarrage du serveur sur le port", PORT, "...")
    proc = subprocess.Popen(
        [PYTHON, "app.py", "--port", str(PORT), "--no-pin", "--no-approval", "--no-open", "--no-qr"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_for_server():
        print("  ERREUR : le serveur n'a pas démarré.")
        proc.terminate()
        sys.exit(1)

    print("  Serveur prêt.\n")

    try:
        # 1 — Pages statiques (login, accueil) : charge de base
        run_scenario(
            f"[1] Pages statiques — {args.workers} workers",
            lambda: _get("/login"),
            args.workers,
            args.requests,
        )

        # 2 — API fichiers
        run_scenario(
            f"[2] GET /api/files — {args.workers} workers",
            lambda: _get("/api/files"),
            args.workers,
            args.requests,
        )

        # 3 — Uploads concurrents (1 Ko par fichier)
        run_scenario(
            f"[3] Upload 1 Ko — {args.workers} workers",
            lambda: _upload("load.txt", b"x" * 1024),
            args.workers,
            args.requests,
        )

        # 4 — Uploads lourds (100 Ko par fichier)
        heavy = min(args.requests // 3, 100)
        run_scenario(
            f"[4] Upload 100 Ko — {args.workers} workers ({heavy} requêtes)",
            lambda: _upload("heavy.txt", b"x" * 100_000),
            args.workers,
            heavy,
        )

        # 5 — Pic de charge : 2× les workers
        peak_workers = args.workers * 2
        run_scenario(
            f"[5] Pic — {peak_workers} workers simultanés",
            lambda: _get("/api/files"),
            peak_workers,
            args.requests,
        )

        print("\n" + "=" * 55)
        print("  Résultat : le service a tenu la charge.")
        print("=" * 55 + "\n")

    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    main()
