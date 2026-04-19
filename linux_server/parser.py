"""
Icarus SaveGame binary parser — self-contained library.

All functions extracted from parse_players.py so linux_server/ can be
deployed standalone without the rest of the project.

Public API:
  load_binary(path)               → raw bytes (decompress GD.json)
  extract_players_compat(binary)  → list of player dicts
  extract_caves_scan(binary)      → list of cave dicts
  extract_deposits_scan(binary)   → list of deposit dicts
  parse_state_recorder_blobs(b)   → (blobs, end_pos)
  categorize(blobs)               → dict keyed by short type name
  extract_geysers(categories)     → {"enzyme": [...], "oil": [...]}
  detect_world(binary)            → "olympus" | "styx" | ... | None
"""

import base64
import json
import math
import struct
import zlib

# ── Primitives ────────────────────────────────────────────────────────────────

def _int32(d, p):
    v, = struct.unpack_from("<i", d, p)
    return v, p + 4

def _uint32(d, p):
    v, = struct.unpack_from("<I", d, p)
    return v, p + 4

def _int64(d, p):
    v, = struct.unpack_from("<q", d, p)
    return v, p + 8

def _float(d, p):
    v, = struct.unpack_from("<f", d, p)
    return v, p + 4

def _fstring(d, p):
    length, p = _int32(d, p)
    if length == 0:
        return "", p
    if length < 0:
        bl = -length * 2
        s = d[p:p + bl].decode("utf-16-le", errors="replace").rstrip("\x00")
        return s, p + bl
    s = d[p:p + length - 1].decode("latin-1")
    return s, p + length


# ── Atomic struct types ───────────────────────────────────────────────────────

ATOMIC_STRUCTS = {
    "Vector":       ("xyz",  "<fff",  12),
    "Vector2D":     ("xy",   "<ff",    8),
    "Rotator":      ("pyr",  "<fff",  12),
    "Quat":         ("xyzw", "<ffff", 16),
    "LinearColor":  ("rgba", "<ffff", 16),
    "Color":        ("bgra", "4B",     4),
    "IntPoint":     ("xy",   "<ii",    8),
    "IntVector":    ("xyz",  "<iii",  12),
    "Guid":         (None,   None,    16),
    "DateTime":     (None,   "<q",     8),
    "Timespan":     (None,   "<q",     8),
}

def _read_atomic(d, p, struct_name):
    info = ATOMIC_STRUCTS[struct_name]
    keys, fmt, size = info
    if struct_name == "Guid":
        return d[p:p + 16].hex(), p + 16
    vals = struct.unpack_from(fmt, d, p)
    if keys is None:
        return vals[0], p + size
    return dict(zip(keys, (round(v, 4) for v in vals))), p + size


# ── Property reader ───────────────────────────────────────────────────────────

def read_properties(d, pos, byte_limit=None):
    result = {}
    end = (pos + byte_limit) if byte_limit is not None else len(d)

    while pos < end:
        try:
            name, pos = _fstring(d, pos)
        except Exception:
            break
        if not name or name == "None":
            break
        try:
            prop_type, pos = _fstring(d, pos)
        except Exception:
            break
        try:
            size,  pos = _int32(d, pos)
            _,     pos = _int32(d, pos)
        except Exception:
            break

        extra = {}
        try:
            if prop_type == "StructProperty":
                struct_name, pos = _fstring(d, pos)
                pos += 16
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["struct_name"] = struct_name
            elif prop_type == "BoolProperty":
                bool_val = d[pos]; pos += 1
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                result[name] = bool(bool_val)
                continue
            elif prop_type in ("ByteProperty", "EnumProperty"):
                enum_name, pos = _fstring(d, pos)
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["enum_name"] = enum_name
            elif prop_type == "ArrayProperty":
                inner_type, pos = _fstring(d, pos)
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["inner_type"] = inner_type
            elif prop_type == "MapProperty":
                key_type, pos = _fstring(d, pos)
                val_type, pos = _fstring(d, pos)
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["key_type"] = key_type
                extra["val_type"] = val_type
            elif prop_type == "SetProperty":
                inner_type, pos = _fstring(d, pos)
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["inner_type"] = inner_type
            else:
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
        except Exception:
            pos += size
            continue

        payload_start = pos
        try:
            value = _read_payload(d, pos, prop_type, size, extra)
        except Exception:
            value = f"<parse_error size={size}>"
        pos = payload_start + size

        if name in result:
            if not isinstance(result[name], list):
                result[name] = [result[name]]
            result[name].append(value)
        else:
            result[name] = value

    return result, pos


