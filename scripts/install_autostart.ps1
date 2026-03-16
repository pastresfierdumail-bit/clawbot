# ─── Clawbot v3 — Installation auto-start via Task Scheduler ─────
# Exécuter en tant qu'administrateur :
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1

$TaskName = "Clawbot v3"
$Description = "Démarre Clawbot v3 (bot Telegram autonome) au boot de la VM"
$BatPath = (Resolve-Path "$PSScriptRoot\autostart.bat").Path

# Supprimer la tâche existante si elle existe
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[INFO] Ancienne tâche '$TaskName' supprimée."
}

# Créer la tâche
$Action = New-ScheduledTaskAction -Execute $BatPath
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Trigger.Delay = "PT30S"  # Délai 30 secondes après le boot

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)  # Pas de limite de durée

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description $Description `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal

Write-Host ""
Write-Host "✅ Tâche '$TaskName' créée avec succès !" -ForegroundColor Green
Write-Host "   → Se lance 30s après le démarrage de Windows"
Write-Host "   → Redémarre automatiquement en cas de crash (3 tentatives)"
Write-Host "   → Pas de limite de durée d'exécution"
Write-Host ""
Write-Host "Pour vérifier : Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "Pour supprimer : Unregister-ScheduledTask -TaskName '$TaskName'"
