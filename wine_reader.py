"""
wine_reader.py  –  Live player positions from a Wine/Docker process
UE4.27 (Icarus Dedicated Server)

Läuft auf dem HOST (außerhalb Docker).
Der Wine-Prozess ist für den Host ein normaler Linux-Prozess –
/proc/<pid>/mem lässt sich direkt lesen (root erforderlich).

Kein --cap-add=SYS_PTRACE im Container nötig.

Abhängigkeiten:
    pip install psutil
    pip install numpy   (optional, schnellerer Scan)

Verwendung:
    sudo python wine_reader.py                    # einmalig → live_players.json
    sudo python wine_reader.py --loop 2           # alle 2 s → live_players.json
    sudo python wine_reader.py --serve 8081       # HTTP API auf :8081/players
    sudo python wine_reader.py --loop 2 --serve 8081
    sudo python wine_reader.py --trace            # Offsets ermitteln
"""

import json
import math
import os
import struct
import sys
import time
import argparse
import threading
import http.server
from concurrent.futures import ThreadPoolExecutor, as_completed

np = None
try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

try:
    import psutil
except ImportError:
    sys.exit("[!] psutil fehlt: pip install psutil")

if sys.platform == "win32":
    sys.exit("[!] wine_reader.py läuft nur auf Linux (Host außerhalb Docker)")


# ---------------------------------------------------------------------------
# Offsets (UE4.27, Icarus Dedicated Server)
# ---------------------------------------------------------------------------

OFF_PLAYER_NAME     = 0x368   # APlayerState    -> FString
OFF_PAWN_PRIVATE    = 0x3A0   # APlayerState    -> APawn*
OFF_ROOT_COMPONENT  = 0x198   # AActor          -> USceneComponent*
OFF_REL_LOCATION    = 0x11C   # USceneComponent -> FVector (cm)

# GWorld: MOV r64, [rip+rel32] Patterns (UE4.27 Shipping)
GWORLD_PATTERNS = [
    (bytes([0x48, 0x8B, 0x1D]), 3, 7),   # mov rbx, [rip+rel32]
    (bytes([0x48, 0x8B, 0x05]), 3, 7),   # mov rax, [rip+rel32]
    (bytes([0x48, 0x8B, 0x0D]), 3, 7),   # mov rcx, [rip+rel32]
    (bytes([0x48, 0x8B, 0x15]), 3, 7),   # mov rdx, [rip+rel32]
    (bytes([0x4C, 0x8B, 0x05]), 3, 7),   # mov r8,  [rip+rel32]
]
GWORLD_CONTEXT = [
    bytes([0x48, 0x85]),   # test reg, reg
    bytes([0x48, 0x83]),   # cmp/test r64, imm8
    bytes([0x48, 0x3B]),   # cmp r64, r/m64
]

MAP_LIMIT_CM = 500_000.0


# ---------------------------------------------------------------------------
# ModuleInfo
# ---------------------------------------------------------------------------

class ModuleInfo:
    def __init__(self, base: int, size: int, name: str):
        self.base = base
        self.size = size
        self.name = name


# ---------------------------------------------------------------------------
# Linux-Prozess-Schicht (/proc/<pid>/mem + /proc/<pid>/maps)
# ---------------------------------------------------------------------------