def _read_payload(d, pos, prop_type, size, extra):
    if prop_type == "StrProperty":
        val, _ = _fstring(d, pos)
        return val
    elif prop_type in ("NameProperty", "TextProperty"):
        try:
            val, _ = _fstring(d, pos)
            return val
        except Exception:
            return f"<text size={size}>"
    elif prop_type == "IntProperty":
        v, _ = _int32(d, pos)
        return v
    elif prop_type == "UInt32Property":
        v, _ = _uint32(d, pos)
        return v
    elif prop_type == "Int64Property":
        v, _ = _int64(d, pos)
        return v
    elif prop_type == "FloatProperty":
        v, _ = _float(d, pos)
        return round(v, 6)
    elif prop_type in ("ByteProperty", "EnumProperty"):
        enum_name = extra.get("enum_name", "None")
        if enum_name == "None" or prop_type == "ByteProperty":
            if size == 1:
                return d[pos]
        val, _ = _fstring(d, pos)
        return val
    elif prop_type == "ObjectProperty":
        v, _ = _int32(d, pos)
        return v
    elif prop_type == "SoftObjectProperty":
        asset_path, _ = _fstring(d, pos)
        return asset_path
    elif prop_type == "StructProperty":
        struct_name = extra.get("struct_name", "")
        if struct_name in ATOMIC_STRUCTS:
            val, _ = _read_atomic(d, pos, struct_name)
            return val
        props, _ = read_properties(d, pos, byte_limit=size)
        return props
    elif prop_type == "ArrayProperty":
        return _read_array(d, pos, size, extra)
    elif prop_type == "MapProperty":
        try:
            _, pos2 = _int32(d, pos)
            count, _ = _int32(d, pos2)
            return f"<map count={count}>"
        except Exception:
            return "<map>"
    elif prop_type == "SetProperty":
        try:
            _, pos2 = _int32(d, pos)
            count, _ = _int32(d, pos2)
            return f"<set count={count}>"
        except Exception:
            return "<set>"
    else:
        return f"<{prop_type} size={size}>"


SIMPLE_ARRAY_TYPES = {
    "IntProperty":    ("<i", 4),
    "UInt32Property": ("<I", 4),
    "Int64Property":  ("<q", 8),
    "FloatProperty":  ("<f", 4),
    "DoubleProperty": ("<d", 8),
}

def _read_array(d, pos, total_size, extra):
    inner = extra.get("inner_type", "")
    count, pos = _int32(d, pos)
    if count <= 0 or count > 500_000:
        return []

    if inner == "ByteProperty":
        if count > 4096:
            return f"<binary {count} bytes: {d[pos:pos+16].hex()}>"
        return list(d[pos:pos + count])

    if inner in SIMPLE_ARRAY_TYPES:
        fmt, stride = SIMPLE_ARRAY_TYPES[inner]
        vals = [struct.unpack_from(fmt, d, pos + i * stride)[0] for i in range(count)]
        if inner == "FloatProperty":
            vals = [round(v, 6) for v in vals]
        return vals

    if inner in ("StrProperty", "NameProperty"):
        result = []
        for _ in range(count):
            s, pos = _fstring(d, pos)
            result.append(s)
        return result

    if inner == "StructProperty":
        result = []
        for _ in range(count):
            props, pos = read_properties(d, pos)
            result.append(props)
        return result

    if inner == "ObjectProperty":
        return [struct.unpack_from("<i", d, pos + i * 4)[0] for i in range(count)]

    raw = d[pos:pos + total_size - 4]
    return f"<array[{inner}] count={count} raw={raw[:32].hex()}>"


def _decode_binary_data(array_value):
    if not isinstance(array_value, list) or not array_value:
        return array_value
    raw = bytes(array_value)
    try:
        props, _ = read_properties(raw, 0)
        if props:
            return props
    except Exception:
        pass
    return f"<binary {len(raw)} bytes: {raw[:16].hex()}...>"


# ── StateRecorderBlob top-level parser ───────────────────────────────────────

def _skip_struct_prop_tag(d, pos):
    _, pos = _fstring(d, pos)
    _, pos = _fstring(d, pos)
    _, pos = _int32(d, pos)
    _, pos = _int32(d, pos)
    _, pos = _fstring(d, pos)
    pos += 16
    has_guid = d[pos]; pos += 1
    if has_guid: pos += 16
    return pos


