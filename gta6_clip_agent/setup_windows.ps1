# One-time setup for the GTA 6 clip agent on Windows.
# Run from the gta6_clip_agent folder in PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
$ErrorActionPreference = "Stop"

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Write-Host "== 1/5 Checking tools ==" -ForegroundColor Cyan
if (-not (Have "winget")) {
    throw "winget was not found. Install 'App Installer' from the Microsoft Store, then run this again."
}
if (-not (Have "python")) { winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements }
if (-not (Have "ffmpeg")) { winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements }
# Make tools installed a moment ago visible in this window.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
if (-not (Have "python")) { throw "Python was installed but is not on PATH yet. Close this window, open a new one, and run this again." }
if (-not (Have "ffmpeg")) { throw "ffmpeg was installed but is not on PATH yet. Close this window, open a new one, and run this again." }

Write-Host "== 2/5 Creating the Python environment ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt

Write-Host "== 3/5 Creating folders and settings file ==" -ForegroundColor Cyan
foreach ($d in "footage", "voice", "out", "proofs") { New-Item -ItemType Directory -Force -Path $d | Out-Null }
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env - open it in Notepad and paste in your keys."
} else {
    Write-Host ".env already exists; left untouched."
}

Write-Host "== 4/5 Running the tests ==" -ForegroundColor Cyan
& $py -m pytest -q tests
if ($LASTEXITCODE -ne 0) { Write-Host "Some tests failed. Send the output above to Claude." -ForegroundColor Yellow }

Write-Host "== 5/5 Account check ==" -ForegroundColor Cyan
& $py -m clipagent accounts

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Green
Write-Host "  1. notepad .env                      (paste your API keys)"
Write-Host "  2. $py -m clipagent sources list"
Write-Host "  3. See SETUP.md for adding creators and making the first clips."
