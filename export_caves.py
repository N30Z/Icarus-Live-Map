#!/usr/bin/env python3
"""
export_caves.py  —  Extract cave entrance positions from GD.json.

Cave locations are fixed game-world coordinates that never change between
sessions.  Run this once (or after a map reset) to generate the caves file,
which index.html will load automatically on every page visit.

Usage:
    python export_caves.py
    python export_caves.py /path/to/GD.json

Writes:  caves/{map}.json   (e.g. caves/olympus.json)
         caves.json          (legacy alias for Olympus, kept for compatibility)
"""

import json, base64, zlib, struct, math, sys, os

INPUT_FILE = "GD.json"
KNOWN_MAPS = ["Olympus", "Styx", "Prometheus", "Elysium"]


# ── Binary helpers ────────────────────────────────────────────────────────────

def _int32(d, p):
    v, = struct.unpack_from("<i", d, p)
    return v, p + 4

def _float(d, p):
    v, = struct.unpack_from("<f", d, p)
    return v, p + 4

def _fstring(d, p):
    length, p = _int32(d, p)
    if length == 0:
        return "", p
    if length < 0:
        bl = -length * 2
        return d[p:p + bl].decode("utf-16-le", errors="replace").rstrip("\x00"), p + bl
    return d[p:p + length - 1].decode("latin-1"), p + length

def find_first(data, pat, start=0, end=None):
    return data.find(pat, start, len(data) if end is None else end)

def find_last(data, pat, start=0, end=None):
    return data.rfind(pat, start, len(data) if end is None else end)


# ── Property readers ──────────────────────────────────────────────────────────

def read_prop_val(buf, pos, ptype):
    """pos = first byte after the name FString null terminator."""
    try:
        tl, pos = _int32(buf, pos); pos += tl   # skip type-name body
        pos += 4                                  # payload_size
        pos += 4                                  # array_index
        if ptype == 'bool':
            return buf[pos] != 0
        hg = buf[pos]; pos += 1
        if hg: pos += 16
        if ptype in ('str', 'name'):
            vl, pos = _int32(buf, pos)
            if 0 < vl < 500:
                return buf[pos:pos + vl - 1].decode("latin-1")
        elif ptype == 'float':
            return _float(buf, pos)[0]
        elif ptype == 'int':
            return _int32(buf, pos)[0]
    except Exception:
        pass
    return None

def scan_prop(buf, pat, start, end, ptype):
    pos = find_first(buf, pat, start, end)
    return None if pos == -1 else read_prop_val(buf, pos + len(pat), ptype)

def rscan_prop(buf, pat, before, lookback, ptype):
    pos = find_last(buf, pat, max(0, before - lookback), before)
    return None if pos == -1 else read_prop_val(buf, pos + len(pat), ptype)


# ── Transform readers  (mirrors JS scanTranslation / scanRotationYaw) ─────────

PAT_TRANS = b"Translation\x00"
PAT_ROT   = b"Rotation\x00"

def scan_translation(buf, start, end):
    pos = find_first(buf, PAT_TRANS, start, end)
    if pos == -1:
        return None
    try:
        p = pos + 12                               # skip 'Translation\x00'
        tl, p = _int32(buf, p); p += tl            # skip type-name body
        sz,  p = _int32(buf, p)                    # payload_size
        p += 4                                     # array_index
        tl2, p = _int32(buf, p); p += tl2          # skip struct_name body
        p += 16                                    # StructGuid
        hg = buf[p]; p += 1
        if hg: p += 16
        if sz == 12:
            x = _float(buf, p)[0]
            y = _float(buf, p + 4)[0]
            z = _float(buf, p + 8)[0]
            return round(x / 100, 1), round(y / 100, 1), round(z / 100, 1)
    except Exception:
        pass
    return None

def scan_rotation_yaw(buf, start, end):
    pos = find_first(buf, PAT_ROT, start, end)
    if pos == -1:
        return None
    try:
        p = pos + 9                                # skip 'Rotation\x00'
        tl, p = _int32(buf, p); p += tl
        sz,  p = _int32(buf, p)
        p += 4
        tl2, p = _int32(buf, p); p += tl2
        p += 16
        hg = buf[p]; p += 1
        if hg: p += 16
        if sz == 16:
            rx = _float(buf, p)[0];     ry = _float(buf, p + 4)[0]
            rz = _float(buf, p + 8)[0]; rw = _float(buf, p + 12)[0]
            return round(math.degrees(math.atan2(2*(rw*rz + rx*ry), 1 - 2*(ry*ry + rz*rz))), 1)
    except Exception:
        pass
    return None


# ── Cave extraction  (mirrors JS extractCaves) ────────────────────────────────

PAT_CERC     = b"CaveEntranceRecorderComponent\x00"
PAT_ACN      = b"ActorClassName\x00"
PAT_COMPNAME = b"ComponentClassName\x00"
PAT_BINDATA  = b"BinaryData\x00"

def extract_caves(buf):
    caves = []
    i = 0
    while True:
        i = find_first(buf, PAT_CERC, i)
        if i == -1:
            break

        actor_class = rscan_prop(buf, PAT_ACN, i, 700, 'name') or ""

        end = find_first(buf, PAT_COMPNAME, i + len(PAT_CERC), i + 15000)
        if end == -1:
            end = i + 12000

        bd_pos = find_first(buf, PAT_BINDATA, i, i + 500)
        if bd_pos != -1:
            loc = scan_translation(buf, bd_pos, end)
            yaw = scan_rotation_yaw(buf, bd_pos, end)
            if loc:
                caves.append({
                    "x_m":         loc[0],
                    "y_m":         loc[1],
                    "z_m":         loc[2],
                    "yaw":         yaw,
                    "actor_class": actor_class,
                })

        i += len(PAT_CERC)

    return caves


# ── World detection ───────────────────────────────────────────────────────────

def detect_world(binary):
    for name in KNOWN_MAPS:
        if name.encode("latin-1") in binary:
            return name
    return "Unknown"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE

    print(f"[+] Reading {input_file} ...")
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    blob   = data["ProspectBlob"]["BinaryBlob"]
    binary = zlib.decompress(base64.b64decode(blob))
    print(f"[+] Decompressed: {len(binary):,} bytes")

    world = detect_world(binary)
    print(f"[+] Detected world: {world}")

    print("[+] Extracting cave entrances ...")
    caves = extract_caves(binary)
    print(f"[+] Found {len(caves)} cave entrances")

    # Summarise by biome/size
    from collections import Counter
    import re
    counts = Counter()
    for c in caves:
        m = re.search(r'CaveEntrance_([A-Z]+)_([A-Z]+)', c["actor_class"], re.I)
        if m:
            biome = {"CF": "Conifer Forest", "AC": "Arctic", "DC": "Desert"}.get(m.group(1).upper(), m.group(1))
            size  = {"SML": "Small", "MED": "Medium", "LRG": "Large"}.get(m.group(2).upper(), m.group(2))
            counts[f"{biome} {size}"] += 1
    for label, n in sorted(counts.items()):
        print(f"    {n:3d}  {label}")

    os.makedirs("caves", exist_ok=True)
    out_path = os.path.join("caves", f"{world.lower()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(caves, f, separators=(",", ":"))
    print(f"\n[+] Written to {out_path}")

    # Legacy alias for Olympus
    if world.lower() == "olympus":
        with open("caves.json", "w", encoding="utf-8") as f:
            json.dump(caves, f, separators=(",", ":"))
        print(f"[+] Also written to caves.json (legacy alias)")

    print(f"[+] Cave positions are permanent — index.html will load them automatically.")

if __name__ == "__main__":
    main()
