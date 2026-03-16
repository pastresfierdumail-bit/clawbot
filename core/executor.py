"""
core/executor.py — Exécution sécurisée des tools appelés par l'agent.

Chaque fonction correspond à un tool défini dans tools.py.
Toutes les actions passent par la couche security avant exécution.
"""

import json
import os
import subprocess
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import mss
from PIL import Image

from .security import check_command, check_path_access, log_audit

logger = logging.getLogger(__name__)

BASE_DIR = os.getenv("CLAWBOT_BASE_DIR", r"C:\Openclaw")
MEMORY_DIR = Path(__file__).parent.parent / "memory"
REPORTS_DIR = MEMORY_DIR / "reports"

# ─── Timeouts adaptatifs ─────────────────────────────────────────

TIMEOUT_PROFILES = {
    "default": 30,
    "install": 120,     # pip install, npm install, choco
    "download": 180,    # curl, Invoke-WebRequest
    "build": 120,       # dotnet build, cmake, msbuild
    "git_clone": 120,   # git clone
    "long": 300,        # max autorisé
}

def _get_adaptive_timeout(command: str, requested_timeout: int = 30) -> int:
    """Détermine le timeout optimal selon la commande."""
    cmd_lower = command.lower()
    if any(kw in cmd_lower for kw in ["pip install", "npm install", "choco install", "conda install"]):
        return max(requested_timeout, TIMEOUT_PROFILES["install"])
    if any(kw in cmd_lower for kw in ["invoke-webrequest", "curl", "wget", "download"]):
        return max(requested_timeout, TIMEOUT_PROFILES["download"])
    if any(kw in cmd_lower for kw in ["dotnet build", "cmake", "msbuild", "cargo build"]):
        return max(requested_timeout, TIMEOUT_PROFILES["build"])
    if "git clone" in cmd_lower:
        return max(requested_timeout, TIMEOUT_PROFILES["git_clone"])
    return min(max(requested_timeout, TIMEOUT_PROFILES["default"]), TIMEOUT_PROFILES["long"])


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
    """
    log_audit("TOOL_CALL", f"{tool_name}({json.dumps(args, ensure_ascii=False)[:200]})")

    try:
        if tool_name == "shell_exec":
            return await _shell_exec(args, confirm_callback)
        elif tool_name == "file_read":
            return await asyncio.to_thread(_file_read, args)
        elif tool_name == "file_write":
            return await asyncio.to_thread(_file_write, args)
        elif tool_name == "file_list":
            return await asyncio.to_thread(_file_list, args)
        elif tool_name == "screenshot":
            return await _screenshot(args)
        elif tool_name == "app_launch":
            return await _app_launch(args, confirm_callback)
        elif tool_name == "git_command":
            return await _git_command(args, confirm_callback)
        elif tool_name == "search_web":
            return await _search_web(args)
        elif tool_name == "memory_save":
            return await asyncio.to_thread(_memory_save, args)
        elif tool_name == "memory_recall":
            return await asyncio.to_thread(_memory_recall, args)
        elif tool_name == "report_save":
            return await asyncio.to_thread(_report_save, args)
        elif tool_name == "schedule_task":
            return await asyncio.to_thread(_schedule_task, args)
        elif tool_name == "task_list":
            return await asyncio.to_thread(_task_list, args)
        elif tool_name == "kb_update":
            return await asyncio.to_thread(_kb_update, args)
        elif tool_name == "kb_query":
            return await asyncio.to_thread(_kb_query, args)
        else:
            return f"❌ Tool '{tool_name}' inconnu. Tools disponibles : shell_exec, file_read, file_write, file_list, screenshot, app_launch, git_command, search_web, memory_save, memory_recall, report_save"
    except Exception as e:
        error_msg = f"❌ Erreur lors de l'exécution de {tool_name}: {str(e)}"
        log_audit("TOOL_ERROR", error_msg)
        return error_msg


async def _shell_exec(args: dict, confirm_callback) -> str:
    command = args.get("command", "")
    if not command:
        return "❌ Paramètre 'command' manquant."

    working_dir = args.get("working_dir", BASE_DIR)
    requested_timeout = args.get("timeout", 30)
    timeout = _get_adaptive_timeout(command, requested_timeout)

    # Security check
    check = check_command(command)
    if check.get("allowed") is False:
        return f"⛔ {check['reason']}"

    if check.get("needs_confirm"):
        if confirm_callback is None:
            return f"⚠️ Action bloquée (pas de confirmation possible) : {check['reason']}"
        confirmed = await confirm_callback(
            f"⚠️ Commande sensible détectée :\n`{command}`\n\n{check['reason']}\n\nConfirmer ?"
        )
        if not confirmed:
            log_audit("DENIED", command, "User refused")
            return "❌ Action annulée par l'utilisateur."

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["powershell", "-Command", command],
            capture_output=True, text=True,
            cwd=working_dir, timeout=timeout
        )
        output = result.stdout.strip()
        error = result.stderr.strip()

        log_audit("EXEC", command[:200], f"rc={result.returncode}, timeout={timeout}s")

        if result.returncode != 0:
            # Message d'erreur enrichi pour aider l'agent à comprendre
            return (
                f"❌ Erreur (code {result.returncode}):\n"
                f"Commande : {command[:200]}\n"
                f"Stderr : {error[:500]}\n"
                f"Stdout : {output[:500]}"
            )
        return output if output else "✅ Commande exécutée (pas de sortie)."
    except subprocess.TimeoutExpired:
        return f"⏰ Timeout après {timeout}s. Pour les commandes longues, augmente le paramètre timeout (max 300)."
    except Exception as e:
        return f"❌ Erreur système: {e}"


def _file_read(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return "❌ Paramètre 'path' manquant."
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
        return f"❌ Fichier non trouvé : {path}. Utilise file_list pour vérifier les fichiers disponibles."
    except Exception as e:
        return f"❌ Erreur lecture : {e}"


def _file_write(args: dict) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    if not path:
        return "❌ Paramètre 'path' manquant."

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
    path = args.get("path", "")
    if not path:
        return "❌ Paramètre 'path' manquant."
    recursive = args.get("recursive", False)

    check = check_path_access(path)
    if not check["allowed"]:
        return f"⛔ {check['reason']}"

    if not os.path.exists(path):
        return f"❌ Chemin introuvable : {path}"

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

        if img.width > 1280:
            ratio = 1280 / img.width
            img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)

        tmp_path = os.path.join(BASE_DIR, "screenshot_tmp.png")
        img.save(tmp_path)

        log_audit("SCREENSHOT", f"analyze={analyze}")

        if not analyze:
            return f"📸 Screenshot sauvegardé : {tmp_path}"

        gemini = _get_gemini()
        if not gemini:
            return "📸 Screenshot pris mais Gemini non configuré (pas de GOOGLE_API_KEY)."

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
    app_name = args.get("app_name", "")
    if not app_name:
        return "❌ Paramètre 'app_name' manquant."
    app_args = args.get("args", "")

    path = APP_PATHS.get(app_name)
    if not path:
        available = ", ".join(APP_PATHS.keys())
        return f"❌ Application inconnue : {app_name}. Disponibles : {available}"

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
    command = args.get("command", "")
    repo_path = args.get("repo_path", "")
    if not command or not repo_path:
        return "❌ Paramètres 'command' et 'repo_path' requis."

    if not os.path.exists(repo_path):
        return f"❌ Répertoire introuvable : {repo_path}"

    # Push/reset nécessitent confirmation
    if "push" in command or "reset --hard" in command:
        if confirm_callback:
            confirmed = await confirm_callback(f"⚠️ Git : `git {command}` dans {repo_path}\nConfirmer ?")
            if not confirmed:
                return "❌ Annulé."
        else:
            return "⚠️ git push/reset nécessite confirmation."

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git"] + command.split(),
            capture_output=True, text=True,
            cwd=repo_path, timeout=60
        )
        log_audit("GIT", f"git {command} @ {repo_path}", f"rc={result.returncode}")
        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode != 0:
            return f"❌ Git erreur (code {result.returncode}):\n{error}\n{output}"
        return (output + "\n" + error).strip() if (output or error) else "✅ Done."
    except subprocess.TimeoutExpired:
        return "⏰ Git timeout (60s). La commande prend trop de temps."
    except Exception as e:
        return f"❌ Git erreur : {e}"


async def _search_web(args: dict) -> str:
    """Recherche web via DuckDuckGo HTML."""
    query = args.get("query", "")
    if not query:
        return "❌ Paramètre 'query' manquant."
    num = min(args.get("num_results", 5), 10)

    try:
<<<<<<< HEAD
        # Utilise PowerShell + Invoke-WebRequest pour DuckDuckGo Lite
        cmd = f'(Invoke-WebRequest -Uri "https://lite.duckduckgo.com/lite/?q={query}" -UseBasicParsing).Content'
        result = await asyncio.to_thread(
            subprocess.run,
=======
        import re

        # Méthode 1 : DuckDuckGo HTML (plus fiable que Lite)
        encoded_query = quote_plus(query)
        cmd = (
            f'$ProgressPreference="SilentlyContinue"; '
            f'(Invoke-WebRequest -Uri "https://html.duckduckgo.com/html/?q={encoded_query}" '
            f'-UseBasicParsing -TimeoutSec 10).Content'
        )
        result = subprocess.run(
>>>>>>> 65e15227f670b7ed6418beb63a3d01eb4021d908
            ["powershell", "-Command", cmd],
            capture_output=True, text=True, timeout=20
        )

        if result.returncode != 0:
            # Fallback : essayer avec lite
            cmd_lite = (
                f'$ProgressPreference="SilentlyContinue"; '
                f'(Invoke-WebRequest -Uri "https://lite.duckduckgo.com/lite/?q={encoded_query}" '
                f'-UseBasicParsing -TimeoutSec 10).Content'
            )
            result = subprocess.run(
                ["powershell", "-Command", cmd_lite],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode != 0:
                return f"❌ Recherche échouée. Erreur : {result.stderr[:200]}"

        content = result.stdout

        # Parse des résultats DuckDuckGo HTML
        # Extraire les blocs de résultats
        results = []

        # Pattern pour les liens de résultats
        links = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            content, re.DOTALL
        )
        snippets = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            content, re.DOTALL
        )

        # Fallback : pattern alternatif
        if not links:
            links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>', content)
            snippets = re.findall(r'<td class="result-snippet">(.*?)</td>', content, re.DOTALL)

        for i, (url, title) in enumerate(links[:num]):
            title = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
            if title and url:
                results.append(f"**{title}**\n{url}\n{snippet}\n")

        log_audit("SEARCH", query, f"{len(results)} results")

        if not results:
            return f"🔍 Pas de résultats exploitables pour : {query}. Essaie de reformuler."
        return f"🔍 Résultats pour '{query}':\n\n" + "\n".join(results)

    except subprocess.TimeoutExpired:
        return "⏰ Recherche timeout. Le réseau est peut-être lent."
    except Exception as e:
        return f"❌ Erreur recherche : {e}"


# ─── Memory tools ─────────────────────────────────────────────────

def _memory_save(args: dict) -> str:
    category = args.get("category", "")
    key = args.get("key", "")
    content = args.get("content", "")
    if not category or not key or not content:
        return "❌ Paramètres 'category', 'key' et 'content' requis."

    cat_file = MEMORY_DIR / f"{category}.json"
    MEMORY_DIR.mkdir(exist_ok=True)

    data = {}
    if cat_file.exists():
        try:
            with open(cat_file) as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = {}

    data[key] = {
        "content": content,
        "updated_at": datetime.now().isoformat(),
    }

    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    log_audit("MEMORY_SAVE", f"{category}/{key}")
    return f"💾 Mémorisé dans {category}/{key}."


def _memory_recall(args: dict) -> str:
    category = args.get("category", "all")
    query = args.get("query", "").lower()

    MEMORY_DIR.mkdir(exist_ok=True)

    categories = [category] if category != "all" else ["projects", "research", "tasks", "preferences", "notes"]
    results = []

    for cat in categories:
        cat_file = MEMORY_DIR / f"{cat}.json"
        if not cat_file.exists():
            continue
        try:
            with open(cat_file) as f:
                data = json.load(f)
        except json.JSONDecodeError:
            continue
        for key, val in data.items():
            content = val["content"] if isinstance(val, dict) else str(val)
            if not query or query in key.lower() or query in content.lower():
                results.append(f"**[{cat}/{key}]** {content}")

    if not results:
        return "🔍 Rien trouvé en mémoire."
    return "🧠 Mémoire :\n\n" + "\n\n".join(results)


def _report_save(args: dict) -> str:
    report_type = args.get("report_type", "")
    title = args.get("title", "")
    content = args.get("content", "")
    if not report_type or not title or not content:
        return "❌ Paramètres 'report_type', 'title' et 'content' requis."

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filename = f"{timestamp}_{report_type}.md"

    report = f"# {title}\n\n**Type:** {report_type}\n**Date:** {datetime.now().isoformat()}\n\n{content}"

    with open(REPORTS_DIR / filename, "w", encoding="utf-8") as f:
        f.write(report)

    log_audit("REPORT", f"{report_type}: {title}")
    return f"📝 Rapport sauvegardé : {filename}"


def _schedule_task(args: dict) -> str:
    from .scheduler import add_task
    desc = args["description"]
    sched = args["schedule"]
    time_str = args.get("time", "09:00")
    
    task = add_task(desc, sched, time_str)
    return f"📅 Tâche planifiée #{task['id']} : {desc} ({sched} at {time_str})"


def _task_list(args: dict) -> str:
    from .scheduler import load_tasks
    tasks = load_tasks()
    if not tasks:
        return "📅 Aucune tâche planifiée."
    
    res = "📅 **Tâches planifiées :**\n"
    for t in tasks:
        status = "✅ Active" if t["active"] else "❌ Inactive"
        res += f"- #{t['id']} : {t['description']} ({t['schedule']}) [{status}]\n"
    return res


def _kb_update(args: dict) -> str:
    task_name = args["task_name"]
    theme = args["theme"]
    content = args["content"]
    
    kb_file = MEMORY_DIR / "kb.json"
    MEMORY_DIR.mkdir(exist_ok=True)
    
    kb = {"tasks": {}}
    if kb_file.exists():
        with open(kb_file, "r", encoding="utf-8") as f:
            kb = json.load(f)
            
    if "tasks" not in kb: kb["tasks"] = {}
    if task_name not in kb["tasks"]:
        kb["tasks"][task_name] = {"description": "", "themes": {}}
    
    if theme not in kb["tasks"][task_name]["themes"]:
        kb["tasks"][task_name]["themes"][theme] = {"items": []}
        
    kb["tasks"][task_name]["themes"][theme]["items"].append({
        "content": content,
        "timestamp": datetime.now().isoformat()
    })
    
    # Limiter à 50 items par thème pour éviter la surcharge
    kb["tasks"][task_name]["themes"][theme]["items"] = kb["tasks"][task_name]["themes"][theme]["items"][-50:]
    
    with open(kb_file, "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=2, ensure_ascii=False)
        
    log_audit("KB_UPDATE", f"{task_name}/{theme}")
    return f"🧠 Savoir enregistré dans {task_name} > {theme}."


def _kb_query(args: dict) -> str:
    task_filter = args.get("task_name")
    query = args["query"].lower()
    
    kb_file = MEMORY_DIR / "kb.json"
    if not kb_file.exists():
        return "🧠 La base de connaissances est vide."
        
    with open(kb_file, "r", encoding="utf-8") as f:
        kb = json.load(f)
        
    results = []
    tasks = kb.get("tasks", {})
    
    for t_name, t_data in tasks.items():
        if task_filter and task_filter.lower() not in t_name.lower():
            continue
            
        for theme, theme_data in t_data.get("themes", {}).items():
            for item in theme_data.get("items", []):
                if query in item["content"].lower() or query in theme.lower() or query in t_name.lower():
                    results.append(f"**[{t_name} / {theme}]** ({item['timestamp'][:10]})\n{item['content']}")
                    
    if not results:
        return f"🔍 Aucun savoir trouvé pour '{query}'."
        
    # Retourner les 10 plus récents/pertinents
    res_text = "\n\n---\n\n".join(results[-10:])
    return f"🧠 **Savoirs trouvés :**\n\n{res_text}"
