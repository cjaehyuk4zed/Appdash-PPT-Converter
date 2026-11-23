$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (Get-Command uv -ErrorAction SilentlyContinue) { uv venv --python 3.14 .venv }
    else { py -3 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
if (Get-Command uv -ErrorAction SilentlyContinue) { uv pip install --python '.venv\Scripts\python.exe' -r requirements.lock }
else { & '.venv\Scripts\python.exe' -m pip install -r requirements.lock }
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Run .\start.ps1 or import appdash-gateway-config.json into AppDash.'
