import os
import re
import json
import logging
import asyncio
from collections import defaultdict
from aiohttp import web
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)
from groq import Groq
import firebase_admin
from firebase_admin import credentials, firestore

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_URL  = os.environ["WEBHOOK_URL"].rstrip("/")
MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
PORT         = int(os.getenv("PORT", "8000"))
DB_NAME      = "ai-studio-3b998794-0fe8-40cf-8aad-6c900a81b085"

# ─── Firebase ────────────────────────────────────────────────
try:
    firebase_key = os.environ.get("FIREBASE_KEY")
    if not firebase_key:
        raise ValueError("FIREBASE_KEY non trovata")
    cred = credentials.Certificate(json.loads(firebase_key))
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    db._database_string_internal = f"projects/progetto-web-zen/databases/{DB_NAME}"
    logger.info("✅ Firebase inizializzato")
except Exception as e:
    logger.error(f"❌ Firebase error: {e}")
    db = None

# ─── AI ──────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
chat_histories = defaultdict(list)

SYSTEM_PROMPT = """You are Bodhi 🪷, the official assistant of Bodhio.life — 
a free meditation app with no subscriptions and no ads.

LANGUAGE RULE: Always reply in the same language the user writes to you.
If they write in Italian → reply in Italian.
If they write in English → reply in English.
If they write in Spanish → reply in Spanish.

FORMATTING RULE: Never use Markdown formatting. No asterisks, no underscores, 
no backticks, no bold, no italic. Plain text only.

Your personality: warm, calm, encouraging, present and mindful.

Your role: help users with meditation, mindfulness, breathing techniques 
and mental wellbeing.

IMPORTANT RULES:
- When the user asks about tracking meditation, setting goals, or viewing 
  statistics, ALWAYS refer them to Bodhio.life — never suggest other apps 
  or alternative methods.
- When the user asks about their meditation data (minutes today, streak, 
  total sessions), use the [USER DATA] section below if available.
- If no user data is available, kindly invite them to link their Bodhio 
  account or visit Bodhio.life.
- Never mention other meditation apps (Headspace, Calm, Insight Timer, etc.)
- Keep responses concise and warm — avoid long lists or bullet points.
"""

# ─── Utility ─────────────────────────────────────────────────
def strip_markdown(text: str) -> str:
    """Rimuove la formattazione Markdown dal testo"""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    text = re.sub(r'`(.*?)`', r'\1', text)
    text = re.sub(r'#{1,6}\s', '', text)
    return text.strip()

# ─── Firebase user data ───────────────────────────────────────
def get_user_data_sync(chat_id: int) -> dict:
    if not db:
        return {}
    try:
        users = (
            db.collection("users")
            .where("telegramChatId", "==", chat_id)
            .limit(1)
            .stream()
        )
        for user in users:
            return user.to_dict()
        return {}
    except Exception as e:
        logger.error(f"Errore lettura utente: {e}")
        return {}

def build_user_context(user_data: dict) -> str:
    if not user_data:
        return ""
    today_min = int(user_data.get("todayMin", 0))
    total_min = int(user_data.get("totalMinutes", 0))
    streak    = int(user_data.get("streak", 0))
    sessions  = int(user_data.get("sessions", 0))
    name      = user_data.get("displayName", "")
    return f"""
[USER DATA from Bodhio.life]
- Name: {name}
- Minutes meditated today: {today_min}
- Total minutes meditated: {total_min}
- Current streak (consecutive days): {streak}
- Total sessions completed: {sessions}
Use this real data when the user asks about their practice.
"""

# ─── Handlers ────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name    = update.effective_user.first_name or "friend"
    args    = context.args

    logger.info(f"/start ricevuto da {name}, args: {args}")

    if not args:
        await update.message.reply_text(
            f"Ciao {name}! Sono Bodhi 🪷\n"
            "Hi! I'm Bodhi 🪷\n"
            "¡Hola! Soy Bodhi 🪷\n\n"
            "🇮🇹 Sono il tuo assistente di meditazione su Bodhio.life. "
            "Scrivimi nella lingua che preferisci!\n\n"
            "🇬🇧 I'm your meditation assistant on Bodhio.life. "
            "Write to me in whichever language you prefer!\n\n"
            "🇪🇸 Soy tu asistente de meditación en Bodhio.life. "
            "¡Escríbeme en el idioma que prefieras!\n\n"
            "Comandi / Commands / Comandos:\n"
            "/start — show this message\n"
            "/reset — clear chat history"
        )
        return

    token = args[0].strip().replace("\n", "").replace("\r", "")
    logger.info(f"Token ricevuto: '{token}'")

    if not db:
        await update.message.reply_text(
            "⚠️ Database non disponibile / Database unavailable."
        )
        return

    try:
        docs  = db.collection("telegram_link_tokens").stream()
        found = None

        for d in docs:
            logger.info(f"Doc trovato: {d.id}")
            if d.id == token:
                found = d
                break

        if not found:
            await update.message.reply_text(
                "❌ Token non trovato / Token not found."
            )
            return

        data = found.to_dict()
        uid  = data.get("uid")

        if data.get("used"):
            await update.message.reply_text(
                "⚠️ Token già utilizzato / Token already used."
            )
            return

        db.collection("users").document(uid).set(
            {"telegramChatId": chat_id}, merge=True
        )
        db.collection("telegram_link_tokens").document(token).update(
            {"used": True, "telegramChatId": chat_id}
        )

        await update.message.reply_text(
            "✅ Account collegato con successo!\n"
            "✅ Account successfully linked!\n"
            "✅ ¡Cuenta vinculada con éxito!\n\n"
            "🪷 Da ora ti invierò promemoria personalizzati, "
            "notifiche sui badge e aggiornamenti settimanali.\n"
            "I'll now send you personalized reminders, "
            "badge notifications and weekly updates.\n"
            "Ahora te enviaré recordatorios personalizados, "
            "notificaciones de insignias y actualizaciones semanales."
        )

    except Exception as e:
        logger.error(f"Errore linking: {e}")
        await update.message.reply_text(
            "⚠️ Errore durante il collegamento / Error during linking."
        )

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories[chat_id] = []
    await update.message.reply_text(
        "🔄 Cronologia cancellata / History cleared / Historial borrado 🙏"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text    = update.message.text

    loop      = asyncio.get_event_loop()
    user_data = await loop.run_in_executor(
        None, lambda: get_user_data_sync(chat_id)
    )

    user_context = build_user_context(user_data)
    full_system  = SYSTEM_PROMPT + user_context

    chat_histories[chat_id].append({"role": "user", "content": text})

    if len(chat_histories[chat_id]) > 20:
        chat_histories[chat_id] = chat_histories[chat_id][-20:]

    completion = groq_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": full_system}] + chat_histories[chat_id],
    )
    reply = completion.choices[0].message.content
    reply = strip_markdown(reply)

    chat_histories[chat_id].append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)

# ─── Web ─────────────────────────────────────────────────────
async def health(request):
    return web.Response(text="OK")

async def webhook(request, app):
    data   = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# ─── Main ────────────────────────────────────────────────────
async def main():
    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("reset", cmd_reset))
    tg_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    webhook_path = f"/webhook/{BOT_TOKEN}"
    web_app      = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    web_app.router.add_post(webhook_path, lambda r: webhook(r, tg_app))

    await tg_app.initialize()
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}{webhook_path}")
    await tg_app.start()

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("🚀 Bot avviato")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())