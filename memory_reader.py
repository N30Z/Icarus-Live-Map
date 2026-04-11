"""
memory_reader.py  –  Live player positions via ReadProcessMemory
UE4.27 (Icarus Dedicated Server / Client)

Requirements:
    pip install psutil pywin32

Usage:
    python memory_reader.py               # einmalige Ausgabe
    python memory_reader.py --loop 2      # alle 2 Sekunden -> players.json
"""

import ctypes
import ctypes.wintypes as wintypes
import struct
import json
import time
import argparse
import sys

def load_offsets(path="offsets.json"):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return None
    
try:
    import psutil
except ImportError:
    sys.exit("[!] psutil fehlt: pip install psutil")

# ---------------------------------------------------------------------------
# Windows API
# ---------------------------------------------------------------------------

PROCESS_VM_READ       = 0x0010
PROCESS_QUERY_INFO    = 0x0400
TH32CS_SNAPMODULE     = 0x00000008
TH32CS_SNAPMODULE32   = 0x00000010

kernel32 = ctypes.windll.kernel32


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize",             wintypes.DWORD),
        ("th32ModuleID",       wintypes.DWORD),
        ("th32ProcessID",      wintypes.DWORD),
        ("GlblcntUsage",       wintypes.DWORD),
        ("ProccntUsage",       wintypes.DWORD),
        ("modBaseAddr",        ctypes.POINTER(wintypes.BYTE)),
        ("modBaseSize",        wintypes.DWORD),
        ("hModule",            wintypes.HMODULE),
        ("szModule",           ctypes.c_char * 256),
        ("szExePath",          ctypes.c_char * 260),
    ]


# ---------------------------------------------------------------------------
# UE4.27 Offsets
# ---------------------------------------------------------------------------

# UWorld
OFF_GSTATE          = 0x038   # UWorld          -> AGameStateBase*  (Icarus verified)

# AGameStateBase
OFF_PLAYERARRAY     = 0x090   # AGameStateBase  -> TArray<APlayerState*>  (Icarus verified)
                               #   TArray: [ptr 8B][count 4B][max 4B]

# APlayerState
OFF_PAWN_PRIVATE    = 0x3A0   # APlayerState    -> APawn* (PawnPrivate)
OFF_PLAYER_NAME     = 0x368   # APlayerState    -> FString (PlayerName) [ptr 8B][len 4B][max 4B]

# AActor / APawn
OFF_ROOT_COMPONENT  = 0x198   # AActor          -> USceneComponent*

# USceneComponent
OFF_REL_LOCATION    = 0x11C   # USceneComponent -> FVector { f32 X, Y, Z }  (in cm)
OFF_REL_ROTATION    = 0x128   # USceneComponent -> FRotator { f32 Pitch, Yaw, Roll }

# GWorld patterns (UE4.27 Shipping) – verschiedene Register-Varianten
# Format: (pattern_bytes, mask, rel32_offset, instr_total_len)
#   rel32_offset = Byte-Position des 4-Byte-Offsets innerhalb der Instruktion
#   instr_total_len = Länge der kompletten MOV-Instruktion (RIP-Basis = instr_addr + instr_total_len)
GWORLD_PATTERNS = [
    # mov rbx, [rip+rel32]  ;  48 8B 1D xx xx xx xx
    (bytes([0x48, 0x8B, 0x1D]), "xxx", 3, 7),
    # mov rax, [rip+rel32]  ;  48 8B 05 xx xx xx xx
    (bytes([0x48, 0x8B, 0x05]), "xxx", 3, 7),
    # mov rcx, [rip+rel32]  ;  48 8B 0D xx xx xx xx
    (bytes([0x48, 0x8B, 0x0D]), "xxx", 3, 7),
    # mov rdx, [rip+rel32]  ;  48 8B 15 xx xx xx xx
    (bytes([0x48, 0x8B, 0x15]), "xxx", 3, 7),
    # mov r8,  [rip+rel32]  ;  4C 8B 05 xx xx xx xx
    (bytes([0x4C, 0x8B, 0x05]), "xxx", 3, 7),
]
# Kontextbytes nach der MOV-Instruktion um False-Positives zu filtern:
# test reg, reg  (48 85 xx) oder cmp reg, 0 (48 83 F8 00)
GWORLD_CONTEXT = [
    bytes([0x48, 0x85]),  # test reg, reg
    bytes([0x48, 0x83]),  # cmp/test r64, imm8
    bytes([0x48, 0x3B]),  # cmp r64, r/m64
]


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def open_process(pid: int):
    handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFO, False, pid)
    if not handle:
        raise PermissionError(f"OpenProcess({pid}) fehlgeschlagen – als Admin ausführen?")
    return handle


def read_bytes(handle, address: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
    )
    if not ok or read.value != size:
        return b'\x00' * size
    return buf.raw


def read_ptr(handle, address: int) -> int:
    """Liest einen 64-bit Pointer."""
    raw = read_bytes(handle, address, 8)
    val, = struct.unpack('<Q', raw)
    return val


def read_float3(handle, address: int):
    raw = read_bytes(handle, address, 12)
    return struct.unpack('<fff', raw)


def read_float3_rotator(handle, address: int):
    raw = read_bytes(handle, address, 12)
    return struct.unpack('<fff', raw)  # pitch, yaw, roll


def read_fstring(handle, address: int) -> str:
    """Liest eine UE4 FString { TCHAR* data, int32 len, int32 max }."""
    data_ptr = read_ptr(handle, address)
    length, = struct.unpack('<i', read_bytes(handle, address + 8, 4))
    if data_ptr == 0 or length <= 0 or length > 512:
        return ""
    raw = read_bytes(handle, data_ptr, length * 2)
    return raw.decode('utf-16-le', errors='replace').rstrip('\x00')


# ---------------------------------------------------------------------------
# Modul-Basis + Größe ermitteln (MODULEENTRY32)
# ---------------------------------------------------------------------------

