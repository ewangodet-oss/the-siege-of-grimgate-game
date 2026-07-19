# The Siege of Grimgate - Updater
# Lance par le jeu (bouton "Update" de la popup du menu) : le jeu copie ce
# script dans TEMP puis quitte ; ici on telecharge la DERNIERE release GitHub,
# on ecrase les fichiers du jeu (settings.json et le raccourci .lnk ne sont pas
# dans le zip -> preserves), puis on relance le jeu.
param([string]$Install)

$ErrorActionPreference = 'Stop'
$repo = 'ewangodet-oss/the-siege-of-grimgate-game'
$Host.UI.RawUI.WindowTitle = 'The Siege of Grimgate - Updater'

try {
    if (-not $Install -or -not (Test-Path (Join-Path $Install 'Scripts'))) {
        throw "Install folder not found: $Install"
    }
    Write-Host '==============================================='
    Write-Host '   The Siege of Grimgate - Updater'
    Write-Host '==============================================='
    Write-Host ''

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'TSOG-updater' }
    $asset = $rel.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1
    if (-not $asset) { throw 'No zip file found in the latest release.' }

    Write-Host ("Downloading {0}  ({1:N0} MB)..." -f $rel.tag_name, ($asset.size / 1MB))
    $zip = Join-Path $env:TEMP 'tsog_update.zip'
    $bar = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'   # 10x plus rapide sans la barre
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UserAgent 'TSOG-updater'
    $ProgressPreference = $bar
    Write-Host 'Download complete.'

    Write-Host 'Waiting for the game to close...'
    $limite = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $limite) {
        $p = Get-Process pythonw, python -ErrorAction SilentlyContinue |
             Where-Object { $_.Path -like "$Install*" }
        if (-not $p) { break }
        Start-Sleep -Milliseconds 500
    }

    Write-Host 'Extracting...'
    $tmp = Join-Path $env:TEMP 'tsog_update_files'
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Unblock-File $zip -ErrorAction SilentlyContinue      # retire la marque "internet"
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    # ceinture+bretelles : aucun fichier extrait ne doit garder de marque internet
    Get-ChildItem $tmp -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
    # zip avec ou sans dossier racine unique : on vise le dossier qui contient Scripts/
    $src = $tmp
    $unique = @(Get-ChildItem $tmp)
    if ($unique.Count -eq 1 -and $unique[0].PSIsContainer) { $src = $unique[0].FullName }

    Write-Host 'Installing update...'
    robocopy $src $Install /E /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "File copy failed (robocopy code $LASTEXITCODE)." }

    # Purge des fichiers OBSOLETES (presents chez le joueur mais retires du jeu),
    # d'apres le manifest.json de la release. Perimetre STRICT : Scripts/ et
    # assets/ uniquement ; on ne touche jamais aux fichiers per-PC (settings,
    # marqueur pare-feu, raccourcis, __pycache__) ni a python_portable.
    $manif = Join-Path $src 'manifest.json'
    if (Test-Path $manif) {
        $garde = @{}
        foreach ($f in (Get-Content $manif -Raw | ConvertFrom-Json).files) {
            $garde[$f.ToLower()] = $true
        }
        $purges = 0
        foreach ($dossier in 'Scripts', 'assets') {
            $base = Join-Path $Install $dossier
            if (-not (Test-Path $base)) { continue }
            Get-ChildItem $base -Recurse -File | ForEach-Object {
                $rel = $_.FullName.Substring($Install.Length).TrimStart('\').Replace('\', '/')
                if ($garde.ContainsKey($rel.ToLower())) { return }
                if ($rel -match '(^|/)(settings\.json|\.mp_firewall_skip)$') { return }
                if ($rel -like '*.lnk' -or $rel -like '*__pycache__*') { return }
                Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
                $script:purges++
            }
        }
        if ($purges) { Write-Host ("Removed {0} obsolete file(s)." -f $purges) }
    }

    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

    Write-Host ''
    Write-Host ("Updated to {0} ! Relaunching the game..." -f $rel.tag_name) -ForegroundColor Green
    Start-Sleep -Seconds 2
    Start-Process -FilePath (Join-Path $Install 'Jouer TSOG.bat') -WorkingDirectory $Install
}
catch {
    Write-Host ''
    Write-Host "Update failed : $_" -ForegroundColor Red
    Write-Host 'Try again later. If the game no longer starts, re-download it'
    Write-Host 'from the itch.io page (your settings are kept).'
    Write-Host 'Press a key to close...'
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
}
