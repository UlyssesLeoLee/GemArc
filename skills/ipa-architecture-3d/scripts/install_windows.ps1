# Install this complete reusable skill into selected agent channels on Windows.
#
# Channel notes:
#   claude  - ~/.claude/skills          (skill fallback; the plugin channel is the
#                                        `claude plugin marketplace add` path in README)
#   codex   - ~/.agents/skills          (also read by ~/.agents/plugins/marketplace.json)
#   hermes  - %LOCALAPPDATA%\hermes\skills\<category>
#   minimax - ~/.minimax/skills         (local Skill fallback; the MiniMax plugin channel
#                                        is ~/.minimax/plugins/<plugin>/.minimax-plugin)
[CmdletBinding()]
param(
    [string[]]$Agents = @('claude','codex','hermes','minimax'),
    [ValidateSet('software-development','creative','productivity','research','devops')][string]$HermesCategory = 'software-development',
    [string]$UserHome = [Environment]::GetFolderPath('UserProfile'),
    [string]$HermesHome = $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'hermes' } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.hermes' }),
    [switch]$Force
)
$ErrorActionPreference = 'Stop'

# PS 5.1 defaults to the OEM code page; force UTF-8 so Chinese messages are
# readable rather than mojibake exactly when something has failed.
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# `powershell -File ... -Agents codex,hermes` arrives as ONE comma-joined string,
# while an in-session call arrives as a real array. Accept both.
$knownAgents = @('claude','codex','hermes','minimax')
$requested = @()
foreach ($a in $Agents) { $requested += ($a -split ',') }
$requested = @($requested | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
$unknown = @($requested | Where-Object { $knownAgents -notcontains $_ })
if ($unknown.Count -gt 0) {
    throw "Unknown channel(s): $($unknown -join ', '). Valid values: $($knownAgents -join ', ')"
}
$Agents = $requested

$skillRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$skillName = 'ipa-architecture-3d'
$paths = [ordered]@{
    claude  = Join-Path $UserHome '.claude\skills'
    codex   = Join-Path $UserHome '.agents\skills'
    hermes  = Join-Path $HermesHome "skills\$HermesCategory"
    minimax = Join-Path $UserHome '.minimax\skills'
}
foreach ($agent in $Agents) {
    $base = $paths[$agent]
    $target = Join-Path $base $skillName
    New-Item -Path $base -ItemType Directory -Force | Out-Null
    if (Test-Path -LiteralPath $target) {
        if (-not $Force) {
            Write-Warning "$agent : $target already exists; use -Force to preserve a backup and replace it."
            continue
        }
        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backup = Join-Path $base "$skillName.backup-$timestamp"
        Move-Item -LiteralPath $target -Destination $backup
        Write-Host "Backup: $backup"
    }
    Copy-Item -LiteralPath $skillRoot -Destination $target -Recurse
    if (-not (Test-Path -LiteralPath (Join-Path $target 'SKILL.md'))) {
        throw "SKILL.md not found after installation: $target"
    }
    Write-Host "Installed $agent : $target" -ForegroundColor Green
}
Write-Host "Restart your agent CLI / desktop app for skill discovery."