class ModuleInfo:
    def __init__(self, base: int, size: int, name: str):
        self.base = base
        self.size = size
        self.name = name


def get_module_info(pid: int, module_name: str) -> ModuleInfo | None:
    snap = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snap == wintypes.HANDLE(-1).value:
        raise RuntimeError("CreateToolhelp32Snapshot fehlgeschlagen")

    entry = MODULEENTRY32()
    entry.dwSize = ctypes.sizeof(MODULEENTRY32)

    mod_name_lower = module_name.lower().encode()
    result = None

    if kernel32.Module32First(snap, ctypes.byref(entry)):
        while True:
            if entry.szModule.lower() == mod_name_lower:
                base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                result = ModuleInfo(base, entry.modBaseSize,
                                    entry.szModule.decode(errors='replace'))
                break
            if not kernel32.Module32Next(snap, ctypes.byref(entry)):
                break

    kernel32.CloseHandle(snap)
    return result


# ---------------------------------------------------------------------------
# VirtualQueryEx – nur lesbare Regions scannen
# ---------------------------------------------------------------------------

MEM_COMMIT  = 0x1000
PAGE_NOACCESS          = 0x01
PAGE_GUARD             = 0x100
PAGE_EXECUTE_READ      = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_READONLY          = 0x02
PAGE_READWRITE         = 0x04
PAGE_WRITECOPY         = 0x08
READABLE_PROTECT = (PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE |
                    PAGE_EXECUTE_WRITECOPY | PAGE_READONLY |
                    PAGE_READWRITE | PAGE_WRITECOPY)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress",       ctypes.c_void_p),
        ("AllocationBase",    ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId",       wintypes.WORD),
        ("RegionSize",        ctypes.c_size_t),
        ("State",             wintypes.DWORD),
        ("Protect",           wintypes.DWORD),
        ("Type",              wintypes.DWORD),
    ]


def readable_regions_in_module(handle, mod: ModuleInfo) -> list:
    """
    Gibt Liste von (start, size) aller lesbaren, committeten Pages
    innerhalb des Modul-Bereichs zurück.
    """
    mbi  = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)
    regions  = []
    addr = mod.base
    end  = mod.base + mod.size

    while addr < end:
        ret = kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(addr),
            ctypes.byref(mbi), mbi_size
        )
        if ret == 0:
            addr += 0x1000
            continue

        reg_base = mbi.BaseAddress or addr
        reg_size = mbi.RegionSize
        protect  = mbi.Protect

        if (mbi.State == MEM_COMMIT
                and (protect & READABLE_PROTECT)
                and not (protect & PAGE_GUARD)
                and not (protect & PAGE_NOACCESS)):
            actual_start = max(reg_base, mod.base)
            actual_end   = min(reg_base + reg_size, end)
            if actual_end > actual_start:
                regions.append((actual_start, actual_end - actual_start))

        addr = reg_base + reg_size

    return regions


def diag_readability(handle, mod: ModuleInfo) -> None:
    """Diagnose: wie viele Bytes können wir tatsächlich lesen?"""
    regions = readable_regions_in_module(handle, mod)
    total   = sum(s for _, s in regions)
    print(f"[i] Modul {mod.name}  Basis=0x{mod.base:X}  PE-Größe={mod.size//1024//1024} MB")
    print(f"[i] Lesbare Regions: {len(regions)}  gesamt {total//1024//1024} MB")

    # Teste ob wir tatsächlich Bytes lesen können (erste Region)
    if regions:
        test_addr, test_size = regions[0]
        sample = read_bytes(handle, test_addr, min(16, test_size))
        non_null = sum(1 for b in sample if b != 0)
        print(f"[i] Probe @ 0x{test_addr:X}: {sample.hex()}  ({non_null}/16 nicht-null)")
        if non_null == 0:
            print("[!] Alle Bytes sind 0 → ReadProcessMemory wird geblockt (Anti-Cheat? Rechte?)")
    else:
        print("[!] Keine lesbaren Regions gefunden → Modul nicht im Adressraum?")


def scan_regions(handle, regions: list, needle: bytes) -> list:
    """Scannt eine Liste von (addr, size)-Regions nach needle."""
    chunk_size = 0x100000  # 1 MB
    overlap    = len(needle) - 1
    results    = []
    total      = sum(s for _, s in regions)
    scanned    = 0
    reported   = -1

    for (reg_addr, reg_size) in regions:
        offset = 0
        while offset < reg_size:
            mb = scanned >> 20
            if mb // 32 != reported:
                reported = mb // 32
                print(f"    scan {scanned//1_000_000:4d} / {total//1_000_000} MB ...", end="\r")

            read_sz = min(chunk_size, reg_size - offset)
            chunk = read_bytes(handle, reg_addr + offset, read_sz)
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
# GWorld per Pattern-Scan finden
# ---------------------------------------------------------------------------

