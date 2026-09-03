# Restarts `python main.py` if it exits — Windows equivalent of systemd Restart=always.
# Usage (from repo root or this folder):
#   powershell -ExecutionPolicy Bypass -File ops\windows\watchdog.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$python = $null
foreach ($candidate in @(
    (Join-Path $RepoRoot "venv\Scripts\python.exe"),
    (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
    "python",
    "py"
)) {
    if ($candidate -eq "py") {
        $python = "py"
        $pythonArgs = @("-3", "main.py")
        break
    }
    if ($candidate -eq "python") {
        $python = "python"
        $pythonArgs = @("main.py")
        break
    }
    if (Test-Path $candidate) {
        $python = $candidate
        $pythonArgs = @("main.py")
        break
    }
}

if (-not $python) {
    Write-Error "Python not found. Create venv and pip install -r requirements.txt"
    exit 1
}

Write-Host "solbot watchdog in $RepoRoot using $python"
while ($true) {
    $proc = Start-Process -FilePath $python -ArgumentList $pythonArgs -WorkingDirectory $RepoRoot -PassThru -NoNewWindow
    $proc.WaitForExit()
    $code = $proc.ExitCode
    Write-Host "$(Get-Date -Format o) solbot exited with $code — restarting in 5s"
    Start-Sleep -Seconds 5
}
