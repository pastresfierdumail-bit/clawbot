"""
apps/telegram_bot.py — Interface Telegram pour Clawbot v3.

Commandes :
  /start          → message d'accueil
  /status         → quota tokens + état de la VM
  /report [type]  → consulter les rapports (daily, research, all)
  /memory [query] → consulter la mémoire
  /reset          → remet à zéro la conversation
  (texte libre)   → envoyé à l'agent Kimi K2

Lancer :
  cd h:/0perso/clawbot
  pip install -r apps/clawbot/requirements.txt
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

# ─── Config ───────────────────────────────────────────────────────

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "").strip()
AUTHORIZED_USER_ID = os.getenv("TELEGRAM_USER_ID", "").strip()  # ton user_id Telegram

if not TELEGRAM_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN manquant dans .env")
    sys.exit(1)
if not KIMI_API_KEY:
    print("❌ KIMI_API_KEY manquant dans .env")
    sys.exit(1)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Agent singleton ─────────────────────────────────────────────

agent = create_agent(api_key=KIMI_API_KEY)

# Store pour les confirmations en attente
_pending_confirms: dict[int, asyncio.Future] = {}

MEMORY_DIR = Path(__file__).parent.parent / "memory"
REPORTS_DIR = MEMORY_DIR / "reports"


# ─── Auth middleware ──────────────────────────────────────────────

def authorized(func):
    """Décorateur : vérifie que l'utilisateur est autorisé."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
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

        # Attendre la réponse (timeout 60s)
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
        "/reset — nouvelle conversation",
        parse_mode="Markdown",
    )


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quota = get_quota_status()
    pct = int((quota["used"] / quota["limit"]) * 100) if quota["limit"] > 0 else 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)

    msg = (
        f"📊 **Statut Clawbot**\n\n"
        f"**Tokens aujourd'hui :**\n"
        f"`[{bar}]` {pct}%\n"
        f"{quota['used']:,} / {quota['limit']:,}\n\n"
        f"**Conversation :** {len(agent.conversation_history)} messages"
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

    # Tronquer si trop long pour Telegram
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
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent.reset_conversation()
    await update.message.reply_text("🔄 Conversation réinitialisée.")


@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Message libre → envoyé à l'agent Kimi K2."""
    user_text = update.message.text
    chat_id = update.message.chat_id

    # Indicateur de traitement
    thinking = await update.message.reply_text("🧠 Réflexion en cours...")

    # Créer le callback de confirmation
    confirm = await confirm_callback_factory(chat_id, context.bot)
    agent.set_confirm_callback(confirm)

    try:
        # Lancer l'agent
        response = await agent.run(user_text)

        await thinking.delete()

        # Découper si la réponse est trop longue pour Telegram (4096 chars max)
        if len(response) <= 4000:
            await update.message.reply_text(response)
        else:
            chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk)

    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        await thinking.delete()
        await update.message.reply_text(f"❌ Erreur agent : {str(e)[:500]}")


# ─── Main ─────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(handle_confirm_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Clawbot v3 démarré.")
    log_audit("BOT_START", "Clawbot v3 started")
    app.run_polling()


if __name__ == "__main__":
    main()
