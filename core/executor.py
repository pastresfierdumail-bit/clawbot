"""
core/executor.py — Exécution sécurisée des tools appelés par l'agent.

Chaque fonction correspond à un tool défini dans tools.py.
Toutes les actions passent par la couche security avant exécution.
"""

import json
import os
import subprocess
import base64
import time
import logging
from datetime import datetime
from pathlib import Path

import mss
from PIL import Image

from .security import check_command, check_path_access, log_audit

logger = logging.getLogger(__name__)

BASE_DIR = os.getenv("CLAWBOT_BASE_DIR", r"C:\Openclaw")
MEMORY_DIR = Path(__file__).parent.parent / "memory"
REPORTS_DIR = MEMORY_DIR / "reports"

# ─── Gemini client (lazy init pour vision) ────────────────────────

_gemini_client = None

def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if api_key:
            from google import genai
            _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


# ─── App launch paths ────────────────────────────────────────────

APP_PATHS = {
    "blender": r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    "vscode": "code",
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "unreal": r"C:\Program Files\Epic Games\UE_5.4\Engine\Binaries\Win64\UnrealEditor.exe",
    "n8n": "n8n",
    "notion": r"C:\Users\%USERNAME%\AppData\Local\Programs\Notion\Notion.exe",
}


# ─── Tool implementations ────────────────────────────────────────

async def execute_tool(tool_name: str, args: dict, confirm_callback=None) -> str:
    """
    Exécute un tool et retourne le résultat sous forme de string.

    confirm_callback: async function(message) -> bool
        Appelée quand une action nécessite confirmation.
        Si None, les actions nécessitant confirmation sont bloquées.
    """
    log_audit("TOOL_CALL", f"{tool_name}({json.dumps(args, ensure_ascii=False)[:200]})")

    try:
        if tool_name == "shell_exec":
            return await _shell_exec(args, confirm_callback)
        elif tool_name == "file_read":
            return _file_read(args)
        elif tool_name == "file_write":
            return _file_write(args)
        elif tool_name == "file_list":
            return _file_list(args)
        elif tool_name == "screenshot":
            return await _screenshot(args)
        elif tool_name == "app_launch":
            return await _app_launch(args, confirm_callback)
        elif tool_name == "git_command":
            return await _git_command(args, confirm_callback)
        elif tool_name == "search_web":
            return await _search_web(args)
        elif tool_name == "memory_save":
            return _memory_save(args)
        elif tool_name == "memory_recall":
            return _memory_recall(args)
        elif tool_name == "report_save":
            return _report_save(args)
        else:
            return f"Erreur : tool '{tool_name}' inconnu."
    except Exception as e:
        error_msg = f"Erreur lors de l'exécution de {tool_name}: {str(e)}"
        log_audit("TOOL_ERROR", error_msg)
        return error_msg


async def _shell_exec(args: dict, confirm_callback) -> str:
    command = args["command"]
    working_dir = args.get("working_dir", BASE_DIR)
    timeout = min(args.get("timeout", 30), 300)

    # Security check
    check = check_command(command)
    if check.get("allowed") is False:
        return f"⛔ {check['reason']}"

    if check.get("needs_confirm"):
        if confirm_callback is None:
            return f"⚠️ Action bloquée (pas de confirmation possible) : {check['reason']}"
        confirmed = await confirm_callback(
            f"⚠️ Commande sensible détectée :\n`{command}`\n\n{check['reason']}\n\nConfirmer ? (oui/non)"
        )
        if not confirmed:
            log_audit("DENIED", command, "User refused")
            return "❌ Action annulée par l'utilisateur."

    try:
        result = subprocess.run(
            ["powershell", "-Command", command],
            capture_output=True, text=True,
            cwd=working_dir, timeout=timeout
        )
        output = result.stdout.strip()
        error = result.stderr.strip()

        log_audit("EXEC", command, f"rc={result.returncode}")

        if result.returncode != 0:
            return f"❌ Erreur (code {result.returncode}):\n{error}\n{output}"
        return output if output else "✅ Commande exécutée (pas de sortie)."
    except subprocess.TimeoutExpired:
        return f"⏰ Timeout après {timeout}s."
    except Exception as e:
        return f"❌ Erreur: {e}"