class Process:
    """Kapselt Lese-Zugriff auf einen laufenden Linux-Prozess."""

    def __init__(self, pid: int):
        self.pid = pid
        mem_path = f"/proc/{pid}/mem"
        try:
            self._mem = open(mem_path, "rb")
        except PermissionError:
            sys.exit(
                f"[!] Kein Zugriff auf {mem_path}\n"
                "    → als root ausführen:  sudo python wine_reader.py"
            )
        except FileNotFoundError:
            sys.exit(f"[!] Prozess PID {pid} existiert nicht (mehr)")

    # -- Speicher lesen ------------------------------------------------------

    def read_bytes(self, address: int, size: int) -> bytes:
        try:
            self._mem.seek(address)
            data = self._mem.read(size)
        except OSError:
            return b"\x00" * size
        if len(data) < size:
            data += b"\x00" * (size - len(data))
        return data

    # -- Modul-Info aus /proc/maps -------------------------------------------

    def get_module(self, module_name: str) -> ModuleInfo | None:
        """
        Sucht den ersten Treffer für module_name im Pfad einer /proc/maps-Zeile.
        Gibt die gesamte Spanne aller gematchten Segmente zurück.
        """
        name_lower = module_name.lower()
        base = None
        end  = None
        try:
            with open(f"/proc/{self.pid}/maps") as f:
                for line in f:
                    parts = line.split()
                    path  = parts[5] if len(parts) > 5 else ""
                    if name_lower not in path.lower():
                        continue
                    lo, hi = parts[0].split("-")
                    seg_start = int(lo, 16)
                    seg_end   = int(hi, 16)
                    if base is None or seg_start < base:
                        base = seg_start
                    if end is None or seg_end > end:
                        end = seg_end
        except OSError as e:
            print(f"[!] maps lesen fehlgeschlagen: {e}")
            return None

        if base is None:
            return None
        return ModuleInfo(base, end - base, module_name)

    # -- Lesbare Regionen in einem Modul -------------------------------------

    def readable_regions_in_module(self, mod: ModuleInfo) -> list:
        regions = []
        try:
            with open(f"/proc/{self.pid}/maps") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 2 or "r" not in parts[1]:
                        continue
                    lo, hi    = parts[0].split("-")
                    seg_start = int(lo, 16)
                    seg_end   = int(hi, 16)
                    if seg_end <= mod.base or seg_start >= mod.base + mod.size:
                        continue
                    actual_start = max(seg_start, mod.base)
                    actual_end   = min(seg_end,   mod.base + mod.size)
                    if actual_end > actual_start:
                        regions.append((actual_start, actual_end - actual_start))
        except OSError:
            pass
        return regions

    # -- Alle lesbaren Regionen (gesamter Prozess) ---------------------------

    def all_readable_regions(self) -> list:
        regions = []
        try:
            with open(f"/proc/{self.pid}/maps") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 2 or "r" not in parts[1]:
                        continue
                    lo, hi = parts[0].split("-")
                    regions.append((int(lo, 16), int(hi, 16) - int(lo, 16)))
        except OSError:
            pass
        return regions

    def close(self):
        self._mem.close()


# ---------------------------------------------------------------------------
# Wrapper-Funktionen (gleiche Signatur wie altes memory_reader.py)
# ---------------------------------------------------------------------------

def read_bytes(proc: Process, address: int, size: int) -> bytes:
    return proc.read_bytes(address, size)


def read_ptr(proc: Process, address: int) -> int:
    raw = proc.read_bytes(address, 8)
    val, = struct.unpack("<Q", raw)
    return val


def read_float3(proc: Process, address: int):
    raw = proc.read_bytes(address, 12)
    return struct.unpack("<fff", raw)


def read_fstring(proc: Process, address: int) -> str:
    data_ptr = read_ptr(proc, address)
    length,  = struct.unpack("<i", proc.read_bytes(address + 8, 4))
    if data_ptr == 0 or length <= 0 or length > 512:
        return ""
    raw = proc.read_bytes(data_ptr, length * 2)
    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")


def get_module_info(proc: Process, module_name: str) -> ModuleInfo | None:
    return proc.get_module(module_name)


def readable_regions_in_module(proc: Process, mod: ModuleInfo) -> list:
    return proc.readable_regions_in_module(mod)


def _all_readable_regions(proc: Process) -> list:
    return proc.all_readable_regions()


# ---------------------------------------------------------------------------
# UE4-Diagnose
# ---------------------------------------------------------------------------

def diag_readability(proc: Process, mod: ModuleInfo) -> None:
    regions = readable_regions_in_module(proc, mod)
    total   = sum(s for _, s in regions)
    print(f"[i] Modul {mod.name}  Basis=0x{mod.base:X}  Größe={mod.size//1024//1024} MB")
    print(f"[i] Lesbare Regions: {len(regions)}  gesamt {total//1024//1024} MB")
    if regions:
        test_addr, test_size = regions[0]
        sample   = proc.read_bytes(test_addr, min(16, test_size))
        non_null = sum(1 for b in sample if b != 0)
        print(f"[i] Probe @ 0x{test_addr:X}: {sample.hex()}  ({non_null}/16 nicht-null)")
        if non_null == 0:
            print("[!] Alle Bytes = 0 → Zugriff verweigert (root?)")


# ---------------------------------------------------------------------------
# Region-Scan
# ---------------------------------------------------------------------------

