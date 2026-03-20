# Icarus Live Map

An interactive, browser-based map for **Icarus** dedicated server sessions.
Upload your `GD.json` savegame file and explore player positions, ore deposits, cave entrances, and geysers — all processed locally; nothing leaves your browser.

**[Open the map →](https://n30z.github.io/Icarus-Live-Map/)**

---

## Features

- **Player tracking** — live positions with names and coordinates
- **Ore deposits** — all resource types with surface / cave / meta categories and depletion status; per-ore filter chips
- **Exotics highlight** — one-click toggle that enlarges and brightens Exotic deposits, even when the Deposits layer is disabled
- **Cave entrances** — all 73 entrances per prospect, with facing direction and mined-out state
- **Geysers** — enzyme and oil geysers with active status
- **Layer control** — toggle Deposits, Caves, Enzyme Geysers, and Oil Geysers independently
- **Opacity sliders** — per-category opacity controls

## Usage

1. Open the map at <https://n30z.github.io/Icarus-Live-Map/>
2. Export `GD.json` from your Icarus dedicated server (or copy it from the server data directory)
3. Drop the file onto the upload card — the map loads instantly

## Local development

```bash
# Serve the map locally (opens http://localhost:8080)
python server.py

# Optional: parse player positions from GD.json into players.json
python parse_players.py

# Optional: full save parse → savegame.json + players.json
python read_save.py
```

All scripts require `GD.json` in the same directory and use only the Python standard library.

## How it works

The browser downloads `GD.json`, base64-decodes and zlib-decompresses the `ProspectBlob.BinaryBlob`, then scans the raw UE4 FProperty binary stream for players, ore deposits, cave entrances, and geysers — the same logic as `parse_players.py` / `read_save.py`, ported to JavaScript.  The map is rendered with [Leaflet.js](https://leafletjs.com/) using `L.CRS.Simple` over a static `map.jpg` overlay.
