# compile.ps1  -  Baut LiveMapMod.dll als DaedalusLoader CoreMod

$vsdir  = 'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64'
$clexe  = Join-Path $vsdir 'cl.exe'
$libexe = Join-Path $vsdir 'lib.exe'
$srcdir = 'H:\Projects\Icarus-Live-Map\LiveMapMod'
$ini    = Join-Path $srcdir 'LiveMapMod.ini'

$env:INCLUDE = @(
    'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207\include'
    'C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\ucrt'
    'C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\um'
    'C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\shared'
) -join ';'

$env:LIB = @(
    'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207\lib\x64'
    'C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\um\x64'
    'C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\ucrt\x64'
    $srcdir   # DaedalusLoader.lib liegt hier
) -join ';'

$env:PATH = "$vsdir;$env:PATH"

Set-Location $srcdir

# Schritt 1: Import-Library aus DaedalusLoader.dll erzeugen
Write-Host "[~] Erstelle DaedalusLoader.lib aus DaedalusLoader.def..."
& $libexe /DEF:DaedalusLoader.def /MACHINE:X64 /OUT:DaedalusLoader.lib 2>&1 |
    ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { Write-Host "[!] lib.exe fehlgeschlagen"; exit 1 }

# Schritt 2: LiveMapMod.dll kompilieren
Write-Host "[~] Kompiliere LiveMapMod.cpp..."
& $clexe /nologo /W3 /O2 /EHa /LD /FeLiveMapMod.dll `
    LiveMapMod.cpp `
    Psapi.lib kernel32.lib winhttp.lib DaedalusLoader.lib `
    /link /DLL /MACHINE:X64 `
           /LIBPATH:$srcdir 2>&1 |
    ForEach-Object { Write-Host $_ }

if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Kompilierung fehlgeschlagen (Exit $LASTEXITCODE)"
    exit 1
}

Write-Host "[+] LiveMapMod.dll gebaut: $srcdir\LiveMapMod.dll"

# Schritt 3: INI anlegen falls nicht vorhanden
# OutputPath weggelassen — DLL leitet es automatisch aus ihrem Verzeichnis ab.
if (-not (Test-Path $ini)) {
    @"
[Config]
IntervalMs=2000
; ServerUrl=http://192.168.1.x:9090
"@ | Set-Content $ini -Encoding ASCII
    Write-Host "[+] INI erstellt: $ini"
} else {
    Write-Host "[~] INI existiert: $ini"
}

Write-Host ""
Write-Host "Fertig. DLL + INI liegen in: $srcdir"
Write-Host "Zum Deployen manuell nach mods\ kopieren."
