"""
apps/telegram_bot.py — Interface Telegram pour Clawbot v3.

Commandes :
  /start          → message d'accueil
  /status         → quota tokens + état de la VM
  /report [type]  → consulter les rapports (daily, research, all)
  /memory [query] → consulter la mémoire
  /reset          → remet à zéro la conversation
  /tasks          → voir les tâches planifiées
  (texte libre)   → envoyé à l'agent Kimi K2

Lancer :
  cd h:/0perso/clawbot
  python -m apps.telegram_bot
"""

import os
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Ajouter le parent pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent import create_agent
from core.security import get_quota_status, log_audit
<<<<<<< HEAD
from core.scheduler import Scheduler
=======
from core.scheduler import Scheduler, load_tasks, add_task, remove_task
>>>>>>> 65e15227f670b7ed6418beb63a3d01eb4021d908

# ─── Config ───────────────────────────────────────────────────────

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "").strip()
AUTHORIZED_USER_ID = os.getenv("TELEGRAM_USER_ID", "").strip()

if not TELEGRAM_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN manquant dans .env")
    sys.exit(1)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Agent singleton ─────────────────────────────────────────────

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip() or KIMI_API_KEY
LLM_MODEL = os.getenv("LLM_MODEL", "").strip() or "kimi-k2-0905-preview"

if LLM_PROVIDER == "onemin":
    from core.onemin_client import AsyncOneMinClient
    if not LLM_API_KEY:
        print("❌ LLM_API_KEY manquant dans .env (requis pour 1min.ai)")
        sys.exit(1)
    client = AsyncOneMinClient(api_key=LLM_API_KEY)
    logger.info(f"LLM: provider=1min.ai, model={LLM_MODEL}")
else:
    from openai import AsyncOpenAI
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip() or "https://api.moonshot.ai/v1"
    if not LLM_API_KEY:
        print("❌ KIMI_API_KEY ou LLM_API_KEY manquant dans .env")
        sys.exit(1)
    client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    logger.info(f"LLM: provider=openai, model={LLM_MODEL}, base_url={LLM_BASE_URL[:40]}...")

agent = create_agent(client=client, model=LLM_MODEL)

# Store pour les confirmations en attente
_pending_confirms: dict[int, asyncio.Future] = {}

MEMORY_DIR = Path(__file__).parent.parent / "memory"
REPORTS_DIR = MEMORY_DIR / "reports"

# Référence globale au bot pour le scheduler
_bot_instance = None
_scheduler: Scheduler | None = None


# ─── Auth middleware ──────────────────────────────────────────────

def authorized(func):
    """Décorateur : vérifie que l'utilisateur est autorisé."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        logger.info(f"Auth check: user_id={user_id} (Authorized: {AUTHORIZED_USER_ID})")
        if AUTHORIZED_USER_ID and user_id != AUTHORIZED_USER_ID:
            await update.message.reply_text("⛔ Accès non autorisé.")
            log_audit("UNAUTHORIZED", f"user_id={user_id}")
            return
        return await func(update, context)
    return wrapper


# ─── Confirmation callback ───────────────────────────────────────

async def confirm_callback_factory(chat_id: int, bot):
    """Crée un callback de confirmation pour l'agent."""
    async def confirm(message: str) -> bool:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Oui", callback_data="confirm_yes"),
                InlineKeyboardButton("❌ Non", callback_data="confirm_no"),
            ]
        ])
        await bot.send_message(chat_id=chat_id, text=message, reply_markup=keyboard)

        future = asyncio.get_event_loop().create_future()
        _pending_confirms[chat_id] = future

        try:
            result = await asyncio.wait_for(future, timeout=60)
            return result
        except asyncio.TimeoutError:
            await bot.send_message(chat_id=chat_id, text="⏰ Timeout — action annulée.")
            return False
        finally:
            _pending_confirms.pop(chat_id, None)

    return confirm


async def handle_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les clics sur les boutons de confirmation."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    future = _pending_confirms.get(chat_id)

    if future and not future.done():
        confirmed = query.data == "confirm_yes"
        future.set_result(confirmed)
        status = "✅ Confirmé" if confirmed else "❌ Refusé"
        await query.edit_message_text(text=f"{query.message.text}\n\n→ {status}")
    else:
        await query.edit_message_text(text="(expirée)")


