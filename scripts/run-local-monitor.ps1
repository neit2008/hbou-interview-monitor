$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $ProjectRoot ".env.local"
$LogDir = Join-Path $ProjectRoot "logs"
$RunLog = Join-Path $LogDir "last-run.log"
$ErrorLog = Join-Path $LogDir "last-error.log"

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

$PythonExe = $env:PYTHON_EXE
if (-not $PythonExe) {
    $PythonExe = "python"
}

Push-Location $ProjectRoot
try {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] Starting local monitor run..." | Out-File -LiteralPath $RunLog -Encoding utf8
    $output = & $PythonExe "monitor.py" 2>&1
    $exitCode = $LASTEXITCODE
    $output | Out-File -LiteralPath $RunLog -Append -Encoding utf8
    if ($exitCode -ne 0) {
        throw "monitor.py exited with code $exitCode"
    }
    "[$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")] Local monitor run finished." | Out-File -LiteralPath $RunLog -Append -Encoding utf8
}
catch {
    "[$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")] $($_.Exception.Message)" | Out-File -LiteralPath $ErrorLog -Append -Encoding utf8
    throw
}
finally {
    Pop-Location
}
