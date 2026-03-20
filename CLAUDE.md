# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the local web server (opens http://localhost:8080 automatically)
python server.py

# Parse player positions from GD.json → players.json (legacy, fast)
python parse_players.py

# Full save parser: all actors → savegame.json + players.json
python read_save.py
```

All scripts expect `GD.json` in the same directory. No external dependencies beyond Python stdlib.

## Architecture

### Data Flow

```
GD.json  (Icarus dedicated server export)
  └─ ProspectBlob.BinaryBlob  (base64 → zlib → UE4 FProperty binary stream)
       ├─ parse_players.py   → players.json   (player positions only)
       └─ read_save.py       → savegame.json  (all 2000+ actors, categorised)
                                    │
                              index.html polls players.json every 5 s
                              via Leaflet.js (CRS.Simple, map.jpg overlay)
```

### Python Parsers

**`parse_players.py`** — lightweight, single-purpose. Scans the binary for `PlayerStateRecorderComponent` anchors and extracts player positions by direct byte search. Faster but less complete.

**`read_save.py`** — full UE4 FProperty parser (`read_properties()`). Recursively decodes every `StateRecorderBlob` entry into a typed Python dict. Handles StructProperty, ArrayProperty, MapProperty, BoolProperty, EnumProperty, etc. Produces `savegame.json` with all actors grouped by short type name.

Key internals in `read_save.py`:
- `parse_state_recorder_blobs()` — top-level blob array parser
- `read_properties()` — recursive FProperty tag reader
- `categorize()` — maps `ComponentClassName` → short type and extracts type-specific fields
- `SKIP_BINARY` set — component types whose `BinaryData` is too large to parse (voxel terrain, resource deposits)
- `TYPE_SHORT` dict — maps full UE4 class paths to short category names used in `savegame.json`

### Frontend (`index.html`)

Single HTML file, no build step. Uses Leaflet.js with `L.CRS.Simple` and a static `map.jpg` overlay. Polls `players.json` at `POLL_INTERVAL` (5 s default). Key config constants at the top:

- `MAP_MIN / MAP_MAX` — prospect bounds in meters (default ±4096 m for standard prospects)
- `Y_SIGN` — set to `-1` to flip UE4's south-positive Y to north-positive for Leaflet
- `toLatLng(x_m, y_m)` — converts UE4 meter coordinates to Leaflet `[lat, lng]`

UE4 coordinates are stored in centimeters; `x_m = x / 100`.

### Actor Types in savegame.json

Categories present in a typical Olympus prospect:
`player_state`, `player`, `deployable`, `building`, `bed`, `crop`, `sign`, `drill`, `resource_deposit`, `cave_entrance`, `cave_ai`, `enzyme_geyser`, `oil_geyser`, `mount`, `rocket`, `weather`, `game_mode`, `voxel`, `flod`, `flod_tile`, `spline`, `trap`, `teleport`, `instanced_level`

### Available Data Per Actor Type

**Cave Entrances** (`cave_entrance`, 73 per prospect):
- `location` — x/y/z in cm and meters (from `ActorTransform.Translation` inside `BinaryData`)
- `rotation` — quaternion (from `ActorTransform.Rotation`)
- `ActorClassName` — encodes biome (`CF`=Crimson Forest, `AC`=Arctic, `DC`=Desert) and size (`SML`)
- `ActorPathName` — map tile (e.g. `T016_Generated_x3_y5`)
- `ObjectFName` — unique instance name
- `VoxelBlockerSaveData` — `bIsVoxelFullyMined`, `TotalUnminedVoxels`, `CurrentUnminedVoxels`, `NumResourcesGranted`, `TotalResourceCount`, `VoxelResourceOverride`
- `CaveActorSpawnTimeStamp` — when cave AI last spawned

**Resource Deposits** (`resource_deposit`, 644 per prospect):
- `location` — from top-level `ActorTransform` (not inside `BinaryData`)
- `ResourceDTKey` — ore type: `Iron`, `Coal`, `Copper`, `Gold`, `Platinum`, `Titanium`, `Aluminium`, `Silicon`, `Oxite`, `Sulfur`, `Salt`, `Stone`, `Exotic`
- `ResourceRemaining` — float (NaN = untouched/full; 0.0 = depleted)
- `ActorClassName` — `BP_Deep_Mining_Ore_Deposit_C` (surface), `BP_Deep_Mining_Ore_Deposit_Cave_C` (cave), `BP_MetaDeposit_Conifer_C` (meta)
- `FLODComponentData` — `TileName`, `LevelIndex`, `RecordIndex`, `InstanceIndex`

> **Note:** `ResourceDepositRecorderComponent` is in `SKIP_BINARY` in `read_save.py`, so `ResourceDTKey` and `ResourceRemaining` are currently not decoded into `savegame.json`. They must be extracted via direct binary scan (like `parse_players.py` does for players), or by removing the class from `SKIP_BINARY` with a size guard.
