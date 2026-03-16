# Clawbot v3 — Context for Claude Code

## Project Overview
Clawbot is an autonomous AI assistant that controls a Windows VM via Telegram.
It uses **Kimi K2 (moonshot-v1-8k)** as its primary agent brain with native function calling,
and **Gemini 2.0 Flash** for vision (screenshot analysis).

## Architecture

```
Telegram User
    │
    ▼
apps/telegram_bot.py        ← Telegram interface (v3, refactored)
    │
    ▼
core/agent.py               ← Agent loop: Kimi K2 + function calling
    │                          Sends messages, receives tool_calls, executes, loops
    │
    ├── core/tools.py        ← Tool definitions (OpenAI function calling format)
    ├── core/executor.py     ← Secure tool execution
    ├── core/security.py     ← Blacklist, confirm, token quota, audit
    ├── core/scheduler.py    ← Scheduled tasks + daily reports
    │
    ├── Kimi K2 (moonshot-v1-8k)  ← Agent brain (planning, tool calls)
    └── Gemini 2.0 Flash           ← Vision only (screenshot analysis)

Legacy (v1, kept for reference):
    apps/clawbot/main.py           ← Old bot with [EXEC]/[VISUAL] tags
    apps/blender_navigator/        ← Blender overlay plugin
```

## Key Files (v3)
- `core/agent.py` — Agent loop with Kimi K2 function calling
- `core/tools.py` — 11 tools: shell_exec, file_*, screenshot, app_launch, git, search, memory, reports
- `core/executor.py` — Secure execution of each tool
- `core/security.py` — Blacklist (destructive cmds), confirm (sensitive cmds), token quota, audit log
- `core/scheduler.py` — Cron-like task scheduler + daily summaries
- `apps/telegram_bot.py` — Telegram interface: /start, /status, /report, /memory, /reset + free text

## Environment
- Windows VM, base dir: `C:\Openclaw`
- Python 3.11+
- `.env` keys: TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, KIMI_API_KEY, GOOGLE_API_KEY

## Running
```bash
cd clawbot
pip install -r requirements.txt
python -m apps.telegram_bot
```

## Security Model
- Token quota: 500k tokens/day (configurable in security.py)
- Blacklist: format, diskpart, bcdedit, registry deletion, etc.
- Confirm via Telegram buttons: rm, del, pip install, git push, exe downloads
- All actions logged to memory/audit.log
- Only TELEGRAM_USER_ID can interact with the bot

## Conventions
- All responses in French
- Agent uses native function calling (no more [EXEC] tag parsing)
- Memory persisted in memory/*.json
- Reports persisted in memory/reports/*.md
