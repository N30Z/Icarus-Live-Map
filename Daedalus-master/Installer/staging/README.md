# Daedalus Mod Loader for Icarus

<p align="center">
  <strong>v2.3.0</strong> — A mod loader for <a href="https://store.steampowered.com/app/1149460/ICARUS/">Icarus</a> supporting both Blueprint (pak) and C++ (DLL) mods.
</p>

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation Guide](#installation-guide)
  - [Installer (Recommended)](#installer-recommended)
  - [Manual Client Installation](#manual-client-installation)
  - [Dedicated Server Installation](#dedicated-server-installation)
  - [Verifying Installation](#verifying-installation)
- [Configuration](#configuration)
  - [Config File Reference](#config-file-reference)
  - [Renderer Settings](#renderer-settings)
  - [Key Bindings](#key-bindings)
  - [Logging Settings](#logging-settings)
  - [Mod Management](#mod-management)
  - [Mod Dependencies](#mod-dependencies)
- [Using the In-Game Menu](#using-the-in-game-menu)
  - [Opening the Menu](#opening-the-menu)
  - [Logic Mods Panel](#logic-mods-panel)
  - [Core Mods Panel](#core-mods-panel)
  - [Debug / Dump Tools](#debug--dump-tools)
  - [Console Commands](#console-commands)
  - [Console Log Window](#console-log-window)
- [Hot-Reload System](#hot-reload-system)
- [Installing Mods](#installing-mods)
  - [Blueprint Mods (Pak Files)](#blueprint-mods-pak-files)
  - [C++ Core Mods (DLL Files)](#c-core-mods-dll-files)
  - [Mod Load Order](#mod-load-order)
  - [Declaring Mod Dependencies](#declaring-mod-dependencies)
- [Creating Your Own Mods](#creating-your-own-mods)
  - [Mod API Overview](#mod-api-overview)
  - [Mod Lifecycle](#mod-lifecycle)
  - [Available Hooks](#available-hooks)
  - [Example Mod Walkthrough](#example-mod-walkthrough)
- [Building from Source](#building-from-source)
- [Troubleshooting](#troubleshooting)
- [How It Works (Technical)](#how-it-works-technical)
  - [Injection and Symbol Resolution](#injection-and-symbol-resolution)
  - [Offset Caching System](#offset-caching-system)
  - [Crash Handler](#crash-handler)
  - [Dependency Resolution](#dependency-resolution)
- [Credits](#credits)
- [3rd Party Software](#3rd-party-software)
- [License](#license)

---

## Features

- **Dual renderer support** — works with both DirectX 11 and DirectX 12. Auto-detects the active renderer at runtime, or can be forced via config.
- **PDB-first symbol resolution** — uses the game's shipped PDB file for function lookups, making the loader resilient to game updates. Falls back to pattern scanning if PDB lookups fail.
- **Offset caching** — resolved symbol addresses are cached to disk between launches. After the first successful launch, subsequent startups skip PDB parsing and pattern scanning entirely, loading near-instantly. The cache is automatically invalidated when the game executable changes (via file size + timestamp fingerprinting).
- **Blueprint mod loading** — automatically discovers and loads `.pak` mods from the LogicMods folder when the game world initializes.
- **C++ core mod support** — loads DLL-based mods from the `mods` folder with full UE4 SDK access, giving modders deep control over game systems.
- **Mod load order** — explicit load ordering via config. Mods listed in `loadorder` are loaded first in that order; unlisted mods load alphabetically after.
- **Mod dependency system** — declare dependencies via sidecar `.deps` files or the INI `[Dependencies]` section. Daedalus performs topological sorting to ensure dependencies load before the mods that need them, and skips mods with missing or circular dependencies with clear warnings.
- **Hot-reload** — press **F7** (configurable) to unload all mods, re-read the config, and reload mods without restarting the game. Change load order, enable/disable mods, or swap DLLs and press F7 to apply instantly.
- **In-game ImGui overlay** — press **F1** (configurable) to toggle. Shows loaded mods, mod info, mod action buttons, debug dump tools, and a console command executor.
- **Console log window** — a separate, draggable ImGui window showing all Daedalus log output in real-time with color-coded severity levels (debug, info, warn, error), level filtering, auto-scroll, and a copy-to-clipboard button.
- **Configurable logging** — control log level, file output, and console window visibility via the INI config. Log levels include debug, info, warn, error, and off.
- **Configurable key bindings** — menu toggle, dump key, and reload key are all configurable via Virtual Key Codes in the INI file.
- **Crash handler** — catches unhandled exceptions and writes both a `.dmp` minidump (loadable in Visual Studio/WinDbg) and a human-readable `.txt` crash report to a `crashes` folder. The crash report includes the exception type, crash address with module attribution, full x64 register dump, 32-frame stack trace with module names, and a complete list of loaded modules. A MessageBox popup also notifies you of the crash before the game exits.
- **Event system** — mods can hook into engine events like `InitGameState`, `BeginPlay`, `PostBeginPlay`, `DX11Present`, `DX12Present`, and `DrawImGui`.
- **Resolution-aware UI** — the overlay automatically scales its font and layout to match your screen resolution.
- **SEH crash protection** — dump tools are wrapped in structured exception handlers so a failed dump won't crash your game.
- **Graceful failure handling** — validates all critical function lookups before hooking, with clear error messages if the game has updated beyond compatibility.

---

## Requirements

- **Icarus** (Steam version) — the game must be installed via Steam.
- **Windows 10/11** (64-bit).
- No additional runtimes or dependencies are needed for end users. Everything is self-contained.

---

## Installation Guide

### Installer (Recommended)

The easiest way to install Daedalus is with the included installer (`DaedalusInstaller.exe`):

1. **Run `DaedalusInstaller.exe`** — it will automatically detect your Steam installation path for Icarus.
2. **Choose your install folder** — if auto-detection fails or you have a custom install location, use the folder picker to browse to `Icarus\Binaries\Win64\`.
3. **Click Install** — the installer copies `xinput1_3.dll`, `version.dll`, `DaedalusLoader.dll`, the default config, and creates the `mods` folder.
4. **Launch Icarus** normally through Steam. A console window will appear showing the Daedalus startup log.

The installer validates the target folder by checking for `Icarus-Win64-Shipping.exe` before installing. It supports both the client proxy (`xinput1_3.dll`) and the dedicated server proxy (`version.dll`).

### Manual Client Installation

1. **Download** the latest release. You need these two files:
   - `xinput1_3.dll` (proxy DLL — this is what loads Daedalus into the game)
   - `DaedalusLoader.dll` (the mod loader itself)

2. **Locate your Icarus install folder.** The easiest way:
   - Open Steam → Right-click **ICARUS** → **Manage** → **Browse local files**
   - Navigate into: `Icarus\Binaries\Win64\`
   - The full path is typically:
     ```
     C:\Program Files (x86)\Steam\steamapps\common\Icarus\Icarus\Binaries\Win64\
     ```

3. **Copy both files** into that `Win64` folder:
   ```
   Icarus\Binaries\Win64\
   ├── Icarus-Win64-Shipping.exe    ← the game executable (already here)
   ├── Icarus-Win64-Shipping.pdb    ← the game's PDB (already here, required)
   ├── xinput1_3.dll                ← COPY HERE (proxy loader)
   └── DaedalusLoader.dll           ← COPY HERE (mod loader)
   ```

4. **Create the mods folder** (for C++ mods):
   ```
   Icarus\Binaries\Win64\mods\
   ```

5. **Launch Icarus** normally through Steam. A console window will appear alongside the game showing the Daedalus startup log.

### Dedicated Server Installation

1. Download the release files for server:
   - `version.dll` (server proxy DLL — used instead of `xinput1_3.dll`)
   - `DaedalusLoader.dll`

2. Copy both files into the server's `Win64` folder:
   ```
   Icarus\Binaries\Win64\
   ├── Icarus-Win64-Shipping.exe
   ├── version.dll                  ← COPY HERE (server proxy)
   └── DaedalusLoader.dll           ← COPY HERE
   ```

3. Start the dedicated server normally. Daedalus will load automatically.

### Verifying Installation

When the game launches with Daedalus installed, you will see:

1. **A console window** opens with the Daedalus ASCII banner and startup log.
2. The log should show all critical systems found:
   ```
   [Daedalus][INFO] Daedalus Mod Loader V 2.3.0
   [Daedalus][INFO] Profile Detected: Icarus-Win64-Shipping
   [Daedalus][INFO] FoundNamePool: 0x...
   [Daedalus][INFO] GObject: 0x...
   [Daedalus][INFO] GWorld: 0x...
   [Daedalus][INFO] Auto-detected renderer: DirectX 11  (or DirectX 12)
   [Daedalus][INFO] DX11 ImGui initialized successfully  (or DX12)
   ```
3. Press **F1** in-game to verify the overlay opens.

If you see `[ERROR]` lines for critical systems (GName, GObject, GWorld, GameStateInit, or BeginPlay), the game may have updated and Daedalus needs a new version. Check for updates.

---

## Configuration

On first launch, Daedalus creates a config file at:
```
Icarus\Binaries\Win64\daedalus.ini
```

You can edit this with any text editor while the game is closed, or edit it while the game is running and press **F7** to hot-reload with the new settings. If you delete the file, a fresh default config will be regenerated on next launch.

### Config File Reference

```ini
# Daedalus Mod Loader Configuration
# =================================

[Renderer]
# Renderer backend: auto, dx11, dx12
renderer = auto

[Input]
# Key bindings (VK codes, hex or decimal)
menukey = 0x70      # F1 - toggle overlay
dumpkey = 0x75      # F6 - dump all (objects, engine, world actors)
reloadkey = 0x76    # F7 - hot-reload mods

[Logging]
# Log level: debug, info, warn, error, off
loglevel = info
# Write log to Daedalus-Log.txt
logtofile = true
# Show console window with log output
logtoconsole = true

[Mods]
# Comma-separated list of mod DLL names to disable (without .dll extension)
disabled = 
# Load order: mods listed here load first, in this order (without .dll)
loadorder = 

[Dependencies]
# Declare mod dependencies (without .dll extension)
# Format: ModName = Dep1, Dep2, Dep3
```

### Renderer Settings

| Value  | Behavior |
|--------|----------|
| `auto` | **(Recommended)** Detects whether the game is using DX11 or DX12 at runtime and initializes the correct ImGui backend automatically. |
| `dx11` | Forces the DX11 rendering backend. Use this if auto-detection picks the wrong renderer. |
| `dx12` | Forces the DX12 rendering backend. |

Most users should leave this set to `auto`. Icarus supports both DX11 and DX12 depending on your graphics settings.

### Key Bindings

All key bindings use Windows Virtual Key Codes. The three configurable keys are:

| Setting | Default | Key | Description |
|---------|---------|-----|-------------|
| `menukey` | `0x70` | F1 | Toggle the Daedalus ImGui overlay |
| `dumpkey` | `0x75` | F6 | Dump objects, engine info, and world actors to files |
| `reloadkey` | `0x76` | F7 | Hot-reload all mods (unload, re-read config, reload) |

Common alternative keys:

| Key     | Hex Code | Decimal |
|---------|----------|---------|
| F1      | `0x70`   | 112     |
| F2      | `0x71`   | 113     |
| F3      | `0x72`   | 114     |
| F4      | `0x73`   | 115     |
| F5      | `0x74`   | 116     |
| F6      | `0x75`   | 117     |
| F7      | `0x76`   | 118     |
| F8      | `0x77`   | 119     |
| Insert  | `0x2D`   | 45      |
| Home    | `0x24`   | 36      |

Full list: [Microsoft VK Code Reference](https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes)

### Logging Settings

| Setting | Values | Description |
|---------|--------|-------------|
| `loglevel` | `debug`, `info`, `warn`, `error`, `off` | Controls the minimum severity of messages that are logged. `debug` shows everything; `off` silences all output. |
| `logtofile` | `true` / `false` | When enabled, all log output is written to `Daedalus-Log.txt` in the game folder. |
| `logtoconsole` | `true` / `false` | When enabled, log output is displayed in the console window that opens alongside the game. |

The log level filter applies to both file and console output, as well as the in-game console log window.

### Mod Management

The `[Mods]` section controls which mods are loaded and in what order.

**Disabling mods** — add mod names (without `.dll`) to the `disabled` list:
```ini
disabled = ExampleMod, BrokenMod
```
Disabled mods are skipped during loading. You can re-enable them by removing them from the list and pressing F7 to reload.

**Load order** — specify the order mods should load in:
```ini
loadorder = CoreLib, GameplayMod, OptionalMod
```
Mods listed in `loadorder` are loaded first, in exactly that order. Any mods not listed are loaded after, sorted alphabetically. This is important when one mod depends on another being loaded first.

### Mod Dependencies

The `[Dependencies]` section lets you declare that a mod requires other mods to be loaded before it. If a dependency is missing, the mod (and anything that depends on it) will be skipped with a warning.

```ini
[Dependencies]
MyMod = CoreLib, UtilMod
AdvancedMod = MyMod, CoreLib
```

Dependencies can also be declared via sidecar `.deps` files — see [Declaring Mod Dependencies](#declaring-mod-dependencies) below.

---

## Using the In-Game Menu

### Opening the Menu

Press **F1** (or your configured `menukey`) at any time while in-game to toggle the Daedalus overlay. The overlay renders on top of the game and captures mouse/keyboard input while open, so you can click buttons and type without affecting the game underneath.

Press **F1** again to close the overlay and return full input control to the game.

### Logic Mods Panel

The **Logic Mods** collapsing section shows all Blueprint/pak mods that were discovered and loaded. For each mod you can see:

- **Mod Name** — click to expand details.
- **Created By** — the mod author.
- **Description** — what the mod does.
- **Version** — the mod's version string.
- **Mod Buttons** — if the mod registered Blueprint-callable buttons, they appear here. Clicking a button executes `ModMenuButtonPressed` on the mod's actor.
- **Enable checkbox** — toggle the mod on/off at runtime.

### Core Mods Panel

The **Core Mods** collapsing section shows all C++ DLL mods loaded from the `mods` folder. Each mod displays:

- **Mod Name** — click to expand.
- **Created By** — the mod author(s).
- **Description** — what the mod does.
- **Version** — the mod's version string.
- **Mod Menu Button** — if the mod set `UseMenuButton = true`, a button appears that calls `OnModMenuButtonPressed()` on the mod.
- **Custom ImGui** — mods can draw their own ImGui widgets in this section via `DrawImGui()`.

### Debug / Dump Tools

The overlay includes three dump buttons under the **Tools** collapsible header. These can also be triggered by pressing **F6** (configurable via `dumpkey`), which runs all three dumps at once.

| Button | Output File | Description |
|--------|-------------|-------------|
| **Dump Objects** | `ObjectDump.txt` | Dumps the entire UObject array — every object currently loaded in memory. Includes the object index, full name, and memory address. This file can be very large (100+ MB with 400,000+ objects). |
| **Dump Engine Info** | `EngineInfo.txt` | Dumps internal engine offsets — GName, GObject, GWorld addresses (as RVAs from base), and the UObject/UField/UStruct/UFunction struct offsets Daedalus is using. Useful for verifying offsets after a game update. |
| **Dump World Actors** | `WorldActors_Dump.txt` | Dumps all actors currently in the world. Shows actor name, class name, and full UObject path. Useful for finding specific actors to interact with in mods. |

All dump files are written to the same folder as the game executable (`Icarus\Binaries\Win64\`). Dumps use absolute paths internally to avoid working directory issues.

### Console Commands

At the bottom of the Tools section there is a text input field and an **Execute** button. This allows you to run Unreal Engine console commands via `CallFunctionByNameWithArguments`. Type a command, click Execute (or press Enter), and the result will be shown in the console log.

### Console Log Window

The **Daedalus Console** is a separate, movable ImGui window that appears alongside the main overlay. It displays all log output from Daedalus in real-time, starting from startup. Features:

- **Color-coded entries** — errors are red, warnings are yellow, debug messages are gray, print messages are purple, and info messages are white.
- **Level filter buttons** — click **All**, **Info+**, **Warn+**, or **Error** to filter which messages are shown. The active filter is highlighted in orange.
- **Auto-scroll** — when enabled (default), the window automatically scrolls to show the newest log entries as they appear.
- **Copy All** — copies all currently-filtered log entries to your clipboard, useful for pasting into bug reports.
- **Closeable** — click the X to close the console window. It re-appears when you reopen the overlay.
- **Resizable and draggable** — the console window is independent from the main menu. Position and resize it however you like.

---

## Hot-Reload System

Daedalus supports hot-reloading mods without restarting the game. Press **F7** (configurable via `reloadkey`) to:

1. **Unload** all currently loaded DLL mods in reverse order (LIFO — last loaded, first unloaded).
2. **Re-read** the `daedalus.ini` config file to pick up any changes.
3. **Reload** all enabled mods from the `mods` folder using the updated config.

This allows you to:

- **Enable or disable mods** by editing the `disabled` list in the INI, then pressing F7.
- **Change load order** by editing the `loadorder` list, then pressing F7.
- **Swap mod DLLs** by replacing a `.dll` in the `mods` folder, then pressing F7 to unload the old version and load the new one.
- **Adjust log levels, key bindings, or other settings** without leaving the game.

The console log will show the full unload/reload sequence:
```
[Daedalus][INFO] === Hot-Reload: Reloading mods ===
[Daedalus][INFO] Unloaded mod: OptionalMod.dll
[Daedalus][INFO] Unloaded mod: CoreLib.dll
[Daedalus][INFO] Mod unloading complete: 2 unloaded
[Daedalus][INFO] Loaded mod: CoreLib.dll [order #0]
[Daedalus][INFO] Loaded mod: OptionalMod.dll [deps resolved]
[Daedalus][INFO] Mod loading complete: 2 loaded
[Daedalus][INFO] === Hot-Reload complete ===
```

> **Note:** Hot-reload calls `FreeLibrary` on each mod DLL. Mods that allocate persistent resources (hooks, threads, global state) should handle cleanup in their `DllMain` `DLL_PROCESS_DETACH` handler, or they may leave stale hooks or leak memory after a reload.

---

## Installing Mods

### Blueprint Mods (Pak Files)

Blueprint mods are packaged as `.pak` files created with the Unreal Engine editor. To install:

1. Navigate to:
   ```
   Icarus\Content\Paks\LogicMods\
   ```
   If the `LogicMods` folder doesn't exist, create it.

2. Copy the `.pak` file into this folder.

3. Launch the game. Daedalus will automatically discover and load the pak mod during world initialization. You'll see it listed in the **Logic Mods** section of the in-game menu.

### C++ Core Mods (DLL Files)

Core mods are compiled C++ DLLs that use the Daedalus SDK. To install:

1. Navigate to:
   ```
   Icarus\Binaries\Win64\mods\
   ```
   If the `mods` folder doesn't exist, create it.

2. Copy the `.dll` file into this folder.

3. Launch the game. Daedalus will load all DLLs in the `mods` folder at startup. You'll see each one logged:
   ```
   [Daedalus][INFO] Loaded mod: YourMod.dll
   ```

> **Note:** If a mod was compiled with a different version of the Daedalus SDK, you'll see a warning: `Mod: X was created with a different version of Daedalus Mod Loader. This mod may be unstable.` The mod will still load, but there may be compatibility issues.

### Mod Load Order

By default, mods are loaded in alphabetical order. You can control the exact load order via the `[Mods]` section:

```ini
[Mods]
loadorder = CoreLib, GameplayMod, UtilityMod
```

Mods listed in `loadorder` are loaded first, in exactly the order specified. Any mods in the `mods` folder that are not listed will be loaded after, sorted alphabetically. This is important when one mod's `DllMain` or initialization depends on another mod already being loaded.

The in-game log tags ordered mods with their position:
```
[Daedalus][INFO] Loaded mod: CoreLib.dll [order #0]
[Daedalus][INFO] Loaded mod: GameplayMod.dll [order #1]
[Daedalus][INFO] Loaded mod: SomeMod.dll
```

### Declaring Mod Dependencies

Dependencies ensure that a mod's required libraries are loaded before it. There are two ways to declare dependencies:

**Method 1: INI `[Dependencies]` section**

Add entries to `daedalus.ini` (mod names without `.dll`):
```ini
[Dependencies]
MyMod = CoreLib, UtilMod
AdvancedMod = MyMod
```

**Method 2: Sidecar `.deps` file**

Create a file with the same name as your mod DLL but with a `.deps` extension, in the `mods` folder. List one dependency per line:
```
mods/MyMod.deps:
CoreLib
UtilMod
```

Lines starting with `#` or `;` are treated as comments. Both methods can be combined — dependencies are merged with no duplicates.

**How resolution works:**

Daedalus performs a topological sort (Kahn's algorithm) on the dependency graph. This guarantees that if mod A depends on mod B, mod B will always be loaded first. If dependencies cannot be satisfied:

- **Missing dependency** — if a required mod is not present in the `mods` folder, the dependent mod (and anything that transitively depends on it) is skipped with an error:
  ```
  [Daedalus][ERROR] Mod MyMod skipped: missing dependencies: CoreLib
  ```

- **Circular dependency** — if mod A depends on mod B and mod B depends on mod A, both are skipped:
  ```
  [Daedalus][ERROR] Mod MyMod skipped: circular dependency detected
  ```

- **Transitive skipping** — if mod C depends on mod B, and mod B was skipped due to a missing dependency, mod C is also skipped:
  ```
  [Daedalus][ERROR] Mod ModC skipped: depends on unavailable mod ModB
  ```

All dependency matching is case-insensitive. The load summary reports both loaded and skipped counts:
```
[Daedalus][INFO] Mod loading complete: 5 loaded, 2 skipped
```

---

## Creating Your Own Mods

### Mod API Overview

To create a C++ mod, you subclass the `Mod` base class and override the hooks you need. Your mod DLL is loaded by Daedalus at startup and receives callbacks for game events.

**Mod Properties** — set these in your constructor:

| Property | Type | Description |
|----------|------|-------------|
| `ModName` | `std::string` | Your mod's name. If using a Blueprint ModActor, this should match your pak name. |
| `ModVersion` | `std::string` | Version string (e.g. `"1.0.0"`). |
| `ModDescription` | `std::string` | Short description shown in the menu. |
| `ModAuthors` | `std::string` | Author name(s). |
| `ModLoaderVersion` | `std::string` | The Daedalus version this mod was built against. |
| `UseMenuButton` | `bool` | Set to `true` to show a button in the menu that calls `OnModMenuButtonPressed()`. |

### Mod Lifecycle

```
Game Launches
  └─ DLL loaded from mods\ folder
       └─ Constructor runs → set mod info, call CompleteModCreation()
            └─ InitializeMod() → one-time setup, register hooks, register BP functions
                 └─ Game world loads
                      ├─ InitGameState() → called when game mode initializes
                      ├─ BeginPlay(Actor) → called for EVERY actor that begins play
                      └─ PostBeginPlay(ModActorName, Actor) → called for your Blueprint ModActor
  
  Every frame:
  ├─ DX11Present() / DX12Present() → raw rendering hook
  └─ DrawImGui() → draw custom ImGui widgets in the overlay
  
  On menu button click:
  └─ OnModMenuButtonPressed()
```

### Available Hooks

| Hook | Signature | When Called |
|------|-----------|------------|
| `InitializeMod()` | `void` | Once, after the mod DLL is loaded. Use for one-time setup. |
| `InitGameState()` | `void` | When `AGameModeBase::InitGameState` fires. The world is being set up. |
| `BeginPlay(AActor*)` | `void` | For **every** actor that calls `BeginPlay`. Filter by class name to find specific actors. |
| `PostBeginPlay(wstring, AActor*)` | `void` | For every Blueprint ModActor. The first parameter is the mod name — filter to find yours. |
| `DX11Present(device, context, rtv)` | `void` | Every frame (DX11 mode). For custom rendering. |
| `DX12Present(swapchain, cmdlist)` | `void` | Every frame (DX12 mode). For custom rendering. |
| `DrawImGui()` | `void` | Every frame while the overlay is open. Draw custom ImGui UI here. |
| `OnModMenuButtonPressed()` | `void` | When the user clicks your mod's button in the menu (requires `UseMenuButton = true`). |

### Example Mod Walkthrough

The `ExampleMod` project in this repository demonstrates the full mod structure.

**ExampleMod.h** — the mod class declaration:
```cpp
#pragma once
#include "Mod/Mod.h"

class ExampleMod : public Mod
{
public:
    ExampleMod()
    {
        ModName = "ExampleMod";
        ModVersion = "1.0.0";
        ModDescription = "An example mod for Daedalus";
        ModAuthors = "YourName";
        ModLoaderVersion = "2.3.0";
        
        ModRef = this;
        CompleteModCreation();
    }

    virtual void InitializeMod() override;
    virtual void InitGameState() override;
    virtual void BeginPlay(UE4::AActor* Actor) override;
    virtual void PostBeginPlay(std::wstring ModActorName, UE4::AActor* Actor) override;
    virtual void DX11Present(ID3D11Device* pDevice, ID3D11DeviceContext* pContext, 
                             ID3D11RenderTargetView* pRenderTargetView) override;
    virtual void DX12Present(IDXGISwapChain* pSwapChain, 
                             ID3D12GraphicsCommandList* pCommandList) override;
    virtual void OnModMenuButtonPressed() override;
    virtual void DrawImGui() override;

private:
    UE4::AActor* ModActor;
};
```

**ExampleMod.cpp** — key implementation patterns:
```cpp
void ExampleMod::InitializeMod()
{
    UE4::InitSDK();
    SetupHooks();

    UseMenuButton = true;

    REGISTER_FUNCTION(WriteToFile);
}

void ExampleMod::BeginPlay(UE4::AActor* Actor)
{
    if (Actor->GetClass()->GetFullName() == "BlueprintGeneratedClass BP_YourActor.BP_YourActor_C")
    {
        Log::Print("Found our actor!");
    }
}

void ExampleMod::DrawImGui()
{
    ImGui::Text("Hello from ExampleMod!");
    if (ImGui::Button("Do Something"))
    {
        // Your logic here
    }
}
```

**Building your mod:**
1. Open `Daedalus.sln` in Visual Studio.
2. The ExampleMod project is already configured to build against the Daedalus SDK.
3. Build in **Release x64**.
4. Copy the resulting `.dll` to `Icarus\Binaries\Win64\mods\`.

---

## Building from Source

### Prerequisites

- **Visual Studio 2022** (or newer) with the **C++ Desktop Development** workload.
- **Windows SDK 10.0+**.
- **Microsoft Detours** library (included via NuGet, restores automatically on build).

### Build Steps

1. Clone the repository:
   ```
   git clone https://github.com/YourRepo/Daedalus.git
   ```

2. Open `DaedalusLoader\Daedalus.sln` in Visual Studio.

3. Select **Release** configuration and **x64** platform.

4. Build the solution (Ctrl+Shift+B). This builds:
   - `DaedalusLoader.dll` — the mod loader
   - `ExampleMod.dll` — the example mod
   - `xinput1_3.dll` — the client proxy DLL (from LoaderAutoInjector)

5. Output files are in `DaedalusLoader\x64\Release\`.

### Building the Installer

The installer uses [NSIS](https://nsis.sourceforge.io/) (Nullsoft Scriptable Install System):

1. Install NSIS 3.x and ensure `makensis` is on your PATH.
2. Run `Installer\stage_build.bat` to copy build artifacts into the staging folder.
3. Run `makensis Installer\DaedalusInstaller.nsi` to compile the installer.
4. Output: `Installer\DaedalusInstaller.exe` (~674 KB).

The installer auto-detects the Steam install path for Icarus, validates the target folder, and includes a GUI folder picker for custom install locations.

---

## Troubleshooting

### The game launches but no console window appears
- Verify that `xinput1_3.dll` (client) or `version.dll` (server) is in the correct `Win64` folder alongside the game executable.
- Make sure you're using the 64-bit DLLs, not 32-bit.
- Check that your antivirus hasn't quarantined the DLLs. Proxy DLL injection is a common false positive.

### Console appears but shows CRITICAL errors
- The game may have updated and changed its executable. Check for an updated Daedalus release.
- Ensure `Icarus-Win64-Shipping.pdb` exists in the `Win64` folder. Daedalus relies on this PDB file for symbol resolution. If the PDB is missing, function lookups will fail.
- If the offset cache (`daedalus_cache.ini`) exists from a previous version, it will be automatically invalidated when the exe changes. You can also delete it manually to force a full re-resolve.

### Overlay doesn't appear when pressing F1
- Check the console log for `ImGui initialized successfully`. If you don't see this, there was a rendering hook issue.
- Try forcing a specific renderer in `daedalus.ini` (e.g. `renderer = dx11` or `renderer = dx12`).
- Verify your `menukey` is set correctly in the config. The default is `0x70` (F1).
- If using DX12, make sure the log shows `DX12: Captured game command queue`. If it doesn't, the command queue hook failed.

### Dump files are empty or missing
- Dump files are written to `Icarus\Binaries\Win64\` using absolute paths. Check that folder directly.
- If the dump buttons show errors in the console, check for file permission issues or locked files from a previous session.
- You can also press **F6** to trigger all three dumps at once without opening the overlay.

### Mods aren't loading
- Check that the mod DLL is in the `mods` folder (not a subfolder).
- Check the console log for skip messages — the mod may be in the `disabled` list, or it may have unsatisfied dependencies.
- Ensure the mod filename ends in `.dll` (case-insensitive).

### Mods load in wrong order
- Set the `loadorder` key in `[Mods]` to explicitly control load sequence.
- If using dependencies, check the log for `[deps resolved]` tags showing that topological sorting was applied.

### Hot-reload doesn't work
- Make sure your `reloadkey` is set correctly (default: `0x76` = F7).
- Check the console log for the `=== Hot-Reload ===` messages. If unload fails, a mod may be holding resources.
- Some mods may not support being unloaded and reloaded if they don't clean up in `DLL_PROCESS_DETACH`.

### Game crashes
- Check the `crashes` folder in the game directory (`Icarus\Binaries\Win64\crashes\`). Daedalus writes both a `.dmp` minidump and a `.txt` crash report for each crash.
- The `.txt` file shows the exception type, crash address (with module attribution like `ExampleMod.dll+0x1A3F`), register values, and stack trace. This is usually enough to identify which mod caused the crash.
- The `.dmp` file can be loaded in Visual Studio or WinDbg for deeper analysis.
- A MessageBox will also appear when a crash is caught, showing the crash address and exception type before the game exits.

### "Mod was created with a different version" warning
- This means the mod was compiled against a different Daedalus SDK version. It will still load, but stability is not guaranteed. Recompile the mod against the current SDK if you have the source.

### Game crashes on startup after installing Daedalus
- Remove `xinput1_3.dll` and `DaedalusLoader.dll` to restore vanilla behavior.
- Check if the crash happens before or after the console window appears. If before, the proxy DLL itself may be incompatible. If after, check the console log for which system failed.

### Overlay input is stuck or game freezes when opening menu
- This is rare but can happen if another overlay (Steam, Discord, NVIDIA) conflicts. Try disabling other overlays.
- The overlay only blocks input events it needs (mouse, keyboard) while open. Press F1 again to close and restore game input.

---

## How It Works (Technical)

### Injection and Symbol Resolution

Daedalus injects into the Icarus process via a proxy DLL (`xinput1_3.dll` for clients, `version.dll` for dedicated servers). When the game loads the proxy DLL, Daedalus:

1. **Installs the crash handler** as the very first step, so any subsequent failures during initialization are caught and reported.

2. **Resolves the game's PDB** for reliable symbol lookup via Microsoft Detours' `DetourFindFunction`. This is preferred over pattern scanning because it survives game updates as long as the PDB ships with the game.

3. **Locates critical engine globals** via pattern scanning:
   - `FNamePool` (GName) — the global name table for resolving UObject names.
   - `FUObjectArray` (GObjects) — the global object array containing every UObject in memory. Icarus uses the chunked `FChunkedFixedUObjectArray` variant.
   - `UWorld` (GWorld) — pointer to the active game world.

4. **Resolves engine functions** using a three-tier resolution strategy:
   - **Tier 1: Offset cache** — checks `daedalus_cache.ini` for previously resolved addresses. If the cache is valid (exe fingerprint matches), addresses are loaded instantly with no PDB parsing or scanning.
   - **Tier 2: PDB lookup** — uses `DetourFindFunction` to resolve symbols from the game's PDB file.
   - **Tier 3: Pattern scan** — falls back to byte pattern scanning in the game's executable memory.
   
   Resolved functions include: `AGameModeBase::InitGameState`, `AActor::BeginPlay`, `UObject::ProcessEvent`, `UWorld::SpawnActor`, `StaticLoadObject`, `StaticConstructObject_Internal`, and `CallFunctionByNameWithArguments`.

5. **Hooks the DXGI swap chain** (`IDXGISwapChain::Present` and `ResizeBuffers`) plus `ID3D12CommandQueue::ExecuteCommandLists` to auto-detect whether the game is using DX11 or DX12, then initializes the appropriate ImGui backend.

6. **Resolves mod dependencies** via topological sort, then **loads mods** from the `mods` folder (C++ DLLs) and `LogicMods` folder (Blueprint paks), dispatching engine events to all loaded mods through the event system.

### Offset Caching System

To avoid the overhead of PDB parsing and pattern scanning on every launch, Daedalus caches all resolved symbol addresses to `daedalus_cache.ini` in the game folder.

**Cache format:**
```ini
fingerprint=<file_size>_<timestamp_low>_<timestamp_high>
# Daedalus offset cache - auto-generated, do not edit
GName=a1b2c3d
GObject=e4f5678
UObject::ProcessEvent=9abcdef
```

Addresses are stored as RVAs (relative virtual addresses) so they are valid regardless of ASLR base address randomization. On load, RVAs are converted back to absolute addresses using the current module base.

**Fingerprinting:** The cache includes a fingerprint derived from the game executable's file size and last-write timestamp. If the game updates (new exe), the fingerprint won't match and the entire cache is discarded, forcing a full re-resolve. This happens automatically with no user intervention.

**Resolution sources:** Each cached entry is tagged with how it was resolved (`cache`, `pdb`, or `pattern`). This is logged at startup so you can verify which resolution path was used for each symbol.

### Crash Handler

The crash handler is installed at the very start of Daedalus initialization, before any other systems. It uses Windows Structured Exception Handling (`SetUnhandledExceptionFilter`) to catch any unhandled exceptions.

When a crash occurs, Daedalus:

1. **Writes a minidump** (`.dmp`) via `MiniDumpWriteDump` with data segments, handle data, and thread info included. This file can be loaded directly in Visual Studio or WinDbg for full debugging.

2. **Writes a human-readable crash report** (`.txt`) containing:
   - Exception code and human-readable name (ACCESS_VIOLATION, STACK_OVERFLOW, INT_DIVIDE_BY_ZERO, etc.)
   - Crash address with module attribution (e.g. `ExampleMod.dll+0x1A3F` or `Icarus-Win64-Shipping.exe+0x2B3C40`)
   - For access violations: whether it was a read, write, or execute violation and the target address
   - Full x64 register dump (RAX through R15, RIP, RSP, RBP)
   - 32-frame stack trace with module attribution for each frame
   - Complete list of all loaded modules with base and end addresses
   - Contents of the `mods` directory

3. **Shows a MessageBox** popup with the crash address and exception type, so you know a crash occurred even if the console window has closed.

All crash files are written to `Icarus\Binaries\Win64\crashes\` with timestamps in the filename (e.g. `crash_2025-01-15_14-30-22.dmp` and `.txt`).

### Dependency Resolution

When loading mods, Daedalus builds a dependency graph from both `.deps` sidecar files and the INI `[Dependencies]` section. It then performs a topological sort using Kahn's algorithm:

1. **Gather** all enabled mod DLLs and their declared dependencies.
2. **Validate** that all required dependencies are present. Mark mods with missing deps as unavailable.
3. **Propagate** unavailability transitively — if mod B depends on mod A, and mod A is unavailable, mod B is also marked unavailable.
4. **Build** an adjacency list and in-degree map for all available mods.
5. **Sort** using Kahn's algorithm — start with mods that have no dependencies (in-degree 0), load them, then decrement in-degrees of their dependents. Repeat until all mods are ordered.
6. **Detect cycles** — any mods remaining with in-degree > 0 after the sort are part of a cycle and are skipped.

All dependency matching is case-insensitive. The final load order ensures every mod's dependencies are loaded before it.

---

## Credits

This project was originally a fork of an old version of [SatisfactoryModLoader](https://github.com/satisfactorymodding/SatisfactoryModLoader) but has since transitioned to using a fork of [UnrealModLoader](https://github.com/RussellJerome/UnrealModLoader).

Most credit goes to [RussellJerome](https://github.com/RussellJerome) for his UnrealModLoader project, and the reverse engineers behind [UnrealDumper](https://github.com/guttir14/UnrealDumper-4.25) who created the UObject offset profiles.

Adapted by [edmiester777](https://github.com/edmiester777) for use with Icarus.

---

## 3rd Party Software

- [MinHook](https://github.com/TsudaKageyu/minhook) — x86/x64 hooking library
- [Dear ImGui](https://github.com/ocornut/imgui) — immediate-mode GUI for the overlay
- [feather-ini-parser](https://github.com/Turbine1991/cpp-feather-ini-parser) — lightweight INI parsing
- [Microsoft Detours](https://github.com/microsoft/Detours) — PDB symbol resolution and function interception

---

## License

This project is licensed under the **GNU General Public License v3.0** — see the [LICENSE](LICENSE) file for details.
