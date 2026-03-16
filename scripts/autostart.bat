@echo off
REM ─── Clawbot v3 Auto-Start Script ───────────────────────────────
REM Ce script est destiné au Planificateur de tâches Windows.
REM Trigger : "Au démarrage de l'ordinateur"
REM
REM Installation :
REM   1. Ouvrir le Planificateur de tâches (taskschd.msc)
REM   2. Créer une tâche > Nom : "Clawbot v3"
REM   3. Déclencheur : "Au démarrage" (+ délai de 30 secondes)
REM   4. Action : Démarrer un programme
REM      Programme : Ce fichier .bat
REM   5. Conditions : Décocher "Démarrer uniquement si alimenté"
REM   6. Paramètres : "Exécuter même si l'utilisateur n'est pas connecté" (optionnel)
REM
REM Ou utiliser le script PowerShell install_autostart.ps1

cd /d "h:\0perso\clawbot"

REM Attendre que le réseau soit disponible
timeout /t 10 /nobreak > nul

REM Vérifier que Python est accessible
where python >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python non trouvé dans le PATH
    exit /b 1
)

REM Lancer le bot (pythonw pour mode silencieux, python pour debug avec console)
start /b pythonw -m apps.telegram_bot

echo [OK] Clawbot v3 lancé en arrière-plan.