def find_gworld(handle, mod: ModuleInfo) -> int:
    """
    Scannt nur lesbare Modul-Regions, probiert alle GWORLD_PATTERNS.
    Gibt die Adresse des GWorld-Pointers zurück, oder 0.
    """
    diag_readability(handle, mod)
    regions = readable_regions_in_module(handle, mod)
    if not regions:
        return 0

    for (prefix, _, rel_off, instr_len) in GWORLD_PATTERNS:
        reg_name = {0x1D: "rbx", 0x05: "rax", 0x0D: "rcx",
                    0x15: "rdx"}.get(prefix[-1], f"r??")
        if prefix[0] == 0x4C:
            reg_name = "r8"
        print(f"[~] Suche 'mov {reg_name}, [rip+rel32]'  ({sum(s for _,s in regions)//1_000_000} MB lesbar)...")

        hits = scan_regions(handle, regions, prefix)
        print(f"    {len(hits)} Treffer für {prefix.hex()}")

        for instr_addr in hits:
            after = read_bytes(handle, instr_addr + instr_len, 2)
            if not any(after.startswith(ctx) for ctx in GWORLD_CONTEXT):
                continue

            rel_bytes = read_bytes(handle, instr_addr + rel_off, 4)
            rel32, = struct.unpack('<i', rel_bytes)
            candidate = instr_addr + instr_len + rel32

            val = read_ptr(handle, candidate)

            # Muss Heap-Adresse sein: nicht NULL, nicht im Modul-Image selbst
            if val < 0x10000 or val > 0x7FFFFFFFFFFF:
                continue
            if mod.base <= val < mod.base + mod.size:
                continue  # zeigt ins Modul → kein Heap-Objekt, False Positive

            # UObject-Validierung: erstes Qword muss vtable sein (zeigt ins Modul)
            vtable = read_ptr(handle, val)
            if not (mod.base <= vtable < mod.base + mod.size):
                continue  # kein gültiger vtable-Pointer → kein UObject

            print(f"[+] GWorld @ 0x{instr_addr:X}  →  ptr 0x{candidate:X}  →  UWorld* 0x{val:X}  (vtable 0x{vtable:X})")
            return candidate

    return 0


# ---------------------------------------------------------------------------
# TArray<APlayerState*> automatisch finden
# ---------------------------------------------------------------------------

def _find_player_array(handle, game_state: int, mod: ModuleInfo):
    """
    Scannt den GameState-Speicher auf ein TArray<APlayerState*>-Pattern:
      +0  Data*   (Heap-Pointer auf Array-Puffer)
      +8  Count   (int32, 1..32 = plausible Spielerzahl)
      +C  Max     (int32, >= Count)
    Jedes Element des Arrays muss ein gültiger UObject-Pointer sein
    (erstes Qword = vtable ins Modul).

    Gibt (arr_data, arr_count, offset) zurück, oder (0, 0, 0) wenn nichts gefunden.
    """
    scan_size = 0x600
    raw = read_bytes(handle, game_state, scan_size)

    for off in range(0, scan_size - 16, 8):
        data_ptr, = struct.unpack_from('<Q', raw, off)
        count,    = struct.unpack_from('<i', raw, off + 8)
        max_,     = struct.unpack_from('<i', raw, off + 12)

        if count < 1 or count > 32:
            continue
        if max_ < count or max_ > 128:
            continue
        # Data* muss Heap-Adresse sein, nicht NULL, nicht im Modul
        if data_ptr < 0x10000 or data_ptr > 0x7FFFFFFFFFFF:
            continue
        if mod.base <= data_ptr < mod.base + mod.size:
            continue

        # Erstes Element: muss ein UObject mit vtable ins Modul sein
        first_elem = read_ptr(handle, data_ptr)
        if first_elem < 0x10000 or first_elem > 0x7FFFFFFFFFFF:
            continue
        if mod.base <= first_elem < mod.base + mod.size:
            continue
        vtable = read_ptr(handle, first_elem)
        if not (mod.base <= vtable < mod.base + mod.size):
            continue

        return data_ptr, count, off

    return 0, 0, 0


# ---------------------------------------------------------------------------
# Spieler-Position automatisch finden (2-Level-Scan)
# ---------------------------------------------------------------------------

MAP_LIMIT_CM = 500_000.0   # ±5000m Toleranz

def _is_heap_uobject(handle, addr: int, mod: ModuleInfo) -> bool:
    """Prüft ob addr ein Heap-UObject ist (nicht NULL, nicht im Modul, vtable → Modul)."""
    if addr < 0x10000 or addr > 0x7FFFFFFFFFFF:
        return False
    if mod.base <= addr < mod.base + mod.size:
        return False
    vtable = read_ptr(handle, addr)
    return mod.base <= vtable < mod.base + mod.size


def _scan_for_location(raw: bytes) -> list:
    """
    Scannt rohe Bytes nach FVector-Tripeln (3x float32) die plausible
    Map-Koordinaten sind: mind. ein Wert > 100cm, alle < MAP_LIMIT_CM.
    Gibt Liste von (offset, x, y, z) zurück.
    """
    import math
    results = []
    for off in range(0, len(raw) - 12, 4):
        x, y, z = struct.unpack_from('<fff', raw, off)
        if any(math.isnan(v) or math.isinf(v) for v in (x, y, z)):
            continue
        if max(abs(x), abs(y), abs(z)) > MAP_LIMIT_CM:
            continue
        if max(abs(x), abs(y), abs(z)) < 100.0:   # mind. 1m entfernt
            continue
        results.append((off, x, y, z))
    return results


def find_player_location(handle, ps_ptr: int, mod: ModuleInfo):
    """
    Sucht die Spielerposition ausgehend vom PlayerState-Pointer.

    Ablauf:
      1. Scannt PlayerState-Bytes nach Heap-UObject-Pointern (→ Pawn-Kandidaten)
      2. Für jeden Pawn-Kandidaten: scannt dessen Bytes nach Heap-UObject-Pointern
         (→ Component-Kandidaten = RootComponent)
      3. Für jeden Component-Kandidaten: sucht FVector mit plausiblen Koordinaten

    Gibt (x_cm, y_cm, z_cm, pawn_off, comp_off, loc_off) zurück oder None.
    """
    import math
    PS_SCAN   = 0x1200
    PAWN_SCAN = 0x2000
    COMP_SCAN = 0x400

    ps_raw = read_bytes(handle, ps_ptr, PS_SCAN)
    best = None
    best_mag = 0.0

    for pawn_off in range(0, PS_SCAN - 8, 8):
        pawn, = struct.unpack_from('<Q', ps_raw, pawn_off)
        if not _is_heap_uobject(handle, pawn, mod):
            continue

        pawn_raw = read_bytes(handle, pawn, PAWN_SCAN)

        # -- Level 2: Suche FVector direkt im Pawn --
        for loc_off, x, y, z in _scan_for_location(pawn_raw):
            mag = abs(x) + abs(y) + abs(z)
            if mag > best_mag:
                best_mag = mag
                best = (x, y, z, pawn_off, -1, loc_off)

        # -- Level 3: Suche Component-Pointer im Pawn → FVector im Component --
        for comp_off in range(0, PAWN_SCAN - 8, 8):
            comp, = struct.unpack_from('<Q', pawn_raw, comp_off)
            if not _is_heap_uobject(handle, comp, mod):
                continue

            comp_raw = read_bytes(handle, comp, COMP_SCAN)
            for loc_off, x, y, z in _scan_for_location(comp_raw):
                mag = abs(x) + abs(y) + abs(z)
                if mag > best_mag:
                    best_mag = mag
                    best = (x, y, z, pawn_off, comp_off, loc_off)

    return best


