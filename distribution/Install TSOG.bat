@echo off
setlocal EnableExtensions
title The Siege of Grimgate - Installer
REM ============================================================
REM  The Siege of Grimgate - Installer (fichier publie sur itch.io)
REM  Telecharge la DERNIERE version du jeu depuis GitHub et
REM  l'installe dans le dossier choisi par le joueur (par defaut :
REM  "The Siege of Grimgate" cree a cote de ce fichier).
REM  Aucune dependance : PowerShell integre a Windows.
REM ============================================================

echo ===============================================
echo    The Siege of Grimgate - Installer
echo ===============================================
echo.
echo This will download the latest version of the game
echo from GitHub and install it on this computer.
echo.
echo Default install folder :
echo    %~dp0The Siege of Grimgate
echo.
echo Press ENTER to install there, or type C then ENTER
echo to choose another location.
set "REP="
set /p "REP=> "

set "INSTALL=%~dp0The Siege of Grimgate"
if /i not "%REP%"=="C" goto :installer

REM --- selecteur de dossier Windows (boite native, premier plan) ---
set "CHOIX="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; $f=New-Object System.Windows.Forms.Form; $f.TopMost=$true; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Choose WHERE to install The Siege of Grimgate (a game folder will be created inside).'; $d.ShowNewFolderButton=$true; if($d.ShowDialog($f) -eq 'OK'){ $d.SelectedPath }"`) do set "CHOIX=%%i"
if not defined CHOIX (
    echo.
    echo Installation cancelled.
    pause
    exit /b 1
)
set "INSTALL=%CHOIX%\The Siege of Grimgate"

:installer
echo.
echo Installing into :
echo    %INSTALL%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $rel=Invoke-RestMethod -Uri 'https://api.github.com/repos/ewangodet-oss/the-siege-of-grimgate-game/releases/latest' -Headers @{'User-Agent'='TSOG-installer'}; $asset=$rel.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1; if(-not $asset){ throw 'No zip found in the latest release.' }; Write-Host ('Downloading {0}  ({1:N0} MB)...' -f $rel.tag_name, ($asset.size/1MB)); $zip=Join-Path $env:TEMP 'tsog_install.zip'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UserAgent 'TSOG-installer'; Write-Host 'Download complete. Extracting...'; $tmp=Join-Path $env:TEMP 'tsog_install_files'; if(Test-Path $tmp){ Remove-Item -Recurse -Force $tmp }; Expand-Archive -Path $zip -DestinationPath $tmp -Force; $src=$tmp; $u=@(Get-ChildItem $tmp); if($u.Count -eq 1 -and $u[0].PSIsContainer){ $src=$u[0].FullName }; New-Item -ItemType Directory -Force $env:INSTALL | Out-Null; robocopy $src $env:INSTALL /E /NFL /NDL /NJH /NJS | Out-Null; if($LASTEXITCODE -ge 8){ throw ('File copy failed (robocopy code {0}).' -f $LASTEXITCODE) }; Remove-Item $zip -Force -ErrorAction SilentlyContinue; Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue; Write-Host ''; Write-Host ('Installed {0} successfully !' -f $rel.tag_name) -ForegroundColor Green; exit 0 } catch { Write-Host ''; Write-Host ('Install failed : {0}' -f $_) -ForegroundColor Red; exit 1 }"

if errorlevel 1 (
    echo.
    echo The installation failed. Check your internet connection
    echo and try again.
    pause
    exit /b 1
)

echo.
echo Launching the game... ^(Windows may show a SmartScreen or
echo firewall popup on first launch : this is expected, the game
echo needs network access only for the LAN multiplayer mode.
echo The game will also offer to create shortcuts.^)
start "" /d "%INSTALL%" "%INSTALL%\Jouer TSOG.bat"
exit /b 0
