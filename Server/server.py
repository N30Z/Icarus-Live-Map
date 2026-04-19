"""
Icarus Live Map — Linux server with Docker integration.

Live player positions can come from:
  A) docker exec cat <save_path>   (GD.json polling — simple, no extra setup)
  B) /proc/<pid>/mem via linux_reader.py inside the container (true live, <1s latency)

POI data (caves, deposits, geysers) is parsed from savegame files on demand.

Endpoints:
  GET  /                      → linux_server/index.html
  GET  /setup                 → linux_server/setup.html  (setup & debug page)
  GET  /api/players           → live player positions
  GET  /api/savegames         → list of savegame files in savegames_dir
  GET  /api/poi/<file>        → POI data for one savegame (parsed server-side)
  POST /api/config/save       → update config.json (container, PID, paths)
  POST /api/pull-saves        → docker cp savegame JSONs from container → savegames/
  GET  /api/debug/processes   → list Icarus processes inside container
  GET  /api/debug/trace       → SSE: stream --trace output (find UE4 offsets)
  GET  /api/debug/test-read   → run linux_reader.py once, return JSON
  GET  /api/debug/offsets     → current offsets.json content
  POST /api/debug/offsets     → save offsets.json
  GET  /*                     → static files from project root
"""

import http.server
import json
import mimetypes
import os
import queue
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parser as p

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

CONFIG_FILE  = os.path.join(SCRIPT_DIR, "config.json")
OFFSETS_FILE = os.path.join(SCRIPT_DIR, "offsets.json")
READER_SCRIPT = os.path.join(SCRIPT_DIR, "linux_reader.py")

DEFAULT_CONFIG = {
    "_comment": "Edit this file, then restart server.py",
    "port": 9090,
    "live": {
        "mode": "savegame",
        "_comment_mode": "savegame = poll GD.json via docker/host path | memory = use PID via linux_reader.py",
        "host_save_path": "",
        "docker_container": "",
        "docker_save_path": "/home/icarus/Saved/PlayerData/DedicatedServer/Prospects/GD.json",
        "pid": 0,
        "_comment_pid": "PID of IcarusServer process inside the Docker container (for memory mode)"
    },
    "docker": {
        "container": "",
        "_comment_container": "Docker container name or ID",
        "prospects_path": "~/Icarus/Saved/PlayerData/DedicatedServer/Prospects",
        "_comment_prospects": "Path inside the container where GD.json files live"
    },
    "savegames_dir": "./savegames",
    "poll_interval_players": 5,
    "savegames_scan_interval": 30
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        for key in ("live", "docker"):
            if key in cfg:
                merged[key] = dict(DEFAULT_CONFIG.get(key, {}))
                merged[key].update(cfg[key])
        return merged
    with open(CONFIG_FILE, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    print(f"[!] Created default {CONFIG_FILE} — please edit it.")
    return DEFAULT_CONFIG


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


CFG = load_config()

PORT          = int(CFG.get("port", 9090))
SAVEGAMES_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, CFG.get("savegames_dir", "./savegames")))
POLL_PLAYERS  = int(CFG.get("poll_interval_players", 5))
SCAN_INTERVAL = int(CFG.get("savegames_scan_interval", 30))

