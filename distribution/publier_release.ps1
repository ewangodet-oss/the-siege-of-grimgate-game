# The Siege of Grimgate - Publication d'une release
# 1) lit VERSION dans Scripts/TSOG Game.py
# 2) assemble le dossier portable (jeu du repo + python_portable externe)
# 3) zippe -> distribution/out/TSOG-<version>-win.zip
# 4) cree la release GitHub avec le zip attache (via gh CLI)
# Prerequis (1 seule fois) : winget install GitHub.cli   puis   gh auth login
param([switch]$SansRelease)   # -SansRelease : construit juste le zip, sans publier

$ErrorActionPreference = 'Stop'
$repoDir  = Split-Path -Parent $PSScriptRoot                      # racine du repo
$pyPortable = 'C:\Perso\Projet Algosss\The Siege Of Grimgate_beta-1.0\python_portable'
$githubRepo = 'ewangodet-oss/the-siege-of-grimgate-game'

# --- version depuis le code (source unique) ---
$code = Get-Content (Join-Path $repoDir 'Scripts\TSOG Game.py') -Raw -Encoding UTF8
if ($code -notmatch 'VERSION\s*=\s*"([^"]+)"') { throw 'VERSION introuvable dans TSOG Game.py' }
$version = $Matches[1]
Write-Host "=== Publication de la version : $version ===" -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $pyPortable 'pythonw.exe'))) {
    throw "python_portable introuvable : $pyPortable (edite `$pyPortable en haut du script)"
}

# --- garde-fous git : tout committe et pousse ? ---
$sale = git -C $repoDir status --porcelain
if ($sale) { Write-Host 'ATTENTION : changements non commites dans le repo :' -ForegroundColor Yellow; $sale | Select-Object -First 5 | Write-Host }
$local  = git -C $repoDir rev-parse main 2>$null
$distant = git -C $repoDir rev-parse origin/main 2>$null
if ($local -ne $distant) { Write-Host 'ATTENTION : main local et origin/main different (pense a push !)' -ForegroundColor Yellow }

# --- assemblage du dossier portable ---
$staging = Join-Path $env:TEMP 'tsog_build\The Siege of Grimgate'
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force $staging | Out-Null

Write-Host 'Copie du jeu (Scripts + assets + lanceur)...'
robocopy (Join-Path $repoDir 'Scripts') (Join-Path $staging 'Scripts') /E /NFL /NDL /NJH /NJS `
    /XF settings.json .mp_firewall_skip /XD __pycache__ | Out-Null
robocopy (Join-Path $repoDir 'assets') (Join-Path $staging 'assets') /E /NFL /NDL /NJH /NJS | Out-Null
foreach ($f in 'Jouer TSOG.bat', 'README.md', 'LICENSE') {
    $p = Join-Path $repoDir $f
    if (Test-Path $p) { Copy-Item $p $staging }
}
Write-Host 'Copie de python_portable...'
robocopy $pyPortable (Join-Path $staging 'python_portable') /E /NFL /NDL /NJH /NJS | Out-Null

# --- manifeste de version : liste exacte des fichiers du build. L'updater s'en
# --- sert pour SUPPRIMER les fichiers obsoletes (renommes/retires) chez le joueur.
Write-Host 'Generation du manifest.json...'
$fichiers = Get-ChildItem $staging -Recurse -File | ForEach-Object {
    $_.FullName.Substring($staging.Length + 1).Replace('\', '/')
}
@{ version = $version; files = @($fichiers) } | ConvertTo-Json |
    Set-Content (Join-Path $staging 'manifest.json') -Encoding UTF8
Write-Host ("  {0} fichiers listes." -f @($fichiers).Count)

# --- zip ---
$out = Join-Path $PSScriptRoot 'out'
New-Item -ItemType Directory -Force $out | Out-Null
$zip = Join-Path $out "TSOG-$version-win.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Write-Host "Compression -> $zip ..."
Compress-Archive -Path (Join-Path (Split-Path $staging) '*') -DestinationPath $zip -CompressionLevel Optimal
"{0:N0} Mo" -f ((Get-Item $zip).Length / 1MB) | ForEach-Object { Write-Host "Zip pret : $_" -ForegroundColor Green }

if ($SansRelease) { Write-Host 'Mode -SansRelease : pas de publication GitHub.'; exit 0 }

# --- release GitHub ---
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host 'gh CLI absent. Installe-le (1 seule fois) :' -ForegroundColor Yellow
    Write-Host '   winget install GitHub.cli'
    Write-Host '   gh auth login'
    Write-Host "Puis relance ce script, ou cree la release a la main sur GitHub et attache $zip"
    exit 1
}
Write-Host "Creation de la release '$version' sur $githubRepo ..."
gh release create $version $zip --repo $githubRepo --title "The Siege of Grimgate $version" `
    --notes "Portable Windows build. Download the zip, extract it and run 'Jouer TSOG.bat'."
if ($LASTEXITCODE -ne 0) { throw 'gh release create a echoue (deja publiee ? pas authentifie ?)' }
Write-Host "=== Release $version publiee ! Les installers/updaters la verront immediatement. ===" -ForegroundColor Green
