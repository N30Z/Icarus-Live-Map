#!/usr/bin/env python3
"""
linux_reader.py — Live player positions via /proc/<pid>/mem (Linux).

Runs INSIDE the Docker container (uploaded there by server.py via docker cp).
Reads Icarus server process memory the same way memory_reader.py does on Windows,
but using Linux /proc/<pid>/mem instead of ReadProcessMemory.

Requirements in container:  Python 3.6+, root or CAP_SYS_PTRACE

Usage (run inside container):
  python3 /tmp/icarus_reader.py <pid>           # read once → JSON stdout
  python3 /tmp/icarus_reader.py <pid> --trace   # trace offsets → save offsets.json
  python3 /tmp/icarus_reader.py <pid> --loop 5  # loop every 5 s → stdout stream

The server calls this via:
  docker exec <container> python3 /tmp/icarus_reader.py <pid>
"""

import json
import math
import os
import struct
import sys
import time
import argparse

# ── Memory access via /proc/<pid>/mem ─────────────────────────────────────────

_mem_fd = -1
_pid    = 0


def init_mem(pid: int) -> bool:
    global _mem_fd, _pid
    _pid = pid
    path = f"/proc/{pid}/mem"
    try:
        _mem_fd = os.open(path, os.O_RDONLY)
        return True
    except PermissionError:
        print(f"[!] Cannot open {path}: Permission denied.", file=sys.stderr)
        print("    Run as root, or start Docker with --cap-add=SYS_PTRACE", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(f"[!] {path} not found — is PID {pid} correct?", file=sys.stderr)
        return False


def read_bytes(addr: int, size: int) -> bytes:
    if _mem_fd < 0 or size <= 0:
        return b"\x00" * size
    try:
        raw = os.pread(_mem_fd, size, addr)
        if len(raw) < size:
            raw = raw + b"\x00" * (size - len(raw))
        return raw
    except OSError:
        return b"\x00" * size


def read_ptr(addr: int) -> int:
    raw = read_bytes(addr, 8)
    v, = struct.unpack("<Q", raw)
    return v


def read_float3(addr: int):
    raw = read_bytes(addr, 12)
    return struct.unpack("<fff", raw)


def read_fstring(addr: int) -> str:
    """UE4 FString { TCHAR* data, int32 len, int32 max }."""
    data_ptr = read_ptr(addr)
    length, = struct.unpack("<i", read_bytes(addr + 8, 4))
    if data_ptr == 0 or length <= 0 or length > 512:
        return ""
    raw = read_bytes(data_ptr, length * 2)
    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")


# ── /proc/<pid>/maps parser ────────────────────────────────────────────────────

def parse_maps(pid: int) -> list:
    """Parse /proc/<pid>/maps → [(start, end, perms, name), ...]."""
    result = []
    try:
        with open(f"/proc/{pid}/maps", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 5:
                    continue
                lo, hi = parts[0].split("-")
                start, end = int(lo, 16), int(hi, 16)
                perms = parts[1]
                name = parts[5] if len(parts) > 5 else ""
                result.append((start, end, perms, name))
    except Exception as e:
        print(f"[!] parse_maps: {e}", file=sys.stderr)
    return result


class ModuleInfo:
    def __init__(self, base: int, size: int, name: str, regions: list):
        self.base    = base
        self.size    = size
        self.name    = name
        self.regions = regions   # [(start, end), ...] all readable regions


def find_wine_module(pid: int) -> ModuleInfo | None:
    """Find IcarusServer-Win64-Shipping.exe in the Wine process address space.

    Wine maps PE sections as anonymous private pages, so the exe filename only
    appears on a few small header/data regions.  We use the named regions to
    find the module base, then collect ALL contiguous regions from that base
    (named or anonymous) so that the full .text / .data are included.
    """
    maps = parse_maps(pid)

    EXE_NAMES = [
        "IcarusServer-Win64-Shipping.exe",
        "IcarusServer.exe",
        "Icarus-Win64-Shipping.exe",
    ]

    # Try to find by exe name in maps
    for exe in EXE_NAMES:
        exe_lower = exe.lower()
        named = [(s, e, p) for s, e, p, n in maps if exe_lower in n.lower()]
        if not named:
            continue

        base = min(s for s, e, p in named)

        # Collect ALL contiguous regions starting from base.
        # Wine remaps PE sections (especially .text) as anonymous pages — they
        # won't carry the exe filename.  Walk forward while gaps are ≤ 64 KB.
        sorted_maps = sorted([(s, e, p) for s, e, p, n in maps if s >= base],
                              key=lambda t: t[0])
        module_regions = []
        expected = base
        for s, e, p in sorted_maps:
            if s > expected + 0x10000:      # gap > 64 KB → different mapping
                break
            module_regions.append((s, e, p))
            expected = e

        if not module_regions:
            module_regions = named          # shouldn't happen, but be safe

        top      = max(e for s, e, p in module_regions)
        readable = [(s, e) for s, e, p in module_regions if "r" in p]

        total_mb = sum(e - s for s, e in readable) // (1024 * 1024)
        print(f"[+] Found module '{exe}' base=0x{base:X} size={top-base:,} bytes "
              f"({len(readable)} readable regions, {total_mb} MB)", file=sys.stderr)
        return ModuleInfo(base, top - base, exe, readable)

    # Fallback: find large anonymous executable regions (Wine may not show exe name)
    anon_exec = [(s, e, p) for s, e, p, n in maps if "x" in p and not n and e - s > 0x400000]
    if anon_exec:
        # Pick the largest contiguous block
        anon_exec.sort(key=lambda t: t[1] - t[0], reverse=True)
        s, e, p = anon_exec[0]
        print(f"[!] Exe not found by name; using largest anon executable region "
              f"0x{s:X}–0x{e:X} ({(e-s)//1024//1024} MB)", file=sys.stderr)
        readable = [(ss, ee) for ss, ee, pp in anon_exec if "r" in pp]
        return ModuleInfo(s, e - s, "anonymous", readable)

    print("[!] No Wine module found in /proc/maps", file=sys.stderr)
    return None


def all_readable_regions(pid: int) -> list:
    """All readable, committed memory regions for any address space scan."""
    maps = parse_maps(pid)
    return [(s, e - s) for s, e, p, n in maps if "r" in p and e > s]


# ── UE4 GWorld pattern scan ────────────────────────────────────────────────────

GWORLD_PATTERNS = [
    # mov rbx, [rip+rel32]  48 8B 1D
    (bytes([0x48, 0x8B, 0x1D]), 3, 7, "rbx"),
    # mov rax, [rip+rel32]  48 8B 05
    (bytes([0x48, 0x8B, 0x05]), 3, 7, "rax"),
    # mov rcx, [rip+rel32]  48 8B 0D
    (bytes([0x48, 0x8B, 0x0D]), 3, 7, "rcx"),
    # mov rdx, [rip+rel32]  48 8B 15
    (bytes([0x48, 0x8B, 0x15]), 3, 7, "rdx"),
    # mov r8,  [rip+rel32]  4C 8B 05
    (bytes([0x4C, 0x8B, 0x05]), 3, 7, "r8"),
]
GWORLD_CONTEXT = [bytes([0x48, 0x85]), bytes([0x48, 0x83]), bytes([0x48, 0x3B])]


def _scan_regions_for(regions: list, needle: bytes, chunk=0x100000) -> list:
    """Scan a list of (addr, size) regions for needle bytes."""
    overlap = len(needle) - 1
    results = []
    total   = sum(s for _, s in regions)
    done    = 0
    rep     = -1

    for (reg_addr, reg_size) in regions:
        off = 0
        while off < reg_size:
            mb = done >> 20
            if mb // 64 != rep:
                rep = mb // 64
                print(f"  scan {done//1_000_000}/{total//1_000_000} MB ...\r",
                      end="", flush=True, file=sys.stderr)
            read_sz = min(chunk, reg_size - off)
            data = read_bytes(reg_addr + off, read_sz)
            pos = 0
            while True:
                pos = data.find(needle, pos)
                if pos == -1:
                    break
                results.append(reg_addr + off + pos)
                pos += 1
            done += read_sz
            off  += chunk - overlap

    print(f"  scan {total//1_000_000}/{total//1_000_000} MB ... done  ",
          file=sys.stderr)
    return results


def find_gworld(mod: ModuleInfo) -> int:
    """Pattern-scan the module for GWorld pointer. Returns the ptr-to-ptr address."""
    print(f"[~] Scanning module for GWorld ({len(mod.regions)} regions, "
          f"~{sum(e-s for s,e in mod.regions)//1_000_000} MB) ...", file=sys.stderr)

    for (prefix, rel_off, instr_len, reg_name) in GWORLD_PATTERNS:
        hits = _scan_regions_for([(s, e-s) for s, e in mod.regions], prefix)
        print(f"    {len(hits)} hits for 'mov {reg_name},[rip+rel32]'", file=sys.stderr)

        for instr_addr in hits:
            after = read_bytes(instr_addr + instr_len, 2)
            if not any(after[:2] == ctx for ctx in GWORLD_CONTEXT):
                continue

            rel32, = struct.unpack("<i", read_bytes(instr_addr + rel_off, 4))
            candidate = instr_addr + instr_len + rel32

            val = read_ptr(candidate)
            if val < 0x10000 or val > 0x7FFFFFFFFFFF:
                continue
            if mod.base <= val < mod.base + mod.size:
                continue   # points into module → not a heap object

            vtable = read_ptr(val)
            if not (mod.base <= vtable < mod.base + mod.size):
                continue   # no valid vtable → not a UObject

            print(f"[+] GWorld ptr @ 0x{candidate:X}  →  UWorld* @ 0x{val:X}  "
                  f"(vtable 0x{vtable:X})", file=sys.stderr)
            return candidate

    return 0


# ── Player array helpers ──────────────────────────────────────────────────────

def _is_heap_uobj(addr: int, mod: ModuleInfo) -> bool:
    if addr < 0x10000 or addr > 0x7FFFFFFFFFFF:
        return False
    if mod.base <= addr < mod.base + mod.size:
        return False
    vtable = read_ptr(addr)
    return mod.base <= vtable < mod.base + mod.size


def _find_playerarray(handle_unused, game_state: int, mod: ModuleInfo):
    """Scan GameState bytes for TArray<APlayerState*>."""
    scan_size = 0x600
    raw = read_bytes(game_state, scan_size)
    for off in range(0, scan_size - 16, 8):
        data_ptr, = struct.unpack_from("<Q", raw, off)
        count,    = struct.unpack_from("<i", raw, off + 8)
        max_,     = struct.unpack_from("<i", raw, off + 12)
        if count < 1 or count > 32 or max_ < count or max_ > 128:
            continue
        if not (0x10000 <= data_ptr <= 0x7FFFFFFFFFFF):
            continue
        if mod.base <= data_ptr < mod.base + mod.size:
            continue
        first = read_ptr(data_ptr)
        if not _is_heap_uobj(first, mod):
            continue
        return data_ptr, count, off
    return 0, 0, 0


def _find_gs_playerarray(gworld: int, mod: ModuleInfo):
    """Scan UWorld for GameState, then scan GameState for PlayerArray."""
    raw = read_bytes(gworld, 0x300)
    for off in range(0, 0x300 - 8, 8):
        candidate, = struct.unpack_from("<Q", raw, off)
        if not _is_heap_uobj(candidate, mod):
            continue
        arr_data, arr_count, arr_off = _find_playerarray(None, candidate, mod)
        if arr_data:
            return candidate, arr_data, arr_count, off, arr_off
    return 0, 0, 0, 0, 0


# ── Offsets ───────────────────────────────────────────────────────────────────

OFF_PLAYER_NAME    = 0x368   # APlayerState → FString PlayerName

DEFAULT_OFFSETS = {
    "OFF_PAWN_PRIVATE":   0x3A0,
    "OFF_ROOT_COMPONENT": 0x198,
    "OFF_REL_LOCATION":   0x11C,
}

OFFSETS_FILE = "/tmp/icarus_offsets.json"


def load_offsets() -> dict:
    try:
        with open(OFFSETS_FILE) as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_OFFSETS)


def save_offsets(d: dict):
    with open(OFFSETS_FILE, "w") as f:
        json.dump(d, f, indent=2)
    print(f"[+] Offsets saved to {OFFSETS_FILE}", file=sys.stderr)


# ── Read players (live) ───────────────────────────────────────────────────────

def read_players(gworld_ptr_addr: int, mod: ModuleInfo, offsets: dict) -> list:
    gworld = read_ptr(gworld_ptr_addr)
    if not gworld:
        print("[!] GWorld is NULL", file=sys.stderr)
        return []

    game_state, arr_data, arr_count, gs_off, arr_off = _find_gs_playerarray(gworld, mod)
    if not game_state:
        print("[!] GameState / PlayerArray not found", file=sys.stderr)
        return []

    print(f"[+] GameState @ UWorld+0x{gs_off:X}  "
          f"PlayerArray @ GS+0x{arr_off:X}  count={arr_count}", file=sys.stderr)

    players = []
    for i in range(arr_count):
        ps_ptr = read_ptr(arr_data + i * 8)
        if not ps_ptr:
            continue
        name = read_fstring(ps_ptr + OFF_PLAYER_NAME) or f"Player{i}"
        pawn = read_ptr(ps_ptr + offsets["OFF_PAWN_PRIVATE"])
        if not pawn:
            players.append({"name": name, "online": False})
            continue
        comp = read_ptr(pawn + offsets["OFF_ROOT_COMPONENT"])
        if not comp:
            players.append({"name": name, "online": False})
            continue
        x, y, z = read_float3(comp + offsets["OFF_REL_LOCATION"])
        print(f"  [{name}]  {x/100:.1f}m  {y/100:.1f}m  {z/100:.1f}m", file=sys.stderr)
        players.append({"name": name, "online": True,
                         "x_m": round(x/100, 2), "y_m": round(y/100, 2), "z_m": round(z/100, 2)})
    return players


# ── Trace mode: find offsets from a reference players.json ───────────────────

MAP_LIMIT_CM = 500_000.0


def _scan_single_float(regions: list, target: float, tol: float, chunk=0x100000):
    needle_range = [struct.pack("<f", target + d * 0.01)
                    for d in range(-int(tol), int(tol) + 1, 100)]
    results = []
    for (reg_addr, reg_size) in regions:
        off = 0
        while off < reg_size:
            read_sz = min(chunk, reg_size - off)
            data = read_bytes(reg_addr + off, read_sz)
            for needle in needle_range:
                pos = 0
                while True:
                    pos = data.find(needle, pos)
                    if pos == -1:
                        break
                    fy, = struct.unpack_from("<f", data, pos)
                    results.append((reg_addr + off + pos, fy))
                    pos += 4
            off += chunk
    return results


def _find_object_base(addr: int, mod: ModuleInfo) -> int:
    for back in range(0, 0x800, 8):
        candidate = addr - back
        if candidate < 0x10000:
            break
        vtable = read_ptr(candidate)
        if not (mod.base <= vtable < mod.base + mod.size):
            continue
        vfunc0 = read_ptr(vtable)
        if mod.base <= vfunc0 < mod.base + mod.size:
            return candidate
    return 0


def trace_offsets(gworld_ptr_addr: int, mod: ModuleInfo, ref_json: str) -> dict | None:
    """
    Trace mode: find OFF_PAWN_PRIVATE / OFF_ROOT_COMPONENT / OFF_REL_LOCATION
    using reference coordinates from ref_json (players.json or savegame output).
    """
    try:
        with open(ref_json) as f:
            data = json.load(f)
    except Exception as e:
        print(f"[!] Cannot load {ref_json}: {e}", file=sys.stderr)
        return None

    # Support both {players:[...]} dict format and bare list format
    if isinstance(data, list):
        players_list = data
    else:
        players_list = data.get("players", [])
    targets = []
    for p in players_list:
        if not p.get("online"):
            continue
        xf = float(p.get("x") or p.get("x_m", 0) * 100)
        yf = float(p.get("y") or p.get("y_m", 0) * 100)
        zf = float(p.get("z") or p.get("z_m", 0) * 100)
        targets.append((p.get("character_name") or p.get("name", "?"), xf, yf, zf))

    if not targets:
        print("[!] No online players in reference JSON", file=sys.stderr)
        return None

    # GWorld chain
    gworld = read_ptr(gworld_ptr_addr)
    gs, arr_data, arr_count, gs_off, arr_off = _find_gs_playerarray(gworld, mod)
    if not gs:
        print("[!] GameState not found during trace", file=sys.stderr)
        return None
    print(f"[+] GameState @ UWorld+0x{gs_off:X}  "
          f"PlayerArray @ GS+0x{arr_off:X}  count={arr_count}", file=sys.stderr)

    regions = all_readable_regions(_pid)
    total_mb = sum(s for _, s in regions) // (1024 * 1024)
    print(f"[i] {len(regions)} readable regions  ~{total_mb} MB total", file=sys.stderr)

    TOL = 1000.0

    for pi in range(arr_count):
        ps_ptr = read_ptr(arr_data + pi * 8)
        if not ps_ptr:
            continue
        ps_name = read_fstring(ps_ptr + OFF_PLAYER_NAME)
        print(f"\n[+] PlayerState[{pi}] @ 0x{ps_ptr:X}  Name='{ps_name}'", file=sys.stderr)

        # Match reference player by name, fall back to index, then first entry
        ref = next((t for t in targets if t[0].lower() == ps_name.lower()), None)
        if ref is None:
            ref = targets[pi] if pi < len(targets) else targets[0]
        name, xf, yf, zf = ref
        print(f"[~] Reference: {name}  X={xf/100:.2f}m  Y={yf/100:.2f}m  Z={zf/100:.2f}m",
              file=sys.stderr)

        print("[~] Scanning for FVector (Y-anchor ±1000 cm) ...", file=sys.stderr)
        hits_y = _scan_single_float(regions, yf, TOL)
        good_hits = []
        for hit_addr, fy in hits_y:
            ctx = read_bytes(hit_addr - 4, 12)
            if len(ctx) < 12:
                continue
            hx, hy, hz = struct.unpack_from("<fff", ctx)
            if abs(hx - xf) < TOL and abs(hy - yf) < TOL and abs(hz - zf) < TOL:
                good_hits.append((hit_addr - 4, hx, hy, hz))

        if not good_hits:
            print("[!] FVector not found in memory — is the player online?", file=sys.stderr)
            continue

        best = min(good_hits, key=lambda t: abs(t[1]-xf)+abs(t[2]-yf)+abs(t[3]-zf))
        triplet_addr, hx, hy, hz = best
        print(f"[+] FVector @ 0x{triplet_addr:X}:  {hx/100:.2f}m  {hy/100:.2f}m  {hz/100:.2f}m",
              file=sys.stderr)

        component_base = _find_object_base(triplet_addr, mod)
        if not component_base:
            print("[!] Component vtable not found, using fallback", file=sys.stderr)
            for back in range(0, 0x200, 8):
                candidate = triplet_addr - back
                vtable = read_ptr(candidate)
                if 0x10000 < vtable < 0x7FFFFFFFFFFF:
                    component_base = candidate
                    break
        loc_offset = triplet_addr - component_base
        print(f"[+] Component @ 0x{component_base:X}  FVector offset +0x{loc_offset:X}",
              file=sys.stderr)

        PS_SCAN, PAWN_SCAN = 0x1200, 0x2000
        ps_raw = read_bytes(ps_ptr, PS_SCAN)
        for pawn_off in range(0, PS_SCAN - 8, 8):
            pawn_addr, = struct.unpack_from("<Q", ps_raw, pawn_off)
            if not _is_heap_uobj(pawn_addr, mod):
                continue
            pawn_raw = read_bytes(pawn_addr, PAWN_SCAN)
            for comp_off in range(0, len(pawn_raw) - 8, 8):
                v, = struct.unpack_from("<Q", pawn_raw, comp_off)
                if abs(v - component_base) < 0x10:
                    print(f"\n  *** OFFSET CHAIN FOUND ***", file=sys.stderr)
                    print(f"  PlayerState + 0x{pawn_off:03X}  → Pawn @ 0x{pawn_addr:X}",
                          file=sys.stderr)
                    print(f"  Pawn        + 0x{comp_off:03X}  → Component @ 0x{component_base:X}",
                          file=sys.stderr)
                    print(f"  Component   + 0x{loc_offset:03X}  → FVector", file=sys.stderr)
                    print(f"\n  OFF_PAWN_PRIVATE   = 0x{pawn_off:03X}", file=sys.stderr)
                    print(f"  OFF_ROOT_COMPONENT = 0x{comp_off:03X}", file=sys.stderr)
                    print(f"  OFF_REL_LOCATION   = 0x{loc_offset:03X}", file=sys.stderr)

                    offsets = {
                        "OFF_PAWN_PRIVATE":   pawn_off,
                        "OFF_ROOT_COMPONENT": comp_off,
                        "OFF_REL_LOCATION":   loc_offset,
                    }
                    save_offsets(offsets)
                    return offsets

        print(f"[!] No Pawn→Component chain found for player {pi}", file=sys.stderr)

    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Icarus live position reader (Linux)")
    ap.add_argument("pid", type=int, help="PID of IcarusServer process")
    ap.add_argument("--trace", action="store_true",
                    help="Trace mode: find offsets from reference players JSON")
    ap.add_argument("--ref", default="/tmp/icarus_players_ref.json",
                    help="Reference players.json for --trace mode")
    ap.add_argument("--loop", type=float, default=0,
                    help="Loop interval in seconds (0 = run once)")
    args = ap.parse_args()

    if not init_mem(args.pid):
        sys.exit(1)

    mod = find_wine_module(args.pid)
    if not mod:
        sys.exit(1)

    gworld_ptr = find_gworld(mod)
    if not gworld_ptr:
        print("[!] GWorld not found — wrong PID or wrong process?", file=sys.stderr)
        sys.exit(1)

    if args.trace:
        result = trace_offsets(gworld_ptr, mod, args.ref)
        if result:
            # Print result as JSON to stdout for the server to capture
            print(json.dumps({"status": "ok", "offsets": result}))
        else:
            print(json.dumps({"status": "error", "message": "Trace failed — see stderr"}))
        return

    offsets = load_offsets()
    print(f"[i] Offsets: PAWN={offsets['OFF_PAWN_PRIVATE']:#x}  "
          f"COMP={offsets['OFF_ROOT_COMPONENT']:#x}  "
          f"LOC={offsets['OFF_REL_LOCATION']:#x}", file=sys.stderr)

    def once():
        players = read_players(gworld_ptr, mod, offsets)
        print(json.dumps({"players": players, "ts": time.time()}), flush=True)

    once()
    if args.loop > 0:
        while True:
            time.sleep(args.loop)
            once()


if __name__ == "__main__":
    main()
