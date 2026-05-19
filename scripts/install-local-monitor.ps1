param(
    [string]$PushPlusToken,
    [int]$IntervalMinutes = 10,
    [string]$TaskName = "HBOU Interview Monitor"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $ProjectRoot ".env.local"
$Runner = Join-Path $PSScriptRoot "start-local-monitor-loop.ps1"

if ($PushPlusToken) {
    "PUSHPLUS_TOKEN=$PushPlusToken" | Out-File -LiteralPath $EnvFile -Encoding utf8
    "MONITOR_INTERVAL_MINUTES=$IntervalMinutes" | Out-File -LiteralPath $EnvFile -Append -Encoding utf8
}
elseif (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Missing .env.local. Re-run with -PushPlusToken '<your token>' or create .env.local first."
}

$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    throw "Python was not found on PATH."
}

Push-Location $ProjectRoot
try {
    & python -c "import requests, yaml" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & python -m pip install -r requirements.txt
        & python -c "import requests, yaml"
        if ($LASTEXITCODE -ne 0) {
            throw "Python dependencies are missing and pip install did not fix them."
        }
    }
}
finally {
    Pop-Location
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Run the HBOU page monitor locally after Windows logon. Stops naturally when the computer shuts down." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Project: $ProjectRoot"
Write-Host "Logs: $ProjectRoot\logs"
