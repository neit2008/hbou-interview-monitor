param(
    [ValidateSet("status", "start", "stop", "restart", "run-once", "install", "uninstall")]
    [string]$Action,
    [string]$TaskName = "HBOU Interview Monitor"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $ProjectRoot ".env.local"
$LogDir = Join-Path $ProjectRoot "logs"
$RunLog = Join-Path $LogDir "last-run.log"
$LoopLog = Join-Path $LogDir "monitor-loop.log"
$LockPath = Join-Path $env:TEMP "hbou-interview-monitor-loop.lock"

function Get-LoopProcess {
    if (-not (Test-Path -LiteralPath $LockPath)) {
        return $null
    }
    $existingPid = (Get-Content -LiteralPath $LockPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $existingPid) {
        return $null
    }
    return Get-Process -Id $existingPid -ErrorAction SilentlyContinue
}

function Get-TokenStatus {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        return "not configured"
    }
    $line = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match "^PUSHPLUS_TOKEN=.+" } | Select-Object -First 1
    if ($line) {
        return "configured"
    }
    return "missing"
}

function Show-Tail {
    param([string]$Path, [int]$Count = 8)
    if (Test-Path -LiteralPath $Path) {
        Get-Content -LiteralPath $Path -Tail $Count
    }
    else {
        Write-Host "(no log yet)"
    }
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $loop = Get-LoopProcess

    Write-Host ""
    Write-Host "=== HBOU local monitor status ==="
    Write-Host "Project: $ProjectRoot"
    Write-Host "PushPlus token: $(Get-TokenStatus)"

    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "Scheduled task: installed"
        Write-Host "Task state: $($task.State)"
        Write-Host "Last task start: $($info.LastRunTime)"
        Write-Host "Last task result: $($info.LastTaskResult)"
    }
    else {
        Write-Host "Scheduled task: not installed"
    }

    if ($loop) {
        Write-Host "Monitor loop: running (PID $($loop.Id))"
    }
    else {
        Write-Host "Monitor loop: stopped"
    }

    Write-Host ""
    Write-Host "--- Last monitor run ---"
    Show-Tail -Path $RunLog -Count 8

    Write-Host ""
    Write-Host "--- Loop log ---"
    Show-Tail -Path $LoopLog -Count 8
    Write-Host ""
}

function Start-Monitor {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Scheduled task is not installed. Run scripts\install-local-monitor.ps1 first."
    }
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
    Show-Status
}

function Stop-Monitor {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    $loop = Get-LoopProcess
    if ($loop) {
        Stop-Process -Id $loop.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Show-Status
}

function Run-Once {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run-local-monitor.ps1")
    Show-Status
}

function Show-Menu {
    Write-Host ""
    Write-Host "HBOU local monitor switch"
    Write-Host "1. Status"
    Write-Host "2. Start"
    Write-Host "3. Stop"
    Write-Host "4. Restart"
    Write-Host "5. Run once now"
    Write-Host "6. Install/update startup task"
    Write-Host "7. Uninstall startup task"
    Write-Host "0. Exit"
    $choice = Read-Host "Choose"
    switch ($choice) {
        "1" { return "status" }
        "2" { return "start" }
        "3" { return "stop" }
        "4" { return "restart" }
        "5" { return "run-once" }
        "6" { return "install" }
        "7" { return "uninstall" }
        default { return "" }
    }
}

if (-not $Action) {
    $Action = Show-Menu
}

switch ($Action) {
    "status" { Show-Status }
    "start" { Start-Monitor }
    "stop" { Stop-Monitor }
    "restart" {
        Stop-Monitor
        Start-Monitor
    }
    "run-once" { Run-Once }
    "install" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "install-local-monitor.ps1")
        Show-Status
    }
    "uninstall" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "uninstall-local-monitor.ps1")
        Show-Status
    }
    default { Write-Host "Exit." }
}
