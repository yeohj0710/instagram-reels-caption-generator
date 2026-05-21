$ErrorActionPreference = "Stop"

$DevRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $DevRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
$env:PYTHONPATH = Join-Path $DevRoot "src"
& ".\.venv\Scripts\python.exe" -m reels_caption_generator
