# Cave Data — Field Reference

Source: icarusintel.com `/map/layers/<map>` API, converted from 8192×8192 pixel coordinates to world meters.

**Coordinate conversion:** `x_m = raw_x − 4096`, `y_m = raw_y − 4096`  
Compatible with `toLatLng(x_m, y_m)` in index.html (CRS.Simple, bounds ±4096 m).

## Files

| File | Caves |
|---|---|
| `olympus.json` | 157 |
| `elysium.json` | 256 |
| `prometheus.json` | 276 |
| `styx.json` | 181 |

## Entry Format

```json
{
  "id":   "225",
  "x_m":  2968,
  "y_m":  2334,
  "yaw":  0,
  "size": "large",
  "oc":   "72",
  "doc":  "2",
  "hw":   true,  "wmin": "1", "wmax": "3",
  "hb":   true,  "bmin": "1", "bmax": "1",
  "haz":  true,
  "tun":  true,
  "uw":   false,
  "ug":   false,
  "wat":  true,
  "fsh":  true,
  "msh":  true,
  "wf":   true,
  "fld":  true,
  "rem":  false,
  "unf":  false,
  "gid":  "R-18",
  "n":    "Connects with 226. Contains hidden area reached by swimming."
}
```

## Field Descriptions

### Always present

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique cave ID (e.g. `"100"`, `"EL302"`, `"P0632"`, `"S0101"`) |
| `x_m` | number | World X position in meters (east/west, range ≈ ±3900) |
| `y_m` | number | World Y position in meters (north/south, range ≈ ±3900) |
| `yaw` | number | Entrance facing direction in degrees (0 = north, 90 = east) |
| `size` | string\|null | Cave size: `"small"`, `"medium"`, or `"large"` (`null` = unknown) |
| `oc` | string\|null | Ore count — total ore nodes inside. Used to determine tier: ≤24 → T1, ≤34 → T2, ≤54 → T3, >54 → T5. `null` = unknown. |

### Optional — ore & wildlife

| Field | Type | Description |
|---|---|---|
| `doc` | string | Deep ore vein count (number of deep-drill deposit slots) |
| `hw` | bool | Has worms |
| `wmin` | string | Minimum worm spawn count |
| `wmax` | string | Maximum worm spawn count (`"x"` = unknown upper bound) |
| `hb` | bool | Has bees |
| `bmin` | string | Minimum bee spawn count |
| `bmax` | string | Maximum bee spawn count |

### Optional — cave properties / flags

| Field | Type | Description |
|---|---|---|
| `haz` | bool | Pit hazard — dangerous drop inside the cave |
| `tun` | bool | Tunnel — connects two surface entrances, walkthrough cave |
| `uw` | bool | Underwater — entrance is submerged |
| `ug` | bool | Underground — entrance is below the surface (not visible from outside) |
| `wat` | bool | Has water inside |
| `fsh` | bool | Has fish |
| `msh` | bool | Has mushrooms |
| `wf` | bool | Waterfall cave |
| `fld` | bool | Flooded interior |
| `rem` | bool | Removed — cave was patched out of the game |
| `unf` | bool | Unfinished — cave geometry is incomplete / placeholder |

### Optional — metadata

| Field | Type | Description |
|---|---|---|
| `gid` | string | Grid cell reference on the in-game map (e.g. `"R-18"`) |
| `n` | string | Freeform notes (connections to other caves, hidden areas, etc.) |
