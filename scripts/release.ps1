<#
.SYNOPSIS
  agent-control-tower 的「一条命令发布」。

.DESCRIPTION
  先自己写好 CHANGELOG，然后跑：

      .\scripts\release.ps1 -Version 0.7.4

  它会依次做：
    1. 确认 CHANGELOG 里已经有 "## [0.7.4] - 日期" 这一段（没有就停，不许发空 Release）
    2. 把版本号写进三处：pyproject.toml、src/control_tower/__init__.py、packages/dsh-tower/package.json
    3. 真跑测试（pytest + node --test），并把**真实数量**写回 README / README.zh.md 的 tests 徽章
    4. 跑 scripts/check-release-consistency.mjs --tag v0.7.4
    5. git commit "chore(release): v0.7.4" → 打 annotated tag → push main 和 tag
    6. GitHub Actions 的 release workflow 接手：再跑测试、建 GitHub Release（正文抄 CHANGELOG）、挂 wheel/sdist；配了 PYPI_API_TOKEN 就顺便发 PyPI

  其他用法：
      .\scripts\release.ps1 -BadgesOnly              # 只按真实测试数量刷新 README 徽章
      .\scripts\release.ps1 -Version 0.7.4 -DryRun   # 只报计划，不写任何文件
      .\scripts\release.ps1 -Version 0.7.4 -NoPush   # 本地提交+打 tag，不 push
