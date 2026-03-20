"""
Icarus SaveGame - vollstaendiger UE4 FProperty Parser
Liest alle StateRecorderBlob-Eintraege und gibt strukturiertes JSON aus.

Ausgabedateien:
  savegame.json  - alle Akteure, kategorisiert
  players.json   - Spielerpositionen (kompatibel mit Vorversion)
"""

import json
import base64
import zlib
import struct
import sys

INPUT_FILE  = "GD.json"
OUT_ALL     = "savegame.json"
OUT_PLAYERS = "players.json"

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _int32(d, p):
    v, = struct.unpack_from("<i", d, p)
    return v, p + 4

def _uint32(d, p):
    v, = struct.unpack_from("<I", d, p)
    return v, p + 4

def _int64(d, p):
    v, = struct.unpack_from("<q", d, p)
    return v, p + 4 + 4

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


# ---------------------------------------------------------------------------
# Struct payload - atomare Typen werden direkt als Bytes gelesen
# ---------------------------------------------------------------------------

ATOMIC_STRUCTS = {
    "Vector":       ("xyz",  "<fff",  12),
    "Vector2D":     ("xy",   "<ff",    8),
    "Rotator":      ("pyr",  "<fff",  12),
    "Quat":         ("xyzw", "<ffff", 16),
    "LinearColor":  ("rgba", "<ffff", 16),
    "Color":        ("bgra", "4B",     4),
    "IntPoint":     ("xy",   "<ii",    8),
    "IntVector":    ("xyz",  "<iii",  12),
    "Guid":         (None,   None,    16),   # als hex
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


# ---------------------------------------------------------------------------
# Zentraler Property-Reader  (rekursiv)
# ---------------------------------------------------------------------------

def read_properties(d, pos, byte_limit=None):
    """
    Liest eine Folge von FPropertyTags bis zum 'None'-Sentinel.
    Gibt (dict_of_values, end_pos) zurueck.
    byte_limit: maximale Bytes ab pos (fuer Payload-Groessen-Kontrolle).
    """
    result = {}
    end = (pos + byte_limit) if byte_limit is not None else len(d)

    while pos < end:
        # --- Name ---
        try:
            name, pos = _fstring(d, pos)
        except Exception:
            break
        if not name or name == "None":
            break

        # --- Type ---
        try:
            prop_type, pos = _fstring(d, pos)
        except Exception:
            break

        # --- Size + ArrayIndex ---
        try:
            size,  pos = _int32(d, pos)
            _,     pos = _int32(d, pos)   # array_index (ignoriert)
        except Exception:
            break

        # --- Typ-spezifische Tag-Metadaten ---
        extra = {}
        try:
            if prop_type == "StructProperty":
                struct_name, pos = _fstring(d, pos)
                pos += 16                   # StructGuid
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["struct_name"] = struct_name

            elif prop_type == "BoolProperty":
                bool_val = d[pos]; pos += 1
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                # BoolProperty hat keine Payload-Bytes
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
                key_type, pos   = _fstring(d, pos)
                val_type, pos   = _fstring(d, pos)
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["key_type"]  = key_type
                extra["val_type"]  = val_type

            elif prop_type == "SetProperty":
                inner_type, pos = _fstring(d, pos)
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16
                extra["inner_type"] = inner_type

            else:
                has_guid = d[pos]; pos += 1
                if has_guid: pos += 16

        except Exception:
            # Metadaten kaputt -> Payload ueberspringen und weiter
            pos += size
            continue

        # --- Payload ---
        payload_start = pos
        try:
            value = _read_payload(d, pos, prop_type, size, extra)
        except Exception:
            value = f"<parse_error size={size}>"
        pos = payload_start + size

        # Duplikate als Liste akkumulieren
        if name in result:
            if not isinstance(result[name], list):
                result[name] = [result[name]]
            result[name].append(value)
        else:
            result[name] = value

    return result, pos


def _read_payload(d, pos, prop_type, size, extra):
    """Liest den Payload-Block einer Property."""

    if prop_type == "StrProperty":
        val, _ = _fstring(d, pos)
        return val

    elif prop_type in ("NameProperty", "TextProperty"):
        # NameProperty: FString-Format
        # TextProperty: komplex (Flags, History) - als rohes String
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
            # Manche ByteProperty sind groesser (EnumProperty mit Namen)
        val, _ = _fstring(d, pos)
        return val

    elif prop_type == "ObjectProperty":
        v, _ = _int32(d, pos)
        return v   # Objekt-Index (nicht aufloesbar)

    elif prop_type == "SoftObjectProperty":
        asset_path, _ = _fstring(d, pos)
        return asset_path

    elif prop_type == "StructProperty":
        struct_name = extra.get("struct_name", "")
        if struct_name in ATOMIC_STRUCTS:
            val, _ = _read_atomic(d, pos, struct_name)
            return val
        # Komplexe Structs: Property-Block lesen
        props, _ = read_properties(d, pos, byte_limit=size)
        return props

    elif prop_type == "ArrayProperty":
        return _read_array(d, pos, size, extra)

    elif prop_type == "MapProperty":
        return _read_map(d, pos, size, extra)

    elif prop_type == "SetProperty":
        return _read_set(d, pos, size, extra)

    else:
        return f"<{prop_type} size={size}>"


# ---------------------------------------------------------------------------
# Array / Map / Set
# ---------------------------------------------------------------------------

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

    # Bytes (ByteProperty ohne Enum) -> kompakte Darstellung
    # Grosse Byte-Arrays NICHT als Python-Liste expandieren (50x RAM-Overhead)
    if inner == "ByteProperty":
        if count > 4096:
            return f"<binary {count} bytes: {d[pos:pos+16].hex()}>"
        return list(d[pos:pos + count])

    # Einfache skalare Typen
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
        # Struct-Name ist in extra (wurde beim Tag gesetzt durch den aeusseren ArrayProperty-Tag)
        # Wir lesen einfach count Property-Bloecke
        result = []
        for _ in range(count):
            props, pos = read_properties(d, pos)
            result.append(props)
        return result

    if inner == "ObjectProperty":
        return [struct.unpack_from("<i", d, pos + i * 4)[0] for i in range(count)]

    # Fallback: rohe Bytes als Hex
    raw = d[pos:pos + total_size - 4]
    return f"<array[{inner}] count={count} raw={raw[:32].hex()}>"


def _read_map(d, pos, total_size, extra):
    # MapProperty: _Unknown(4) + count + [key,value]*count
    # Wir geben einen Hinweis zurueck (sehr komplex, selten benoetigt)
    try:
        _, pos2 = _int32(d, pos)      # zero (unbekannt)
        count, _ = _int32(d, pos2)
        return f"<map count={count}>"
    except Exception:
        return "<map>"


def _read_set(d, pos, total_size, extra):
    try:
        _, pos2 = _int32(d, pos)      # zero
        count, _ = _int32(d, pos2)
        return f"<set count={count}>"
    except Exception:
        return "<set>"


# ---------------------------------------------------------------------------
# BinaryData-Blob (nested property stream)
# ---------------------------------------------------------------------------

def _decode_binary_data(array_value):
    """
    Wandelt einen ByteProperty-Array-Wert (Liste von ints) in einen
    geparsten Property-Dict um, falls moeglich.
    """
    if not isinstance(array_value, list) or not array_value:
        return array_value
    raw = bytes(array_value)
    try:
        props, _ = read_properties(raw, 0)
        if props:
            return props
    except Exception:
        pass
    # Nicht parsebar -> raw bytes als Hex (abgeschnitten)
    return f"<binary {len(raw)} bytes: {raw[:16].hex()}...>"


# ---------------------------------------------------------------------------
# Top-Level: StateRecorderBlobs einlesen
# ---------------------------------------------------------------------------

def _skip_struct_prop_tag(d, pos):
    """
    Uberspringt den StructProperty-Tag-Header eines Array-Elements
    (Name, Type, Size, ArrayIndex, StructName, StructGuid, HasPropertyGuid).
    Gibt die Position NACH dem Tag-Header zurueck (= Beginn der inneren Properties).
    """
    _, pos = _fstring(d, pos)   # name (z.B. "StateRecorderBlobs")
    _, pos = _fstring(d, pos)   # type = "StructProperty"
    _, pos = _int32(d, pos)     # size (ignorieren - unzuverlaessig)
    _, pos = _int32(d, pos)     # array_index
    _, pos = _fstring(d, pos)   # struct_name (z.B. "StateRecorderBlob")
    pos += 16                   # StructGuid
    has_guid = d[pos]; pos += 1
    if has_guid: pos += 16
    return pos


def parse_state_recorder_blobs(d):
    """
    Liest alle StateRecorderBlob-Eintraege.
    Gibt Liste von dicts zurueck.

    Format:
      ArrayProperty-Tag (StateRecorderBlobs)
      count (int32)
      Fuer jedes Element:
        StructProperty-Tag-Header (Name="StateRecorderBlobs", StructName="StateRecorderBlob")
        [Properties: ComponentClassName, BinaryData, ActorTransform, ...]
        None
    """
    pos = 0

    # Aeusserer ArrayProperty-Tag lesen
    name, pos = _fstring(d, pos)
    assert name == "StateRecorderBlobs", f"Erwartet StateRecorderBlobs, got {name!r}"
    prop_type, pos = _fstring(d, pos)
    assert prop_type == "ArrayProperty"

    _, pos = _int32(d, pos)             # total_size (ignorieren)
    _, pos = _int32(d, pos)             # array_index
    _, pos = _fstring(d, pos)           # inner_type = "StructProperty"
    has_guid = d[pos]; pos += 1
    if has_guid: pos += 16

    # Element-Anzahl
    count, pos = _int32(d, pos)
    print(f"    {count} StateRecorderBlob-Eintraege gefunden")

    # Alle 2391 Eintraege stecken in EINEM aeusseren StructProperty-Tag.
    # Diesen einen Tag-Header uberspringen, dann 2391 bare Property-Bloecke lesen.
    try:
        pos = _skip_struct_prop_tag(d, pos)
    except Exception as e:
        raise RuntimeError(f"Aeusseren StructProperty-Tag konnte nicht geskippt werden: {e}")

    blobs = []
    for i in range(count):
        if i % 200 == 0 and i > 0:
            print(f"    ... {i}/{count} verarbeitet")

        # Property-Block bis zum None-Sentinel lesen (kein Tag-Header pro Element)
        try:
            entry_props, pos = read_properties(d, pos)
        except Exception as e:
            print(f"[!] Property-Lesen fehlgeschlagen bei Element {i} (pos={pos}): {e}")
            break

        if not entry_props:
            continue

        # BinaryData-Blob nur fuer relevante Typen dekodieren.
        # Voxel/ResourceDeposit/etc. enthalten riesige Terrain-Daten die
        # beim rekursiven Parsen 50GB+ RAM verursachen.
        SKIP_BINARY = {
            "/Script/Icarus.VoxelRecorderComponent",
            "/Script/Icarus.SpawnedVoxelRecorderComponent",
            "/Script/Icarus.ResourceDepositRecorderComponent",
            "/Script/Icarus.FLODRecorderComponent",
            "/Script/Icarus.FLODTileRecorderComponent",
        }
        cname = entry_props.get("ComponentClassName", "")
        if "BinaryData" in entry_props and cname not in SKIP_BINARY:
            entry_props["BinaryData"] = _decode_binary_data(entry_props["BinaryData"])
        elif "BinaryData" in entry_props:
            bd = entry_props["BinaryData"]
            if isinstance(bd, list):
                entry_props["BinaryData"] = f"<binary {len(bd)} bytes skipped>"
            elif isinstance(bd, str) and bd.startswith("<binary"):
                pass  # already summarized

        blobs.append(entry_props)

    return blobs, pos


# ---------------------------------------------------------------------------
# Kategorisierung & Extraktion
# ---------------------------------------------------------------------------

def _get_location(props):
    """Extrahiert X/Y/Z aus ActorTransform > Translation."""
    t = props.get("ActorTransform")
    if not isinstance(t, dict):
        return None
    tr = t.get("Translation")
    if isinstance(tr, dict) and "x" in tr:
        return {
            "x": round(tr["x"], 2),
            "y": round(tr["y"], 2),
            "z": round(tr["z"], 2),
            "x_m": round(tr["x"] / 100, 2),
            "y_m": round(tr["y"] / 100, 2),
            "z_m": round(tr["z"] / 100, 2),
        }
    return None


TYPE_SHORT = {
    "/Script/Icarus.PlayerStateRecorderComponent":         "player_state",
    "/Script/Icarus.PlayerRecorderComponent":              "player",
    "/Script/Icarus.PlayerHistoryRecorderComponent":       "player_history",
    "/Script/Icarus.DeployableRecorderComponent":          "deployable",
    "/Script/Icarus.BuildingGridRecorderComponent":        "building",
    "/Script/Icarus.BedRecorderComponent":                 "bed",
    "/Script/Icarus.CropPlotRecorderComponent":            "crop",
    "/Script/Icarus.SignRecorderComponent":                "sign",
    "/Script/Icarus.DrillRecorderComponent":               "drill",
    "/Script/Icarus.SplineRecorderComponent":              "spline",
    "/Script/Icarus.ResourceDepositRecorderComponent":     "resource_deposit",
    "/Script/Icarus.VoxelRecorderComponent":               "voxel",
    "/Script/Icarus.SpawnedVoxelRecorderComponent":        "spawned_voxel",
    "/Script/Icarus.EnzymeGeyserRecorderComponent":        "enzyme_geyser",
    "/Script/Icarus.OilGeyserRecorderComponent":           "oil_geyser",
    "/Script/Icarus.CaveAIRecorderComponent":              "cave_ai",
    "/Script/Icarus.CaveEntranceRecorderComponent":        "cave_entrance",
    "/Script/Icarus.IcarusMountCharacterRecorderComponent":"mount",
    "/Script/Icarus.RocketRecorderComponent":              "rocket",
    "/Script/Icarus.DynamicRocketSpawnRecorderComponent":  "rocket_spawn",
    "/Script/Icarus.FLODRecorderComponent":                "flod",
    "/Script/Icarus.FLODTileRecorderComponent":            "flod_tile",
    "/Script/Icarus.GameModeStateRecorderComponent":       "game_mode",
    "/Script/Icarus.WeatherForecastRecorderComponent":     "weather_forecast",
    "/Script/Icarus.WeatherControllerRecorderComponent":   "weather",
    "/Script/Icarus.WorldBossManagerRecorderComponent":    "world_boss",
    "/Script/Icarus.MapManagerRecorderComponent":          "map_manager",
    "/Script/Icarus.IcarusContainerManagerRecorderComponent": "container_manager",
    "/Script/Icarus.IcarusQuestManagerRecorderComponent":  "quest_manager",
    "/Script/Icarus.WorldTalentManagerRecorderComponent":  "talent_manager",
    "/Script/Icarus.CharacterTrapRecorderComponent":       "trap",
    "/Script/Icarus.BaseLevelTeleportRecorderComponent":   "teleport",
    "/Script/Icarus.InstancedLevelRecorderComponent":      "instanced_level",
}


def categorize(blobs):
    """Ordnet Blobs nach Typ und baut saubere Ausgabe-Struktur."""
    categories = {}

    for b in blobs:
        cname = b.get("ComponentClassName", "<unknown>")
        short = TYPE_SHORT.get(cname, cname.split(".")[-1].replace("RecorderComponent", "").lower())

        entry = {
            "type":  short,
            "actor": b.get("ObjectFName"),
        }

        loc = _get_location(b)
        if loc:
            entry["location"] = loc

        # Typ-spezifische Zusatzfelder
        if short == "player_state":
            bd = b.get("BinaryData")
            if isinstance(bd, dict):
                pcid = bd.get("PlayerCharacterID", {})
                if isinstance(pcid, dict):
                    entry["steam_id"] = pcid.get("PlayerID")
                    entry["slot"]     = pcid.get("ChrSlot", 0)
                if "Location" in bd:
                    loc2 = bd.get("Location")
                    if isinstance(loc2, dict):
                        entry["location"] = {
                            "x": round(loc2.get("x", 0), 2),
                            "y": round(loc2.get("y", 0), 2),
                            "z": round(loc2.get("z", 0), 2),
                            "x_m": round(loc2.get("x", 0) / 100, 2),
                            "y_m": round(loc2.get("y", 0) / 100, 2),
                            "z_m": round(loc2.get("z", 0) / 100, 2),
                        }

        elif short == "sign":
            bd = b.get("BinaryData", {})
            if isinstance(bd, dict):
                entry["text"] = bd.get("SignText") or bd.get("SignMessage")

        elif short in ("resource_deposit",):
            entry["tile"]  = b.get("TileName")
            entry["index"] = b.get("RecordIndex")

        elif short == "weather":
            bd = b.get("BinaryData", {})
            if isinstance(bd, dict):
                entry["current_weather"]  = bd.get("CurrentWeather") or bd.get("WeatherState")
                entry["transition_time"]  = bd.get("TransitionTime")

        elif short == "game_mode":
            bd = b.get("BinaryData", {})
            if isinstance(bd, dict):
                entry["elapsed_time"]   = bd.get("ElapsedTime")
                entry["storm_active"]   = bd.get("bStormActive")

        elif short in ("bed",):
            entry["owner_id"] = None
            bd = b.get("BinaryData", {})
            if isinstance(bd, dict):
                entry["owner_id"] = bd.get("OwnerPlayerID") or bd.get("PlayerID")

        # Rohfelder fuer vollstaendige Info
        entry["raw"] = {k: v for k, v in b.items()
                        if k not in ("ComponentClassName", "ObjectFName", "ActorTransform",
                                     "ActorStateRecorderVersion", "FLODComponentData",
                                     "SavedInventories", "IcarusActorGUID")}

        categories.setdefault(short, []).append(entry)

    return categories


# ---------------------------------------------------------------------------
# Spieler-Extraktion (rueckwaertskompatibel mit players.json)
# ---------------------------------------------------------------------------

def extract_players_compat(binary):
    """Wiederverwendung der bewährten direkten Binary-Suche fuer players.json."""

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
            return d[p:p+vl-1].decode("latin-1"), p+vl
        except: return None, pos

    def intprop_val(d, pos):
        try:
            tl, p = _int32(d, pos); p += tl
            _, p = _int32(d, p); _, p = _int32(d, p)
            hg = d[p]; p += 1
            if hg: p += 16
            v, p = _int32(d, p); return v, p
        except: return None, pos

    # Namen
    name_map = {}
    for cn_pos in find_all(binary, b"CachedCharacterName\x00"):
        char_name, _ = strprop_val(binary, cn_pos + 20)
        if not char_name: continue
        uid_pos = binary.rfind(b"UserID\x00", max(0, cn_pos-600), cn_pos)
        if uid_pos == -1: continue
        steam_id, _ = strprop_val(binary, uid_pos + 7)
        if not steam_id: continue
        slot = 0
        sp = binary.find(b"ChrSlot\x00", uid_pos, cn_pos)
        if sp != -1:
            sv, _ = intprop_val(binary, sp + 8)
            if sv is not None: slot = sv
        name_map[(steam_id, slot)] = char_name

    # Positionen
    players = []
    seen = set()
    for pcid_pos in find_all(binary, b"PlayerCharacterID\x00"):
        fs = pcid_pos - 4
        if fs < 0: continue
        dl, = struct.unpack_from("<i", binary, fs)
        if dl != 18: continue
        sb = max(0, pcid_pos - 5000)
        if binary.find(b"PlayerStateRecorderComponent", sb, pcid_pos) == -1: continue

        # Tag lesen
        try:
            name, p = _fstring(binary, fs)
            _, p    = _fstring(binary, p)
            size, p = _int32(binary, p); _, p = _int32(binary, p)
            _, p    = _fstring(binary, p); p += 16
            hg = binary[p]; p += 1
            if hg: p += 16
        except: continue

        end_struct = p + size
        steam_id = slot = None
        pp = binary.find(b"PlayerID\x00", p, end_struct)
        if pp != -1: steam_id, _ = strprop_val(binary, pp + 9)
        sp = binary.find(b"ChrSlot\x00", p, end_struct)
        if sp != -1:
            sv, _ = intprop_val(binary, sp + 8)
            if sv is not None: slot = sv or 0

        if not steam_id or steam_id in seen: continue
        seen.add(steam_id)

        # Location
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
            except: pass

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
                except: pass

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
                "x_m": round(loc["x"]/100, 2), "y_m": round(loc["y"]/100, 2), "z_m": round(loc["z"]/100, 2),
            })
        if ps["rotation"]:
            r = ps["rotation"]
            entry["rotation"] = {k: round(v, 4) for k, v in r.items()}
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("[+] Lade Savegame...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    binary = zlib.decompress(base64.b64decode(raw["ProspectBlob"]["BinaryBlob"]))
    print(f"    Binary: {len(binary):,} bytes")

    # --- Vollstaendiger Parser ---
    print("[+] Parse alle StateRecorderBlobs...")
    try:
        blobs, end_pos = parse_state_recorder_blobs(binary)
    except Exception as e:
        print(f"[!] Fehler beim Parsen der Blobs: {e}")
        blobs = []

    print("[+] Kategorisiere Eintraege...")
    categories = categorize(blobs)

    # Zusammenfassung
    print()
    print("    Typ                   Anzahl")
    print("    " + "-" * 30)
    total = 0
    for t, entries in sorted(categories.items(), key=lambda x: -len(x[1])):
        print(f"    {t:<22} {len(entries):>5}")
        total += len(entries)
    print(f"    {'GESAMT':<22} {total:>5}")

    # savegame.json schreiben
    output = {
        "binary_size": len(binary),
        "total_actors": total,
        "types": sorted(categories.keys()),
        "actors": categories,
    }
    with open(OUT_ALL, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n[+] -> {OUT_ALL}")

    # --- Spielerpositionen (rueckwaertskompatibel) ---
    print("[+] Extrahiere Spielerpositionen...")
    players = extract_players_compat(binary)
    for p in players:
        if p["online"]:
            print(f"    [{p['character_name']}] X={p['x_m']}m  Y={p['y_m']}m  Z={p['z_m']}m")
        else:
            print(f"    [{p['character_name']}] offline")

    with open(OUT_PLAYERS, "w", encoding="utf-8") as f:
        json.dump({"player_count": len(players), "players": players}, f,
                  indent=2, ensure_ascii=False)
    print(f"[+] -> {OUT_PLAYERS}")


if __name__ == "__main__":
    main()