def _file_read(args: dict) -> str:
    path = args["path"]
    max_lines = args.get("max_lines", 200)

    check = check_path_access(path)
    if not check["allowed"]:
        return f"⛔ {check['reason']}"

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[:max_lines]
        content = "".join(lines)
        if len(lines) == max_lines:
            content += f"\n... (tronqué à {max_lines} lignes)"
        log_audit("FILE_READ", path)
        return content if content else "(fichier vide)"
    except FileNotFoundError:
        return f"❌ Fichier non trouvé : {path}"
    except Exception as e:
        return f"❌ Erreur lecture : {e}"


def _file_write(args: dict) -> str:
    path = args["path"]
    content = args["content"]

    check = check_path_access(path)
    if not check["allowed"]:
        return f"⛔ {check['reason']}"

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log_audit("FILE_WRITE", path, f"{len(content)} chars")
        return f"✅ Fichier écrit : {path} ({len(content)} caractères)"
    except Exception as e:
        return f"❌ Erreur écriture : {e}"


def _file_list(args: dict) -> str:
    path = args["path"]
    recursive = args.get("recursive", False)

    check = check_path_access(path)
    if not check["allowed"]:
        return f"⛔ {check['reason']}"

    try:
        entries = []
        if recursive:
            for root, dirs, files in os.walk(path):
                depth = root.replace(path, "").count(os.sep)
                if depth >= 3:
                    dirs.clear()
                    continue
                indent = "  " * depth
                entries.append(f"{indent}📁 {os.path.basename(root)}/")
                for f in files[:20]:
                    entries.append(f"{indent}  📄 {f}")
                if len(files) > 20:
                    entries.append(f"{indent}  ... (+{len(files)-20} fichiers)")
        else:
            for item in sorted(os.listdir(path)):
                full = os.path.join(path, item)
                prefix = "📁" if os.path.isdir(full) else "📄"
                entries.append(f"{prefix} {item}")

        log_audit("FILE_LIST", path)
        return "\n".join(entries) if entries else "(dossier vide)"
    except Exception as e:
        return f"❌ Erreur : {e}"


async def _screenshot(args: dict) -> str:
    analyze = args.get("analyze", True)
    prompt = args.get("prompt", "Décris ce que tu vois à l'écran, en français, de manière concise.")

    try:
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # Redimensionner
        if img.width > 1280:
            ratio = 1280 / img.width
            img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)

        # Sauvegarder temporairement
        tmp_path = os.path.join(BASE_DIR, "screenshot_tmp.png")
        img.save(tmp_path)

        log_audit("SCREENSHOT", f"analyze={analyze}")

        if not analyze:
            return f"📸 Screenshot sauvegardé : {tmp_path}"

        # Analyse Gemini Vision
        gemini = _get_gemini()
        if not gemini:
            return f"📸 Screenshot pris mais Gemini non configuré (pas de GOOGLE_API_KEY)."

        with open(tmp_path, "rb") as f:
            img_data = f.read()

        from google.genai import types
        response = gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(data=img_data, mime_type="image/png"),
                prompt,
            ]
        )
        return f"📸 Analyse de l'écran :\n{response.text}"

    except Exception as e:
        return f"❌ Erreur screenshot : {e}"


async def _app_launch(args: dict, confirm_callback) -> str:
    app_name = args["app_name"]
    app_args = args.get("args", "")

    path = APP_PATHS.get(app_name)
    if not path:
        return f"❌ Application inconnue : {app_name}"

    path = os.path.expandvars(path)
    cmd = f'Start-Process "{path}"'
    if app_args:
        cmd += f' -ArgumentList "{app_args}"'

    if confirm_callback:
        confirmed = await confirm_callback(f"🚀 Lancer {app_name} ?\n`{cmd}`")
        if not confirmed:
            return "❌ Lancement annulé."

    try:
        subprocess.Popen(
            ["powershell", "-Command", cmd],
            cwd=BASE_DIR
        )
        log_audit("APP_LAUNCH", f"{app_name} {app_args}")
        return f"✅ {app_name} lancé."
    except Exception as e:
        return f"❌ Erreur lancement : {e}"