# ---------------------------------------------------------------------------
# Player-Positionen lesen
# ---------------------------------------------------------------------------

def dump_heap(handle, addr: int, size: int = 0x300, label: str = "") -> None:
    """
    Liest `size` Bytes ab `addr` und gibt sie als Hex-Dump aus.
    Zeigt auch alle Pointer-sized (8-Byte) Werte die wie Heap-Adressen aussehen.
    """
    raw = read_bytes(handle, addr, size)
    non_null = sum(1 for b in raw if b != 0)
    print(f"\n--- Heap-Dump {label} @ 0x{addr:X}  ({non_null}/{size} nicht-null) ---")
    if non_null == 0:
        print("    [alle Bytes = 0, ReadProcessMemory fehlgeschlagen oder EAC blockiert]")
        return
    # Hex-Zeilen à 16 Bytes
    for off in range(0, min(size, len(raw)), 16):
        chunk = raw[off:off+16]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        asc_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f"  +{off:04X}  {hex_part:<47}  {asc_part}")
    # Pointer-Kandidaten
    print("  -- Pointer-Kandidaten (Heap-like: 0x000001xx_xxxxxxxx) --")
    for off in range(0, size - 7, 8):
        val, = struct.unpack_from('<Q', raw, off)
        if 0x10000_00000000 < val < 0x7FFF_FFFFFFFF and val > 0x100000:
            print(f"  +{off:04X} ({addr+off:X}) -> 0x{val:X}")
    print("---")


def _find_gamestate_and_playerarray(handle, gworld: int, mod: ModuleInfo):
    """
    Scannt alle 8-Byte-Pointer im UWorld-Objekt.
    Für jeden Pointer der auf ein gültiges UObject zeigt (Vtable → Modul),
    sucht _find_player_array darin nach dem TArray<APlayerState*>.
    Gibt (game_state_addr, arr_data, arr_count, gs_off, arr_off) zurück.
    """
    scan_size = 0x300
    raw = read_bytes(handle, gworld, scan_size)

    for off in range(0, scan_size - 8, 8):
        candidate, = struct.unpack_from('<Q', raw, off)

        # Muss Heap-Adresse sein
        if candidate < 0x10000 or candidate > 0x7FFFFFFFFFFF:
            continue
        if mod.base <= candidate < mod.base + mod.size:
            continue

        # Vtable-Check: erstes Qword des Kandidaten muss ins Modul zeigen
        vtable = read_ptr(handle, candidate)
        if not (mod.base <= vtable < mod.base + mod.size):
            continue

        # Jetzt nach PlayerArray suchen
        arr_data, arr_count, arr_off = _find_player_array(handle, candidate, mod)
        if arr_data != 0:
            return candidate, arr_data, arr_count, off, arr_off

    return 0, 0, 0, 0, 0


def read_players(handle, gworld_ptr_addr: int, mod: ModuleInfo, offsets: dict) -> list:
    gworld = read_ptr(handle, gworld_ptr_addr)
    if not gworld:
        print("[!] GWorld ist NULL")
        return []

    print(f"[i] UWorld @ 0x{gworld:X} – suche GameState+PlayerArray automatisch...")
    game_state, arr_data, arr_count, gs_off, arr_off = \
        _find_gamestate_and_playerarray(handle, gworld, mod)

    if not game_state:
        print("[!] Kein GameState/PlayerArray gefunden. UWorld-Dump:")
        dump_heap(handle, gworld, 0x300, "UWorld")
        return []

    print(f"[+] GameState @ UWorld+0x{gs_off:X} = 0x{game_state:X}")
    print(f"[+] PlayerArray @ GameState+0x{arr_off:X}  count={arr_count}")

    players = []
    for i in range(arr_count):
        ps_ptr = read_ptr(handle, arr_data + i * 8)
        if not ps_ptr:
            continue

        name = read_fstring(handle, ps_ptr + OFF_PLAYER_NAME)

        pawn = read_ptr(handle, ps_ptr + offsets["OFF_PAWN_PRIVATE"])
        if not pawn:
            players.append({"name": name or f"Player{i}", "online": False})
            continue

        comp = read_ptr(handle, pawn + offsets["OFF_ROOT_COMPONENT"])
        if not comp:
            players.append({"name": name or f"Player{i}", "online": False})
            continue

        x, y, z = read_float3(handle, comp + offsets["OFF_REL_LOCATION"])

        players.append({"name": name or f"Player{i}", "online": True, "x_m": x/100, "y_m": y/100, "z_m": z/100})
        print(f"  [{name or f'Player{i}'}] "
            f"({x/100:.1f}m, {y/100:.1f}m, {z/100:.1f}m)")

    return players


# ---------------------------------------------------------------------------
# Prozess finden
# ---------------------------------------------------------------------------

PROCESS_NAMES = [
    "IcarusServer-Win64-Shipping.exe",
    "IcarusServer.exe",
    "Icarus-Win64-Shipping.exe",
    "ICARUS.exe",
]


