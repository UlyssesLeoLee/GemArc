$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root 'src'
Push-Location $root
try {
  py -3.11 -m unittest discover -s tests -v
  if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
} finally { Pop-Location }