async def _git_command(args: dict, confirm_callback) -> str:
    command = args["command"]
    repo_path = args["repo_path"]

    # Push nécessite confirmation
    if "push" in command or "reset --hard" in command:
        if confirm_callback:
            confirmed = await confirm_callback(f"⚠️ Git : `git {command}` dans {repo_path}\nConfirmer ?")
            if not confirmed:
                return "❌ Annulé."
        else:
            return "⚠️ git push/reset nécessite confirmation."

    try:
        result = subprocess.run(
            ["git"] + command.split(),
            capture_output=True, text=True,
            cwd=repo_path, timeout=30
        )
        log_audit("GIT", f"git {command} @ {repo_path}", f"rc={result.returncode}")
        output = result.stdout.strip() + result.stderr.strip()
        return output if output else "✅ Done."
    except Exception as e:
        return f"❌ Git erreur : {e}"


async def _search_web(args: dict) -> str:
    """Recherche web via DuckDuckGo (pas de clé API requise)."""
    query = args["query"]
    num = min(args.get("num_results", 5), 10)

    try:
        # Utilise PowerShell + Invoke-WebRequest pour DuckDuckGo Lite
        cmd = f'(Invoke-WebRequest -Uri "https://lite.duckduckgo.com/lite/?q={query}" -UseBasicParsing).Content'
        result = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return f"❌ Erreur recherche : {result.stderr}"

        # Parse basique des résultats DDG Lite
        import re
        content = result.stdout
        # Extraire les liens et descriptions
        links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>', content)
        snippets = re.findall(r'<td class="result-snippet">(.*?)</td>', content, re.DOTALL)

        results = []
        for i, (url, title) in enumerate(links[:num]):
            title = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
            results.append(f"**{title}**\n{url}\n{snippet}\n")

        log_audit("SEARCH", query, f"{len(results)} results")

        if not results:
            return f"🔍 Pas de résultats pour : {query}"
        return f"🔍 Résultats pour '{query}':\n\n" + "\n".join(results)

    except Exception as e:
        return f"❌ Erreur recherche : {e}"


# ─── Memory tools ─────────────────────────────────────────────────

def _memory_save(args: dict) -> str:
    category = args["category"]
    key = args["key"]
    content = args["content"]

    cat_file = MEMORY_DIR / f"{category}.json"
    MEMORY_DIR.mkdir(exist_ok=True)

    data = {}
    if cat_file.exists():
        with open(cat_file) as f:
            data = json.load(f)

    data[key] = {
        "content": content,
        "updated_at": datetime.now().isoformat(),
    }

    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    log_audit("MEMORY_SAVE", f"{category}/{key}")
    return f"💾 Mémorisé dans {category}/{key}."


def _memory_recall(args: dict) -> str:
    category = args["category"]
    query = args.get("query", "").lower()

    MEMORY_DIR.mkdir(exist_ok=True)

    categories = [category] if category != "all" else ["projects", "research", "tasks", "preferences", "notes"]
    results = []

    for cat in categories:
        cat_file = MEMORY_DIR / f"{cat}.json"
        if not cat_file.exists():
            continue
        with open(cat_file) as f:
            data = json.load(f)
        for key, val in data.items():
            content = val["content"] if isinstance(val, dict) else str(val)
            if not query or query in key.lower() or query in content.lower():
                results.append(f"**[{cat}/{key}]** {content}")

    if not results:
        return "🔍 Rien trouvé en mémoire."
    return "🧠 Mémoire :\n\n" + "\n\n".join(results)


def _report_save(args: dict) -> str:
    report_type = args["report_type"]
    title = args["title"]
    content = args["content"]

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filename = f"{timestamp}_{report_type}.md"

    report = f"# {title}\n\n**Type:** {report_type}\n**Date:** {datetime.now().isoformat()}\n\n{content}"

    with open(REPORTS_DIR / filename, "w", encoding="utf-8") as f:
        f.write(report)

    log_audit("REPORT", f"{report_type}: {title}")
    return f"📝 Rapport sauvegardé : {filename}"
