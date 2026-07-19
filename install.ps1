# Hachi one-liner installer (Windows / PowerShell)
#
#   irm https://raw.githubusercontent.com/OpenGigantic/hachi/main/install.ps1 | iex
#
# Bootstraps uv (which manages Python), fetches Hachi, installs deps, and
# launches the guided setup. Override the source repo or install location with
# the HACHI_REPO / HACHI_HOME environment variables.
#Requires -Version 5
$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:HACHI_REPO) { $env:HACHI_REPO } else { "https://github.com/OpenGigantic/hachi" }
$InstallDir = if ($env:HACHI_HOME) { $env:HACHI_HOME } else { "$env:USERPROFILE\.hachi-src" }

function Info($message) { Write-Host "==> $message" -ForegroundColor Cyan }

# 1. Ensure uv (it provisions a suitable Python automatically).
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Info "Installing uv (Python toolchain manager)..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Error: git is required but not installed. Please install git and re-run." -ForegroundColor Red
    exit 1
}

# 2. Fetch (or update) the source.
if (Test-Path "$InstallDir\.git") {
    Info "Updating Hachi in $InstallDir..."
    git -C $InstallDir pull --ff-only
}
else {
    Info "Cloning Hachi into $InstallDir..."
    git clone --depth 1 $RepoUrl $InstallDir
}

# 3. Install dependencies.
Set-Location $InstallDir
Info "Installing dependencies (this can take a minute)..."
uv sync

# 4. Launch the guided setup.
Info "Installed! Launching setup..."
uv run --project $InstallDir hachi quickstart

Write-Host ""
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "Hachi is installed. Start chatting with:" -ForegroundColor Green
Write-Host "  uv run --project `"$InstallDir`" hachi run"
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor DarkGray
