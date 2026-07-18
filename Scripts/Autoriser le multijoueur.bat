@echo off
title Autoriser le multijoueur - The Siege of Grimgate
setlocal

REM ============================================================
REM  Ouvre les ports du jeu dans le pare-feu Windows pour que
REM  le multijoueur LAN fonctionne (a lancer UNE fois par PC).
REM  Tente aussi de REPARER le service pare-feu s'il est arrete.
REM  Necessite les droits admin -> Windows demande "Oui".
REM
REM    TCP 50321 = connexion de jeu (rejoindre l'hote)
REM    UDP 50322 = decouverte des sessions sur le reseau
REM ============================================================

REM --- Auto-elevation en administrateur si necessaire ---
net session >nul 2>&1
if errorlevel 1 (
    echo   Demande des droits administrateur...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

echo.
echo   1) Verification / demarrage du service pare-feu Windows...
REM Le pare-feu (MpsSvc) depend du "Moteur de filtrage de base" (BFE) : on demarre BFE d'abord.
sc config bfe start= auto >nul 2>&1
net start bfe >nul 2>&1
sc config mpssvc start= auto >nul 2>&1
net start mpssvc >nul 2>&1

echo   2) Ouverture des ports du jeu...
netsh advfirewall firewall delete rule name=TSOG_MP_TCP >nul 2>&1
netsh advfirewall firewall delete rule name=TSOG_MP_UDP >nul 2>&1
netsh advfirewall firewall add rule name=TSOG_MP_TCP dir=in action=allow protocol=TCP localport=50321 profile=any
netsh advfirewall firewall add rule name=TSOG_MP_UDP dir=in action=allow protocol=UDP localport=50322 profile=any

REM --- Verifie le resultat ---
netsh advfirewall firewall show rule name=TSOG_MP_TCP >nul 2>&1
if errorlevel 1 goto :echec

del "%~dp0.mp_firewall_skip" >nul 2>&1
echo.
echo   ============================================
echo     C'est bon ! Le multijoueur est autorise.
echo     Tu peux fermer cette fenetre et jouer.
echo   ============================================
echo.
pause
exit /b 0

:echec
echo.
echo   ============================================================
echo    [!] ECHEC : impossible d'ouvrir les ports du pare-feu.
echo.
sc query mpssvc | find /i "RUNNING" >nul 2>&1
if errorlevel 1 goto :service_hs
echo    Le service tourne mais netsh a echoue (regle non creee).
goto :fin
:service_hs
echo    Le service "Pare-feu Windows Defender" (MpsSvc) ne demarre pas.
echo    C'est un probleme de Windows sur ce PC (souvent : le service
echo    "Moteur de filtrage de base" / BFE est desactive, ou des droits
echo    registre casses par un outil de "nettoyage"/antivirus tiers).
echo.
echo    A essayer, dans une invite de commandes ADMIN :
echo        sc config bfe start= auto
echo        net start bfe
echo        net start mpssvc
echo    Si BFE refuse de demarrer : verifier avec un antivirus / SFC
echo        sfc /scannow
echo.
echo    NB : si ton pare-feu est de toute facon DESACTIVE, l'hebergement
echo    multi peut fonctionner sans cette regle (rien ne bloque).
:fin
echo   ============================================================
echo.
pause
exit /b 1
