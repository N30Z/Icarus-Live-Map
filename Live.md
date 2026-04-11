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

## Verifiziertе Offsets

```python
OFF_GSTATE      = 0x038   # UWorld       → AGameStateBase*   ✓ verifiziert
OFF_PLAYERARRAY = 0x090   # GameState    → TArray<APlayerState*>  ✓ verifiziert
```

### Noch nicht verifiziert (Schätzwerte aus UE4-Standard)
```python
OFF_PAWN_PRIVATE   = 0x3A0   # APlayerState → APawn*           ✗ falsch/unbekannt
OFF_ROOT_COMPONENT = 0x198   # AActor       → USceneComponent* ✗ falsch/unbekannt
OFF_REL_LOCATION   = 0x11C   # USceneComponent → FVector (XYZ) ✗ falsch/unbekannt
```

**→ `--trace` Mode ermittelt diese drei Offsets automatisch.**

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

## Scan-Ergebnisse (Stand: letzter bekannter Serverstand)

Referenzposition aus `players.json` (parse_players.py, Lokal.json):

| Spieler | X | Y | Z |
|---|---|---|---|
| PETER | 161.15 m | -230.83 m | -104.13 m |

### Gefundene FVector-Adressen im Prozessspeicher

Bester Treffer (genaueste Übereinstimmung):

| Adresse | X | Y | Z | Abweichung |
|---|---|---|---|---|
| `0x5DC5570BA0` | 161.15 m | -230.83 m | -104.07 m | ΔZ ≈ 0.06 m |

**Container-Objekt (USceneComponent):** `0x5DC5570B20`
**FVector-Offset innerhalb des Objekts:** `+0x80`

### Back-Pointer-Analyse

```
Pointer bei 0x5DC55706A0  →  0x5DC5570B20  (FVector @ struct+0x80)
Pointer bei 0x5DC5570720  →  0x5DC5570B20  (FVector @ struct+0x80)
```

Das bedeutet: Irgendetwas bei `0x5DC55706A0` (und `0x5DC5570720`) enthält
einen Zeiger auf die Component. Das Objekt, das `0x5DC55706A0` enthält,
ist vermutlich der Pawn (oder ein AActor).

**Offene Frage:** Was ist das Objekt, das `0x5DC55706A0` als Feld besitzt?
→ Dessen Basis-Adresse ergibt `OFF_ROOT_COMPONENT = 0x5DC55706A0 - obj_base`.

---

## Nächste Schritte

1. **`--trace` ausführen** (Spieler muss online sein):
   ```
   python memory_reader.py --trace
   ```
   Liefert `OFF_PAWN_PRIVATE`, `OFF_ROOT_COMPONENT`, `OFF_REL_LOCATION`.

2. **Offsets in `memory_reader.py` eintragen** (Zeilen 65–73).

3. **`--loop` Mode testen**:
   ```
   python memory_reader.py --loop 2
   ```
   Schreibt alle 2 s nach `live_players.json`.

4. **`index.html` anpassen**: `players.json` → `live_players.json` (oder `--output players.json` nutzen).

---

## Datei-Übersicht

| Datei | Zweck |
|---|---|
| `memory_reader.py` | Live-Reader via ReadProcessMemory |
| `parse_players.py` | Einmalig aus GD.json / Lokal.json parsen → `players.json` |
| `read_save.py` | Vollständiger Save-Parser → `savegame.json` + `players.json` |
| `players.json` | Ausgabe von parse_players.py — **nicht überschreiben** |
| `live_players.json` | Ausgabe von memory_reader.py (Live-Daten) |
| `index.html` | Leaflet-Karte, pollt alle 5 s |
| `Lokal.json` | Aktueller Dedicated-Server-Speicherstand |

---

## Bekannte Probleme / Notizen

- **Exakter float32-Scan schlägt fehl**: Die Bytes im Speicher stimmen nicht bit-exakt mit
  den aus dem Save geparsten Werten überein (geringe Positions-Drift oder unterschiedliche
  Float-Darstellung). Lösung: Y-Anker-Scan mit ±1000 cm Toleranz, dann XYZ-Validierung.

- **`modBaseSize = 0`** in MODULEENTRY32: Bei manchen Prozessen liefert die Windows-API
  die Modulgröße als 0. Workaround: Hardcoded `0x10_000_000` (105 MB) oder
  VirtualQueryEx-basierte Region-Enumeration.

- **False-Positives bei GWorld**: Niagara-CVar-Text liegt im `.rdata`-Bereich und sieht
  kurz wie ein GWorld-Pointer aus. Fix: Zieladresse darf nicht im Modul-Image liegen +
  vtable-Validierung.

- **Spieler bei Spawn**: Position (0, 0, 0) ist valide — der Spieler stand am Spawn-Punkt.
  Kein Bug.

- **`IcarusServer.exe` vs. `IcarusServer-Win64-Shipping.exe`**: Mehrere Prozesse laufen
  gleichzeitig. Nur der `-Win64-Shipping`-Prozess enthält die echten Spieldaten.
  PROCESS_NAMES hat diesen jetzt an erster Stelle.