def list_icarus_processes() -> None:
    """Zeigt alle laufenden Prozesse die 'icarus' im Namen haben."""
    found = []
    for proc in psutil.process_iter(['pid', 'name', 'exe']):
        if 'icarus' in proc.info['name'].lower():
            found.append(proc.info)
    if not found:
        print("[!] Keine Icarus-Prozesse gefunden")
    else:
        print(f"{'PID':>8}  Name")
        print("-" * 50)
        for p in found:
            print(f"{p['pid']:>8}  {p['name']}")


def find_icarus_pid() -> tuple:
    for proc in psutil.process_iter(['pid', 'name']):
        for candidate in PROCESS_NAMES:
            if proc.info['name'].lower() == candidate.lower():
                return proc.info['pid'], proc.info['name']
    return None, None


# ---------------------------------------------------------------------------
# Offset-Tracer: ermittelt OFF_PAWN_PRIVATE / OFF_ROOT_COMPONENT / OFF_REL_LOCATION
# ---------------------------------------------------------------------------

def _find_object_base(handle, ptr_addr: int, mod: ModuleInfo) -> int:
    """
    Scannt rückwärts ab ptr_addr (8-Byte-Schritte) nach einem UObject-Header.
    UObject-Header: erstes Qword = vtable (→ Modul), vtable[0] = vfunc (→ Modul).
    Gibt die Basis-Adresse des Objekts zurück, oder 0 wenn keines gefunden.
    """
    for back in range(0, 0x800, 8):
        candidate = ptr_addr - back
        if candidate < 0x10000:
            break
        vtable = read_ptr(handle, candidate)
        if not (mod.base <= vtable < mod.base + mod.size):
            continue
        # Vtable-Sanity: vtable[0] muss ebenfalls ins Modul zeigen
        vfunc0 = read_ptr(handle, vtable)
        if mod.base <= vfunc0 < mod.base + mod.size:
            return candidate
    return 0


