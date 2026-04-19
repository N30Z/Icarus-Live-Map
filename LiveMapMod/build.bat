@echo off
setlocal

:: Visual Studio 2022 Developer-Umgebung
call "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat" -arch=amd64 -no_logo
if errorlevel 1 (
    echo [!] VsDevCmd.bat nicht gefunden
    pause & exit /b 1
)

set SRC=%~dp0LiveMapMod.cpp
set OUT=%~dp0LiveMapMod.dll
set DEPLOY=C:\IcarusServer\Icarus\Binaries\Win64\CoreMods\LiveMapMod.dll

echo [~] Kompiliere %SRC%
cl.exe /nologo /W3 /O2 /EHa ^
       /LD /Fe"%OUT%" ^
       "%SRC%" ^
       Psapi.lib kernel32.lib ^
       /link /DLL /MACHINE:X64

if errorlevel 1 (
    echo [!] Kompilierung fehlgeschlagen
    pause & exit /b 1
)

echo [+] Gebaut: %OUT%

:: Deploy
copy /Y "%OUT%" "%DEPLOY%"
echo [+] Deployed: %DEPLOY%

:: INI erzeugen falls nicht vorhanden
set INI=C:\IcarusServer\Icarus\Binaries\Win64\CoreMods\LiveMapMod.ini
if not exist "%INI%" (
    echo [Config]                                          > "%INI%"
    echo OutputPath=H:\Projects\Icarus-Live-Map\live_players.json >> "%INI%"
    echo IntervalMs=2000                                   >> "%INI%"
    echo [+] INI erstellt: %INI%
)

echo.
echo Fertig. Server neu starten um LiveMapMod zu laden.
pause
