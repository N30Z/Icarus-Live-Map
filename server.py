"""
Icarus Live Map — combined HTTP server + save parser.

Start:  python server.py          # normal mode (opens browser, polls mod file)
        python server.py --serv   # server mode (no browser, no polling, DLL pushes via POST)

Endpoints:
  GET  /api/state        — players (live or GD.json), geysers, caves, deposits
  GET  /players.json     — live player positions
  POST /players          — DLL pushes live positions (--serv mode)
  GET  /api/pull         — pulls live_players.json from Docker container (auto-discovers)
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import parse_players as pp

PORT      = int(os.environ.get("MAP_PORT", 9090))
GD_FILE   = "GD.json"
SERV_MODE = "--serv" in sys.argv  # no browser, no polling — DLL pushes via POST
JSON_MODE = "--json" in sys.argv  # auto-pull live_players.json from Docker every 2 s

# Path inside Docker container where the DLL writes live_players.json
DOCKER_JSON_PATH = os.environ.get(
    "DOCKER_JSON_PATH",
    "/home/container/Icarus/Binaries/Win64/mods/live_players.json",
)

# URL des wine_reader HTTP API (legacy).
LIVE_READER_URL    = os.environ.get("LIVE_READER_URL", "").rstrip("/")
LIVE_POLL_INTERVAL = float(os.environ.get("LIVE_POLL_INTERVAL", "2"))

os.chdir(os.path.dirname(os.path.abspath(__file__)))


# ── GD.json State-Cache ────────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_parse_lock = threading.Lock()
_cached = {"data": None, "mtime": 0.0, "version": 0}


def _do_parse(path, mtime):
    binary = pp.load_binary(path)
    blobs, _ = pp.parse_state_recorder_blobs(binary)
    cats     = pp.categorize(blobs)

    players  = pp.extract_players_compat(binary)
    geysers  = pp.extract_geysers(cats)
    caves    = pp.extract_caves_scan(binary)
    deposits = pp.extract_deposits_scan(binary)

    data = {
        "version":  0,
        "players":  players,
        "geysers":  geysers,
        "caves":    caves,
        "deposits": deposits,
    }

    with _cache_lock:
        new_ver = _cached["version"] + 1
        data["version"] = new_ver
        _cached["data"]    = data
        _cached["mtime"]   = mtime
        _cached["version"] = new_ver

    e = geysers.get("enzyme", [])
    o = geysers.get("oil",    [])
    print(f"[+] Parsed v{new_ver} — "
          f"{len(players)} players | "
          f"{len(e)} enzyme / {len(o)} oil geysers | "
          f"{len(caves)} caves | "
          f"{len(deposits)} deposits")


def reparse_if_stale(path=GD_FILE):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return

    with _cache_lock:
        if mtime <= _cached["mtime"]:
            return

    if not _parse_lock.acquire(blocking=False):
        return
    try:
        with _cache_lock:
            if mtime <= _cached["mtime"]:
                return
        _do_parse(path, mtime)
    except Exception as exc:
        print(f"[!] Parse error: {exc}")
    finally:
        _parse_lock.release()


# ── Live-Player-Cache (wine_reader / mod file) ────────────────────────────────
_live_lock  = threading.Lock()
_live_cache = {"timestamp": 0.0, "player_count": 0, "players": [], "source": "none"}

# Path to live_players.json written by LiveMapMod.
# In Wine/Docker: set LIVE_JSON_PATH to the host-side path where the mod writes its output,
# e.g.  LIVE_JSON_PATH=/srv/icarus/Binaries/Win64/mods/live_players.json
LIVE_JSON = os.environ.get(
    "LIVE_JSON_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_players.json"),
)
_live_json_mtime = 0.0


def _build_steam_id_map() -> dict:
    """Returns {steam_id_str: character_name} from current GD.json parse."""
    with _cache_lock:
        data = _cached["data"]
    if not data:
        return {}
    return {str(p["steam_id"]): p["character_name"]
            for p in data.get("players", [])
            if p.get("steam_id") and p.get("character_name")}


def _read_live_mod_file() -> dict | None:
    """Reads live_players.json written by LiveMapMod, merges steam_id → character_name."""
    global _live_json_mtime
    try:
        mtime = os.path.getmtime(LIVE_JSON)
        if mtime <= _live_json_mtime:
            return None
        _live_json_mtime = mtime
        with open(LIVE_JSON, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    id_map = _build_steam_id_map()
    players = []
    for p in raw.get("players", []):
        sid       = str(p.get("steam_id", ""))
        char_name = id_map.get(sid) or p.get("name", "Player")
        players.append({
            "character_name": char_name,
            "steam_id":       sid,
            "online":         p.get("online", False),
            "x_m":            p.get("x_m", 0.0),
            "y_m":            p.get("y_m", 0.0),
            "z_m":            p.get("z_m", 0.0),
        })
    return {
        "timestamp":    raw.get("timestamp", time.time()),
        "player_count": sum(1 for p in players if p["online"]),
        "players":      players,
        "source":       "mod",
    }


def _mod_file_poller():
    """Background thread: watches live_players.json for updates every second."""
    while True:
        data = _read_live_mod_file()
        if data is not None:
            with _live_lock:
                _live_cache.update(data)
        time.sleep(1)


def _normalize_player(p: dict) -> dict:
    """Vereinheitlicht wine_reader- und parse_players-Format für das Frontend."""
    return {
        "character_name": p.get("character_name") or p.get("name") or "Unknown",
        "steam_id":       p.get("steam_id", ""),
        "online":         p.get("online", True),
        "x_m":            p.get("x_m", 0.0),
        "y_m":            p.get("y_m", 0.0),
        "z_m":            p.get("z_m", 0.0),
    }


def _fetch_live_players() -> dict | None:
    """
    Holt Spielerdaten vom wine_reader HTTP API.
    Gibt None zurück wenn nicht erreichbar.
    """
    if not LIVE_READER_URL:
        return None
    try:
        url = LIVE_READER_URL + "/players"
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode())
        players = [_normalize_player(p) for p in data.get("players", [])]
        return {
            "timestamp":    data.get("timestamp", time.time()),
            "player_count": sum(1 for p in players if p.get("online")),
            "players":      players,
            "source":       "live",
        }
    except Exception:
        return None


def _live_poller():
    """Hintergrund-Thread: pollt wine_reader alle LIVE_POLL_INTERVAL Sekunden."""
    last_ok  = False
    while True:
        data = _fetch_live_players()
        if data is not None:
            with _live_lock:
                _live_cache.update(data)
            if not last_ok:
                print(f"[+] Live-Verbindung hergestellt  →  {LIVE_READER_URL}/players")
            last_ok = True
        else:
            if last_ok:
                print(f"[!] Live-Verbindung verloren  ({LIVE_READER_URL}/players)")
            last_ok = False
        time.sleep(LIVE_POLL_INTERVAL)


def get_live_players() -> list:
    """Gibt aktuelle Spielerliste zurück (live oder GD.json-Fallback)."""
    with _live_lock:
        ts      = _live_cache["timestamp"]
        players = _live_cache["players"]

    # Live-Daten vorhanden und nicht älter als 30 s
    if players and (time.time() - ts) < 30:
        return players

    # Fallback: letzte GD.json-Daten
    with _cache_lock:
        data = _cached["data"]
    if data:
        return [_normalize_player(p) for p in data.get("players", [])]

    return []


# ── Docker pull ───────────────────────────────────────────────────────────────

def _find_icarus_container() -> str | None:
    """Returns the first running container ID that has DOCKER_JSON_PATH."""
    try:
        ids = subprocess.check_output(["docker", "ps", "-q"], text=True, timeout=5).split()
    except Exception:
        return None
    for cid in ids:
        try:
            r = subprocess.run(
                ["docker", "exec", cid, "test", "-f", DOCKER_JSON_PATH],
                capture_output=True, timeout=3,
            )
            if r.returncode == 0:
                return cid
        except Exception:
            pass
    return None


def do_docker_pull() -> dict:
    cid = _find_icarus_container()
    if not cid:
        return {"ok": False, "error": "No Icarus container found"}
    try:
        r = subprocess.run(
            ["docker", "cp", f"{cid}:{DOCKER_JSON_PATH}", LIVE_JSON],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.strip()}
        global _live_json_mtime
        _live_json_mtime = 0.0          # force re-read
        data = _read_live_mod_file()
        if data:
            with _live_lock:
                _live_cache.update(data)
        return {"ok": True, "container": cid}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _json_pull_poller():
    """Background thread: docker-pulls live_players.json every 2 s (--json mode)."""
    last_ok = None
    while True:
        result = do_docker_pull()
        if result.get("ok"):
            if last_ok is not True:
                print(f"[+] --json: Docker pull OK  (container {result.get('container','?')[:12]})")
            last_ok = True
        else:
            if last_ok is not False:
                print(f"[!] --json: Docker pull failed — {result.get('error','?')}")
            last_ok = False
        time.sleep(2)


# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(http.server.SimpleHTTPRequestHandler):

    def _send_json(self, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type",  "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?")[0]

        # ── POST /players  (Blueprint-Mod Push) ───────────────────────────────
        if path == "/players":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length)
                data   = json.loads(body.decode("utf-8"))
            except Exception as exc:
                self.send_error(400, f"Bad JSON: {exc}")
                return

            players = [_normalize_player(p) for p in data.get("players", [])]
            update  = {
                "timestamp":    data.get("timestamp", time.time()),
                "player_count": sum(1 for p in players if p.get("online")),
                "players":      players,
                "source":       "mod",
            }
            with _live_lock:
                _live_cache.update(update)

            self.send_response(204)
            self.end_headers()
            return

        self.send_error(404)

    def do_GET(self):
        path = self.path.split("?")[0]

        # ── /api/players  alias + /players.json  (live positions) ───────────────
        if path in ("/api/players", "/players.json"):
            with _live_lock:
                source = _live_cache["source"]
                ts     = _live_cache["timestamp"]

            players = get_live_players()
            self._send_json({
                "timestamp": ts if source == "live" else time.time(),
                "players":   players,
                "source":    source,
            })
            return

        # ── /api/pull  (Docker cp live_players.json → live cache) ───────────
        if path == "/api/pull":
            self._send_json(do_docker_pull())
            return

        # ── /api/state  (alles: players + geysers + caves + deposits) ─────────
        if path == "/api/state":
            reparse_if_stale()
            with _cache_lock:
                data = _cached["data"]
            if data is None:
                self.send_error(503, "Savegame not yet parsed")
                return
            # Live-Player-Positionen einblenden (überschreiben GD.json-Positionen)
            merged = dict(data)
            merged["players"] = get_live_players() or data.get("players", [])
            self._send_json(merged)
            return

        super().do_GET()

    def log_message(self, fmt, *args):
        pass


# ── Startup ───────────────────────────────────────────────────────────────────
print(f"[+] Icarus Live Map  →  http://localhost:{PORT}")
if JSON_MODE:
    print(f"[+] --json: Docker-Pull alle 2 s  ({DOCKER_JSON_PATH})")
    threading.Thread(target=_json_pull_poller, daemon=True).start()

if SERV_MODE:
    print("[+] --serv: kein Browser, kein Polling — DLL pushed via POST /players")
    print(f"[+] Docker Pull     →  GET /api/pull  ({DOCKER_JSON_PATH})")
else:
    if LIVE_READER_URL:
        print(f"[+] Live-Reader     →  {LIVE_READER_URL}/players  "
              f"(poll {LIVE_POLL_INTERVAL}s)")
        threading.Thread(target=_live_poller, daemon=True).start()
    else:
        print("[i] Kein LIVE_READER_URL gesetzt – nur GD.json Modus")
    threading.Thread(target=_mod_file_poller, daemon=True).start()
    print(f"[+] Mod-File-Watcher →  {LIVE_JSON}")

reparse_if_stale()

print("    Ctrl+C to stop\n")

server = http.server.HTTPServer(("", PORT), Handler)
if sys.stdout.isatty() and not SERV_MODE:
    threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\n[+] Server stopped.")
