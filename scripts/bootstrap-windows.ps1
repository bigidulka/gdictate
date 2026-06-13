param(
  [switch]$InstallSystem,
  [switch]$NoGui,
  [switch]$WithBatch
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Write-Step($Text) {
  Write-Host "==> $Text"
}

if ($InstallSystem) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Step "Installing system packages with winget"
    winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    winget install --id Google.Chrome --accept-source-agreements --accept-package-agreements
    if (-not $NoGui) {
      winget install --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
      winget install --id Rustlang.Rustup --accept-source-agreements --accept-package-agreements
      winget install --id Microsoft.VisualStudio.2022.BuildTools --accept-source-agreements --accept-package-agreements
    }
  } else {
    Write-Warning "winget not found. Install Python 3, Chrome/Edge, Node.js, Rust, and Visual Studio Build Tools manually."
  }
} else {
  Write-Host "system packages not changed"
  Write-Host "to install OS deps:"
  Write-Host "  .\\scripts\\bootstrap-windows.ps1 -InstallSystem"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
  throw "Python not found"
}

Write-Step "Creating .venv"
& $python.Source -m venv .venv

$venvPython = Join-Path (Get-Location) ".venv\\Scripts\\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
if ($WithBatch) {
  & $venvPython -m pip install -r requirements-batch.txt
}

if (-not $NoGui) {
  if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Step "Installing GUI deps"
    npm install
  } else {
    Write-Warning "npm not found; GUI deps skipped"
  }
}

& $venvPython gdictate.py --capabilities

Write-Host ""
Write-Host "Speaker capture on Windows requires a recording endpoint: Stereo Mix, VB-CABLE, Virtual Audio Cable, or Voicemeeter."
