# Build a local MiniMax Plugin V1 package for ipa-architecture-3d.
#
# The package is written to <PluginsRoot>\ipa-architecture-3d\ and picked up by the
# MiniMax Code Desktop local Plugin rescan. Nothing is uploaded anywhere: this is a
# local installation channel, not a Marketplace publication.
#
#   .\build_minimax_plugin.ps1
#   .\build_minimax_plugin.ps1 -PluginsRoot C:\temp\plugins -Force
[CmdletBinding()]
param(
    [string]$PluginsRoot = $(if ($env:MINIMAX_PLUGINS_DIR) { $env:MINIMAX_PLUGINS_DIR } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.minimax\plugins' }),
    [ValidateSet('Code','Productivity','Design & Sites','Studio','Other')][string]$Category = 'Code',
    [switch]$Force
)
$ErrorActionPreference = 'Stop'

# PS 5.1 defaults to the OEM code page; force UTF-8 so Chinese messages are
# readable rather than mojibake exactly when something has failed.
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$skillDir  = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$repoRoot  = (Resolve-Path (Join-Path $skillDir '..\..')).Path
$skillName = 'ipa-architecture-3d'
$agentManifest = Join-Path $repoRoot 'plugin.json'
if (-not (Test-Path -LiteralPath $agentManifest)) {
    throw "Agent Plugins v1 manifest not found: $agentManifest"
}
$agent = Get-Content -LiteralPath $agentManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $agent.version
if (-not $version) { throw 'plugin.json has no version field' }
$description = $agent.description
if (-not $description) { throw 'plugin.json has no description field' }

$pkg = Join-Path $PluginsRoot $skillName
if ((Test-Path -LiteralPath $pkg) -and -not $Force) {
    throw "Package already exists: $pkg  (re-run with -Force to replace it)"
}
if (Test-Path -LiteralPath $pkg) { Remove-Item -LiteralPath $pkg -Recurse -Force }
New-Item -Path (Join-Path $pkg '.minimax-plugin') -ItemType Directory -Force | Out-Null
New-Item -Path (Join-Path $pkg 'skills')           -ItemType Directory -Force | Out-Null

# Skill capability: skills/<name>/SKILL.md plus its references/assets/scripts.
Copy-Item -LiteralPath $skillDir -Destination (Join-Path $pkg "skills\$skillName") -Recurse

# Icon: prefer the pair bundled with the local MiniMax installation so the plugin
# matches its category. Fall back to a generated placeholder when that pool is absent.
$categorySlug = switch ($Category) {
    'Design & Sites'  { 'design-and-sites' }
    'Science & Healthcare' { 'science-and-healthcare' }
    default           { $Category.ToLowerInvariant() }
}
$pool = Join-Path ([Environment]::GetFolderPath('UserProfile')) ".minimax\.builtin-skills\plugin-creator\assets"
$lightDir = Join-Path $pool "category-icons\$categorySlug"
$darkDir  = Join-Path $pool "category-icons-dark\$categorySlug"
function New-FallbackIcon([string]$path, [System.Drawing.Color]$bg) {
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
    $bmp = New-Object System.Drawing.Bitmap 256, 256
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.Clear($bg)
    $font = New-Object System.Drawing.Font('Segoe UI', 84, ([System.Drawing.FontStyle]::Bold))
    $fmt = New-Object System.Drawing.StringFormat
    $fmt.Alignment = 'Center'; $fmt.LineAlignment = 'Center'
    $g.DrawString('3D', $font, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF 0, 0, 256, 256), $fmt)
    $g.Dispose(); $font.Dispose(); $fmt.Dispose()
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}
$iconRel = 'icon.png'; $darkRel = $darkIconRel = $null
if ((Test-Path -LiteralPath $lightDir) -and (Test-Path -LiteralPath $darkDir)) {
    $candidates = @(Get-ChildItem -LiteralPath $lightDir -File -Filter *.png | Where-Object { $_.Name -ne 'DESCRIPTION.md' })
    if ($candidates.Count -gt 0) {
        # deterministic pick, and never the first candidate
        $pick = $candidates[[Math]::Min(1, $candidates.Count - 1)]
        $darkMatch = Join-Path $darkDir $pick.Name
        if (Test-Path -LiteralPath $darkMatch) {
            Copy-Item -LiteralPath $pick.FullName -Destination (Join-Path $pkg $iconRel)
            Copy-Item -LiteralPath $darkMatch   -Destination (Join-Path $pkg 'icon-dark.png')
            $darkIconRel = 'icon-dark.png'
            Write-Host "Icon: bundled $Category pair ($($pick.Name))"
        }
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $pkg $iconRel))) {
    New-FallbackIcon (Join-Path $pkg $iconRel) ([System.Drawing.Color]::FromArgb(26, 54, 92))
    New-FallbackIcon (Join-Path $pkg 'icon-dark.png') ([System.Drawing.Color]::FromArgb(10, 18, 32))
    $darkIconRel = 'icon-dark.png'
    Write-Host "Icon: generated placeholder (bundled $Category pool not found under $pool)" -ForegroundColor Yellow
}

$manifest = [ordered]@{
    schemaVersion  = 1
    name           = $skillName
    displayName    = 'IPA Architecture 3D'
    version        = $version
    description    = $description
    author         = 'Ulysses Leo Lee'
    icon           = $iconRel
    darkIcon       = $darkIconRel
    category       = $Category
    exampleQueries = @(
        '用 ipa-architecture-3d 把这个仓库生成三维架构审查网站',
        '帮我把这份基本设计做成可以切换视角的 Three.js 架构图',
        '用 ipa-architecture-3d 校验架构 JSON 的空间信息增益和证据完整性'
    )
    apps           = @()
    mcpServers     = @()
    skills         = @("skills/$skillName/SKILL.md")
}
$manifestPath = Join-Path $pkg '.minimax-plugin\plugin.json'
$json = ($manifest | ConvertTo-Json -Depth 5)
[System.IO.File]::WriteAllText($manifestPath, ($json -replace "`r`n", "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))

# Local validation: manifest parses, every referenced file exists, limits respected.
$check = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($rel in @($check.icon, $check.darkIcon) + @($check.skills)) {
    if (-not $rel) { continue }
    $full = Join-Path $pkg ($rel -replace '/', '\')
    if (-not (Test-Path -LiteralPath $full)) { throw "manifest references a missing file: $rel" }
}
if (@($check.apps).Count -ne 0) { throw 'apps must be an empty array' }
if (@($check.skills).Count -eq 0 -and @($check.mcpServers).Count -eq 0) { throw 'package declares no effective capability' }
$entries = @(Get-ChildItem -LiteralPath $pkg -Recurse -Force)
if ($entries.Count -gt 2048) { throw "package has $($entries.Count) entries, limit is 2048" }
$badSegment = $entries | Where-Object {
    $rel = $_.FullName.Substring($pkg.Length + 1)
    ($rel -split '[\\/]') | Where-Object { $_ -notmatch '^[A-Za-z0-9._-]+$' -or $_ -match '^\.{1,2}$' }
}
if ($badSegment) { throw "non-portable path segment(s): $(($badSegment | Select-Object -First 3) -join ', ')" }

Write-Host "MiniMax plugin package built: $pkg" -ForegroundColor Green
Write-Host "  manifest : $manifestPath"
Write-Host "  version  : $version   category: $Category   entries: $($entries.Count)"
Write-Host 'MiniMax Code Desktop rescans local Plugins automatically; no restart is required unless it does not appear.'