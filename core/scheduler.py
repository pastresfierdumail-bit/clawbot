"""
core/scheduler.py — Tâches autonomes planifiées + rapports quotidiens.

Le scheduler tourne en arrière-plan et :
- Exécute des tâches planifiées (cron-like)
- Génère un rapport quotidien de ce qui a été fait
- Stocke les résultats de recherche consultables via /report

Usage : intégré dans le bot Telegram, démarre automatiquement.
"""

import asyncio
import json
import logging
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Optional, Callable, Awaitable

from .security import log_audit

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(__file__).parent.parent / "memory"
TASKS_FILE = MEMORY_DIR / "scheduled_tasks.json"
REPORTS_DIR = MEMORY_DIR / "reports"


# ─── Task model ───────────────────────────────────────────────────

def load_tasks() -> list[dict]:
    if TASKS_FILE.exists():
        with open(TASKS_FILE) as f:
            return json.load(f)
    return []


def save_tasks(tasks: list[dict]):
    MEMORY_DIR.mkdir(exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)


def add_task(description: str, schedule: str = "daily", time_str: str = "09:00") -> dict:
    """
    Ajoute une tâche planifiée.

    schedule: "daily", "hourly", "once"
    time_str: "HH:MM" pour daily, ignoré pour hourly
    """
    tasks = load_tasks()
    task = {
        "id": len(tasks) + 1,
        "description": description,
        "schedule": schedule,
        "time": time_str,
        "active": True,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
    }
    tasks.append(task)
    save_tasks(tasks)
    log_audit("TASK_ADD", description)
    return task


def remove_task(task_id: int) -> bool:
    tasks = load_tasks()
    tasks = [t for t in tasks if t["id"] != task_id]
    save_tasks(tasks)
    return True


class Scheduler:
    """Scheduler qui tourne en arrière-plan dans l'event loop asyncio."""

    def __init__(self, agent_run_fn: Callable[[str], Awaitable[str]], notify_fn: Callable[[str], Awaitable[None]]):
        """
        agent_run_fn: async function(prompt) -> str — exécute une requête agent
        notify_fn: async function(message) -> None — envoie un message Telegram
        """
        self.agent_run = agent_run_fn
        self.notify = notify_fn
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Démarre le scheduler en arrière-plan."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler démarré.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        """Boucle principale du scheduler — vérifie les tâches toutes les 60s."""
        while self._running:
            try:
                await self._check_tasks()
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
            await asyncio.sleep(60)

    async def _check_tasks(self):
        """Vérifie et exécute les tâches dues."""
        now = datetime.now()
        tasks = load_tasks()
        modified = False

        for task in tasks:
            if not task.get("active"):
                continue

            should_run = False

            if task["schedule"] == "daily":
                target_time = dt_time.fromisoformat(task.get("time", "09:00"))
                # Vérifier si c'est l'heure et pas déjà exécuté aujourd'hui
                if (now.hour == target_time.hour and
                    now.minute == target_time.minute):
                    last_run = task.get("last_run")
                    if not last_run or last_run[:10] != now.strftime("%Y-%m-%d"):
                        should_run = True

            elif task["schedule"] == "hourly":
                if now.minute == 0:
                    last_run = task.get("last_run")
                    if not last_run or last_run[:13] != now.strftime("%Y-%m-%dT%H"):
                        should_run = True

            elif task["schedule"] == "once":
                if not task.get("last_run"):
                    should_run = True

            if should_run:
                logger.info(f"Exécution tâche #{task['id']}: {task['description']}")
                log_audit("TASK_RUN", f"#{task['id']}: {task['description']}")

                try:
                    result = await self.agent_run(
                        f"[TÂCHE PLANIFIÉE #{task['id']}] {task['description']}\n\n"
                        f"Exécute cette tâche et sauvegarde un rapport via report_save."
                    )

                    # Notifier l'utilisateur
                    await self.notify(
                        f"📋 **Tâche #{task['id']}** exécutée\n"
                        f"_{task['description']}_\n\n"
                        f"{result[:2000]}"
                    )
                except Exception as e:
                    await self.notify(f"❌ Tâche #{task['id']} échouée : {e}")

                task["last_run"] = now.isoformat()
                modified = True

                # Désactiver les tâches "once"
                if task["schedule"] == "once":
                    task["active"] = False

        if modified:
            save_tasks(tasks)

    async def generate_daily_summary(self) -> str:
        """Génère un résumé de la journée à partir des logs d'audit."""
        audit_file = MEMORY_DIR / "audit.log"
        if not audit_file.exists():
            return "Pas d'activité enregistrée aujourd'hui."

        today = datetime.now().strftime("%Y-%m-%d")
        today_entries = []

        with open(audit_file, "r", encoding="utf-8") as f:
            for line in f:
                if today in line:
                    today_entries.append(line.strip())

        if not today_entries:
            return "Pas d'activité aujourd'hui."

        # Résumé structuré
        summary = f"# Résumé du {today}\n\n"
        summary += f"**{len(today_entries)} actions** enregistrées.\n\n"

        # Compter par type
        types = {}
        for entry in today_entries:
            parts = entry.split("|")
            if len(parts) >= 2:
                action = parts[0].split("]")[-1].strip()
                types[action] = types.get(action, 0) + 1

        summary += "## Actions par type\n"
        for action, count in sorted(types.items(), key=lambda x: -x[1]):
            summary += f"- {action}: {count}\n"

        summary += f"\n## Dernières actions\n"
        for entry in today_entries[-10:]:
            summary += f"- {entry}\n"

        return summary
