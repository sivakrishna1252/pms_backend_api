# Mon-Sat 8 PM: stop running PMS task timers and email employees.
# If nothing is in progress, backend does nothing (no email).
#
# Run job now:     powershell -ExecutionPolicy Bypass -File scripts/auto-stop-tasks.ps1
# Test anytime:   powershell -ExecutionPolicy Bypass -File scripts/auto-stop-tasks.ps1 -Force
# Register cron:  powershell -ExecutionPolicy Bypass -File scripts/auto-stop-tasks.ps1 -Register

param(
    [switch]$Register,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PmsDir = Join-Path $Root "pms"
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "auto-stop-tasks.log"
$TaskName = "PMS Auto Stop Running Tasks 8pm"
$Days = "MON,TUE,WED,THU,FRI,SAT"
$SelfPath = (Resolve-Path $PSCommandPath).Path

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

if ($Register) {
    foreach ($legacy in @(
            "PMS Evening Auto Stop 8pm",
            "PMS Evening Auto Stop 9pm",
            "PMS Auto Stop Running Tasks 8pm"
        )) {
        try { & schtasks.exe /Delete /TN $legacy /F | Out-Null } catch {}
    }

    $action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$SelfPath`""
    & schtasks.exe /Create /TN $TaskName /TR $action /SC WEEKLY /D $Days /ST 20:00 /F
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks failed (exit $LASTEXITCODE)."
    }

    Write-Host "Registered: $TaskName at 8:00 PM on $Days"
    Write-Host "Logs: $LogFile"
    Write-Host "Test: powershell -ExecutionPolicy Bypass -File `"$SelfPath`" -Force"
    exit 0
}

Write-Log "Task auto-stop starting..."

Push-Location $PmsDir
try {
    $manageArgs = @("auto_stop_task_timers")
    if ($Force) { $manageArgs += "--force" }
    & python manage.py @manageArgs
    if ($LASTEXITCODE -ne 0) { throw "auto_stop_task_timers failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

Write-Log "Done."
