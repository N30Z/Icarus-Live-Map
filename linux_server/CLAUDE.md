# linux_server — Claude Knowledge Base

This document captures all hard-won knowledge about the Linux/Docker Icarus Live Map
deployment so future sessions start with full context.

---

## Deployment layout

The user copies the entire `linux_server/` folder to the Linux host and runs:

```bash
sudo python3 server.py      # must be root for /proc/<pid>/mem access
```

Confirmed working path on the target machine: `/home/tim/icarus_map/`

Files present:
- `server.py`        — HTTP server + player-poll + POI parse threads
- `linux_reader.py`  — reads `/proc/<pid>/mem` directly on the host
- `parser.py`        — self-contained UE4 binary parser (no imports from root)
- `config.json`      — runtime configuration (edit + restart)
- `index.html`       — live map UI (no file-upload, savegame selector)
- `setup.html`       — setup & debug page
- `savegames/`       — GD.json files pulled from the container (POI data)
- `_ref_players.json`— auto-generated reference file for --trace
- `offsets.json`     — discovered UE4 offsets (auto-saved by --trace)

---

## Docker / process topology

- Icarus dedicated server runs as a **Wine** process **inside** a Docker container.
- The Wine process (`IcarusServer-Win64-Shipping.exe`) is **visible from the host**
  via `ps aux` and `/proc/<pid>/maps` — no need to enter the container.
- Confirmed PIDs discovered via:
  ```
  docker top <container> -eo pid,comm,args | grep IcarusServer-Wi
  ```
  Two processes appear: `start.exe` (launcher) and `IcarusServer-Wi` (the game).
  Use the **IcarusServer-Wi** PID.

### Live mode: `host_memory` (recommended)

- `linux_reader.py` runs **directly on the host** using `sys.executable`
- Reads `/proc/<host-pid>/mem` directly — no docker exec needed
- Requires `sudo` (or `CAP_SYS_PTRACE`)
- Config: `live.mode = "host_memory"`, `live.pid = <host PID>`

### Live mode: `memory` (fallback — run reader inside container)

- `linux_reader.py` is `docker cp`'d into the container as `/tmp/icarus_reader.py`
- Server runs it via `docker exec <container> python3 /tmp/icarus_reader.py <pid>`
- `pid` here is the **container-visible PID** (different from host PID)

### Live mode: `savegame` (no memory reading)

- Server runs `docker exec <container> cat <save_path>` every poll interval
- Parses GD.json with `parser.py` for player positions
- Least accurate but most compatible

---

## Wine / `/proc/<pid>/maps` layout — critical insight

Wine maps the PE binary sections **as anonymous private pages** — they do NOT carry
the `.exe` filename in `/proc/maps`. Only 2 small header/data regions have the name.

**Symptom before fix:** `find_wine_module()` reported "2 readable regions, ~0 MB" →
GWorld scan found 0 hits.

**Fix in `find_wine_module()`:** After locating module base from named regions, walk
ALL contiguous regions starting from that base (allowing gaps ≤ 64 KB). This captures
the full 105 MB of code + data as readable regions.

Confirmed result after fix:
```
[+] Found module 'IcarusServer-Win64-Shipping.exe'
    base=0x140000000  size=110,190,592 bytes  (8 readable regions, 105 MB)
```

---

## GWorld discovery

Pattern: scan module readable regions for `MOV reg, [RIP+rel32]` instructions
followed by a NULL-check or compare context byte (`48 85`, `48 83`, `48 3B`).

Validated result (stable across restarts, changes only if server restarts with ASLR):
```
GWorld ptr  @ 0x1462ADC90
UWorld*     @ 0x7FB2F447D850  (vtable 0x145386CC8)
```

`find_gworld()` produces 4405 hits for `mov rbx,[rip+rel32]` and filters them
down to 1 valid GWorld via vtable validation.

---

## UE4 pointer chain

```
GWorld (0x1462ADC90)
  → UWorld* (deref)
    → GameState  @ UWorld + 0x30 or 0x38  (varies; scanned dynamically)
      → PlayerArray TArray @ GS + 0x90 or 0x98  (varies; scanned dynamically)
        → APlayerState[i]*
          → FString PlayerName  (offset scanned dynamically, see below)
          → APawn*  @ PS + OFF_PAWN_PRIVATE   (default 0x3A0 — needs tracing)
            → USceneComponent*  @ Pawn + OFF_ROOT_COMPONENT  (default 0x198)
              → FVector  @ Comp + OFF_REL_LOCATION  (default 0x11C)
                  X, Y, Z in centimeters
```

GameState and PlayerArray offsets vary between UWorld reloads — they are scanned
dynamically every read, not cached.

### Off_PAWN_PRIVATE / OFF_ROOT_COMPONENT / OFF_REL_LOCATION

Default values (Windows UE4) are `0x3A0 / 0x198 / 0x11C`.
These are **wrong** for this specific Wine/Proton build and produce coordinates near
origin (0.2 m, 0.5 m, 0.2 m). Run `--trace` to find correct values.

---

## PlayerState validation — false positives

`_find_playerarray()` originally validated only the **first** entry. This caused
false positives: `count=7` arrays where some entries had garbage pointers, leading
to an `OverflowError` when dereferencing.

**Fix:** validate **ALL** `count` entries via `_is_heap_uobj()` before accepting
the array. `_find_gs_playerarray()` picks the candidate with the **most valid entries**
rather than the first match.

---

## OverflowError in `os.pread`

