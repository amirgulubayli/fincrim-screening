<#
.SYNOPSIS
  Grok Bot morning run: deterministic health check, then (if Grok is installed) a headless
  Grok pass that summarises the report and posts the "Morning run YYYY-MM-DD" issue.

.DESCRIPTION
  1. python tools/morning_run.py  -> out/morning/YYYY-MM-DD.md + .json (no LLM, no network)
  2. if `grok` is on PATH and -NoGrok is not set:
       grok -p "/morning-run ..." headless, with the file-editing tool removed and only the
       gh / task.py / screen.py commands auto-approved. Grok posts the report unchanged and
       adds its narrative as a separate comment; it cannot change a decision or edit code.

  Schedule daily at 08:00 with Windows Task Scheduler (run once in an elevated or normal
  PowerShell; adjust the repo path):

    schtasks /create /tn "fincrim-morning-run" /sc daily /st 08:00 /f `
      /tr "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\fincrim-screening\tools\morning_run.ps1"

  Remove with: schtasks /delete /tn "fincrim-morning-run" /f
  Run now:     schtasks /run /tn "fincrim-morning-run"

.EXAMPLE
  powershell -File tools/morning_run.ps1                 # data/dev, 5 runs, post via Grok
  powershell -File tools/morning_run.ps1 -NoGrok         # report only
  powershell -File tools/morning_run.ps1 -Data tests/fixtures/mini -Runs 2 -NoGrok
#>
param(
    [string]$Data = "data/dev",
    [int]$Runs = 5,
    [switch]$NoGrok,
    [int]$MaxTurns = 30
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Today = Get-Date -Format "yyyy-MM-dd"
$OutDir = Join-Path $Repo "out/morning"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$Python = (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $Python) { $Python = (Get-Command py -ErrorAction SilentlyContinue) }
if ($null -eq $Python) { Write-Error "python not found on PATH"; exit 2 }

Write-Host "== morning run $Today ($Data, $Runs runs)"
& $Python.Source "tools/morning_run.py" --data $Data --runs $Runs --out-dir $OutDir
$Health = $LASTEXITCODE
Write-Host "== deterministic run exit code: $Health (0 healthy, 1 regression, 2 bad input)"
if ($Health -eq 2) { exit 2 }

$Grok = Get-Command grok -ErrorAction SilentlyContinue
if ($NoGrok -or $null -eq $Grok) {
    if ($null -eq $Grok -and -not $NoGrok) { Write-Host "grok not on PATH; skipping the Grok summary/post step." }
    Write-Host "Report: $OutDir\$Today.md"
    exit $Health
}

$Prompt = @"
/morning-run $Data
(If the morning-run skill is not loaded, read and follow .grok/plugins/fincrim-screening/skills/morning-run/SKILL.md.)
Today's deterministic report is already written by tools/morning_run.py at out/morning/$Today.md and out/morning/$Today.json; read them, do not re-run the engine.
Open or update the GitHub issue "Morning run $Today" with the report unchanged, add your 2-3 sentence summary as a separate comment, file one issue per regression with python tools/task.py new, then stop.
Never edit any file, never commit or push.
"@

Write-Host "== grok headless: summarise + post"
$GrokOut = Join-Path $OutDir "$Today.grok.json"
& $Grok.Source -p $Prompt --cwd $Repo --output-format json --max-turns $MaxTurns `
    --disallowed-tools "search_replace" `
    --allow "Bash(gh issue*)" `
    --allow "Bash(python tools/task.py new*)" `
    --allow "Bash(python tools/morning_run.py*)" `
    --allow "Bash(python system/screen.py*)" `
    --allow "MCPTool(*screen_*)" `
    --allow "MCPTool(*review_queue*)" `
    --deny "Bash(git push*)" `
    --deny "Bash(git commit*)" `
    --deny "Bash(git reset*)" `
    --deny "Bash(git checkout*)" `
    --deny "Edit" --deny "Write" |
    Out-File -FilePath $GrokOut -Encoding utf8
$GrokExit = $LASTEXITCODE
Write-Host "== grok exit code: $GrokExit (output: $GrokOut)"
exit [Math]::Max($Health, $GrokExit)
