# Icarus Live Map — Memory Reader Entwicklungsstand

Ziel: Spielerpositionen direkt aus dem Prozessspeicher des Dedicated Servers lesen
(`ReadProcessMemory`) statt aus der periodisch geschriebenen `GD.json`.
Ausgabe: `live_players.json`, die `index.html` alle 5 s pollt.

---

## Prozess & Umgebung

| Eigenschaft | Wert |
|---|---|
| Spiel-Engine | UE4.27 |
| Ziel-Prozess | `IcarusServer-Win64-Shipping.exe` |
| Anti-Cheat | Nur auf `Icarus.exe` (Client), **nicht** auf Dedicated Server |
| Koordinaten-Einheit | Zentimeter (cm); ÷100 = Meter |
| Kartengrenze | ±4096 m (Standard-Prospect) |

### Warum nicht `Icarus.exe`?
Easy Anti-Cheat blockt `ReadProcessMemory` auf dem Game-Client.
Alle Heap-Reads liefern dort nur Nullbytes.
Dedicated Server (`IcarusServer-Win64-Shipping.exe`) hat kein EAC → funktioniert.

---

## Verifizierte Offsets

```python
OFF_GSTATE      = 0x038   # UWorld       → AGameStateBase*        ✓ verifiziert
OFF_PLAYERARRAY = 0x090   # GameState    → TArray<APlayerState*>   ✓ verifiziert
```

### Noch nicht verifiziert (Schätzwerte aus UE4-Standard)
```python
OFF_PAWN_PRIVATE   = 0x3A0   # APlayerState → APawn*           ✗ unbekannt
OFF_ROOT_COMPONENT = 0x198   # AActor       → USceneComponent* ✗ unbekannt
OFF_REL_LOCATION   = 0x11C   # USceneComponent → FVector (XYZ) ✗ unbekannt
```

**→ `--trace` ermittelt diese drei Offsets automatisch und schreibt sie in `offsets.json`.**

---

## Pointer-Kette (UE4-Theorie)

```
GWorld (static ptr in .exe)
  └─ UWorld*
       └─ +0x038 → AGameStateBase*
                    └─ +0x090 → TArray<APlayerState*>  { Data*, Count, Max }
                                  └─ [i*8] → APlayerState*
                                               └─ +OFF_PAWN_PRIVATE → APawn*
                                                                        └─ +OFF_ROOT_COMPONENT → USceneComponent*
                                                                                                   └─ +OFF_REL_LOCATION → FVector { f32 X, Y, Z }
```

### GWorld-Erkennung
Pattern-Scan im Modul nach `MOV reg, [RIP+rel32]` (5 Register-Varianten).
Validierung: Zieladresse ist Heap-Objekt mit vtable → Modul.

---

## Aktueller Laufzeitstatus (2026-04-11)

### GWorld — verifiziert ✓

```
GWorld @ 0x7FF664F7116D  →  ptr 0x7FF66A98DC90  →  UWorld* 0x1C419D86930  (vtable 0x7FF669A66CC8)
```

| Eigenschaft | Wert |
|---|---|
| Modul-Basis | `0x7FF6646E0000` |
| Modulgröße | 105 MB |
| Lesbare Regions | 81 (105 MB) |
| GWorld-Pointer (static) | `0x7FF66A98DC90` |
| UWorld* | `0x1C419D86930` |

### `--trace` — ausstehend

Referenzposition aus `players.json`:

| Spieler | X | Y | Z |
|---|---|---|---|
| PETER | 161.15 m | −230.83 m | −104.13 m |

**Letzter Lauf:** FVector nicht gefunden — `--trace` scannte nur die 105 MB des `.exe`-Images
statt des Heap-Speichers. **Bug behoben** (siehe unten). Nächster Lauf sollte funktionieren.

---

## Scan-Ergebnisse (manueller `--scan`, veraltet)

Bester Treffer aus früherem `--scan`-Lauf (Spieler hat sich seitdem bewegt):

| Adresse | X | Y | Z | Abweichung |
|---|---|---|---|---|
| `0x5DC5570BA0` | 161.15 m | −230.83 m | −104.07 m | ΔZ ≈ 0.06 m |

**Container-Objekt (USceneComponent):** `0x5DC5570B20` — `FVector`-Offset: `+0x80`

```
Pointer bei 0x5DC55706A0  →  0x5DC5570B20  (FVector @ struct+0x80)
Pointer bei 0x5DC5570720  →  0x5DC5570B20  (FVector @ struct+0x80)
```

---

## Nächste Schritte

1. **`--trace` erneut ausführen** (Spieler muss online sein, Bug ist jetzt behoben):
   ```
   python memory_reader.py --trace
   ```
   Liefert `offsets.json` mit `OFF_PAWN_PRIVATE`, `OFF_ROOT_COMPONENT`, `OFF_REL_LOCATION`.

2. **`--loop` Mode testen**:
   ```
   python memory_reader.py --loop 2
   ```
   Schreibt alle 2 s nach `live_players.json`.

3. **`index.html` anpassen**: `--output players.json` nutzen oder direkt auf `live_players.json` umstellen.

---

## Datei-Übersicht

| Datei | Zweck |
|---|---|
| `memory_reader.py` | Live-Reader via ReadProcessMemory |
| `offsets.json` | Automatisch von `--trace` generiert; `OFF_PAWN_PRIVATE`, `OFF_ROOT_COMPONENT`, `OFF_REL_LOCATION` |
| `parse_players.py` | Einmalig aus GD.json parsen → `players.json` (Referenz für `--trace`) |
| `read_save.py` | Vollständiger Save-Parser → `savegame.json` + `players.json` |
| `live_players.json` | Ausgabe von `memory_reader.py` (Live-Daten) |
| `index.html` | Leaflet-Karte, pollt alle 5 s |

---

## Bekannte Probleme / Notizen

- **`--trace` scannte nur Modul-Image (behoben):** `trace_pointer_chain` bekam bisher
  `readable_regions_in_module()` übergeben — das sind nur die 105 MB des `.exe`-Images.
  `UObject`-Heap-Daten liegen bei Adressen wie `0x1C4...` weit außerhalb.
  Fix: `main()` übergibt nun `_all_readable_regions()` (kompletter Prozess-Adressraum).

- **Exakter float32-Scan schlägt fehl**: Die Bytes im Speicher stimmen nicht bit-exakt mit
  den aus dem Save geparsten Werten überein (geringe Positions-Drift oder Float-Rundung).
  Lösung: Y-Anker-Scan mit ±1000 cm Toleranz, dann XYZ-Validierung mit ±50 cm.

- **`modBaseSize = 0`** in MODULEENTRY32: Bei manchen Prozessen liefert die Windows-API
  die Modulgröße als 0. Workaround: VirtualQueryEx-basierte Region-Enumeration
  (`readable_regions_in_module`).

- **False-Positives bei GWorld**: Niagara-CVar-Text liegt im `.rdata`-Bereich und sieht
  kurz wie ein GWorld-Pointer aus. Fix: Zieladresse darf nicht im Modul-Image liegen +
  vtable-Validierung.

- **Spieler bei Spawn**: Position (0, 0, 0) ist valide — der Spieler stand am Spawn-Punkt.
  Kein Bug.

- **`IcarusServer.exe` vs. `IcarusServer-Win64-Shipping.exe`**: Mehrere Prozesse laufen
  gleichzeitig. Nur der `-Win64-Shipping`-Prozess enthält die echten Spieldaten.
  `PROCESS_NAMES` hat diesen an erster Stelle.