**Root cause:** garbage pawn pointer (non-zero but invalid) passes `if not pawn`
check, then `pawn + OFF_ROOT_COMPONENT` exceeds signed 64-bit max →
`os.pread` raises `OverflowError: Python int too large to convert to C long`.

**Fix:** 
1. `read_bytes()` rejects any `addr` outside `[0x1000, 0x7FFFFFFFFFFF]` before
   calling `os.pread`.
2. `read_players()` validates `pawn` and `comp` with `_is_heap_uobj()` (bounds +
   vtable check) before dereferencing.

---

## Player name reading

`OFF_PLAYER_NAME = 0x368` is a static guess that is **wrong** for this build.
Symptom: player shows as `"Player0"` despite being online (e.g. the player was PETER).

**Fix:** `_scan_player_name()` function:
1. Tries the cached offset first (fast path)
2. If that returns empty / non-printable: scans `0x100–0x600` bytes of the
   PlayerState object for an FString (data_ptr + length pair) whose UTF-16LE
   content is printable, length 2–64 chars, doesn't start with `/`
3. Caches the winning offset globally for all subsequent reads
4. Falls back to `"Player{i}"` only if no valid FString found

---

## --trace workflow (finding correct offsets)

**Purpose:** scan live process memory for a known player coordinate, walk back
through the pointer chain to discover `OFF_PAWN_PRIVATE / OFF_ROOT_COMPONENT /
OFF_REL_LOCATION` for this specific build.

**Steps:**
1. Go to `/setup` → Section 4 (Memory Reader)
2. Click **"Use Savegame Data"** — reads GD.json from container, saves online
   player coordinates to `_ref_players.json`
3. Click **"Run --trace"** — SSE-streams `linux_reader.py --trace` output to browser
4. On success, offsets appear in Section 5 and are auto-saved to `offsets.json`

**Known bug fixed:** `trace_offsets()` had a Python operator-precedence bug:
```python
# WRONG — evaluates as: (data.get("players") or data) if isinstance(data, list) else []
players_list = data.get("players") or data if isinstance(data, list) else []

# CORRECT
if isinstance(data, list):
    players_list = data
else:
    players_list = data.get("players", [])
```
Since `data` is always a dict `{"players": [...]}`, the wrong form always
returned `[]` → "No online players in reference JSON".

**Server-side auto-population:** `/api/debug/trace` calls `_auto_populate_ref()`
before starting the trace thread. If `_ref_players.json` is missing or has no
online players, it automatically reads from the live GD.json and writes it.

---

## `pull_saves_from_container` — tilde expansion bug

`docker exec <container> find ~/path ...` does NOT expand `~` because `docker exec`
does not invoke a shell by default.

**Fix:** wrap in `sh -c`:
```python
_docker_exec(["sh", "-c", f"find '{prospects_path}' -name '*.json' -type f 2>&1"], ...)
```

---

## config.json structure

```json
{
  "port": 9090,
  "live": {
    "mode": "host_memory",
    "host_save_path": "",
    "docker_container": "d2d3ad8d0c66",
    "docker_save_path": "/home/container/Icarus/Saved/PlayerData/DedicatedServer/Prospects/GD.json",
    "pid": 2212089
  },
  "docker": {
    "container": "d2d3ad8d0c66",
    "prospects_path": "~/Icarus/Saved/PlayerData/DedicatedServer/Prospects"
  },
  "savegames_dir": "./savegames",
  "poll_interval_players": 5,
  "savegames_scan_interval": 30
}
```

`live.mode` values: `host_memory` | `memory` | `savegame`

---

## Key API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Live map UI |
| GET | `/setup` | Setup & debug page |
| GET | `/api/players` | Current player positions (cached) |
| GET | `/api/savegames` | List savegame files with parse status |
| GET | `/api/poi/<file>` | POI data; 202 while parsing |
| POST | `/api/config/save` | Update config.json |
| POST | `/api/pull-saves` | docker cp savegames from container |
| GET | `/api/debug/processes` | List Icarus/Wine PIDs (host first, then container) |
| GET | `/api/debug/test-read` | Run linux_reader.py once, return JSON |
| GET | `/api/debug/trace` | SSE stream of --trace output |
| GET | `/api/debug/ref-from-savegame` | Auto-populate ref JSON from live GD.json |
| POST | `/api/debug/upload-ref` | Upload reference players.json manually |
| GET/POST | `/api/debug/offsets` | Read / write offsets.json |

---

## Coordinate system

- UE4 stores positions in **centimeters**: `x_cm = x_m * 100`
- Map bounds: ±409,600 cm (±4096 m) for standard Olympus/Styx prospects
- `Y_SIGN = -1` in index.html to convert UE4 south-positive Y to Leaflet north-positive
- `toLatLng(x_m, y_m)` converts meter coords to Leaflet `[lat, lng]`

---

## Server threading

`ThreadingHTTPServer = ThreadingMixIn + HTTPServer` with `daemon_threads = True`.
Required because SSE (`/api/debug/trace`) holds the connection open while other
requests (config saves, player polls) must proceed concurrently.

POI parsing runs in background daemon threads to avoid blocking the HTTP handler
on large GD.json files (parse takes 2–10 s). Returns HTTP 202 while parsing,
client polls every 3 s until ready.

---

## Observed stable addresses (subject to change on server restart)

| Item | Address |
|------|---------|
| Module base | `0x140000000` |
| Module size | 110,190,592 bytes (~105 MB readable) |
| GWorld ptr | `0x1462ADC90` |
| vtable check | `0x145386CC8` (must be inside module range) |

These are **not hardcoded** — they are re-discovered on every `linux_reader.py`
invocation via pattern scan.