def trace_pointer_chain(handle, mod: ModuleInfo, gworld_ptr_addr: int,
                        players_json: str = "players.json") -> None:
    """
    Ermittelt die Offset-Kette:
        PlayerState + OFF_PAWN_PRIVATE    → Pawn
        Pawn        + OFF_ROOT_COMPONENT  → USceneComponent
        Component   + OFF_REL_LOCATION   → FVector (X/Y/Z in cm)

    Vorgehen:
      1. Lädt Referenzkoordinaten aus players_json
      2. Folgt GWorld → GameState → PlayerState
      3. Scannt Prozessspeicher nach dem FVector
      4. Findet Component-Objekt (rückwärts-Vtable-Scan)
      5. Sucht in PlayerState-Bytes nach Pawn-Pointer, der seinerseits
         auf das Component zeigt → liefert alle drei Offsets
    """
    import os, math

    if not os.path.exists(players_json):
        print(f"[!] {players_json} nicht gefunden – erst parse_players.py ausführen")
        return

    with open(players_json, encoding="utf-8") as f:
        data = json.load(f)

    targets = []
    for p in data.get("players", []):
        if not p.get("online"):
            continue
        x = float(p.get("x") or p.get("x_m", 0) * 100)
        y = float(p.get("y") or p.get("y_m", 0) * 100)
        z = float(p.get("z") or p.get("z_m", 0) * 100)
        targets.append((p.get("character_name") or p.get("name", "?"), x, y, z))

    if not targets:
        print("[!] Keine Online-Spieler in players.json")
        return

    # --- GWorld → GameState → PlayerState ---
    gworld = read_ptr(handle, gworld_ptr_addr)
    gs, arr_data, arr_count, gs_off, arr_off = \
        _find_gamestate_and_playerarray(handle, gworld, mod)
    if not gs:
        print("[!] GameState nicht gefunden")
        return

    print(f"[+] GameState @ UWorld+0x{gs_off:X} = 0x{gs:X}")
    print(f"[+] PlayerArray @ GameState+0x{arr_off:X}  count={arr_count}")

    regions = _all_readable_regions(handle)
    total_mb = sum(s for _, s in regions) // (1024 * 1024)
    print(f"[i] {len(regions)} lesbare Regions  ~{total_mb} MB")

    TOL = 1000.0

    for pi in range(arr_count):
        ps_ptr = read_ptr(handle, arr_data + pi * 8)
        if not ps_ptr:
            continue

        ps_name = read_fstring(handle, ps_ptr + OFF_PLAYER_NAME)
        print(f"\n[+] PlayerState[{pi}] @ 0x{ps_ptr:X}  Name='{ps_name}'")

        name, xf, yf, zf = targets[pi] if pi < len(targets) else targets[0]
        print(f"[~] Referenz: X={xf/100:.2f}m  Y={yf/100:.2f}m  Z={zf/100:.2f}m")

        # --- Scan Prozessspeicher nach FVector ---
        print("[~] Scanne Prozessspeicher nach FVector (Y-Anker ±1000 cm) ...")
        hits_y = _scan_single_float(handle, regions, yf, TOL, 0x100000)
        good_hits = []
        for hit_addr, fy in hits_y:
            ctx = read_bytes(handle, hit_addr - 4, 12)
            if len(ctx) < 12:
                continue
            hx, hy, hz = struct.unpack_from('<fff', ctx)
            if abs(hx - xf) < TOL and abs(hy - yf) < TOL and abs(hz - zf) < TOL:
                good_hits.append((hit_addr - 4, hx, hy, hz))

        if not good_hits:
            print("[!] Kein FVector im Speicher – Spieler nicht online?")
            continue

        # Genauesten Treffer wählen
        best = min(good_hits, key=lambda t: abs(t[1]-xf)+abs(t[2]-yf)+abs(t[3]-zf))
        triplet_addr, hx, hy, hz = best
        print(f"[+] FVector @ 0x{triplet_addr:X}:  {hx/100:.2f}m  {hy/100:.2f}m  {hz/100:.2f}m")

        # --- Component-Basis (vtable rückwärts) ---
        component_base = _find_object_base(handle, triplet_addr, mod)
        if not component_base:
            # Fallback: aus vorherigem Scan bekannt (+0x80 offset)
            component_base = 0
            for back in range(0, 0x200, 8):
                candidate = triplet_addr - back
                vtable = read_ptr(handle, candidate)
                if 0x10000 < vtable < 0x7FFFFFFFFFFF:
                    component_base = candidate
                    break
            print(f"[!] Kein vtable vor FVector, Fallback: component @ 0x{component_base:X}")
        loc_offset = triplet_addr - component_base
        print(f"[+] Component @ 0x{component_base:X}  FVector-Offset=+0x{loc_offset:X}")

        # --- 2-Ebenen-Scan in PlayerState ---
        PS_SCAN   = 0x1200
        PAWN_SCAN = 0x2000
        ps_raw = read_bytes(handle, ps_ptr, PS_SCAN)
        found_chain = False

        for pawn_off in range(0, PS_SCAN - 8, 8):
            pawn_addr, = struct.unpack_from('<Q', ps_raw, pawn_off)
            if not _is_heap_uobject(handle, pawn_addr, mod):
                continue
            pawn_raw = read_bytes(handle, pawn_addr, PAWN_SCAN)
            for comp_off in range(0, len(pawn_raw) - 8, 8):
                v, = struct.unpack_from('<Q', pawn_raw, comp_off)
                if abs(v - component_base) < 0x10:
                    print(f"\n  *** OFFSET-KETTE GEFUNDEN ***")
                    print(f"  PlayerState + 0x{pawn_off:03X} → Pawn @ 0x{pawn_addr:X}")
                    print(f"  Pawn        + 0x{comp_off:03X} → Component @ 0x{component_base:X}")
                    print(f"  Component   + 0x{loc_offset:03X} → FVector")
                    print(f"\n  ┌─ Offsets für memory_reader.py ──────────────────")
                    print(f"  │  OFF_PAWN_PRIVATE   = 0x{pawn_off:03X}")
                    print(f"  │  OFF_ROOT_COMPONENT = 0x{comp_off:03X}")
                    print(f"  │  OFF_REL_LOCATION   = 0x{loc_offset:03X}")
                    print(f"  └──────────────────────────────────────────────────")
                    offsets = {
                        "OFF_PAWN_PRIVATE": pawn_off,
                        "OFF_ROOT_COMPONENT": comp_off,
                        "OFF_REL_LOCATION": loc_offset
                    }

                    with open("offsets.json", "w") as f:
                        json.dump(offsets, f, indent=2)

                    print("[+] Offsets gespeichert in offsets.json")

                    found_chain = True
                    break
            if found_chain:
                break

        if found_chain:
            continue

        # --- Fallback: Back-Pointer-Scan auf Component ---
        print(f"[!] Keine direkte Pawn→Component-Referenz in PS+0..{PS_SCAN:#x} gefunden")
        print("[~] Fallback: brute-force Pawn → Component scan")

        PS_SCAN = 0x2000
        PAWN_SCAN = 0x3000

        ps_raw = read_bytes(handle, ps_ptr, PS_SCAN)

        for pawn_off in range(0, PS_SCAN - 8, 8):
            pawn_addr, = struct.unpack_from('<Q', ps_raw, pawn_off)

            if not _is_heap_uobject(handle, pawn_addr, mod):
                continue

            pawn_raw = read_bytes(handle, pawn_addr, PAWN_SCAN)

            for comp_off in range(0, PAWN_SCAN - 8, 8):
                v, = struct.unpack_from('<Q', pawn_raw, comp_off)

                if abs(v - component_base) < 0x200:
                    print("\n*** HARD MATCH FOUND ***")
                    print(f"PlayerState + 0x{pawn_off:X}")
                    print(f"Pawn + 0x{comp_off:X}")
                    print(f"Location + 0x{loc_offset:X}")
                    return
                
        print(f"[~] Suche alle Pointer auf Component 0x{component_base:X} im Prozessspeicher ...")
        lo = component_base
        hi = component_base + 8
        back_ptrs = _scan_backref(handle, regions, lo, hi, component_base)
        print(f"    {len(back_ptrs)} direkte Pointer auf Component gefunden")

        for bptr_addr, _ in back_ptrs[:20]:
            obj_base = _find_object_base(handle, bptr_addr, mod)
            if not obj_base:
                continue
            internal_off = bptr_addr - obj_base

            # Steht obj_base irgendwo in PlayerState?
            for pawn_off in range(0, PS_SCAN - 8, 8):
                v, = struct.unpack_from('<Q', ps_raw, pawn_off)
                if v == obj_base:
                    print(f"\n  *** VIA BACK-POINTER GEFUNDEN ***")
                    print(f"  PlayerState + 0x{pawn_off:03X} → Objekt @ 0x{obj_base:X}")
                    print(f"  Objekt      + 0x{internal_off:03X} → Component @ 0x{component_base:X}")
                    print(f"  Component   + 0x{loc_offset:03X} → FVector")
                    print(f"\n  ┌─ Offsets für memory_reader.py ──────────────────")
                    print(f"  │  OFF_PAWN_PRIVATE   = 0x{pawn_off:03X}")
                    print(f"  │  OFF_ROOT_COMPONENT = 0x{internal_off:03X}")
                    print(f"  │  OFF_REL_LOCATION   = 0x{loc_offset:03X}")
                    print(f"  └──────────────────────────────────────────────────")
                    found_chain = True
                    break
            if found_chain:
                break

        if not found_chain:
            print(f"[!] Keine Kette gefunden. Manuelle Analyse nötig.")
            print(f"    Component @ 0x{component_base:X}  FVector @ +0x{loc_offset:X}")
            if back_ptrs:
                print(f"    Pointer auf Component bei (erste 5):")
                for bptr_addr, _ in back_ptrs[:5]:
                    obj = _find_object_base(handle, bptr_addr, mod)
                    print(f"      0x{bptr_addr:X}  (Objekt-Basis: 0x{obj:X}  Offset +0x{bptr_addr-obj if obj else 0:X})")


