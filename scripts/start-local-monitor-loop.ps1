$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $ProjectRoot ".env.local"
$LogDir = Join-Path $ProjectRoot "logs"
$LoopLog = Join-Path $LogDir "monitor-loop.log"
$LockPath = Join-Path $env:TEMP "hbou-interview-monitor-loop.lock"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (Test-Path -LiteralPath $EnvFile) {
    Get-Content -LiteralPath $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $name, $value = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
    }
}

if (Test-Path -LiteralPath $LockPath) {
    $existingPid = (Get-Content -LiteralPath $LockPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        "[$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")] Monitor loop already running as PID $existingPid." | Out-File -LiteralPath $LoopLog -Append -Encoding utf8
        exit 0
    }
}

$PID | Out-File -LiteralPath $LockPath -Encoding ascii

try {
    $intervalMinutes = 10
    if ($env:MONITOR_INTERVAL_MINUTES) {
        $intervalMinutes = [Math]::Max(1, [int]$env:MONITOR_INTERVAL_MINUTES)
    }

    "[$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")] Monitor loop started. Interval: $intervalMinutes minutes." | Out-File -LiteralPath $LoopLog -Append -Encoding utf8

    while ($true) {
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run-local-monitor.ps1")
            "[$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")] Run completed." | Out-File -LiteralPath $LoopLog -Append -Encoding utf8
        }
        catch {
            "[$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")] Run failed: $($_.Exception.Message)" | Out-File -LiteralPath $LoopLog -Append -Encoding utf8
        }

        Start-Sleep -Seconds ($intervalMinutes * 60)
    }
}
finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
