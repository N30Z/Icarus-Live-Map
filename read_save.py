import json
import base64
import zlib
import struct


INPUT_FILE = "GD.json"
OUTPUT_BIN = "actors.bin"
OUTPUT_JSON = "players.json"


# ---------------------------------------------------------------------------
# Laden & Entpacken
# ---------------------------------------------------------------------------

def load_save():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def decompress_blob(data):
    blob = data["ProspectBlob"]["BinaryBlob"]
    decoded = base64.b64decode(blob)
    decompressed = zlib.decompress(decoded)

    with open(OUTPUT_BIN, "wb") as f:
        f.write(decompressed)

    print(f"[+] Blob entpackt -> {OUTPUT_BIN} ({len(decompressed):,} bytes)")
    return decompressed


# ---------------------------------------------------------------------------
# UE4 FProperty Primitives
#
# Format einer Property:
#   [int32: name_len]["name\0"]
#   [int32: type_len]["type\0"]
#   [int32: payload_size]
#   [int32: array_index]
#   -- nur StructProperty: [int32: struct_type_len]["struct_type\0"] + [16B: StructGuid]
#   [1B: HasPropertyGuid] + optional [16B: PropertyGuid]
#   [payload_size bytes: Nutzdaten]
# ---------------------------------------------------------------------------

def read_int32(data, pos):
    v, = struct.unpack_from("<i", data, pos)
    return v, pos + 4


def read_fstring(data, pos):
    length, pos = read_int32(data, pos)
    if length == 0:
        return "", pos
    if length < 0:
        byte_len = -length * 2
        s = data[pos:pos + byte_len].decode("utf-16-le", errors="replace").rstrip("\x00")
        return s, pos + byte_len
    s = data[pos:pos + length - 1].decode("latin-1")
    return s, pos + length


def read_strprop_after_name(data, pos):
    """Liest StrProperty-Wert. pos = erstes Byte nach dem Name-FString-Nullterminator."""
    try:
        type_len, pos = read_int32(data, pos)
        pos += type_len                        # Typname uberspringen
        _, pos = read_int32(data, pos)         # size
        _, pos = read_int32(data, pos)         # array_index
        has_guid = data[pos]; pos += 1
        if has_guid:
            pos += 16
        val_len, pos = read_int32(data, pos)
        if val_len <= 0 or val_len > 100_000:
            return None, pos
        val = data[pos:pos + val_len - 1].decode("latin-1")
        return val, pos + val_len
    except Exception:
        return None, pos


def read_intprop_after_name(data, pos):
    """Liest IntProperty-Wert. pos = erstes Byte nach dem Name-FString-Nullterminator."""
    try:
        type_len, pos = read_int32(data, pos)
        pos += type_len
        _, pos = read_int32(data, pos)         # size
        _, pos = read_int32(data, pos)         # array_index
        has_guid = data[pos]; pos += 1
        if has_guid:
            pos += 16
        val, pos = read_int32(data, pos)
        return val, pos
    except Exception:
        return None, pos


def parse_struct_prop_tag(binary, pos):
    """
    Liest einen StructProperty FPropertyTag ab dem int32-Namenslangen-Prefix.
    Gibt ({"name", "struct_name", "size"}, payload_start) oder (None, pos) zuruck.
    """
    try:
        name, pos = read_fstring(binary, pos)
        if not name or name == "None":
            return None, pos
        type_name, pos = read_fstring(binary, pos)
        if type_name != "StructProperty":
            return None, pos
        size, pos = read_int32(binary, pos)
        _, pos = read_int32(binary, pos)       # array_index
        struct_name, pos = read_fstring(binary, pos)
        pos += 16                              # StructGuid (16 Bytes)
        has_guid = binary[pos]; pos += 1
        if has_guid:
            pos += 16
        return {"name": name, "struct_name": struct_name, "size": size}, pos
    except Exception:
        return None, pos


def parse_vector_prop(binary, pos):
    """
    Liest eine Location StructProperty(Vector) ab dem int32-Namenslangen-Prefix.
    Gibt ({"x", "y", "z"}, end_pos) oder (None, pos) zuruck.
    """
    try:
        name, pos = read_fstring(binary, pos)
        if name != "Location":
            return None, pos
        type_name, pos = read_fstring(binary, pos)
        if type_name != "StructProperty":
            return None, pos
        size, pos = read_int32(binary, pos)
        _, pos = read_int32(binary, pos)
        struct_name, pos = read_fstring(binary, pos)
        pos += 16
        has_guid = binary[pos]; pos += 1
        if has_guid:
            pos += 16
        if struct_name != "Vector" or size != 12:
            return None, pos
        x, y, z = struct.unpack_from("<fff", binary, pos)
        return {"x": x, "y": y, "z": z}, pos + 12
    except Exception:
        return None, pos


def find_all(data, pattern):
    positions = []
    i = 0
    while True:
        i = data.find(pattern, i)
        if i == -1:
            break
        positions.append(i)
        i += len(pattern)
    return positions


# ---------------------------------------------------------------------------
# Spielernamen: (steam_id, slot) -> character_name
# ---------------------------------------------------------------------------

