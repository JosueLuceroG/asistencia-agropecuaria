$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:ASISTENCIA_CONFIG_DIR = Join-Path $root "demo"
$env:ASISTENCIA_EXCEL_PASSWORD = "demo"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

& $python (Join-Path $root "main v4.py")
