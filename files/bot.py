import os
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

# 🔥 Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# ─── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────
BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_URL  = os.environ["WEBHOOK_URL"].rstrip("/")
MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
PORT         = int(os.getenv("PORT", "8000"))

# ─── Firebase Init ────────────────────────────────────────────
try:
    firebase_key = os.environ.get("FIREBASE_KEY")

    cred = credentials.Certificate(json.loads(firebase_key))
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    logger.info("✅ Firebase inizializzato")

except Exception as e:
    logger.error(f"❌ Firebase error: {e}")
    db = None

# ─── AI ──────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
chat_histories = defaultdict(list)

async def call_ai(messages):
    completion = groq_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": "You are a meditation assistant"}] + messages,
    )
    return completion.choices[0].message.content

# ─────────────────────────────────────────────────────────────
# 🔥 START COMMAND (QUI C’È IL DEBUG)
# ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.effective_user.first_name or "there"

    logger.info(f"/start ricevuto: {update.message.text}")

    args = context.args

    # 👉 START NORMALE
    if not args:
        await update.message.reply_text(
            f"Ciao {name}! Sono Bodhi 🪷\n\n"
            "Usa il sito per collegare il tuo account."
        )
        return

    # 👉 PRENDI TOKEN (CON FIX)
    token = args[0].strip().replace("\n", "").replace("\r", "")

    # 🔥 DEBUG (QUESTA È LA RIGA CHE VOLEVI)
    logger.info(f"Token ricevuto: '{token}'")

    if not db:
        await update.message.reply_text("⚠️ Database non disponibile.")
        return

    try:
        # 🔥 DEBUG LISTA DOCUMENTI
        docs = db.collection("telegram_link_tokens").stream()

        found = None

        for d in docs:
            logger.info(f"Doc trovato: {d.id}")
            if d.id == token:
                found = d
                break

        if not found:
            await update.message.reply_text(f"❌ Token non trovato: {token}")
            return

        data = found.to_dict()
        uid = data.get("uid")

        if data.get("used"):
            await update.message.reply_text("⚠️ Token già utilizzato.")
            return

        # ✅ salva collegamento
        db.collection("users").document(uid).set({
            "telegramChatId": chat_id
        }, merge=True)

        # ✅ aggiorna token
        db.collection("telegram_link_tokens").document(token).update({
            "used": True,
            "telegramChatId": chat_id
        })

        await update.message.reply_text(
            "✅ Collegamento completato!"
        )

    except Exception as e:
        logger.error(f"Errore linking: {e}")
        await update.message.reply_text("⚠️ Errore durante il collegamento.")

# ─── Chat normale ─────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    chat_histories[chat_id].append({"role": "user", "content": text})

    reply = await call_ai(chat_histories[chat_id])

    chat_histories[chat_id].append({"role": "assistant", "content": reply})

    await update.message.reply_text(reply)

# ─── Web ─────────────────────────────────────────────────────
async def health(request):
    return web.Response(text="OK")

async def webhook(request, app):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# ─── Main ────────────────────────────────────────────────────
async def main():
    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    webhook_path = f"/webhook/{BOT_TOKEN}"

    web_app = web.Application()
    web_app.router.add_get("/", health)
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