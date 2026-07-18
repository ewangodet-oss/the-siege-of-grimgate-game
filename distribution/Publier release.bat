@echo off
REM Double-clic = construit le zip portable de la version courante ET publie
REM la release GitHub (les joueurs recevront la popup de maj au lancement).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publier_release.ps1"
pause
