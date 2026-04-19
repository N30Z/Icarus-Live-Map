# Icarus Live Map – Research & Knowledge

## Goal

Read live player positions and names from the Icarus Dedicated Server and display them on a web map.

---

## Architecture (current, working)

```
IcarusServer-Win64-Shipping.exe  (C:\IcarusServer\Icarus\Binaries\Win64\)
  └─ ReadProcessMemory (Windows)
       └─ memory_reader.py  →  live_players.json
            └─ server.py   →  GET /players.json
                 └─ index.html polls every 2 s
```

Fallback (no live reader): `parse_players.py` parses `GD.json` on every server save.

---

## Server Binary

| Property | Value |
|---|---|
| Binary | `IcarusServer-Win64-Shipping.exe` |
| Engine | Unreal Engine 4.27 |
| Location | `C:\IcarusServer\Icarus\Binaries\Win64\` |
| PAK folder | `C:\IcarusServer\Icarus\Content\Paks\` |
| Mod folder | `C:\IcarusServer\Icarus\Content\Paks\mods\` |
| UE4 log | `C:\IcarusServer\Icarus\Saved\Logs\Icarus.log` |
| Game data | `C:\IcarusServer\Icarus\Content\Data\data.pak` |
| GameMode | `/Game/BP/Systems/BP_IcarusGameMode.BP_IcarusGameMode_C` |

---

## UE4 Memory Layout (verified offsets)

```
GWorld  →  UWorld*
  └─ [scan +0x000..+0x300]  →  AGameStateBase*
       └─ PlayerArray (TArray): [ptr 8B][count 4B][max 4B]
            └─ APlayerState*  (each element)
                 ├─ +0x368  FString PlayerName  [ptr 8B][len 4B][max 4B]  (UTF-16LE)
                 └─ +0x3A0  APawn* PawnPrivate
                       └─ +0x198  USceneComponent* RootComponent
                              └─ +0x11C  FVector { f32 X, Y, Z }  (in cm)
```

GWorld pointer found via pattern scan (RIP-relative MOV instruction in .text):
- Patterns: `48 8B 1D`, `48 8B 05`, `48 8B 0D`, `48 8B 15`, `4C 8B 05`
- Context check: followed by `48 85`, `48 83`, or `48 3B`

Offsets saved to `offsets.json` after `--trace` calibration.

---

## Daedalus Log (client-side addresses, one run)

Found by `DaedalusLoader.dll` (UnrealModLoader v2.2.0, CLIENT only):

```
NamePool:      0x00007FF7F42A3A80
GObject:       0x00007FF7F42DFDE0
GWorld ptr:    0x00007FF7F4423ED0
GameStateInit: 0x00007FF7F1817E80
BeginPlay:     0x00007FF7F1579F80
ProcessEvent:  0x00007FF7F00F9D10
```

These are **runtime ASLR addresses** from one specific session.
The module-relative offsets can be derived by subtracting the module base.

---

## Mod System Analysis

### Client (Icarus game)

| Component | Details |
|---|---|
| Loader | `DaedalusLoader.dll` (UnrealModLoader v2.2.0, adapted by edmiester777) |
| Blueprint mods | `Icarus\Content\Paks\LogicMods\` |
| C++ DLL mods | `Icarus\Binaries\Win64\CoreMods\` (e.g. `MiniMap.dll`) |
| Spawn hook | `AGameModeBase::InitGameState` |

**Blueprint PAK mods work on the client** via DaedalusLoader spawning all actors found in LogicMods PAKs.

### Server (IcarusServer)

| Component | Details |
|---|---|
| DaedalusLoader | ❌ Not present |
| LogicMods folder | ❌ Not present |
| `mods/` folder | ✅ Present, PAKs auto-mounted by UE4 |
| `mods/` content type | **JSON data tables only** (same format as `data.pak`) |
| Blueprint code mods | ❌ No loader to spawn actors |

**The `mods/` folder is a data mod system**, not a code mod system.
PAK files placed there are mounted and their JSON tables merged with game data.
Blueprint `.uasset` files would be mountable but nothing spawns them.

### data.pak format

Located at `C:\IcarusServer\Icarus\Content\Data\data.pak`.
Mount point: `../../../Icarus/Content/Data/`.

Must contain `DataTableMetadata.json`:
```json
{
  "LoadingPhases": {
    "Startup": ["D_Keybindings", "D_FeatureLevels", ...],
    "PostEngineInit": ["D_AssetReferences"],
    "PreOrchestration": ["D_RepGraphClassPolicies", ...],
    "PostContentServer": [],
    "NotLoadedOnPhase": []
  }
}
```

Row structs reference C++ classes: `"/Script/Icarus.BlueprintUnlock"` etc.
All game logic is compiled C++ in the server binary — no Blueprint source assets in PAKs.

---

## PAK Tools

| Tool | Version | Location |
|---|---|---|
| UnrealPak | 4.25.3 | `h:\Projects\icarus-save-editor\assets\UnrealPak\Engine\Binaries\Win64\UnrealPak.exe` |
| Server PAKs | UE4.27 WindowsServer format | `C:\IcarusServer\Icarus\Content\Paks\` |
| Client PAKs | UE4.27 WindowsNoEditor format | `G:\SteamLibrary\steamapps\common\Icarus\Icarus\Content\Paks\` |

**Note:** UnrealPak 4.25.3 can list/extract UE4.27 PAKs (index format compatible).
Client and server PAKs are not encrypted — no AES key needed.

---

## Blueprint PAK Mod – Status & Blockers

### What would be needed

1. UE4.27 Editor with access to Icarus C++ class headers (or Icarus Mod Kit)
2. Blueprint Actor extending `AActor` (engine-level, no Icarus-specific parent needed for basic access)
3. Cook target: `WindowsServer` (server-compatible)
4. Deploy to `LogicMods/` on client OR find a server-side spawn mechanism

### Why it doesn't work server-side (yet)

- No DaedalusLoader on server → nothing scans PAKs for actor classes to spawn
- `mods/` folder is data-only → PAK gets mounted but actors never spawned
- To fix: port UnrealModLoader to server (C++ DLL project) or find built-in hook

### Blueprint logic (if a loader existed)

```
Event BeginPlay
  └─ SetTimerByEvent (2.0s, looping)
       └─ OnTick:
            GetGameState → Cast to GameStateBase
            → PlayerArray (TArray<APlayerState*>)
            ForEach PlayerState:
              Name     = PlayerState.GetPlayerName()
              Pawn     = PlayerState.GetPawn()
              Location = Pawn.GetActorLocation() / 100   ← meters
            Build JSON string (manual Append)
            HTTP POST → http://127.0.0.1:9090/players
```

`server.py` has a `do_POST /players` handler ready for this.

---

## Working Data Flows

### Flow A: GD.json (save-file based)

```
Server writes GD.json on each prospect save
  └─ parse_players.py → players.json
       └─ server.py serves /api/state
```

Latency: up to several minutes (depends on server save interval).

### Flow B: memory_reader.py (live, Windows)

```
memory_reader.py --loop 2 → live_players.json
  └─ server.py _live_cache → GET /players.json
```

Requires: admin privileges, same Windows host.

### Flow C: wine_reader.py (live, Linux host / Docker)

```
sudo wine_reader.py --loop 2 --serve 8081
  └─ server.py polls LIVE_READER_URL=http://localhost:8081
```

Requires: root on Linux host, /proc/<pid>/mem access.