def scan_regions(proc: Process, regions: list, needle: bytes) -> list:
    chunk_size = 0x100000
    overlap    = len(needle) - 1
    results    = []
    total      = sum(s for _, s in regions)
    scanned    = 0
    reported   = -1

    for reg_addr, reg_size in regions:
        offset = 0
        while offset < reg_size:
            mb = scanned >> 20
            if mb // 32 != reported:
                reported = mb // 32
                print(f"    scan {scanned//1_000_000:4d} / {total//1_000_000} MB ...", end="\r")
            read_sz = min(chunk_size, reg_size - offset)
            chunk   = proc.read_bytes(reg_addr + offset, read_sz)
            pos = 0
            while True:
                pos = chunk.find(needle, pos)
                if pos == -1:
                    break
                results.append(reg_addr + offset + pos)
                pos += 1
            scanned += read_sz
            offset  += chunk_size - overlap

    print(f"    scan {total//1_000_000:4d} / {total//1_000_000} MB ... fertig")
    return results


# ---------------------------------------------------------------------------
# GWorld finden
# ---------------------------------------------------------------------------

def find_gworld(proc: Process, mod: ModuleInfo) -> int:
    diag_readability(proc, mod)
    regions = readable_regions_in_module(proc, mod)
    if not regions:
        return 0

    for prefix, rel_off, instr_len in GWORLD_PATTERNS:
        reg_name = {0x1D: "rbx", 0x05: "rax", 0x0D: "rcx",
                    0x15: "rdx"}.get(prefix[-1], "r??")
        if prefix[0] == 0x4C:
            reg_name = "r8"
        total_mb = sum(s for _, s in regions) // 1_000_000
        print(f"[~] Suche 'mov {reg_name}, [rip+rel32]'  ({total_mb} MB lesbar)...")

        hits = scan_regions(proc, regions, prefix)
        print(f"    {len(hits)} Treffer für {prefix.hex()}")

        for instr_addr in hits:
            after = proc.read_bytes(instr_addr + instr_len, 2)
            if not any(after.startswith(ctx) for ctx in GWORLD_CONTEXT):
                continue

            rel_bytes = proc.read_bytes(instr_addr + rel_off, 4)
            rel32, = struct.unpack("<i", rel_bytes)
            candidate = instr_addr + instr_len + rel32

            val = read_ptr(proc, candidate)
            if val < 0x10000 or val > 0x7FFFFFFFFFFF:
                continue
            if mod.base <= val < mod.base + mod.size:
                continue

            vtable = read_ptr(proc, val)
            if not (mod.base <= vtable < mod.base + mod.size):
                continue

            print(f"[+] GWorld @ 0x{instr_addr:X}  →  ptr 0x{candidate:X}  →  UWorld* 0x{val:X}")
            return candidate

    return 0


# ---------------------------------------------------------------------------
# PlayerArray automatisch finden
# ---------------------------------------------------------------------------

def _find_player_array(proc: Process, game_state: int, mod: ModuleInfo):
    scan_size = 0x600
    raw = proc.read_bytes(game_state, scan_size)

    for off in range(0, scan_size - 16, 8):
        data_ptr, = struct.unpack_from("<Q", raw, off)
        count,    = struct.unpack_from("<i", raw, off + 8)
        max_,     = struct.unpack_from("<i", raw, off + 12)

        if count < 1 or count > 32:
            continue
        if max_ < count or max_ > 128:
            continue
        if data_ptr < 0x10000 or data_ptr > 0x7FFFFFFFFFFF:
            continue
        if mod.base <= data_ptr < mod.base + mod.size:
            continue

        first_elem = read_ptr(proc, data_ptr)
        if first_elem < 0x10000 or first_elem > 0x7FFFFFFFFFFF:
            continue
        if mod.base <= first_elem < mod.base + mod.size:
            continue
        vtable = read_ptr(proc, first_elem)
        if not (mod.base <= vtable < mod.base + mod.size):
            continue

        return data_ptr, count, off

    return 0, 0, 0


def _find_gamestate_and_playerarray(proc: Process, gworld: int, mod: ModuleInfo):
    scan_size = 0x300
    raw = proc.read_bytes(gworld, scan_size)

    for off in range(0, scan_size - 8, 8):
        candidate, = struct.unpack_from("<Q", raw, off)
        if candidate < 0x10000 or candidate > 0x7FFFFFFFFFFF:
            continue
        if mod.base <= candidate < mod.base + mod.size:
            continue
        vtable = read_ptr(proc, candidate)
        if not (mod.base <= vtable < mod.base + mod.size):
            continue

        arr_data, arr_count, arr_off = _find_player_array(proc, candidate, mod)
        if arr_data != 0:
            return candidate, arr_data, arr_count, off, arr_off

    return 0, 0, 0, 0, 0


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _is_heap_uobject(proc: Process, addr: int, mod: ModuleInfo) -> bool:
    if addr < 0x10000 or addr > 0x7FFFFFFFFFFF:
        return False
    if mod.base <= addr < mod.base + mod.size:
        return False
    vtable = read_ptr(proc, addr)
    return mod.base <= vtable < mod.base + mod.size


