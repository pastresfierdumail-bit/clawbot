# Clawbot v3 — Context for Claude Code

## Project Overview
Clawbot is an autonomous AI assistant that controls a Windows VM via Telegram.
It uses **Kimi K2 (kimi-k2-0905-preview)** as its primary agent brain with native function calling,
and **Gemini 2.0 Flash** for vision (screenshot analysis).

## Architecture

```
Telegram User
    │
    ▼
apps/telegram_bot.py        ← Telegram interface (v3)
    │
    ▼
core/agent.py               ← ReAct agent loop: Kimi K2 + function calling
    │                          - Context pruning (auto-compaction)
    │                          - Reflection loop on consecutive errors
    │                          - Progress notifications to Telegram
    │                          - API retry on transient failures
    │
    ├── core/tools.py        ← Tool definitions (OpenAI function calling format)
    ├── core/executor.py     ← Secure tool execution + adaptive timeouts
    ├── core/security.py     ← Blacklist, confirm, token quota, audit
    ├── core/scheduler.py    ← Scheduled tasks + daily reports (wired to Telegram)
    │
    ├── Kimi K2 (kimi-k2-0905-preview)  ← Agent brain
    └── Gemini 2.0 Flash                ← Vision only (screenshot analysis)

Auto-start:
    scripts/autostart.bat              ← Windows Task Scheduler entry point
    scripts/install_autostart.ps1      ← One-click installer for Task Scheduler

Legacy (v1, kept for reference):
    apps/clawbot/main.py               ← Old bot with [EXEC]/[VISUAL] tags — DO NOT USE
```

## Key Files (v3)
- `core/agent.py` — ReAct agent loop with context pruning, retry, reflection
- `core/tools.py` — 11 tools: shell_exec, file_*, screenshot, app_launch, git, search, memory, reports
- `core/executor.py` — Secure execution with adaptive timeouts + improved search_web
- `core/security.py` — Blacklist (destructive cmds), confirm (sensitive cmds), token quota, audit log
- `core/scheduler.py` — Cron-like task scheduler + daily summaries
- `apps/telegram_bot.py` — Telegram interface: /start, /status, /report, /memory, /tasks, /reset

## Environment
- Windows VM, base dir: `C:\Openclaw`
- Python 3.11+
- `.env` keys: TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, KIMI_API_KEY, GOOGLE_API_KEY

## Running
```bash
cd h:/0perso/clawbot
pip install -r requirements.txt
python -m apps.telegram_bot
```

## Auto-start (boot VM)
```powershell
# Installation one-click :
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

## Security Model
- Token quota: 500k tokens/day (configurable in security.py)
- Blacklist: format, diskpart, bcdedit, registry deletion, etc.
- Confirm via Telegram buttons: rm, del, pip install, git push, exe downloads
- All actions logged to memory/audit.log
- Only TELEGRAM_USER_ID can interact with the bot

## Agent Reliability Features
- **Context pruning**: Auto-compaction when history > 40 messages (keeps 10 recent + summary)
- **Reflection loop**: After 3 consecutive tool errors, injects a "change approach" system message
- **Adaptive timeouts**: pip install → 120s, downloads → 180s, git clone → 120s, default → 30s
- **API retry**: Automatic retry on network/timeout errors with 2s backoff
- **Progress feedback**: Notifies user every 3 iterations during long tasks
- **Max iterations**: 25 (reduced from 50 to prevent runaway loops)

## Conventions
- All responses in French
- Agent uses native function calling (no more [EXEC] tag parsing)
- Memory persisted in memory/*.json
- Reports persisted in memory/reports/*.md