SKIP_BINARY = {
    "/Script/Icarus.VoxelRecorderComponent",
    "/Script/Icarus.SpawnedVoxelRecorderComponent",
    "/Script/Icarus.ResourceDepositRecorderComponent",
    "/Script/Icarus.FLODRecorderComponent",
    "/Script/Icarus.FLODTileRecorderComponent",
}


def parse_state_recorder_blobs(d):
    """Parse all StateRecorderBlob entries. Returns (list_of_dicts, end_pos)."""
    pos = 0
    name, pos = _fstring(d, pos)
    assert name == "StateRecorderBlobs", f"Expected StateRecorderBlobs, got {name!r}"
    prop_type, pos = _fstring(d, pos)
    assert prop_type == "ArrayProperty"
    _, pos = _int32(d, pos)
    _, pos = _int32(d, pos)
    _, pos = _fstring(d, pos)
    has_guid = d[pos]; pos += 1
    if has_guid: pos += 16

    count, pos = _int32(d, pos)
    pos = _skip_struct_prop_tag(d, pos)

    blobs = []
    for i in range(count):
        try:
            entry_props, pos = read_properties(d, pos)
        except Exception as e:
            print(f"[!] Blob {i} failed (pos={pos}): {e}")
            break

        if not entry_props:
            continue

        cname = entry_props.get("ComponentClassName", "")
        if "BinaryData" in entry_props and cname not in SKIP_BINARY:
            entry_props["BinaryData"] = _decode_binary_data(entry_props["BinaryData"])
        elif "BinaryData" in entry_props:
            bd = entry_props["BinaryData"]
            if isinstance(bd, list):
                entry_props["BinaryData"] = f"<binary {len(bd)} bytes skipped>"

        blobs.append(entry_props)

    return blobs, pos


# ── Categorize ────────────────────────────────────────────────────────────────

TYPE_SHORT = {
    "/Script/Icarus.PlayerStateRecorderComponent":          "player_state",
    "/Script/Icarus.PlayerRecorderComponent":               "player",
    "/Script/Icarus.DeployableRecorderComponent":           "deployable",
    "/Script/Icarus.BuildingGridRecorderComponent":         "building",
    "/Script/Icarus.BedRecorderComponent":                  "bed",
    "/Script/Icarus.CropPlotRecorderComponent":             "crop",
    "/Script/Icarus.SignRecorderComponent":                 "sign",
    "/Script/Icarus.DrillRecorderComponent":                "drill",
    "/Script/Icarus.SplineRecorderComponent":               "spline",
    "/Script/Icarus.ResourceDepositRecorderComponent":      "resource_deposit",
    "/Script/Icarus.VoxelRecorderComponent":                "voxel",
    "/Script/Icarus.SpawnedVoxelRecorderComponent":         "spawned_voxel",
    "/Script/Icarus.EnzymeGeyserRecorderComponent":         "enzyme_geyser",
    "/Script/Icarus.OilGeyserRecorderComponent":            "oil_geyser",
    "/Script/Icarus.CaveAIRecorderComponent":               "cave_ai",
    "/Script/Icarus.CaveEntranceRecorderComponent":         "cave_entrance",
    "/Script/Icarus.IcarusMountCharacterRecorderComponent": "mount",
    "/Script/Icarus.RocketRecorderComponent":               "rocket",
    "/Script/Icarus.FLODRecorderComponent":                 "flod",
    "/Script/Icarus.FLODTileRecorderComponent":             "flod_tile",
    "/Script/Icarus.GameModeStateRecorderComponent":        "game_mode",
    "/Script/Icarus.WeatherForecastRecorderComponent":      "weather_forecast",
    "/Script/Icarus.WeatherControllerRecorderComponent":    "weather",
    "/Script/Icarus.CharacterTrapRecorderComponent":        "trap",
    "/Script/Icarus.BaseLevelTeleportRecorderComponent":    "teleport",
    "/Script/Icarus.InstancedLevelRecorderComponent":       "instanced_level",
}


def _get_location(props):
    t = props.get("ActorTransform")
    if not isinstance(t, dict):
        return None
    tr = t.get("Translation")
    if isinstance(tr, dict) and "x" in tr:
        return {
            "x": round(tr["x"], 2), "y": round(tr["y"], 2), "z": round(tr["z"], 2),
            "x_m": round(tr["x"] / 100, 2), "y_m": round(tr["y"] / 100, 2), "z_m": round(tr["z"] / 100, 2),
        }
    return None


