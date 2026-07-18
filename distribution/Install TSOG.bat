@echo off
setlocal EnableExtensions
title The Siege of Grimgate - Installer
REM ============================================================
REM  The Siege of Grimgate - Installer (fichier publie sur itch.io)
REM  Telecharge la DERNIERE version du jeu depuis GitHub et
REM  l'installe dans un dossier "The Siege of Grimgate" cree a
REM  cote de ce fichier. Aucune dependance : PowerShell integre.
REM ============================================================

echo ===============================================
echo    The Siege of Grimgate - Installer
echo ===============================================
echo.
echo This will download the latest version of the game
echo (from GitHub) and install it into the folder :
echo.
echo    %~dp0The Siege of Grimgate
echo.
echo Press any key to start the download...
pause >nul

set "INSTALL=%~dp0The Siege of Grimgate"

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
echo needs network access only for the LAN multiplayer mode.^)
start "" /d "%INSTALL%" "%INSTALL%\Jouer TSOG.bat"
exit /b 0