os.makedirs(SAVEGAMES_DIR, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _docker_container() -> str:
    return (CFG.get("docker", {}).get("container") or
            CFG.get("live", {}).get("docker_container") or "").strip()


def _docker_exec(args: list, timeout=30, text=False) -> subprocess.CompletedProcess:
    """Run docker exec <container> <args>."""
    container = _docker_container()
    if not container:
        raise RuntimeError("No docker container configured (config.json → docker.container)")
    cmd = ["docker", "exec", container] + args
    return subprocess.run(cmd, capture_output=True, timeout=timeout,
                          text=text, encoding="utf-8" if text else None)


# ── Live save reader (savegame mode) ──────────────────────────────────────────

def _read_live_gd_json() -> dict:
    live = CFG.get("live", {})
    host_path = live.get("host_save_path", "").strip()
    if host_path:
        with open(host_path, "r", encoding="utf-8") as f:
            return json.load(f)
    save_path = live.get("docker_save_path", "").strip()
    if not save_path:
        raise RuntimeError("No host_save_path or docker_save_path configured")
    result = _docker_exec(["cat", save_path], timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"docker exec failed: {result.stderr.decode(errors='replace').strip()}")
    return json.loads(result.stdout)


# ── Live reader: memory mode via linux_reader.py ──────────────────────────────

def _deploy_reader_to_container():
    """docker cp linux_reader.py → /tmp/icarus_reader.py in the container."""
    container = _docker_container()
    if not container:
        raise RuntimeError("No docker container configured")
    result = subprocess.run(
        ["docker", "cp", READER_SCRIPT, f"{container}:/tmp/icarus_reader.py"],
        capture_output=True, timeout=15, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker cp failed: {result.stderr.strip()}")


def _read_players_from_memory() -> list:
    """Run linux_reader.py inside container, return player list."""
    pid = int(CFG.get("live", {}).get("pid", 0))
    if not pid:
        raise RuntimeError("PID not configured (config.json → live.pid)")
    result = _docker_exec(
        ["python3", "/tmp/icarus_reader.py", str(pid)],
        timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    data = json.loads(result.stdout)
    return data.get("players", [])


def _read_players_from_host_memory() -> list:
    """Run linux_reader.py directly on the host using the host-visible PID."""
    pid = int(CFG.get("live", {}).get("pid", 0))
    if not pid:
        raise RuntimeError("PID not configured (config.json → live.pid)")
    offs = load_offsets()
    result = subprocess.run(
        [sys.executable, READER_SCRIPT, str(pid),
         "--pawn", hex(offs.get("OFF_PAWN_PRIVATE",   0x3A0)),
         "--comp", hex(offs.get("OFF_ROOT_COMPONENT", 0x198)),
         "--loc",  hex(offs.get("OFF_REL_LOCATION",   0x11C))],
        capture_output=True, timeout=45
    )
    stdout = result.stdout.decode(errors="replace").strip()
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(stderr or f"Exit code {result.returncode}")
    data = json.loads(stdout)
    return data.get("players", [])


# ── Savegame name cache (for enriching memory-read players with real names) ───

_sg_name_lock  = threading.Lock()
_sg_name_cache = {"players": [], "ts": 0.0}
SG_NAME_TTL    = 30.0   # seconds between savegame refreshes for name lookup

import math as _math

def _get_savegame_players() -> list:
    """Return savegame player list (character_name, steam_id, positions).
    Refreshes from the live GD.json at most every SG_NAME_TTL seconds."""
    with _sg_name_lock:
        if time.time() - _sg_name_cache["ts"] < SG_NAME_TTL:
            return list(_sg_name_cache["players"])
    try:
        gd      = _read_live_gd_json()
        binary  = p.gd_json_to_binary(gd)
        players = p.extract_players_compat(binary)
        with _sg_name_lock:
            _sg_name_cache["players"] = players
            _sg_name_cache["ts"]      = time.time()
        return players
    except Exception as e:
        print(f"[names] Savegame read failed: {e}")
        with _sg_name_lock:
            return list(_sg_name_cache["players"])   # return stale if available


def _assign_names_from_savegame(mem_players: list) -> list:
    """Enrich memory-read players (positions only) with names from the savegame.

    Matches each online memory player to the nearest savegame player by XY
    distance.  Tolerance is generous (1 km) to absorb savegame update lag.
    Assigns character_name and steam_id from the savegame match.
    """
    sg_players = _get_savegame_players()
    sg_online  = [sg for sg in sg_players if sg.get("online")]
    if not sg_online:
        return mem_players

    MATCH_RADIUS_M = 1000.0   # savegame positions can lag up to ~60 s

    result    = []
    used_sg   = set()
    for mp in mem_players:
        if not mp.get("online"):
            result.append(mp)
            continue
        mx, my   = mp.get("x_m", 0.0), mp.get("y_m", 0.0)
        best_d   = float("inf")
        best_idx = -1
        for i, sg in enumerate(sg_online):
            if i in used_sg:
                continue
            dx = mx - sg.get("x_m", 0.0)
            dy = my - sg.get("y_m", 0.0)
            d  = _math.hypot(dx, dy)
            if d < best_d:
                best_d   = d
                best_idx = i
        if best_idx >= 0 and best_d < MATCH_RADIUS_M:
            sg = sg_online[best_idx]
            used_sg.add(best_idx)
            result.append({
                **mp,
                "character_name": sg.get("character_name") or mp.get("name", ""),
                "steam_id":       sg.get("steam_id", ""),
            })
        else:
            result.append(mp)
    return result


# ── Player poll thread ────────────────────────────────────────────────────────

_players_lock  = threading.Lock()
_players_cache = {"players": [], "ts": 0.0, "error": None, "mode": ""}


def _player_poll_loop():
    _reader_deployed = False
    while True:
        mode = CFG.get("live", {}).get("mode", "savegame")
        try:
            if mode == "host_memory":
                # Process visible from host — run linux_reader.py locally
                players = _read_players_from_host_memory()
                players = _assign_names_from_savegame(players)
            elif mode == "memory":
                # Process only reachable inside container — deploy + docker exec
                if not _reader_deployed:
                    try:
                        _deploy_reader_to_container()
                        _reader_deployed = True
                        print("[players] linux_reader.py deployed to container")
                    except Exception as e:
                        print(f"[!] Reader deploy failed: {e}")
                players = _read_players_from_memory()
                players = _assign_names_from_savegame(players)
            else:
                gd      = _read_live_gd_json()
                binary  = p.gd_json_to_binary(gd)
                players = p.extract_players_compat(binary)
                # savegame mode already has character_name + steam_id

            online = sum(1 for pl in players if pl.get("online"))
            print(f"[players/{mode}] {online}/{len(players)} online")
            with _players_lock:
                _players_cache.update({"players": players, "ts": time.time(),
                                        "error": None, "mode": mode})
        except Exception as exc:
            print(f"[!] Player poll error: {exc}")
            with _players_lock:
                _players_cache["error"] = str(exc)
        time.sleep(POLL_PLAYERS)


# ── Savegame scanner + POI parser ─────────────────────────────────────────────

_sg_lock   = threading.Lock()
_sg_list   = []

_poi_lock  = threading.Lock()
_poi_cache = {}


def _scan_savegames() -> list:
    if not os.path.isdir(SAVEGAMES_DIR):
        return []
    results = []
    for fname in sorted(os.listdir(SAVEGAMES_DIR)):
        if not fname.lower().endswith(".json"):
            continue
        fpath = os.path.join(SAVEGAMES_DIR, fname)
        try:
            st = os.stat(fpath)
            results.append({"id": fname, "name": os.path.splitext(fname)[0],
                             "mtime": st.st_mtime, "size": st.st_size})
        except OSError:
            pass
    return results


def _sg_scan_loop():
    while True:
        try:
            saves = _scan_savegames()
            with _sg_lock:
                _sg_list.clear(); _sg_list.extend(saves)
        except Exception as e:
            print(f"[!] Savegame scan: {e}")
        time.sleep(SCAN_INTERVAL)


def _parse_poi_bg(filename, path, mtime):
    print(f"[poi] Parsing {filename} …")
    try:
        binary   = p.load_binary(path)
        players  = p.extract_players_compat(binary)
        caves    = p.extract_caves_scan(binary)
        deposits = p.extract_deposits_scan(binary)
        world    = p.detect_world(binary)
        blobs, _ = p.parse_state_recorder_blobs(binary)
        cats     = p.categorize(blobs)
        geysers  = p.extract_geysers(cats)
        data = {"world": world, "players": players,
                "caves": caves, "deposits": deposits, "geysers": geysers}
        with _poi_lock:
            _poi_cache[filename] = {"data": data, "mtime": mtime, "parsing": False, "error": None}
        print(f"[poi] Done {filename}: world={world}, "
              f"{len(caves)} caves, {len(deposits)} deposits")
    except Exception as exc:
        print(f"[!] POI parse {filename}: {exc}")
        with _poi_lock:
            _poi_cache[filename] = {"data": None, "mtime": mtime,
                                    "parsing": False, "error": str(exc)}


def _get_or_start_poi(filename):
    path = os.path.join(SAVEGAMES_DIR, filename)
    if not os.path.isfile(path):
        return None, "not_found"
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, "not_found"
    with _poi_lock:
        c = _poi_cache.get(filename)
        if c:
            if c["parsing"]:     return None, "parsing"
            if c["mtime"] >= mtime and c["data"] is not None: return c["data"], "ok"
            if c["error"] and c["mtime"] >= mtime: return None, "error:" + c["error"]
    with _poi_lock:
        e = _poi_cache.setdefault(filename, {})
        if e.get("parsing"): return None, "parsing"
        e["parsing"] = True; e["error"] = None
    threading.Thread(target=_parse_poi_bg, args=(filename, path, mtime),
                     daemon=True, name=f"poi-{filename}").start()
    return None, "parsing"


# ── Docker / debug operations ─────────────────────────────────────────────────

def pull_saves_from_container() -> dict:
    """Pull all .json files from the prospects path inside the container."""
    docker_cfg     = CFG.get("docker", {})
    prospects_path = docker_cfg.get("prospects_path",
                     "~/Icarus/Saved/PlayerData/DedicatedServer/Prospects").strip()

    # Run via sh so that ~ is expanded inside the container
    result = _docker_exec(
        ["sh", "-c", f"find '{prospects_path}' -name '*.json' -type f 2>&1"],
        timeout=15, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"find failed: {result.stdout.strip() or result.stderr.strip()}")

    files = [f.strip() for f in result.stdout.splitlines()
             if f.strip() and not f.strip().startswith("find:")]
    if not files:
        return {"pulled": [], "message": f"No .json files found in {prospects_path}"}

    pulled = []
    errors = []
    container = _docker_container()
    for fpath in files:
        fname = os.path.basename(fpath)
        dest  = os.path.join(SAVEGAMES_DIR, fname)
        cp = subprocess.run(
            ["docker", "cp", f"{container}:{fpath}", dest],
            capture_output=True, timeout=30, text=True
        )
        if cp.returncode == 0:
            pulled.append(fname)
            print(f"[pull] {fname} → {dest}")
        else:
            errors.append(f"{fname}: {cp.stderr.strip()}")

    return {"pulled": pulled, "errors": errors,
            "savegames_dir": SAVEGAMES_DIR}


def _parse_ps_lines(lines: list) -> list:
    """Parse `ps aux` output lines into process dicts (filter Icarus/Wine)."""
    procs = []
    for line in lines[1:]:
        if "icarus" in line.lower() or "wine" in line.lower():
            parts = line.split(None, 10)
            if len(parts) >= 2:
                procs.append({"pid": parts[1], "user": parts[0],
                               "cmd": parts[-1] if len(parts) > 10 else "",
                               "source": "host"})
    return procs


def list_processes_in_container() -> list:
    """List processes in the container matching 'icarus' (case-insensitive)."""
    result = _docker_exec(["ps", "aux"], timeout=10, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    procs = _parse_ps_lines(result.stdout.splitlines())
    for p in procs:
        p["source"] = "container"
    return procs


def list_processes_on_host() -> list:
    """List Icarus/Wine processes visible on the host (no Docker needed)."""
    result = subprocess.run(["ps", "aux"], capture_output=True, timeout=10, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return _parse_ps_lines(result.stdout.splitlines())


def _stream_trace(pid: int, ref_json_path: str, out_queue: queue.Queue,
                  use_host: bool = False, player_name: str = ""):
    """Stream linux_reader.py --trace output. host=True → run on host directly."""
    if use_host:
        cmd = [sys.executable, "-u", READER_SCRIPT,
               str(pid), "--trace", "--ref", ref_json_path]
    else:
        try:
            _deploy_reader_to_container()
        except Exception as e:
            out_queue.put(f"[!] Deploy failed: {e}\n")
            out_queue.put(None); return
        container = _docker_container()
        cmd = ["docker", "exec", container,
               "python3", "-u", "/tmp/icarus_reader.py",
               str(pid), "--trace", "--ref", ref_json_path]

    if player_name:
        cmd += ["--player", player_name]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
        for line in proc.stdout:
            out_queue.put(line)
            # Auto-save offsets to offsets.json when trace succeeds
            stripped = line.strip()
            if stripped.startswith('{"status": "ok"'):
                try:
                    data = json.loads(stripped)
                    offs = data.get("offsets", {})
                    if offs:
                        save_offsets_file(offs)
                        print(f"[trace] Offsets auto-saved to {OFFSETS_FILE}")
                except Exception:
                    pass
        proc.wait()
        out_queue.put(f"\n[done] Exit code: {proc.returncode}\n")
    except Exception as e:
        out_queue.put(f"[!] Error: {e}\n")
    finally:
        out_queue.put(None)


def _auto_populate_ref(ref_path: str):
    """Populate ref_path from the live savegame (always refreshes to get current positions)."""
    # Always read from the live savegame — the existing ref file may be stale from a
    # previous session (players at old positions, different players online), which causes
    # --trace to search for wrong coordinates and find false-positive offsets.
    try:
        gd     = _read_live_gd_json()
        binary = p.gd_json_to_binary(gd)
        players = p.extract_players_compat(binary)
        online  = [pl for pl in players if pl.get("online")]
        if not online:
            print("[trace] Savegame has no online players — reference not auto-populated")
            return
        with open(ref_path, "w", encoding="utf-8") as f:
            json.dump({"players": players}, f)
        names = ", ".join(pl.get("character_name") or pl.get("name", "?") for pl in online)
        print(f"[trace] Auto-populated {ref_path}: {len(online)} online — {names}")
    except Exception as e:
        print(f"[trace] Could not auto-populate ref from savegame: {e}")
        # Fall back to existing file so trace can still attempt to run
        if os.path.exists(ref_path):
            print("[trace] Using existing reference file as fallback")


def load_offsets() -> dict:
    try:
        with open(OFFSETS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"OFF_PAWN_PRIVATE": 0x3A0, "OFF_ROOT_COMPONENT": 0x198, "OFF_REL_LOCATION": 0x11C}


def save_offsets_file(offsets: dict):
    with open(OFFSETS_FILE, "w") as f:
        json.dump(offsets, f, indent=2)


# ── HTTP handler ──────────────────────────────────────────────────────────────

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path in ("/", "/index.html", ""):
            self._serve_file(os.path.join(SCRIPT_DIR, "index.html"))

        elif path in ("/setup", "/setup.html"):
            self._serve_file(os.path.join(SCRIPT_DIR, "setup.html"))

        elif path == "/api/players":
            with _players_lock:
                self._json(dict(_players_cache))

        elif path == "/api/savegames":
            with _sg_lock:
                saves = list(_sg_list)
            with _poi_lock:
                for sg in saves:
                    c = _poi_cache.get(sg["id"])
                    if c is None:           sg["status"] = "unloaded"
                    elif c.get("parsing"):  sg["status"] = "parsing"
                    elif c.get("error"):    sg["status"] = "error"
                    else:
                        sg["status"] = "ready"
                        sg["world"]  = (c.get("data") or {}).get("world")
            self._json(saves)

        elif path.startswith("/api/poi/"):
            filename = urllib.parse.unquote(path[len("/api/poi/"):])
            if os.sep in filename or ".." in filename:
                self.send_error(400, "Invalid filename"); return
            data, status = _get_or_start_poi(filename)
            if status == "ok":
                self._json(data)
            elif status == "parsing":
                body = json.dumps({"status": "parsing"}).encode()
                self.send_response(202)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            elif status == "not_found":
                self.send_error(404)
            else:
                self.send_error(500, status)

        elif path == "/api/config":
            self._json(CFG)

        elif path == "/api/debug/processes":
            # Try host first (user says process is visible from host)
            try:
                procs = list_processes_on_host()
                if not procs:
                    # Fallback to container if nothing found on host
                    procs = list_processes_in_container()
                self._json({"processes": procs})
            except Exception as e:
                # If host listing fails, try container
                try:
                    procs = list_processes_in_container()
                    self._json({"processes": procs})
                except Exception as e2:
                    self._json({"error": f"host: {e}; container: {e2}"})

        elif path == "/api/debug/host-processes":
            try:
                procs = list_processes_on_host()
                self._json({"processes": procs})
            except Exception as e:
                self._json({"error": str(e)})

        elif path == "/api/debug/test-read":
            pid = int(CFG.get("live", {}).get("pid", 0))
            if not pid:
                self._json({"error": "PID not set"}); return
            mode = CFG.get("live", {}).get("mode", "savegame")
            try:
                if mode == "host_memory":
                    result = subprocess.run(
                        [sys.executable, READER_SCRIPT, str(pid)],
                        capture_output=True, timeout=45
                    )
                    stdout = result.stdout.decode(errors="replace")
                    stderr = result.stderr.decode(errors="replace")
                else:
                    _deploy_reader_to_container()
                    result = _docker_exec(
                        ["python3", "/tmp/icarus_reader.py", str(pid)],
                        timeout=45
                    )
                    stdout = result.stdout.decode(errors="replace")
                    stderr = result.stderr.decode(errors="replace")
                data = {}
                try: data = json.loads(stdout)
                except Exception: pass
                self._json({"returncode": result.returncode,
                             "data": data, "stderr": stderr[-4000:]})
            except Exception as e:
                self._json({"error": str(e)})

        elif path == "/api/debug/trace":
            # SSE stream
            pid = int(CFG.get("live", {}).get("pid", 0))
            if not pid:
                self._json({"error": "PID not set"}); return
            mode = CFG.get("live", {}).get("mode", "savegame")
            use_host = (mode == "host_memory")
            ref_json = "/tmp/icarus_players_ref.json"
            if use_host:
                ref_json = os.path.join(SCRIPT_DIR, "_ref_players.json")
            qs = urllib.parse.parse_qs(parsed.query)
            player_name = qs.get("player", [""])[0]
            # Auto-populate ref from savegame if missing / no online players
            _auto_populate_ref(ref_json)
            q = queue.Queue()
            threading.Thread(target=_stream_trace,
                              args=(pid, ref_json, q, use_host, player_name),
                              daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                while True:
                    line = q.get()
                    if line is None:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        break
                    payload = "data: " + line.rstrip("\n").replace("\n", "\ndata: ") + "\n\n"
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
            except BrokenPipeError:
                pass

        elif path == "/api/debug/ref-from-savegame":
            # Read player positions from the live GD.json, save as ref for --trace
            try:
                gd     = _read_live_gd_json()
                binary = p.gd_json_to_binary(gd)
                players = p.extract_players_compat(binary)
                online  = [pl for pl in players if pl.get("online")]
                if not online:
                    self._json({"ok": False,
                                "error": f"No online players in savegame ({len(players)} total)"})
                    return
                ref_data = json.dumps({"players": players}, ensure_ascii=False).encode()
                mode = CFG.get("live", {}).get("mode", "savegame")
                names = [p.get("character_name") or p.get("name", "?") for p in online]
                if mode == "host_memory":
                    ref_path = os.path.join(SCRIPT_DIR, "_ref_players.json")
                    with open(ref_path, "wb") as f:
                        f.write(ref_data)
                    self._json({"ok": True, "online": len(online),
                                "path": ref_path, "players": names})
                else:
                    tmp      = "/tmp/_icarus_ref_sg.json"
                    ref_path = "/tmp/icarus_players_ref.json"
                    with open(tmp, "wb") as f:
                        f.write(ref_data)
                    cp = subprocess.run(
                        ["docker", "cp", tmp, f"{_docker_container()}:{ref_path}"],
                        capture_output=True, timeout=15, text=True
                    )
                    if cp.returncode != 0:
                        raise RuntimeError(cp.stderr.strip())
                    self._json({"ok": True, "online": len(online),
                                "container_path": ref_path, "players": names})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path == "/api/debug/offsets":
            self._json(load_offsets())

        else:
            self._serve_static(path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""

        if path == "/api/config/save":
            try:
                patch = json.loads(body)
                # Merge patch into CFG
                for k, v in patch.items():
                    if isinstance(v, dict) and isinstance(CFG.get(k), dict):
                        CFG[k].update(v)
                    else:
                        CFG[k] = v
                save_config(CFG)
                self._json({"ok": True, "config": CFG})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path == "/api/pull-saves":
            try:
                result = pull_saves_from_container()
                # Trigger savegame list refresh
                saves = _scan_savegames()
                with _sg_lock:
                    _sg_list.clear(); _sg_list.extend(saves)
                self._json({"ok": True, **result})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path == "/api/debug/offsets":
            try:
                offsets = json.loads(body)
                save_offsets_file(offsets)
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path == "/api/debug/upload-ref":
            # Upload a reference players.json for --trace mode
            mode = CFG.get("live", {}).get("mode", "savegame")
            try:
                if mode == "host_memory":
                    # Save locally — linux_reader.py runs on the host
                    ref_path = os.path.join(SCRIPT_DIR, "_ref_players.json")
                    with open(ref_path, "wb") as f:
                        f.write(body)
                    self._json({"ok": True, "path": ref_path, "location": "host"})
                else:
                    # docker cp into the container
                    ref_path = "/tmp/icarus_players_ref.json"
                    container = _docker_container()
                    tmp = "/tmp/_icarus_ref_upload.json"
                    with open(tmp, "wb") as f:
                        f.write(body)
                    result = subprocess.run(
                        ["docker", "cp", tmp, f"{container}:{ref_path}"],
                        capture_output=True, timeout=15, text=True
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr.strip())
                    self._json({"ok": True, "container_path": ref_path, "location": "container"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        else:
            self.send_error(404)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def _serve_file(self, full_path):
        if not os.path.isfile(full_path):
            self.send_error(404); return
        mime = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        try:
            with open(full_path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(500); return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def _serve_static(self, url_path):
        safe = url_path.lstrip("/")
        full = os.path.normpath(os.path.join(SCRIPT_DIR, safe))
        if not full.startswith(os.path.abspath(SCRIPT_DIR)):
            self.send_error(403); return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        self._serve_file(full)

    def log_message(self, fmt, *args):
        pass


# ── Startup ───────────────────────────────────────────────────────────────────

def main():
    live       = CFG.get("live", {})
    docker_cfg = CFG.get("docker", {})
    container  = _docker_container()
    mode       = live.get("mode", "savegame")

    print(f"[+] Icarus Live Map (Linux)  →  http://0.0.0.0:{PORT}")
    print(f"    Setup page    : http://0.0.0.0:{PORT}/setup")
    print(f"    Savegames dir : {SAVEGAMES_DIR}")
    print(f"    Docker        : {container or '(not configured)'}")
    print(f"    Live mode     : {mode}", end="")
    if mode == "memory":
        print(f"  (PID {live.get('pid', '?')})")
    else:
        print(f"  ({live.get('host_save_path') or live.get('docker_save_path', '?')})")
    print(f"    Player poll   : every {POLL_PLAYERS}s")
    print("    Ctrl+C to stop\n")

    threading.Thread(target=_sg_scan_loop,     daemon=True, name="sg-scanner").start()
    threading.Thread(target=_player_poll_loop,  daemon=True, name="player-poll").start()

    server = ThreadingHTTPServer(("", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Stopped.")


if __name__ == "__main__":
    main()