def categorize(blobs):
    categories = {}
    for b in blobs:
        cname = b.get("ComponentClassName", "<unknown>")
        short = TYPE_SHORT.get(cname, cname.split(".")[-1].replace("RecorderComponent", "").lower())
        entry = {"type": short, "actor": b.get("ObjectFName")}
        loc = _get_location(b)
        if loc:
            entry["location"] = loc
        entry["raw"] = {k: v for k, v in b.items()
                        if k not in ("ComponentClassName", "ObjectFName", "ActorTransform",
                                     "ActorStateRecorderVersion", "FLODComponentData",
                                     "SavedInventories", "IcarusActorGUID")}
        categories.setdefault(short, []).append(entry)
    return categories


# ── Targeted binary scan helpers ──────────────────────────────────────────────

def _read_prop_val(d, p, ptype):
    try:
        tl, = struct.unpack_from("<i", d, p); p += 4
        p += tl
        _, = struct.unpack_from("<i", d, p); p += 4
        _, = struct.unpack_from("<i", d, p); p += 4
        if ptype == "bool":
            return bool(d[p])
        hg = d[p]; p += 1
        if hg: p += 16
        if ptype in ("name", "str"):
            vl, = struct.unpack_from("<i", d, p); p += 4
            if 0 < vl < 500:
                return d[p:p + vl - 1].decode("latin-1")
        elif ptype == "float_bits":
            val, = struct.unpack_from("<f", d, p)
            return val
        elif ptype == "int":
            val, = struct.unpack_from("<i", d, p)
            return val
    except Exception:
        pass
    return None


def _scan_prop(d, key, start, end, ptype):
    pos = d.find(key, start, end)
    if pos == -1:
        return None
    return _read_prop_val(d, pos + len(key), ptype)


def _rscan_prop(d, key, before, lookback, ptype):
    pos = d.rfind(key, max(0, before - lookback), before)
    if pos == -1:
        return None
    return _read_prop_val(d, pos + len(key), ptype)


def _scan_translation(d, start, end):
    pos = d.find(b"Translation\x00", start, end)
    if pos == -1:
        return None
    try:
        p = pos + 12
        tl, = struct.unpack_from("<i", d, p); p += 4
        p += tl
        sz, = struct.unpack_from("<i", d, p); p += 4
        _,  = struct.unpack_from("<i", d, p); p += 4
        tl2, = struct.unpack_from("<i", d, p); p += 4
        p += tl2
        p += 16
        hg = d[p]; p += 1
        if hg: p += 16
        if sz == 12:
            x, y, z = struct.unpack_from("<fff", d, p)
            return round(x / 100, 1), round(y / 100, 1), round(z / 100, 1)
    except Exception:
        pass
    return None


def _scan_rotation_yaw(d, start, end):
    pos = d.find(b"Rotation\x00", start, end)
    if pos == -1:
        return None
    try:
        p = pos + 9
        tl, = struct.unpack_from("<i", d, p); p += 4
        p += tl
        sz, = struct.unpack_from("<i", d, p); p += 4
        _,  = struct.unpack_from("<i", d, p); p += 4
        tl2, = struct.unpack_from("<i", d, p); p += 4
        p += tl2
        p += 16
        hg = d[p]; p += 1
        if hg: p += 16
        if sz == 16:
            rx, ry, rz, rw = struct.unpack_from("<ffff", d, p)
            yaw = math.degrees(math.atan2(2 * (rw * rz + rx * ry), 1 - 2 * (ry * ry + rz * rz)))
            return round(yaw, 1)
    except Exception:
        pass
    return None


# ── Public extraction functions ───────────────────────────────────────────────