def _scan_for_location(raw: bytes) -> list:
    results = []
    for off in range(0, len(raw) - 12, 4):
        x, y, z = struct.unpack_from("<fff", raw, off)
        if any(math.isnan(v) or math.isinf(v) for v in (x, y, z)):
            continue
        if max(abs(x), abs(y), abs(z)) > MAP_LIMIT_CM:
            continue
        if max(abs(x), abs(y), abs(z)) < 100.0:
            continue
        results.append((off, x, y, z))
    return results


# ---------------------------------------------------------------------------
# Spieler lesen
# ---------------------------------------------------------------------------

def _load_offsets(path="offsets.json") -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def read_players(proc: Process, gworld_ptr_addr: int, mod: ModuleInfo,
                 offsets: dict) -> list:
    gworld = read_ptr(proc, gworld_ptr_addr)
    if not gworld:
        print("[!] GWorld ist NULL")
        return []

    print(f"[i] UWorld @ 0x{gworld:X} – suche GameState+PlayerArray...")
    game_state, arr_data, arr_count, gs_off, _ = \
        _find_gamestate_and_playerarray(proc, gworld, mod)

    if not game_state:
        print("[!] Kein GameState/PlayerArray gefunden")
        return []

    print(f"[+] GameState @ UWorld+0x{gs_off:X} = 0x{game_state:X}  count={arr_count}")

    off_name = offsets.get("OFF_PLAYER_NAME",    OFF_PLAYER_NAME)
    off_pawn = offsets.get("OFF_PAWN_PRIVATE",   OFF_PAWN_PRIVATE)
    off_comp = offsets.get("OFF_ROOT_COMPONENT", OFF_ROOT_COMPONENT)
    off_loc  = offsets.get("OFF_REL_LOCATION",   OFF_REL_LOCATION)

    players = []
    for i in range(arr_count):
        ps_ptr = read_ptr(proc, arr_data + i * 8)
        if not ps_ptr:
            continue

        name = read_fstring(proc, ps_ptr + off_name)

        pawn = read_ptr(proc, ps_ptr + off_pawn)
        if not pawn:
            players.append({"name": name or f"Player{i}", "online": False})
            continue

        comp = read_ptr(proc, pawn + off_comp)
        if not comp:
            players.append({"name": name or f"Player{i}", "online": False})
            continue

        x, y, z = read_float3(proc, comp + off_loc)
        players.append({
            "name":   name or f"Player{i}",
            "online": True,
            "x_m":    round(x / 100, 2),
            "y_m":    round(y / 100, 2),
            "z_m":    round(z / 100, 2),
        })
        print(f"  [{name or f'Player{i}'}]  "
              f"({x/100:.1f}m, {y/100:.1f}m, {z/100:.1f}m)")

    return players


# ---------------------------------------------------------------------------
# Offset-Trace (einmalige Kalibrierung)
# ---------------------------------------------------------------------------