# ---------------------------------------------------------------------------
# Position-Scan: sucht bekannte Koordinaten direkt im Prozessspeicher
# ---------------------------------------------------------------------------

def _all_readable_regions(handle) -> list:
    """Gibt alle lesbare committed Regions (addr, size) zurück."""
    regions = []
    mbi  = MEMORY_BASIC_INFORMATION()
    addr = 0
    while addr < 0x7FFFFFFFFFFF:
        ret = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                      ctypes.byref(mbi), ctypes.sizeof(mbi))
        if ret == 0:
            addr += 0x1000
            continue
        reg_base = mbi.BaseAddress or addr
        reg_size = mbi.RegionSize
        protect  = mbi.Protect
        if (mbi.State == MEM_COMMIT
                and (protect & READABLE_PROTECT)
                and not (protect & PAGE_GUARD)
                and not (protect & PAGE_NOACCESS)):
            regions.append((reg_base, reg_size))
        addr = reg_base + reg_size
    return regions


def _scan_known_positions(handle, players_json: str = "players.json") -> None:
    """
    Liest players.json (aus parse_players.py) und sucht im Prozessspeicher
    nach den gespeicherten Float-Tripeln. Da der Spieler sich inzwischen
    bewegt haben könnte, wird zuerst der markanteste einzelne Float (Y) mit
    ±500 cm Toleranz gesucht und dann X/Z in der Nachbarschaft geprüft.
    """
    import os, math

    if not os.path.exists(players_json):
        print(f"[!] {players_json} nicht gefunden – erst parse_players.py ausführen")
        return

    with open(players_json, encoding="utf-8") as f:
        data = json.load(f)

    targets = []
    for p in data.get("players", []):
        if not p.get("online"):
            continue
        x = float(p.get("x") or p.get("x_m", 0) * 100)
        y = float(p.get("y") or p.get("y_m", 0) * 100)
        z = float(p.get("z") or p.get("z_m", 0) * 100)
        targets.append((p.get("character_name") or p.get("name", "?"), x, y, z))

    if not targets:
        print("[!] Keine Online-Spieler in players.json")
        return

    regions = _all_readable_regions(handle)
    total_mb = sum(s for _, s in regions) // (1024 * 1024)
    print(f"[i] {len(regions)} lesbare Regions  gesamt ~{total_mb} MB")

    TOL   = 1000.0   # ±10m Toleranz
    CHUNK = 0x100000

    for name, xf, yf, zf in targets:
        print(f"\n[~] [{name}] Referenz float32: X={xf:.2f} Y={yf:.2f} Z={zf:.2f} cm")
        print(f"    ({struct.pack('<fff', xf, yf, zf).hex()})")

        # ---- 1. Exakte float32 Suche ----
        needle32 = struct.pack('<fff', xf, yf, zf)
        exact_hits = _raw_scan_needle(handle, regions, needle32)
        print(f"    float32 exakt: {len(exact_hits)} Treffer")

        # ---- 2. float64 Suche ----
        needle64 = struct.pack('<ddd', xf, yf, zf)
        exact64 = _raw_scan_needle(handle, regions, needle64)
        print(f"    float64 exakt: {len(exact64)} Treffer")

        # ---- 3. Nur Y als float32 (single anchor, tolerant) ----
        hits_y = _scan_single_float(handle, regions, yf, TOL, CHUNK)
        print(f"    Y-Anker float32 ±{TOL:.0f} cm: {len(hits_y)} Treffer")
        for hit_addr, fy in hits_y[:10]:
            ctx = read_bytes(handle, hit_addr - 8, 20)
            raw_floats = struct.unpack_from('<5f', ctx) if len(ctx) >= 20 else ()
            print(f"      @ 0x{hit_addr:X}  Umgebung: {[f'{v:.1f}' for v in raw_floats]}")

        # ---- 4. Gute Y-Treffer: prüfe ob X+Z auch passen ----
        good_hits = []
        for hit_addr, fy in hits_y:
            ctx = read_bytes(handle, hit_addr - 4, 12)
            if len(ctx) < 12:
                continue
            hx, hy, hz = struct.unpack_from('<fff', ctx)
            if abs(hx - xf) < TOL and abs(hy - yf) < TOL and abs(hz - zf) < TOL:
                good_hits.append((hit_addr - 4, hx, hy, hz))  # Tripel-Start = hit-4

        print(f"\n  [!] XYZ-Treffer (alle 3 innerhalb ±{TOL:.0f} cm): {len(good_hits)}")
        for triplet_addr, hx, hy, hz in good_hits:
            print(f"  @ 0x{triplet_addr:X}  X={hx/100:.2f}m  Y={hy/100:.2f}m  Z={hz/100:.2f}m")

        # ---- 5. Rückwärts-Suche: Wer zeigt auf diese Adressen? ----
        if good_hits:
            print(f"\n  [~] Suche Back-Pointer auf gute Treffer-Regionen ...")
            for triplet_addr, hx, hy, hz in good_hits[:3]:
                # Suche Pointer die auf [triplet_addr-0x400 .. triplet_addr+0x400] zeigen
                lo = triplet_addr - 0x400
                hi = triplet_addr + 0x400
                back_ptrs = _scan_backref(handle, regions, lo, hi, triplet_addr)
                print(f"  Tripel @ 0x{triplet_addr:X}: {len(back_ptrs)} Back-Pointer")
                for bptr_addr, bptr_val in back_ptrs[:8]:
                    offset_in_struct = triplet_addr - bptr_val
                    print(f"    0x{bptr_addr:X} → 0x{bptr_val:X}  (FVector @ struct+0x{offset_in_struct:X})")