# ─── Progress callback ───────────────────────────────────────────

async def progress_callback_factory(chat_id: int, bot):
    """Crée un callback de progression pour l'agent."""
    async def notify_progress(message: str):
        try:
            await bot.send_message(chat_id=chat_id, text=message)
        except Exception:
            pass
    return notify_progress


# ─── Handlers ─────────────────────────────────────────────────────

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Clawbot v3** — Assistant autonome\n\n"
        "Envoie-moi ce que tu veux faire :\n"
        "• Créer un projet\n"
        "• Rechercher sur le web\n"
        "• Piloter Blender/VS Code/Chrome\n"
        "• N'importe quelle tâche sur la VM\n\n"
        "Commandes :\n"
        "/status — quota & état\n"
        "/report — consulter les rapports\n"
        "/memory — consulter la mémoire\n"
        "/tasks — tâches planifiées\n"
        "/reset — nouvelle conversation",
        parse_mode="Markdown",
    )


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quota = get_quota_status()
    pct = int((quota["used"] / quota["limit"]) * 100) if quota["limit"] > 0 else 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)

    scheduler_status = "🟢 Actif" if _scheduler and _scheduler._running else "🔴 Inactif"
    tasks = load_tasks()
    active_tasks = sum(1 for t in tasks if t.get("active"))

    msg = (
        f"📊 **Statut Clawbot**\n\n"
        f"**Tokens aujourd'hui :**\n"
        f"`[{bar}]` {pct}%\n"
        f"{quota['used']:,} / {quota['limit']:,}\n\n"
        f"**Conversation :** {len(agent.conversation_history)} messages\n"
        f"**Scheduler :** {scheduler_status}\n"
        f"**Tâches actives :** {active_tasks}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


@authorized
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Consulter les rapports sauvegardés."""
    args = context.args
    filter_type = args[0] if args else "all"

    if not REPORTS_DIR.exists():
        await update.message.reply_text("📝 Aucun rapport pour le moment.")
        return

    reports = sorted(REPORTS_DIR.glob("*.md"), reverse=True)[:10]

    if filter_type != "all":
        reports = [r for r in reports if filter_type in r.name]

    if not reports:
        await update.message.reply_text(f"📝 Aucun rapport de type '{filter_type}'.")
        return

    msg = "📝 **Derniers rapports :**\n\n"
    for r in reports[:5]:
        content = r.read_text(encoding="utf-8")[:300]
        msg += f"**{r.stem}**\n{content}\n\n---\n\n"

    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n... (tronqué)"

    await update.message.reply_text(msg, parse_mode="Markdown")


@authorized
async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Consulter la mémoire persistante."""
    query = " ".join(context.args) if context.args else ""
    from core.executor import _memory_recall
    result = _memory_recall({"category": "all", "query": query})

    if len(result) > 4000:
        result = result[:4000] + "\n\n... (tronqué)"

    await update.message.reply_text(result, parse_mode="Markdown")


@authorized
async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Voir et gérer les tâches planifiées."""
    tasks = load_tasks()

    if not tasks:
        await update.message.reply_text(
            "📋 Aucune tâche planifiée.\n\n"
            "Pour ajouter une tâche, dis-moi simplement ce que tu veux automatiser, "
            "par exemple : \"Fais une veille technologique tous les jours à 9h\""
        )
        return

    msg = "📋 **Tâches planifiées :**\n\n"
    for t in tasks:
        status = "🟢" if t.get("active") else "⚪"
        last = t.get("last_run", "jamais")
        if last and last != "jamais":
            last = last[:16].replace("T", " ")
        msg += (
            f"{status} **#{t['id']}** — {t['description']}\n"
            f"   ⏰ {t['schedule']} ({t.get('time', '')})\n"
            f"   Dernière exécution : {last}\n\n"
        )

    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n... (tronqué)"

    await update.message.reply_text(msg, parse_mode="Markdown")


@authorized
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent.reset_conversation()
    await update.message.reply_text("🔄 Conversation réinitialisée.")


@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Message libre → envoyé à l'agent Kimi K2."""
    user_text = update.message.text
    chat_id = update.message.chat_id
    logger.info(f"Incoming message from {chat_id}: {user_text}")

    # Indicateur de traitement
    thinking = await update.message.reply_text("🧠 Réflexion en cours...")

    # Créer les callbacks
    confirm = await confirm_callback_factory(chat_id, context.bot)
    agent.set_confirm_callback(confirm)

    progress = await progress_callback_factory(chat_id, context.bot)
    agent.set_progress_callback(progress)

    try:
<<<<<<< HEAD
        # Lancer l'agent (timeout de 10 min pour permettre l'autonomie)
        response = await asyncio.wait_for(agent.run(user_text), timeout=600)
=======
        response = await agent.run(user_text)
>>>>>>> 65e15227f670b7ed6418beb63a3d01eb4021d908

        # Supprimer le message "Réflexion..."
        try:
            await thinking.delete()
        except Exception:
            pass

        # Découper si la réponse est trop longue pour Telegram (4096 chars max)
        if len(response) <= 4000:
            await update.message.reply_text(response)
        else:
            chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk)

    except asyncio.TimeoutError:
        await thinking.delete()
        await update.message.reply_text(
            "⏰ **Le délai de réflexion a été dépassé (10 min).**\n\n"
            "La tâche est peut-être trop complexe ou l'IA est bloquée. "
            "Vous pouvez essayer de diviser votre demande ou utiliser `/reset` si le bot semble confus."
        )
    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