def trace_pointer_chain(proc: Process, mod: ModuleInfo, gworld_ptr_addr: int,
                        ref_xyz: tuple) -> None:
    ref_x, ref_y, ref_z = ref_xyz
    TOL = 500.0
    print(f"[~] Referenz: X={ref_x/100:.2f}m  Y={ref_y/100:.2f}m  Z={ref_z/100:.2f}m")

    gworld = read_ptr(proc, gworld_ptr_addr)
    if not gworld:
        print("[!] GWorld ist NULL")
        return

    game_state, arr_data, arr_count, gs_off, _ = \
        _find_gamestate_and_playerarray(proc, gworld, mod)
    if not game_state:
        print("[!] GameState/PlayerArray nicht gefunden")
        return

    print(f"[+] GameState @ UWorld+0x{gs_off:X}  PlayerArray count={arr_count}")

    PS_SCAN   = 0x1200
    PAWN_SCAN = 0x3000
    COMP_SCAN = 0x400

    for i in range(arr_count):
        ps_ptr = read_ptr(proc, arr_data + i * 8)
        if not ps_ptr:
            continue
        name   = read_fstring(proc, ps_ptr + OFF_PLAYER_NAME)
        ps_raw = proc.read_bytes(ps_ptr, PS_SCAN)

        for pawn_off in range(0, PS_SCAN - 8, 8):
            pawn_addr, = struct.unpack_from("<Q", ps_raw, pawn_off)
            if not _is_heap_uobject(proc, pawn_addr, mod):
                continue
            pawn_raw = proc.read_bytes(pawn_addr, PAWN_SCAN)

            for comp_off in range(0, PAWN_SCAN - 8, 8):
                comp_addr, = struct.unpack_from("<Q", pawn_raw, comp_off)
                if not _is_heap_uobject(proc, comp_addr, mod):
                    continue
                comp_raw = proc.read_bytes(comp_addr, COMP_SCAN)

                for loc_off, x, y, z in _scan_for_location(comp_raw):
                    if (abs(x - ref_x) < TOL and
                            abs(y - ref_y) < TOL and
                            abs(z - ref_z) < TOL):
                        print(f"\n*** MATCH [{name}] ***")
                        print(f"  Pawn      +0x{pawn_off:X}")
                        print(f"  Component +0x{comp_off:X}")
                        print(f"  FVector   +0x{loc_off:X}  "
                              f"({x/100:.2f}m, {y/100:.2f}m, {z/100:.2f}m)")
                        offsets = {
                            "OFF_PAWN_PRIVATE":   pawn_off,
                            "OFF_ROOT_COMPONENT": comp_off,
                            "OFF_REL_LOCATION":   loc_off,
                        }
                        with open("offsets.json", "w") as f:
                            json.dump(offsets, f, indent=2)
                        print("[+] Offsets → offsets.json")
                        return

    print("[!] Keine Kette gefunden")


# ---------------------------------------------------------------------------
# Prozess finden (Host-Sicht, Wine-Prozess im Docker-Container)
# ---------------------------------------------------------------------------

PROCESS_NAMES = [
    "IcarusServer-Win64-Shipping.exe",
    "IcarusServer.exe",
    "Icarus-Win64-Shipping.exe",
    "ICARUS.exe",
]


def find_icarus_pid() -> tuple[int | None, str | None]:
    """
    Sucht den Wine-Prozess auf dem Host.

    Prüft:
      1. /proc/<pid>/comm  (kann auf 15 Zeichen gekürzt sein)
      2. /proc/<pid>/cmdline  (enthält den vollen .exe-Pfad)
    """
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name    = proc.info["name"] or ""
            cmdline = proc.info.get("cmdline") or []

            for candidate in PROCESS_NAMES:
                # vollständiger Name-Match
                if name.lower() == candidate.lower():
                    return proc.info["pid"], candidate
                # gekürzter Name (Linux: max 15 Zeichen in /proc/comm)
                if name.lower() == candidate.lower()[:15]:
                    return proc.info["pid"], candidate
                # cmdline enthält den .exe-Namen (Wine-typisch)
                if any(candidate.lower() in c.lower() for c in cmdline):
                    return proc.info["pid"], candidate
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None, None


