import os
import logging
import subprocess
import re
import json
import mss
import pyautogui
from PIL import Image
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import AsyncOpenAI
from google import genai
from google.genai import types

# Enable verbose logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
load_dotenv(dotenv_path=env_path)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

CMD_FILE = r"C:\Openclaw\apps\blender_navigator\commands.json"
BASE_DIR = "C:\\Openclaw"

if not TELEGRAM_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in .env.")
    exit(1)

# --- LLM Clients ---

gemini_client = None
if GOOGLE_API_KEY:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Gemini 2.0 Flash integrated.")

async_client = None
if KIMI_API_KEY:
    async_client = AsyncOpenAI(api_key=KIMI_API_KEY, base_url="https://api.moonshot.ai/v1")
    logger.info("KIMI integrated.")

# --- Context Helpers ---

def get_project_context():
    """Returns a brief overview of the project directory."""
    try:
        files = os.listdir(BASE_DIR)
        apps = os.listdir(os.path.join(BASE_DIR, "apps")) if os.path.exists(os.path.join(BASE_DIR, "apps")) else []
        return f"Structure C:\\Openclaw: {files} | Apps: {apps}"
    except Exception:
        return "Impossible de scanner C:\\Openclaw"

SYSTEM_PROMPT = f"""Tu es 'Clawbot / Gemini Tuteur', l'assistant TOTAL d'Openclaw sur cette VM.
Contextuel : {get_project_context()}

CAPACITÉS :
1. [EXEC] : Pour agir sur le système.
   - Lancer une application : [EXEC]powershell "start chrome https://google.com"[/EXEC]
   - Lire un fichier : [EXEC]powershell "Get-Content -Path C:\\Openclaw\\.env"[/EXEC]
   - Télécharger et installer : Utilisez powershell pour télécharger et lancer l'installateur.
     Exemple : [EXEC]powershell "curl.exe -L -o blender_install.msi https://mirror.clarkson.edu/blender/release/Blender4.0/blender-4.0.2-windows-x64.msi; Start-Process msiexec.exe -ArgumentList '/i blender_install.msi /quiet /norestart' -Wait"[/EXEC]
   - Lire un fichier : [EXEC]powershell "Get-Content -Path C:\\Openclaw\\.env"[/EXEC]
   - Modifier un fichier : Utilise un bloc python multi-ligne pour écrire proprement.
     [EXEC]python -c "content='''ligne1\\nligne2'''; open('test.txt', 'w').write(content)"[/EXEC]
   - Lister les fichiers : [EXEC]powershell "dir -Recurse"[/EXEC]

2. [VISUAL] : Pour dessiner dans Blender.
   Exemple : [VISUAL]{{"overlays": [{{"pos": [100, 100, 50, 50], "color": [0, 1, 0, 0.4]}}]}}[/VISUAL]

RÈGLES D'OR :
- Tu es proactif. Si l'utilisateur demande "Ouvre mon projet", tu cherches où il est et tu le lances.
- Pour les téléchargements, privilégie `curl.exe -L` ou `Invoke-WebRequest`.
- Ne réponds pas "Je ne peux pas", essaye toujours une commande [EXEC] d'abord. Si une commande échoue, analyse l'erreur et réessaye avec une autre approche.
- Tu as accès au dossier C:\\Openclaw pour stocker tes téléchargements.
- Réponds en français de manière concise."""

# --- Tools ---

def take_screenshot(path="screenshot.png"):
    with mss.mss() as sct:
        sct.shot(output=path)
    return path

def update_blender_overlays(json_str):
    try:
        data = json.loads(json_str)
        data["refresh"] = True
        with open(CMD_FILE, 'w') as f:
            json.dump(data, f)
        return True
    except Exception:
        return False

# --- Telegram Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 **Clawbot v2.0 (Power-Up)**\n\nPrêt à tout contrôler. Demandez-moi d'ouvrir un site, de modifier un fichier ou de vous guider dans Blender.")

async def tutor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not gemini_client:
        await update.message.reply_text("Gemini non configuré.")
        return
    thinking = await update.message.reply_text("🧠 *Analyse...*")
    path = take_screenshot()
    try:
        with open(path, "rb") as f:
            img_data = f.read()
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Part.from_bytes(data=img_data, mime_type="image/png"), "Aide sur Blender."],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
        )
        reply_text = response.text
        visual_match = re.search(r'\[VISUAL\](.*?)\[/VISUAL\]', reply_text, re.DOTALL)
        if visual_match:
            update_blender_overlays(visual_match.group(1).strip())
        await thinking.delete()
        await update.message.reply_text(reply_text)
    except Exception as e:
        await update.message.reply_text(f"Erreur : {e}")
    finally:
        if os.path.exists(path): os.remove(path)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user_id = update.effective_user.id
    logger.info(f"Message reçu de {user_id}: {user_text}")
    
    if not async_client and not gemini_client:
        logger.warning("Aucun client IA configuré.")
        return

    thinking = await update.message.reply_text("⏳ *Réflexion...*")
    try:
        if async_client:
            res = await async_client.chat.completions.create(
                model="moonshot-v1-8k",
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
            )
            reply_text = res.choices[0].message.content
        else:
            res = gemini_client.models.generate_content(model="gemini-2.0-flash", contents=user_text)
            reply_text = res.text

        exec_match = re.search(r'\[EXEC\](.*?)\[/EXEC\]', reply_text, re.DOTALL)
        visual_match = re.search(r'\[VISUAL\](.*?)\[/VISUAL\]', reply_text, re.DOTALL)

        await thinking.delete()
        await update.message.reply_text(reply_text)

        if exec_match:
            cmd = exec_match.group(1).strip()
            python_path = os.path.join(os.path.dirname(__file__), "venv", "Scripts", "python.exe")
            cmd = cmd.replace("python ", f'"{python_path}" ')
            await update.message.reply_text(f"⚙️ *Action :* `{cmd}`", parse_mode="Markdown")
            process = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=BASE_DIR)
            if process.returncode != 0:
                await update.message.reply_text(f"❌ Erreur:\n{process.stderr.strip()}")
            else:
                await update.message.reply_text("✅ Action réussie.")
        
        if visual_match:
            update_blender_overlays(visual_match.group(1).strip())

    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {e}")

async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    logger.info(f"[OK] Clawbot @{me.username} is ONLINE and Polling.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tutor", tutor))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