def _raw_scan_needle(handle, regions, needle: bytes) -> list:
    hits = []
    CHUNK = 0x100000
    for reg_addr, reg_size in regions:
        off = 0
        while off < reg_size:
            raw = read_bytes(handle, reg_addr + off, min(CHUNK, reg_size - off))
            pos = 0
            while True:
                pos = raw.find(needle, pos)
                if pos == -1:
                    break
                hits.append(reg_addr + off + pos)
                pos += 1
            off += CHUNK - len(needle)
    return hits


def _scan_single_float(handle, regions, target: float, tol: float, chunk: int) -> list:
    import math
    hits = []
    for reg_addr, reg_size in regions:
        off = 0
        while off < reg_size:
            raw = read_bytes(handle, reg_addr + off, min(chunk, reg_size - off))
            for fi in range(0, len(raw) - 4, 4):
                v, = struct.unpack_from('<f', raw, fi)
                if not math.isnan(v) and not math.isinf(v) and abs(v - target) <= tol:
                    hits.append((reg_addr + off + fi, v))
            off += chunk - 4
    return hits


def _scan_backref(handle, regions, lo: int, hi: int, component_base: int) -> list:
    """Sucht alle 8-Byte-Pointer im Prozessspeicher die auf [lo, hi) zeigen."""
    hits = []
    CHUNK = 0x100000
    for reg_addr, reg_size in regions:
        off = 0
        while off < reg_size:
            raw = read_bytes(handle, reg_addr + off, min(CHUNK, reg_size - off))
            for pi in range(0, len(raw) - 8, 8):
                v, = struct.unpack_from('<Q', raw, pi)
                if (component_base - 0x100) <= v <= (component_base + 0x100):
                    hits.append((reg_addr + off + pi, v))
            off += CHUNK - 8
    return hits


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Icarus Live Map - Memory Reader")
    parser.add_argument('--loop', type=float, default=0,
                        metavar='SECONDS',
                        help='Intervall in Sekunden (0 = einmalig)')
    parser.add_argument('--output', default='live_players.json',
                        help='Ausgabe-JSON für Live-Daten (default: live_players.json)')
    parser.add_argument('--players-json', default='players.json',
                        dest='players_json',
                        help='Referenz-JSON für --scan/--trace (default: players.json)')
    parser.add_argument('--process', default=None,
                        help='Prozessname überschreiben (z.B. IcarusServer-Win64-Shipping.exe)')
    parser.add_argument('--pid', type=int, default=None,
                        help='Direkt per PID anhängen (überschreibt --process)')
    parser.add_argument('--list', action='store_true',
                        help='Alle laufenden Icarus-Prozesse anzeigen und beenden')
    parser.add_argument('--scan', action='store_true',
                        help='Scannt Prozessspeicher nach Positionen aus --players-json')
    parser.add_argument('--trace', action='store_true',
                        help='Ermittelt OFF_PAWN_PRIVATE/ROOT_COMPONENT/REL_LOCATION automatisch')
    args = parser.parse_args()

    if args.list:
        list_icarus_processes()
        return

    pid, proc_name = find_icarus_pid()

    if args.pid:
        try:
            p = psutil.Process(args.pid)
            pid, proc_name = p.pid, p.name()
        except psutil.NoSuchProcess:
            sys.exit(f"[!] PID {args.pid} nicht gefunden")
    elif args.process:
        pid, proc_name = None, None
        for proc in psutil.process_iter(['pid', 'name']):
            if proc.info['name'].lower() == args.process.lower():
                pid, proc_name = proc.info['pid'], proc.info['name']
                break
        if not pid:
            print(f"[!] Prozess '{args.process}' nicht gefunden. Laufende Icarus-Prozesse:")
            list_icarus_processes()
            sys.exit(1)

    if not pid:
        print(f"[!] Kein Icarus-Prozess gefunden. Gesuchte Namen: {PROCESS_NAMES}")
        print("[i] Laufende Icarus-Prozesse:")
        list_icarus_processes()
        sys.exit(1)

    print(f"[+] Prozess: {proc_name}  PID: {pid}")

    handle = open_process(pid)
    mod = get_module_info(pid, proc_name)
    offsets = load_offsets()

    if not offsets and not args.trace:
        print("[!] Keine Offsets gefunden – bitte einmal mit --trace ausführen")
        return
    
    if not mod:
        sys.exit(f"[!] Modul-Info für {proc_name} nicht gefunden")
    print(f"[+] Modul-Basis: 0x{mod.base:X}  Größe: {mod.size//1024//1024} MB")

    if args.scan:
        _scan_known_positions(handle, args.players_json)
        kernel32.CloseHandle(handle)
        return

    gworld_ptr_addr = find_gworld(handle, mod)
    if not gworld_ptr_addr:
        sys.exit("[!] GWorld nicht gefunden – Pattern passt nicht für diese Build-Version")

    if args.trace:
        trace_pointer_chain(handle, mod, gworld_ptr_addr, args.players_json)
        kernel32.CloseHandle(handle)
        return

    print(f"[+] Schreibe nach '{args.output}'" +
          (f" alle {args.loop}s" if args.loop else " (einmalig)"))

    while True:
        players = read_players(handle, gworld_ptr_addr, mod, offsets)

        output = {
            "timestamp":    time.time(),
            "player_count": len([p for p in players if p.get("online")]),
            "players":      players,
        }

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        for p in players:
            if p.get("online"):
                print(f"  [{p['name']}] {p['x_m']}m, {p['y_m']}m, {p['z_m']}m")
            else:
                print(f"  [{p['name']}] offline")

        if not args.loop:
            break
        time.sleep(args.loop)

    kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