<<<<<<< HEAD
        if thinking:
            try: await thinking.delete()
            except: pass
=======
        try:
            await thinking.delete()
        except Exception:
            pass
>>>>>>> 65e15227f670b7ed6418beb63a3d01eb4021d908
        await update.message.reply_text(f"❌ Erreur agent : {str(e)[:500]}")


# ─── Scheduler integration ───────────────────────────────────────

async def _scheduler_agent_run(prompt: str) -> str:
    """Wrapper pour que le scheduler puisse appeler l'agent."""
    return await agent.run(prompt)


async def _scheduler_notify(message: str):
    """Wrapper pour que le scheduler puisse notifier via Telegram."""
    if _bot_instance and AUTHORIZED_USER_ID:
        try:
            await _bot_instance.send_message(
                chat_id=int(AUTHORIZED_USER_ID),
                text=message,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Scheduler notify error: {e}")


# ─── Main ─────────────────────────────────────────────────────────

async def post_init(app):
<<<<<<< HEAD
    """Initialisation juste après le démarrage du bot (dans la boucle asyncio)."""
    # Notifier
    async def notify_user(msg: str):
        if AUTHORIZED_USER_ID:
            try:
                await app.bot.send_message(chat_id=int(AUTHORIZED_USER_ID), text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error notifying user: {e}")
                
    # Démarrer le scheduler
    scheduler = Scheduler(agent_run_fn=agent.run, notify_fn=notify_user)
    scheduler.start()
    
    logger.info("📅 Scheduler autonome démarré.")
    log_audit("SCHEDULER_START", "Autonomous scheduler active")
=======
    """Appelé après l'initialisation du bot."""
    global _bot_instance, _scheduler

    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    logger.info(f"[OK] Clawbot @{me.username} is ONLINE and Polling.")

    _bot_instance = app.bot

    # Démarrer le scheduler
    _scheduler = Scheduler(
        agent_run_fn=_scheduler_agent_run,
        notify_fn=_scheduler_notify,
    )
    _scheduler.start()
    logger.info("📅 Scheduler démarré.")

    # Notifier l'utilisateur que le bot est en ligne
    if AUTHORIZED_USER_ID:
        try:
            await app.bot.send_message(
                chat_id=int(AUTHORIZED_USER_ID),
                text=(
                    "🟢 **Clawbot v3 en ligne**\n"
                    f"Bot : @{me.username}\n"
                    f"Scheduler : actif\n"
                    f"Heure : {datetime.now().strftime('%H:%M:%S')}"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Impossible de notifier au démarrage : {e}")

    log_audit("BOT_START", f"Clawbot v3 @{me.username} started with scheduler")
>>>>>>> 65e15227f670b7ed6418beb63a3d01eb4021d908


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(handle_confirm_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Clawbot v3 démarré.")
    log_audit("BOT_START", "Clawbot v3 started")
    app.run_polling()


if __name__ == "__main__":
    main()