def extract_name_map(binary):
    """
    Scannt CachedCharacterName-Properties und verbindet sie mit
    UserID und ChrSlot aus demselben Struct-Block.
    Gibt {(steam_id, slot): character_name} zuruck.
    """
    mapping = {}

    for cn_pos in find_all(binary, b"CachedCharacterName\x00"):
        char_name, _ = read_strprop_after_name(binary, cn_pos + 20)
        if not char_name:
            continue

        search_start = max(0, cn_pos - 600)
        uid_pos = binary.rfind(b"UserID\x00", search_start, cn_pos)
        if uid_pos == -1:
            continue
        steam_id, _ = read_strprop_after_name(binary, uid_pos + 7)
        if not steam_id:
            continue

        slot = 0
        slot_pos = binary.find(b"ChrSlot\x00", uid_pos, cn_pos)
        if slot_pos != -1:
            slot_val, _ = read_intprop_after_name(binary, slot_pos + 8)
            if slot_val is not None:
                slot = slot_val

        mapping[(steam_id, slot)] = char_name

    return mapping


# ---------------------------------------------------------------------------
# Spielerpositionen aus PlayerState-Recorder-Blocks
# ---------------------------------------------------------------------------

def extract_player_states(binary):
    """
    Ankert an PlayerCharacterID StructProperty-Tags (nur innerhalb von
    PlayerStateRecorderComponent-Blocks), liest PlayerID + ChrSlot,
    und findet die benachbarte Location/Rotation.
    Gibt Liste von {steam_id, slot, location, rotation} zuruck.
    """
    players = []
    seen_steam_ids = set()

    for pcid_str_pos in find_all(binary, b"PlayerCharacterID\x00"):
        fstr_start = pcid_str_pos - 4
        if fstr_start < 0:
            continue
        declared_len, = struct.unpack_from("<i", binary, fstr_start)
        if declared_len != 18:   # len("PlayerCharacterID\0") == 18
            continue

        # Nur innerhalb von PlayerStateRecorderComponent-Blocks
        search_back = max(0, pcid_str_pos - 5000)
        if binary.find(b"PlayerStateRecorderComponent", search_back, pcid_str_pos) == -1:
            continue

        tag, payload_pos = parse_struct_prop_tag(binary, fstr_start)
        if tag is None or tag["name"] != "PlayerCharacterID":
            continue

        end_of_struct = payload_pos + tag["size"]

        steam_id = None
        slot = 0
        pid_pos = binary.find(b"PlayerID\x00", payload_pos, end_of_struct)
        if pid_pos != -1:
            steam_id, _ = read_strprop_after_name(binary, pid_pos + 9)

        slot_pos = binary.find(b"ChrSlot\x00", payload_pos, end_of_struct)
        if slot_pos != -1:
            slot_val, _ = read_intprop_after_name(binary, slot_pos + 8)
            if slot_val is not None:
                slot = slot_val

        if not steam_id or steam_id in seen_steam_ids:
            continue
        seen_steam_ids.add(steam_id)

        # Sibling-Properties: Location und Rotation
        location = None
        rotation = None
        loc_pos = binary.find(b"Location\x00", end_of_struct, end_of_struct + 3000)

        if loc_pos != -1:
            location, _ = parse_vector_prop(binary, loc_pos - 4)

            rot_pos = binary.find(b"Rotation\x00", loc_pos, loc_pos + 500)
            if rot_pos != -1:
                try:
                    name, p = read_fstring(binary, rot_pos - 4)
                    _, p = read_fstring(binary, p)
                    size, p = read_int32(binary, p)
                    _, p = read_int32(binary, p)
                    struct_name, p = read_fstring(binary, p)
                    p += 16
                    has_guid = binary[p]; p += 1
                    if has_guid:
                        p += 16
                    if name == "Rotation" and struct_name == "Quat" and size == 16:
                        rx, ry, rz, rw = struct.unpack_from("<ffff", binary, p)
                        rotation = {"x": rx, "y": ry, "z": rz, "w": rw}
                except Exception:
                    pass

        players.append({
            "steam_id": steam_id,
            "slot": slot,
            "location": location,
            "rotation": rotation,
        })

    return players


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    data = load_save()
    binary = decompress_blob(data)

    print("[+] Extrahiere Spielernamen...")
    name_map = extract_name_map(binary)
    print(f"    {len(name_map)} Eintraege gefunden:")
    for (sid, slot), name in name_map.items():
        print(f"    [{sid}] Slot {slot} -> {name}")

    print("[+] Extrahiere Spielerpositionen...")
    player_states = extract_player_states(binary)

    result = []
    for ps in player_states:
        sid = ps["steam_id"]
        slot = ps["slot"]
        loc = ps["location"]

        char_name = name_map.get((sid, slot))
        if not char_name:
            char_name = next(
                (n for (s, _), n in name_map.items() if s == sid),
                "<unknown>"
            )

        entry = {
            "steam_id": sid,
            "character_name": char_name,
            "slot": slot,
            "online": loc is not None,
        }

        if loc:
            entry.update({
                "x": round(loc["x"], 2),
                "y": round(loc["y"], 2),
                "z": round(loc["z"], 2),
                "x_m": round(loc["x"] / 100, 2),
                "y_m": round(loc["y"] / 100, 2),
                "z_m": round(loc["z"] / 100, 2),
            })
            print(f"    [{char_name}] X={entry['x_m']}m  Y={entry['y_m']}m  Z={entry['z_m']}m")
        else:
            print(f"    [{char_name}] (offline / keine Position)")

        if ps["rotation"]:
            rot = ps["rotation"]
            entry["rotation"] = {k: round(v, 4) for k, v in rot.items()}

        result.append(entry)

    output = {
        "player_count": len(result),
        "players": result,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Fertig -> {OUTPUT_JSON} ({len(result)} Spieler)")


if __name__ == "__main__":
    main()
