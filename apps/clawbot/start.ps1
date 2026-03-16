# Clawbot Auto-Start Script
# This script starts Clawbot and keeps it running, restarting it if it crashes.
$botScript = "C:\Openclaw\apps\clawbot\main.py"
$pythonExe = "C:\Openclaw\apps\clawbot\venv\Scripts\python.exe"
$logFile = "C:\Openclaw\apps\clawbot\clawbot.log"

Write-Host "Clawbot Watchdog started at $(Get-Date)" -ForegroundColor Green

while ($true) {
    Write-Host "[$(Get-Date)] Starting Clawbot..." | Tee-Object -FilePath $logFile -Append
    
    # Start the bot process
    $process = Start-Process -FilePath $pythonExe -ArgumentList $botScript -NoNewWindow -PassThru -RedirectStandardOutput $logFile -RedirectStandardError $logFile
    
    # Wait for it to exit
    $process.WaitForExit()
    
    $exitCode = $process.ExitCode
    Write-Host "[$(Get-Date)] Clawbot exited with code $exitCode. Restarting in 10 seconds..." | Tee-Object -FilePath $logFile -Append
    Start-Sleep -Seconds 10
}
