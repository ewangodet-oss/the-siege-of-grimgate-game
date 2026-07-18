@echo off
title The Siege of Grimgate
setlocal
cd /d "%~dp0"
set "PY=%~dp0python_portable"
set "GAME=%~dp0Scripts"

REM ============================================================
REM  Lanceur CLICK AND PLAY de The Siege of Grimgate.
REM  Python est embarque (dossier python_portable) : aucune
REM  installation requise. Au tout premier lancement :
REM    - les bibliotheques manquantes sont installees (internet 1x),
REM    - les ports du multijoueur sont ouverts dans le pare-feu.
REM ============================================================

REM --- Garde-fou : le .bat doit etre A COTE du jeu ET reellement extrait ---
REM     (double-cliquer le .bat DANS un .zip le lance depuis un dossier temporaire
REM      Windows -> les fichiers voisins n'existent pas -> plantage. On le detecte.)
if not exist "%GAME%\TSOG Game.py" (
    echo.
    echo   ERREUR : fichiers du jeu introuvables ^(dossier "Scripts" a cote du lanceur^).
    echo   As-tu bien EXTRAIT le dossier du .zip avant de lancer ?
    echo   Extrais tout le dossier quelque part ^(Bureau, Documents...^) puis
    echo   double-clique "Jouer TSOG.bat" depuis le dossier extrait.
    echo.
    pause
    exit /b 1
)

if not exist "%PY%\python.exe" (
    echo.
    echo   ERREUR : le dossier "python_portable" est introuvable.
    echo   Garde le .bat dans le meme dossier que le jeu ^(dossier extrait entier^).
    echo.
    pause
    exit /b 1
)

REM --- Verification des bibliotheques (1er lancement / fichiers manquants) ---
"%PY%\python.exe" -c "import pygame, numpy, pycaw, comtypes" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Premiere utilisation : installation des composants du jeu...
    echo   ^(connexion internet requise une seule fois, patiente un peu^)
    echo.
    "%PY%\python.exe" -m pip install --no-warn-script-location pygame==2.6.1 numpy pycaw comtypes
    if errorlevel 1 (
        echo.
        echo   Echec de l'installation. Verifie ta connexion internet puis relance.
        echo.
        pause
        exit /b 1
    )
)

REM --- Multijoueur : ouvre les ports dans le pare-feu Windows (1re fois seulement) ---
REM     TCP 50321 = connexion, UDP 50322 = decouverte. Regles nommees SANS espace.
REM     Si l'ouverture ECHOUE (service pare-feu arrete...), on PREVIENT au lieu d'ignorer.
if exist "%GAME%\.mp_firewall_skip" goto :apres_parefeu
netsh advfirewall firewall show rule name=TSOG_MP_TCP >nul 2>&1
if not errorlevel 1 goto :apres_parefeu

echo.
echo   Premiere configuration du multijoueur ^(pare-feu^)...
echo   Windows peut demander une confirmation : reponds "Oui".
echo.
powershell -NoProfile -Command "Start-Process -Verb RunAs -Wait -WindowStyle Hidden -FilePath cmd.exe -ArgumentList '/c netsh advfirewall firewall add rule name=TSOG_MP_TCP dir=in action=allow protocol=TCP localport=50321 profile=any & netsh advfirewall firewall add rule name=TSOG_MP_UDP dir=in action=allow protocol=UDP localport=50322 profile=any'" >nul 2>&1

REM --- Verifie que la regle est bien la ; sinon on diagnostique et on avertit ---
netsh advfirewall firewall show rule name=TSOG_MP_TCP >nul 2>&1
if not errorlevel 1 goto :apres_parefeu

echo.
echo   ============================================================
echo    [!] IMPOSSIBLE d'ouvrir les ports du multijoueur.
echo.
sc query mpssvc | find /i "RUNNING" >nul 2>&1
if errorlevel 1 goto :pf_service_hs
echo    Le service pare-feu tourne mais l'ajout de la regle a echoue.
goto :pf_fin
:pf_service_hs
echo    CAUSE : le service "Pare-feu Windows Defender" est ARRETE sur
echo    ce PC (le pare-feu ne repond pas / ne s'active plus).
echo    Reparer : ouvre "services.msc", demarre d'abord le service
echo    "Moteur de filtrage de base" (BFE) PUIS "Pare-feu Windows
echo    Defender", puis relance le jeu. (Details dans LISEZ-MOI.txt.)
echo    NB : si ton pare-feu est completement DESACTIVE, l'hebergement
echo    multi peut fonctionner QUAND MEME (rien ne bloque les connexions).
:pf_fin
echo.
echo    Le jeu SOLO fonctionne normalement.
echo    (Ce message ne reapparaitra plus. Pour reessayer apres reparation,
echo     lance "Autoriser le multijoueur.bat" dans le dossier Scripts.)
echo   ============================================================
echo.
echo skip> "%GAME%\.mp_firewall_skip"
pause

:apres_parefeu

REM --- Lancement du jeu (sans fenetre de console) ---
REM     CWD deja = %~dp0 (cd plus haut) et le jeu se recale seul -> pas besoin de /d.
start "" "%PY%\pythonw.exe" "%GAME%\TSOG Game.py"

REM --- (Re)cree le raccourci avec l'icone du jeu, avec les chemins LOCAUX de cette
REM     machine (apres le lancement -> invisible et sans delai pour le joueur).
REM     L'icone est desormais dans Scripts\ ; le raccourci lance toujours ce .bat. ---
powershell -NoProfile -WindowStyle Hidden -Command "$d=(Get-Location).Path; $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut((Join-Path $d 'The Siege of Grimgate.lnk')); $s.TargetPath=(Join-Path $d 'Jouer TSOG.bat'); $s.WorkingDirectory=$d; $s.IconLocation=((Join-Path $d 'Scripts\Logo_build.ico')+',0'); $s.WindowStyle=7; $s.Description='The Siege of Grimgate'; $s.Save()" >nul 2>&1

endlocal
