param([string]$Workspace = "$PSScriptRoot\..\example-workspace")
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
New-Item -ItemType Directory -Path $Workspace -Force | Out-Null
$env:PYTHONPATH = Join-Path $root 'src'
$env:GEMARC_WORKSPACE_ROOT = (Resolve-Path $Workspace).Path
$env:GEMARC_ALLOW_WRITE = '1'
Push-Location $root
try {
  py -3.11 -m gemarc_mcp.cli init demo
  py -3.11 -m gemarc_mcp.cli validate demo
  py -3.11 -m gemarc_mcp.cli simulate demo normal --disable queue --compare
  py -3.11 -m gemarc_mcp.cli build demo
  py -3.11 -m gemarc_mcp.cli verify demo
  py -3.11 -m gemarc_mcp.cli export demo
} finally { Pop-Location }
