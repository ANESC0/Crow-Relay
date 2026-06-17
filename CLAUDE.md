# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install all dependencies (including dev)
pip install -r requirements-dev.txt

# Run the full test suite
pytest tests/ -v

# Run a single test file
pytest tests/test_auth.py -v

# Run a single test by name
pytest tests/test_scenarios.py::TestLANHappyPath::test_full_flow_pin_approval_upload_download -v

# Run the app
python3 app.py [--port 8000] [--no-pin] [--no-approval] [--https] [--tunnel]
```

## Architecture

The entire backend lives in `app.py` — a single Flask application served by **cheroot** (WSGI, 8 threads). There is no database; state is kept in two places:

- **`_devices` dict** (in-memory + `devices.json` on disk): tracks every device that has ever connected, its approval status, and its permissions (`can_send`, `can_receive`). Writes are done atomically via a `.tmp` rename.
- **`_login_attempts` dict** (in-memory only): brute-force counters, reset on restart.

### Security model — two independent layers

1. **PIN** (`AUTH_ENABLED` / `require_auth` before_request hook): gates the entire service. Stored in `session["auth"]`. Token in URL (QR code) also satisfies this check.
2. **Device approval** (`APPROVAL_ENABLED` / `device_allowed()`): each device gets a UUID cookie (`crow_relay_device`). The device record must exist in `_devices` with `status="approved"` and the right `can_send`/`can_receive` flag before any transfer is allowed. The admin panel (protected by `ADMIN_KEY`) manages these records.

Admin sessions use a separate `session["is_admin"]` flag and bypass device approval entirely. A one-time bootstrap token (`_bootstrap_token`) lets the browser open the admin panel at startup without putting `ADMIN_KEY` in the URL.

### Route/endpoint classification

- `OPEN_ENDPOINTS` — accessible without PIN: `login`, `static`, `api_network_info`
- `ADMIN_ENDPOINTS` — manage their own auth via `_require_admin()`: everything under `/admin` and `/api/admin/`
- Everything else — requires PIN session, then optionally device approval per endpoint

### Key global state (set in `main()`, used everywhere)

`PIN`, `AUTH_ENABLED`, `APPROVAL_ENABLED`, `ADMIN_KEY`, `LAN_URL`, `TUNNEL_MODE`, `TUNNEL_URL`, `LOCAL_IP`

### Test fixtures (`tests/conftest.py`)

- `reset_state` (autouse): resets all globals + clears `_devices`/`_login_attempts` between tests; sets `SHARE_DIR` to a temp dir. Auth and approval are **disabled by default** — tests that need them enable them explicitly in `setup_method` or at the top of the test.
- `approved_device`: client with a pre-populated approved device record and the matching cookie already set.
- `authed_client`: client that has already POSTed the correct PIN.

### Tunnel mode

`--tunnel` spawns `cloudflared` as a subprocess, parses its stdout for the `.trycloudflare.com` URL, and stores it in `TUNNEL_URL`. `--no-pin` and `--no-approval` are rejected at startup in tunnel mode. `SESSION_COOKIE_SECURE` is intentionally **not** set in tunnel mode (Cloudflare handles TLS externally; the local URL remains HTTP).

### MAC-based device re-recognition

On LAN (non-tunnel), the server reads `/proc/net/arp` to get the client's MAC address. If a device with a cleared cookie re-requests access, `find_approved_by_mac()` matches it to an already-approved record and auto-approves the new cookie — without host intervention.
