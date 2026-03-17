"""
core/security.py — Couche de sécurité pour Clawbot.

- Blacklist de commandes destructives
- Confirmation Telegram pour ops sensibles
- Quota de tokens par session/jour
- Audit log complet
"""

import re
import json
import os
import time
import logging
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(__file__).parent.parent / "memory"
AUDIT_LOG = MEMORY_DIR / "audit.log"
QUOTA_FILE = MEMORY_DIR / "quota.json"

# ─── Blacklist : commandes qui DÉTRUISENT la VM ─────────────────

BLACKLIST_PATTERNS = [
    r"format\s+[a-zA-Z]:",          # format C:
    r"diskpart",
    r"Remove-Partition",
    r"rm\s+-rf\s+/",                # rm -rf /
    r"del\s+/s\s+/q\s+C:\\Windows",
    r"reg\s+delete.*HKLM",          # registre système
    r"bcdedit",                      # boot config
    r"net\s+user\s+.*\s+/delete",   # suppression comptes
    r"Clear-Disk",
    r"Initialize-Disk",
    r"Remove-Item\s+-Recurse.*C:\\Windows",
    r"Remove-Item\s+-Recurse.*C:\\Users(?!.*Openclaw)",  # protège Users sauf Openclaw
    r"Stop-Computer",               # shutdown
    r"Restart-Computer",            # restart (sauf demande explicite)
    r"cipher\s+/w",                 # wipe free space
]

# ─── Commandes nécessitant confirmation ──────────────────────────

CONFIRM_PATTERNS = [
    r"Remove-Item",
    r"\brm\b",
    r"\bdel\b",
    r"git\s+push",
    r"git\s+reset\s+--hard",
    r"pip\s+install",
    r"npm\s+install",
    r"choco\s+install",
    r"Invoke-WebRequest.*\.exe",     # téléchargement d'exécutables
    r"Start-Process.*\.exe",
    r"msiexec",
]


def check_command(command: str) -> dict:
    """
    Vérifie une commande shell.

    Returns:
        {"allowed": True}
        {"allowed": False, "reason": "..."}
        {"needs_confirm": True, "reason": "..."}
    """
    cmd_lower = command.lower().strip()

    # Blacklist check
    for pattern in BLACKLIST_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            reason = f"Commande bloquée (destructive) : match '{pattern}'"
            log_audit("BLOCKED", command, reason)
            return {"allowed": False, "reason": reason}

    # Confirm check
    for pattern in CONFIRM_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            reason = f"Commande sensible : match '{pattern}'"
            return {"needs_confirm": True, "reason": reason}

    return {"allowed": True}


def check_path_access(path: str) -> dict:
    """Vérifie qu'un chemin de fichier est autorisé."""
    path_lower = path.lower().replace("/", "\\")

    # Zones interdites
    forbidden = [
        "c:\\windows",
        "c:\\program files",
        "c:\\programdata",
        # Pas d'accès aux données perso hors Openclaw
    ]

    for zone in forbidden:
        if path_lower.startswith(zone):
            return {"allowed": False, "reason": f"Accès interdit : {zone}"}

    return {"allowed": True}


# ─── Quota de tokens ─────────────────────────────────────────────

DEFAULT_DAILY_TOKEN_QUOTA = int(os.getenv("DAILY_TOKEN_QUOTA", "2000000"))  # tokens/jour — configurable via .env

def _load_quota() -> dict:
    if QUOTA_FILE.exists():
        with open(QUOTA_FILE) as f:
            return json.load(f)
    return {}


def _save_quota(data: dict):
    MEMORY_DIR.mkdir(exist_ok=True)
    with open(QUOTA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def track_tokens(tokens_used: int) -> dict:
    """
    Enregistre l'usage de tokens et vérifie le quota.

    Returns:
        {"ok": True, "used": N, "remaining": N}
        {"ok": False, "used": N, "limit": N, "reason": "..."}
    """
    today = str(date.today())
    data = _load_quota()

    if "daily" not in data or data.get("date") != today:
        data = {"date": today, "daily": 0, "sessions": {}}

    data["daily"] += tokens_used
    _save_quota(data)

    remaining = DEFAULT_DAILY_TOKEN_QUOTA - data["daily"]

    if remaining <= 0:
        return {
            "ok": False,
            "used": data["daily"],
            "limit": DEFAULT_DAILY_TOKEN_QUOTA,
            "reason": f"Quota journalier atteint ({DEFAULT_DAILY_TOKEN_QUOTA} tokens)"
        }

    return {"ok": True, "used": data["daily"], "remaining": remaining}


def get_quota_status() -> dict:
    """Retourne le statut actuel du quota."""
    today = str(date.today())
    data = _load_quota()

    if data.get("date") != today:
        return {"used": 0, "limit": DEFAULT_DAILY_TOKEN_QUOTA, "remaining": DEFAULT_DAILY_TOKEN_QUOTA}

    used = data.get("daily", 0)
    return {"used": used, "limit": DEFAULT_DAILY_TOKEN_QUOTA, "remaining": DEFAULT_DAILY_TOKEN_QUOTA - used}


# ─── Audit log ───────────────────────────────────────────────────

def log_audit(action: str, detail: str, result: str = ""):
    """Ajoute une entrée dans le log d'audit."""
    MEMORY_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().isoformat()
    entry = f"[{timestamp}] {action} | {detail}"
    if result:
        entry += f" | {result}"
    entry += "\n"

    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info(f"AUDIT: {action} — {detail}")
