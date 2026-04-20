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

DOCKER_SAVE_PATH = os.environ.get(
    "DOCKER_SAVE_PATH",
    "/home/container/Icarus/Saved/PlayerData/DedicatedServer/Prospects",
)

# URL des wine_reader HTTP API (legacy).
LIVE_READER_URL    = os.environ.get("LIVE_READER_URL", "").rstrip("/")
LIVE_POLL_INTERVAL = float(os.environ.get("LIVE_POLL_INTERVAL", "1"))

os.chdir(os.path.dirname(os.path.abspath(__file__)))

SAVEGAMES_DIR = "savegames"
os.makedirs(SAVEGAMES_DIR, exist_ok=True)

# ── name.json  (steam_id → {name, steam_id, character}) ──────────────────────
NAME_FILE  = "name.json"
_name_lock = threading.Lock()
_name_data = {}   # {steam_id_str: {"steam_id": "", "name": "", "character": ""}}


def _load_names() -> dict:
    try:
        with open(NAME_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_names(snapshot: dict):
    try:
        with open(NAME_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"[!] name.json write error: {exc}")


def _update_names_from_savegame(players: list):
    """Persist character_name for each player that has a steam_id."""
    with _name_lock:
        changed = False
        for pl in players:
            sid  = str(pl.get("steam_id", "")).strip()
            char = (pl.get("character_name") or "").strip()
            if not sid or not char:
                continue
            entry = _name_data.setdefault(sid, {"steam_id": sid, "name": "", "character": ""})
            if entry.get("character") != char:
                entry["character"] = char
                changed = True
        if changed:
            _save_names(dict(_name_data))


def _update_names_from_live(players: list):
    """Persist Steam name for each player that has a steam_id."""
    with _name_lock:
        changed = False
        for pl in players:
            sid        = str(pl.get("steam_id", "")).strip()
            steam_name = (pl.get("name") or "").strip()
            if not sid or not steam_name:
                continue
            entry = _name_data.setdefault(sid, {"steam_id": sid, "name": "", "character": ""})
            if entry.get("name") != steam_name:
                entry["name"] = steam_name
                changed = True
        if changed:
            _save_names(dict(_name_data))


def _enrich_with_character_names(players: list) -> list:
    """Overlay character_name from name.json for players whose name.json entry has one."""
    with _name_lock:
        result = []
        for pl in players:
            sid   = str(pl.get("steam_id", "")).strip()
            entry = _name_data.get(sid)
            if entry and entry.get("character"):
                pl = dict(pl)
                pl["character_name"] = entry["character"]
            result.append(pl)
    return result


def _init_names_from_existing_files():
    """Bootstrap name.json once from live_players.json + players.json if both exist."""
    try:
        if os.path.isfile("live_players.json"):
            with open("live_players.json", encoding="utf-8") as f:
                _update_names_from_live(json.load(f).get("players", []))
    except Exception as exc:
        print(f"[names] live_players.json seed error: {exc}")
    try:
        if os.path.isfile("players.json"):
            with open("players.json", encoding="utf-8") as f:
                data = json.load(f)
                players = data if isinstance(data, list) else data.get("players", [])
                _update_names_from_savegame(players)
    except Exception as exc:
        print(f"[names] players.json seed error: {exc}")


# ── Savegame / POI infrastructure ─────────────────────────────────────────────
_poi_lock  = threading.Lock()
_poi_cache = {}   # {filename: {"data": ..., "mtime": float, "parsing": bool, "error": str|None}}


def _list_docker_savegames() -> list:
    """List .json files from DOCKER_SAVE_PATH inside the running container."""
    cid = _find_icarus_container()
    if not cid:
        return []
    try:
        r = subprocess.run(
            ["docker", "exec", cid, "find", DOCKER_SAVE_PATH,
             "-maxdepth", "1", "-name", "*.json", "-type", "f"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return []
        files = []
        for line in r.stdout.splitlines():
            fname = os.path.basename(line.strip())
            if fname.endswith(".json"):
                files.append(fname)
        return sorted(files)
    except Exception as exc:
        print(f"[savegames] docker list error: {exc}")
        return []


def _list_local_savegames() -> list:
    """List .json files from the local savegames/ directory."""
    if not os.path.isdir(SAVEGAMES_DIR):
        return []
    return sorted(f for f in os.listdir(SAVEGAMES_DIR) if f.lower().endswith(".json"))


def pull_savegames_from_docker() -> dict:
    """docker cp all .json files from DOCKER_SAVE_PATH → local savegames/."""
    cid = _find_icarus_container()
    if not cid:
        return {"ok": False, "error": "No Icarus container found"}
    files = _list_docker_savegames()
    if not files:
        return {"ok": True, "pulled": [], "message": f"No .json files in {DOCKER_SAVE_PATH}"}
    pulled, errors = [], []
    for fname in files:
        src  = f"{cid}:{DOCKER_SAVE_PATH}/{fname}"
        dest = os.path.join(SAVEGAMES_DIR, fname)
        try:
            r = subprocess.run(["docker", "cp", src, dest],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                pulled.append(fname)
            else:
                errors.append(f"{fname}: {r.stderr.strip()}")
        except Exception as exc:
            errors.append(f"{fname}: {exc}")
    # Invalidate POI cache for pulled files so they get re-parsed
    with _poi_lock:
        for fname in pulled:
            _poi_cache.pop(fname, None)
    return {"ok": True, "pulled": pulled, "errors": errors}


def get_savegame_list() -> list:
    """Merge docker listing + local listing, return status-enriched list."""
    docker_files = set(_list_docker_savegames())
    local_files  = set(_list_local_savegames())
    all_files    = sorted(docker_files | local_files)
    result = []
    with _poi_lock:
        for fname in all_files:
            c      = _poi_cache.get(fname)
            in_docker = fname in docker_files
            in_local  = fname in local_files
            if c is None:
                status = "unloaded"
            elif c.get("parsing"):
                status = "parsing"
            elif c.get("error"):
                status = "error"
            else:
                status = "ready"
            entry = {
                "id":        fname,
                "name":      os.path.splitext(fname)[0],
                "source":    "docker+local" if (in_docker and in_local) else ("docker" if in_docker else "local"),
                "status":    status,
            }
            if c and c.get("data"):
                entry["world"] = c["data"].get("world")
            result.append(entry)
    return result


def _parse_poi_bg(filename: str, path: str, mtime: float):
    print(f"[poi] Parsing {filename} …")
    try:
        binary   = pp.load_binary(path)
        players  = pp.extract_players_compat(binary)
        caves    = pp.extract_caves_scan(binary)
        deposits = pp.extract_deposits_scan(binary)
        blobs, _ = pp.parse_state_recorder_blobs(binary)
        cats     = pp.categorize(blobs)
        geysers  = pp.extract_geysers(cats)
        # Try to detect world from filename
        lower = filename.lower()
        world = next((w for w in ("olympus","styx","prometheus","elysium") if w in lower), None)
        data  = {"world": world, "players": players,
                 "caves": caves, "deposits": deposits, "geysers": geysers}
        with _poi_lock:
            _poi_cache[filename] = {"data": data, "mtime": mtime,
                                    "parsing": False, "error": None}
        print(f"[poi] Done {filename}: {len(caves)} caves, {len(deposits)} deposits")
        # Update name.json with character names from this savegame
        _update_names_from_savegame(players)
    except Exception as exc:
        print(f"[!] POI parse {filename}: {exc}")
        with _poi_lock:
            _poi_cache[filename] = {"data": None, "mtime": mtime,
                                    "parsing": False, "error": str(exc)}


def _get_or_start_poi(filename: str):
    """Return (data, status) — status is 'ok', 'parsing', 'not_found', or 'error:...'."""
    local_path = os.path.join(SAVEGAMES_DIR, filename)
    # If not local yet, try pulling from docker
    if not os.path.isfile(local_path):
        cid = _find_icarus_container()
        if cid:
            src = f"{cid}:{DOCKER_SAVE_PATH}/{filename}"
            subprocess.run(["docker", "cp", src, local_path],
                           capture_output=True, timeout=30)
    if not os.path.isfile(local_path):
        return None, "not_found"
    try:
        mtime = os.path.getmtime(local_path)
    except OSError:
        return None, "not_found"
    with _poi_lock:
        c = _poi_cache.get(filename)
        if c:
            if c["parsing"]:                          return None, "parsing"
            if c.get("data") and c["mtime"] >= mtime: return c["data"], "ok"
            if c.get("error") and c["mtime"] >= mtime: return None, "error:" + c["error"]
    # Start background parse
    with _poi_lock:
        e = _poi_cache.setdefault(filename, {})
        if e.get("parsing"):
            return None, "parsing"
        e["parsing"] = True
        e["error"]   = None
    threading.Thread(target=_parse_poi_bg,
                     args=(filename, local_path, mtime),
                     daemon=True, name=f"poi-{filename}").start()
    return None, "parsing"


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
    raw_players = raw.get("players", [])
    # Persist Steam names to name.json
    _update_names_from_live(raw_players)
    with _name_lock:
        names_snap = dict(_name_data)
    players = []
    for p in raw_players:
        sid       = str(p.get("steam_id", ""))
        # Prefer: name.json character → savegame id_map → steam name → fallback
        char_name = (names_snap.get(sid) or {}).get("character") or id_map.get(sid) or p.get("name", "Player")
        players.append({
            "character_name": char_name,
            "name":           p.get("name", ""),
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
        time.sleep(LIVE_POLL_INTERVAL)


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

            raw_players = data.get("players", [])
            _update_names_from_live(raw_players)
            players = [_normalize_player(p) for p in raw_players]
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

        # ── POST /api/pull-saves  (docker cp savegames → local) ──────────────
        if path == "/api/pull-saves":
            self._send_json(pull_savegames_from_docker())
            return

        # ── POST /api/names  (update a name.json entry) ───────────────────────
        if path == "/api/names":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length)
                patch  = json.loads(body.decode("utf-8"))
            except Exception as exc:
                self.send_error(400, f"Bad JSON: {exc}")
                return
            with _name_lock:
                for sid, entry in patch.items():
                    sid = str(sid).strip()
                    if not sid:
                        continue
                    existing = _name_data.setdefault(sid, {"steam_id": sid, "name": "", "character": ""})
                    existing.update({k: v for k, v in entry.items() if k in ("name", "character", "steam_id")})
                _save_names(dict(_name_data))
            self._send_json({"ok": True})
            return

        self.send_error(404)

    def do_GET(self):
        path = self.path.split("?")[0]

        # ── /api/players  alias + /players.json  (live positions) ───────────────
        if path in ("/api/players", "/players.json"):
            with _live_lock:
                source = _live_cache["source"]
                ts     = _live_cache["timestamp"]

            players = _enrich_with_character_names(get_live_players())
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

        # ── /api/savegames  ───────────────────────────────────────────────────
        if path == "/api/savegames":
            self._send_json(get_savegame_list())
            return

        # ── /api/poi/<filename>  ──────────────────────────────────────────────
        if path.startswith("/api/poi/"):
            import urllib.parse as _up
            filename = _up.unquote(path[len("/api/poi/"):])
            if os.sep in filename or ".." in filename:
                self.send_error(400, "Invalid filename")
                return
            data, status = _get_or_start_poi(filename)
            if status == "ok":
                self._send_json(data)
            elif status == "parsing":
                body = json.dumps({"status": "parsing"}).encode()
                self.send_response(202)
                self.send_header("Content-Type",  "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif status == "not_found":
                self.send_error(404)
            else:
                self.send_error(500, status)
            return

        # ── /api/names  ───────────────────────────────────────────────────────
        if path == "/api/names":
            with _name_lock:
                self._send_json(dict(_name_data))
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
            merged["players"] = _enrich_with_character_names(
                get_live_players() or data.get("players", [])
            )
            self._send_json(merged)
            return

        super().do_GET()

    def log_message(self, fmt, *args):
        pass


# ── Startup ───────────────────────────────────────────────────────────────────

# Load persisted name mappings; seed from existing local files on first run
with _name_lock:
    _name_data.update(_load_names())

if not _name_data:
    _init_names_from_existing_files()
    if _name_data:
        print(f"[+] name.json seeded with {len(_name_data)} player(s) from local files")

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