#>
[CmdletBinding()]
param(
  [string]$Version,
  [switch]$BadgesOnly,
  [switch]$DryRun,
  [switch]$SkipTests,
  [switch]$NoPush
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$files = @{
  pyproject  = Join-Path $root 'pyproject.toml'
  initpy     = Join-Path $root 'src\control_tower\__init__.py'
  bundle     = Join-Path $root 'packages\dsh-tower\package.json'
  changelog  = Join-Path $root 'CHANGELOG.md'
  readme     = Join-Path $root 'README.md'
  readmeZh   = Join-Path $root 'README.zh.md'
}

function Read-Utf8([string]$Path) {
  [System.IO.File]::ReadAllText($Path)
}

function Write-Utf8([string]$Path, [string]$Text) {
  [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-CurrentVersion {
  $text = Read-Utf8 $files.pyproject
  $match = [regex]::Match($text, '(?m)^version = "([^"]+)"')
  if (-not $match.Success) { throw "pyproject.toml 里找不到 version = `"...`"" }
  $match.Groups[1].Value
}

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$WorkDir) {
  Push-Location $WorkDir
  try {
    $output = & $Exe @Arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  [pscustomobject]@{ Output = $output; ExitCode = $code }
}

function Invoke-TestSuites {
  $venvPython = Join-Path $root '.venv\Scripts\python.exe'
  if (Test-Path $venvPython) { $python = $venvPython } else { $python = (Get-Command python).Source }

  Write-Host '  pytest ...'
  $pytest = Invoke-Native $python @('-m', 'pytest', '-q') $root
  if ($pytest.ExitCode -ne 0) {
    Write-Host $pytest.Output
    throw "pytest 失败（exit $($pytest.ExitCode)），不发布。"
  }
  $pyMatch = [regex]::Match($pytest.Output, '(\d+) passed')
  $pythonCount = if ($pyMatch.Success) { [int]$pyMatch.Groups[1].Value } else { 0 }

  Write-Host '  node --test ...'
  $bundleDir = Join-Path $root 'packages\dsh-tower'
  $node = Invoke-Native 'node' @('--test', 'test/*.test.js') $bundleDir
  if ($node.ExitCode -ne 0) {
    Write-Host $node.Output
    throw "node --test 失败（exit $($node.ExitCode)），不发布。"
  }
  $nodeMatch = [regex]::Match($node.Output, '(?m)^\s*(?:\u2139|#)\s*tests\s+(\d+)')
  $nodeCount = if ($nodeMatch.Success) { [int]$nodeMatch.Groups[1].Value } else { 0 }

  Write-Host "  测试通过：pytest $pythonCount · node $nodeCount"
  [pscustomobject]@{ Python = $pythonCount; Node = $nodeCount }
}

function Update-ReadmeBadges([int]$PythonCount, [int]$NodeCount) {
  $pattern = 'tests-\d+%20python%20%2B%20\d+%20node'
  $replacement = "tests-$PythonCount%20python%20%2B%20$NodeCount%20node"
  foreach ($path in @($files.readme, $files.readmeZh)) {
    $text = Read-Utf8 $path
    if ($text -notmatch $pattern) {
      Write-Host "  跳过（没有 tests 徽章）：$([System.IO.Path]::GetFileName($path))"
      continue
    }
    if ($text -match [regex]::Escape($replacement)) {
      Write-Host "  已是最新：$([System.IO.Path]::GetFileName($path))"
      continue
    }
    if ($DryRun) {
      Write-Host "  [dry-run] 会改成 $replacement：$([System.IO.Path]::GetFileName($path))"
      continue
    }
    Write-Utf8 $path ([regex]::Replace($text, $pattern, $replacement))
    Write-Host "  徽章 → $replacement（$([System.IO.Path]::GetFileName($path))）"
  }
}

function Assert-ChangelogSection([string]$Target) {
  $text = Read-Utf8 $files.changelog
  $escaped = $Target.Replace('.', '\.')
  if ($text -match "(?m)^## \[$escaped\]") { return }
  throw @"
CHANGELOG.md 里没有 "## [$Target] - 日期" 这一段，先写它再发版。

建议粘这一段（然后把下面几行改成这次真实做了什么）：
## [$Target] - $(Get-Date -Format 'yyyy-MM-dd')

### Added
- ...

### Fixed
- ...
"@
}

function Set-VersionEverywhere([string]$From, [string]$To) {
  $edits = @(
    @{ Path = $files.pyproject; Pattern = '(?m)^version = "[^"]+"'; Replacement = "version = `"$To`"" },
    @{ Path = $files.initpy;    Pattern = '__version__ = "[^"]+"'; Replacement = "__version__ = `"$To`"" },
    @{ Path = $files.bundle;    Pattern = '("version"\s*:\s*")[^"]+(")'; Replacement = '${1}' + $To + '${2}' }
  )
  foreach ($edit in $edits) {
    $text = Read-Utf8 $edit.Path
    if ($text -notmatch $edit.Pattern) { throw "找不到要替换的版本号：$($edit.Path)" }
    $updated = [regex]::Replace($text, $edit.Pattern, $edit.Replacement, 1)
    if ($updated -eq $text) { Write-Host "  已是 $To：$([System.IO.Path]::GetFileName($edit.Path))"; continue }
    if ($DryRun) {
      Write-Host "  [dry-run] $([System.IO.Path]::GetFileName($edit.Path)): $From → $To"
      continue
    }
    Write-Utf8 $edit.Path $updated
    Write-Host "  $([System.IO.Path]::GetFileName($edit.Path)): $From → $To"
  }
}

function Invoke-Git([string[]]$Arguments) {
  $result = Invoke-Native 'git' $Arguments $root
  if ($result.ExitCode -ne 0) {
    Write-Host $result.Output
    throw "git $($Arguments -join ' ') 失败（exit $($result.ExitCode)）"
  }
  $result.Output.Trim()
}

# 主流程整体包一层：任何 throw 都只留一行原因 + exit 1，不给用户看 CategoryInfo 堆栈。
try {

# ---------------------------------------------------------------- BadgesOnly
if ($BadgesOnly) {
  Write-Host '刷新 README 测试徽章'
  $counts = Invoke-TestSuites
  Update-ReadmeBadges $counts.Python $counts.Node
  Write-Host "`n完成。浏览器里刷一下 README 就能看到 tests-$($counts.Python)%20python%20%2B%20$($counts.Node)%20node。"
  exit 0
}

# ---------------------------------------------------------------- full release
if (-not $Version) {
  throw '缺 -Version。用法：.\scripts\release.ps1 -Version 0.7.4（或 -BadgesOnly / -DryRun）'
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
  throw "-Version 要是 X.Y.Z 三段数字，收到 `"$Version`"。"
}

$current = Get-CurrentVersion
if ($current -eq $Version) {
  throw "当前已经是 $Version 了，没有版本可升。改个新版本号，或先跑 -BadgesOnly 刷徽章。"
}

Write-Host "发布 v$Version（当前 $current）"
if ($DryRun) { Write-Host '[dry-run] 只看计划，不写文件、不提交、不打 tag。' }

Write-Host '1/6 检查 CHANGELOG ...'
Assert-ChangelogSection $Version

Write-Host '2/6 改版本号 ...'
Set-VersionEverywhere $current $Version

Write-Host '3/6 跑测试 ...'
if ($SkipTests) {
  Write-Host '  -SkipTests：跳过（不推荐，README 徽章也不会更新）'
  $counts = $null
} else {
  $counts = Invoke-TestSuites
}

if ($counts) {
  Write-Host '4/6 刷新 README 徽章 ...'
  Update-ReadmeBadges $counts.Python $counts.Node
}

Write-Host "5/6 一致性检查（tag v$Version）..."
if ($DryRun) {
  Write-Host '  [dry-run] 文件没真改，跳过（真实执行时会跑 check-release-consistency --tag）'
} else {
  $consistency = Invoke-Native 'node' @('scripts/check-release-consistency.mjs', '--tag', "v$Version") $root
  Write-Host $consistency.Output
  if ($consistency.ExitCode -ne 0) { throw '一致性检查不通过，不发布。' }
}

$notes = Invoke-Native 'node' @('scripts/release-notes.mjs', $Version) $root
if ($notes.ExitCode -ne 0) {
  Write-Host $notes.Output
  throw '取 Release 正文失败，不发布。'
}
Write-Host 'Release 正文（来自 CHANGELOG）：'
Write-Host $notes.Output.Trim()

if ($DryRun) {
  Write-Host "`n[dry-run] 计划完毕。真实执行时还会做："
  Write-Host "  git add pyproject.toml CHANGELOG.md README.md README.zh.md src/control_tower/__init__.py packages/dsh-tower/package.json"
  Write-Host "  git commit -m `"chore(release): v$Version`""
  Write-Host "  git tag -a v$Version -m `"v$Version`""
  Write-Host "  git push origin main; git push origin v$Version"
  exit 0
}

Write-Host '6/6 提交并推 tag ...'
$tracked = @(
  'pyproject.toml',
  'CHANGELOG.md',
  'README.md',
  'README.zh.md',
  'src/control_tower/__init__.py',
  'packages/dsh-tower/package.json'
)
Invoke-Git (@('add', '--') + $tracked) | Out-Null
Write-Host (Invoke-Git @('status', '--short'))
Invoke-Git @('commit', '-m', "chore(release): v$Version") | Out-Null
Invoke-Git @('tag', '-a', "v$Version", '-m', "v$Version") | Out-Null
Write-Host "  已提交并打 tag v$Version"

if ($NoPush) {
  Write-Host "`n-NoPush：都没推。要推就执行："
  Write-Host "  git push origin main; git push origin v$Version"
  exit 0
}

Invoke-Git @('push', 'origin', 'main') | Out-Null
Invoke-Git @('push', 'origin', "v$Version") | Out-Null

$repoUrl = 'https://github.com/evolution301/agent-control-tower'
Write-Host "`n已推送 v$Version。接下来是 GitHub 那边自动完成："
Write-Host "  Actions: $repoUrl/actions"
Write-Host "  Release: $repoUrl/releases/tag/v$Version"
Write-Host '  约 1–2 分钟后去看，Release 正文应该和 CHANGELOG 那段一致，附件里应有 wheel 和 sdist。'

} catch {
  Write-Host ''
  Write-Host "停止：$($_.Exception.Message)" -ForegroundColor Red
  exit 1
}