def list_icarus_processes() -> None:
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name    = proc.info["name"] or ""
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "icarus" in name.lower() or "icarus" in cmdline.lower():
                found.append((proc.info["pid"], name, cmdline[:80]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if not found:
        print("[!] Keine Icarus-Prozesse gefunden")
    else:
        print(f"{'PID':>8}  {'Name':<25}  cmdline")
        print("-" * 80)
        for pid, name, cmd in found:
            print(f"{pid:>8}  {name:<25}  {cmd}")


# ---------------------------------------------------------------------------
# HTTP API (optional, für server.py auf dem Host oder remote)
# ---------------------------------------------------------------------------

_api_lock    = threading.Lock()
_api_players = {"timestamp": 0, "player_count": 0, "players": []}


class _APIHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/players", "/players/"):
            with _api_lock:
                body = json.dumps(_api_players, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


def _start_api_server(port: int):
    server = http.server.HTTPServer(("0.0.0.0", port), _APIHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[+] HTTP API  →  http://0.0.0.0:{port}/players")
    return server


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Icarus Live Map – Wine/Docker memory reader (Host-seitig)"
    )
    parser.add_argument("--loop",    type=float, default=0, metavar="SECS",
                        help="Aktualisierungsintervall in Sekunden (0 = einmalig)")
    parser.add_argument("--output",  default="live_players.json",
                        help="Ausgabe-JSON (default: live_players.json)")
    parser.add_argument("--serve",   type=int, default=0, metavar="PORT",
                        help="HTTP API auf diesem Port starten (zusätzlich zu --loop)")
    parser.add_argument("--pid",     type=int, default=None,
                        help="Direkt per PID verbinden")
    parser.add_argument("--list",    action="store_true",
                        help="Alle Icarus-Prozesse anzeigen")
    parser.add_argument("--trace",   action="store_true",
                        help="Offsets ermitteln (braucht --ref-xyz oder players.json)")
    parser.add_argument("--ref-xyz", default=None, metavar="X,Y,Z",
                        help="Referenzkoordinaten in cm für --trace (z.B. 12345,67890,100)")
    args = parser.parse_args()

    if args.list:
        list_icarus_processes()
        return

    # Prozess finden
    if args.pid:
        try:
            p = psutil.Process(args.pid)
            pid, proc_name = p.pid, p.name()
        except psutil.NoSuchProcess:
            sys.exit(f"[!] PID {args.pid} nicht gefunden")
    else:
        pid, proc_name = find_icarus_pid()

    if not pid:
        print("[!] Kein Icarus-Prozess gefunden.")
        print("    Laufende Icarus-Prozesse:")
        list_icarus_processes()
        sys.exit(1)

    print(f"[+] Prozess: {proc_name}  PID: {pid}")

    proc = Process(pid)
    try:
        mod = get_module_info(proc, proc_name)
        if not mod:
            # Fallback: suche nach dem .exe-Namen im maps-File
            for name in PROCESS_NAMES:
                mod = get_module_info(proc, name)
                if mod:
                    break

        if not mod:
            sys.exit(f"[!] Modul '{proc_name}' nicht in /proc/{pid}/maps gefunden\n"
                     "    Tipp: sudo python wine_reader.py --list")

        print(f"[+] Modul-Basis: 0x{mod.base:X}  Größe: {mod.size//1024//1024} MB")

        offsets = _load_offsets()
        if not offsets and not args.trace:
            print("[!] Keine offsets.json – bitte einmal mit --trace ausführen")
            return

        gworld_ptr_addr = find_gworld(proc, mod)
        if not gworld_ptr_addr:
            sys.exit("[!] GWorld nicht gefunden – Pattern passt nicht für diese Version")

        # -- Offset-Trace --
        if args.trace:
            ref_xyz = None
            if args.ref_xyz:
                try:
                    ref_xyz = tuple(float(v) for v in args.ref_xyz.split(","))
                    assert len(ref_xyz) == 3
                except Exception:
                    sys.exit("[!] --ref-xyz muss 'X,Y,Z' in cm sein")
            else:
                # Aus players.json laden (von parse_players.py)
                for path in ("players.json", "live_players.json"):
                    if not os.path.exists(path):
                        continue
                    with open(path) as f:
                        data = json.load(f)
                    for p in data.get("players", []):
                        if p.get("online"):
                            x = float(p.get("x") or p.get("x_m", 0) * 100)
                            y = float(p.get("y") or p.get("y_m", 0) * 100)
                            z = float(p.get("z") or p.get("z_m", 0) * 100)
                            ref_xyz = (x, y, z)
                            break
                    if ref_xyz:
                        break

            if not ref_xyz:
                sys.exit("[!] Keine Referenzkoordinaten – "
                         "--ref-xyz X,Y,Z angeben oder players.json bereitstellen")

            trace_pointer_chain(proc, mod, gworld_ptr_addr, ref_xyz)
            return

        # -- HTTP API starten (wenn gewünscht) --
        if args.serve:
            _start_api_server(args.serve)

        print(f"[+] Schreibe → '{args.output}'" +
              (f" alle {args.loop}s" if args.loop else " (einmalig)"))

        # -- Lese-Schleife --
        while True:
            players = read_players(proc, gworld_ptr_addr, mod, offsets)

            output = {
                "timestamp":    time.time(),
                "player_count": sum(1 for p in players if p.get("online")),
                "players":      players,
            }

            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)

            if args.serve:
                with _api_lock:
                    _api_players.clear()
                    _api_players.update(output)

            if not args.loop:
                break
            time.sleep(args.loop)

    finally:
        proc.close()


if __name__ == "__main__":
    main()