def load_binary(path):
    """Decompress ProspectBlob.BinaryBlob from a GD.json file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return zlib.decompress(base64.b64decode(raw["ProspectBlob"]["BinaryBlob"]))


def gd_json_to_binary(gd_dict):
    """Decompress ProspectBlob.BinaryBlob from an already-parsed GD.json dict."""
    return zlib.decompress(base64.b64decode(gd_dict["ProspectBlob"]["BinaryBlob"]))


def detect_world(binary):
    """Detect which Icarus prospect this binary belongs to."""
    for label, world_id in (
        (b"Olympus",    "olympus"),
        (b"Styx",       "styx"),
        (b"Prometheus", "prometheus"),
        (b"Elysium",    "elysium"),
    ):
        if binary.find(label) != -1:
            return world_id
    return None


def extract_players_compat(binary):
    """Fast binary scan for player positions. Returns list of player dicts."""

    def find_all(d, pat):
        i, out = 0, []
        while True:
            i = d.find(pat, i)
            if i == -1: break
            out.append(i); i += len(pat)
        return out

    def strprop_val(d, pos):
        try:
            tl, p = _int32(d, pos); p += tl
            _, p  = _int32(d, p); _, p = _int32(d, p)
            hg = d[p]; p += 1
            if hg: p += 16
            vl, p = _int32(d, p)
            if vl <= 0 or vl > 100_000: return None, p
            return d[p:p + vl - 1].decode("latin-1"), p + vl
        except:
            return None, pos

    def intprop_val(d, pos):
        try:
            tl, p = _int32(d, pos); p += tl
            _, p = _int32(d, p); _, p = _int32(d, p)
            hg = d[p]; p += 1
            if hg: p += 16
            v, p = _int32(d, p); return v, p
        except:
            return None, pos

    name_map = {}
    for cn_pos in find_all(binary, b"CachedCharacterName\x00"):
        char_name, _ = strprop_val(binary, cn_pos + 20)
        if not char_name: continue
        uid_pos = binary.rfind(b"UserID\x00", max(0, cn_pos - 600), cn_pos)
        if uid_pos == -1: continue
        steam_id, _ = strprop_val(binary, uid_pos + 7)
        if not steam_id: continue
        slot = 0
        sp = binary.find(b"ChrSlot\x00", uid_pos, cn_pos)
        if sp != -1:
            sv, _ = intprop_val(binary, sp + 8)
            if sv is not None: slot = sv
        name_map[(steam_id, slot)] = char_name

    players = []
    seen = set()
    for pcid_pos in find_all(binary, b"PlayerCharacterID\x00"):
        fs = pcid_pos - 4
        if fs < 0: continue
        dl, = struct.unpack_from("<i", binary, fs)
        if dl != 18: continue
        sb = max(0, pcid_pos - 5000)
        if binary.find(b"PlayerStateRecorderComponent", sb, pcid_pos) == -1: continue

        try:
            name, p = _fstring(binary, fs)
            _, p    = _fstring(binary, p)
            size, p = _int32(binary, p); _, p = _int32(binary, p)
            _, p    = _fstring(binary, p); p += 16
            hg = binary[p]; p += 1
            if hg: p += 16
        except:
            continue

        end_struct = p + size
        steam_id = slot = None
        pp_pos = binary.find(b"PlayerID\x00", p, end_struct)
        if pp_pos != -1: steam_id, _ = strprop_val(binary, pp_pos + 9)
        sp = binary.find(b"ChrSlot\x00", p, end_struct)
        if sp != -1:
            sv, _ = intprop_val(binary, sp + 8)
            if sv is not None: slot = sv or 0

        if not steam_id or steam_id in seen: continue
        seen.add(steam_id)

        location = rotation = None
        lp = binary.find(b"Location\x00", end_struct, end_struct + 3000)
        if lp != -1:
            try:
                nm, p2 = _fstring(binary, lp - 4)
                tn, p2 = _fstring(binary, p2)
                sz, p2 = _int32(binary, p2); _, p2 = _int32(binary, p2)
                sn, p2 = _fstring(binary, p2); p2 += 16
                hg = binary[p2]; p2 += 1
                if hg: p2 += 16
                if nm == "Location" and tn == "StructProperty" and sn == "Vector" and sz == 12:
                    x, y, z = struct.unpack_from("<fff", binary, p2)
                    location = {"x": x, "y": y, "z": z}
            except:
                pass

            rp = binary.find(b"Rotation\x00", lp, lp + 500)
            if rp != -1:
                try:
                    nm, p2 = _fstring(binary, rp - 4)
                    tn, p2 = _fstring(binary, p2)
                    sz, p2 = _int32(binary, p2); _, p2 = _int32(binary, p2)
                    sn, p2 = _fstring(binary, p2); p2 += 16
                    hg = binary[p2]; p2 += 1
                    if hg: p2 += 16
                    if nm == "Rotation" and sn == "Quat" and sz == 16:
                        rx, ry, rz, rw = struct.unpack_from("<ffff", binary, p2)
                        rotation = {"x": rx, "y": ry, "z": rz, "w": rw}
                except:
                    pass

        players.append({"steam_id": steam_id, "slot": slot or 0,
                         "location": location, "rotation": rotation})

    result = []
    for ps in players:
        sid, slot = ps["steam_id"], ps["slot"]
        loc = ps["location"]
        char_name = name_map.get((sid, slot)) or next(
            (n for (s, _), n in name_map.items() if s == sid), "<unknown>")
        entry = {"steam_id": sid, "character_name": char_name,
                 "slot": slot, "online": loc is not None}
        if loc:
            entry.update({
                "x": round(loc["x"], 2), "y": round(loc["y"], 2), "z": round(loc["z"], 2),
                "x_m": round(loc["x"] / 100, 2), "y_m": round(loc["y"] / 100, 2),
                "z_m": round(loc["z"] / 100, 2),
            })
        if ps["rotation"]:
            r = ps["rotation"]
            entry["rotation"] = {k: round(v, 4) for k, v in r.items()}
        result.append(entry)
    return result


def extract_geysers(categories):
    """Extract geysers from categorize() output."""
    def _pull(cat, gtype):
        out = []
        for entry in categories.get(cat, []):
            bd = entry.get("raw", {}).get("BinaryData", {})
            if not isinstance(bd, dict):
                continue
            tr = (bd.get("ActorTransform") or {}).get("Translation")
            if not isinstance(tr, dict) or "x" not in tr:
                continue
            rc = bd.get("ResourceComponentRecord") or {}
            item = {
                "x_m":    round(tr["x"] / 100, 1),
                "y_m":    round(tr["y"] / 100, 1),
                "z_m":    round(tr["z"] / 100, 1),
                "active": rc.get("bDeviceActive", False),
            }
            if gtype == "enzyme":
                item["completions"] = bd.get("Completions", 0)
                item["horde"]       = bd.get("HordeDTKey", "")
            out.append(item)
        return out

    return {
        "enzyme": _pull("enzyme_geyser", "enzyme"),
        "oil":    _pull("oil_geyser",    "oil"),
    }


def extract_deposits_scan(binary):
    """Fast binary scan for all resource deposit actors."""
    deposits = []
    pat = b"ResourceDepositRecorderComponent\x00"
    i = 0
    while True:
        i = binary.find(pat, i)
        if i == -1:
            break

        actor_class = _rscan_prop(binary, b"ActorClassName\x00", i, 700, "name")
        end = binary.find(b"ComponentClassName\x00", i + 33, i + 8000)
        if end == -1:
            end = i + 6000
        bd_pos = binary.find(b"BinaryData\x00", i, i + 500)
        if bd_pos == -1:
            i += len(pat)
            continue

        ore      = _scan_prop(binary, b"ResourceDTKey\x00",     bd_pos, end, "name")
        rr_raw   = _scan_prop(binary, b"ResourceRemaining\x00", bd_pos, end, "float_bits")
        loc      = _scan_translation(binary, bd_pos, end)
        remaining = None
        if rr_raw is not None and not math.isnan(rr_raw):
            remaining = round(float(rr_raw), 4)

        if actor_class == "BP_Deep_Mining_Ore_Deposit_Cave_C":
            category = "cave"
        elif actor_class in ("BP_MetaDeposit_Conifer_C", "BP_Mission_Meta_Voxel_C"):
            category = "meta"
        else:
            category = "surface"

        if loc and ore:
            x_m, y_m, z_m = loc
            deposits.append({"ore": ore, "remaining": remaining,
                              "category": category, "x_m": x_m, "y_m": y_m, "z_m": z_m})
        i += len(pat)
    return deposits


def extract_caves_scan(binary):
    """Fast binary scan for all cave entrance actors."""
    caves = []
    pat   = b"CaveEntranceRecorderComponent\x00"
    i     = 0
    while True:
        i = binary.find(pat, i)
        if i == -1:
            break

        actor_class = _rscan_prop(binary, b"ActorClassName\x00", i, 700, "name")
        end = binary.find(b"ComponentClassName\x00", i + 33, i + 15000)
        if end == -1:
            end = i + 12000
        bd_pos = binary.find(b"BinaryData\x00", i, i + 500)
        if bd_pos == -1:
            i += len(pat)
            continue

        loc         = _scan_translation(binary, bd_pos, end)
        yaw         = _scan_rotation_yaw(binary, bd_pos, end)
        fully_mined = _scan_prop(binary, b"bIsVoxelFullyMined\x00", bd_pos, end, "bool")

        if loc:
            x_m, y_m, z_m = loc
            caves.append({"x_m": x_m, "y_m": y_m, "z_m": z_m,
                          "yaw": yaw, "actor_class": actor_class, "fully_mined": bool(fully_mined)})
        i += len(pat)
    return caves
