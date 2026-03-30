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

try:
    firebase_key = os.environ.get("FIREBASE_KEY")
    cred = credentials.Certificate(json.loads(firebase_key))
    firebase_admin.initialize_app(cred)
    db = firestore.Client(
        project="progetto-web-zen",
        database="ai-studio-3b998794-0fe8-40cf-8aad-6c900a81b085"
    )
    logger.info("✅ Firebase inizializzato")
except Exception as e:
    logger.error(f"❌ Firebase error: {e}")
    db = None

groq_client = Groq(api_key=GROQ_API_KEY)
chat_histories = defaultdict(list)

SYSTEM_PROMPT = """Sei Bodhi 🪷, un assistente di meditazione gentile, 
presente e premuroso su Bodhio.life. Rispondi sempre in italiano con 
tono caldo, calmo e incoraggiante. Aiuta l'utente con meditazione, 
mindfulness, respirazione e benessere mentale."""

async def call_ai(messages):
    completion = groq_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
    )
    return completion.choices[0].message.content

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.effective_user.first_name or "amico"
    args = context.args

    logger.info(f"/start ricevuto da {name}, args: {args}")

    if not args:
        await update.message.reply_text(
            f"Ciao {name}! Sono Bodhi 🪷, il tuo assistente di meditazione su Bodhio.life.\n\n"
            "Puoi chiedermi qualsiasi cosa sulla meditazione, mindfulness o benessere mentale. "
            "Sono qui per te 🙏\n\n"
            "Comandi:\n/start — mostra questo messaggio\n/reset — cancella la cronologia"
        )
        return

    token = args[0].strip().replace("\n", "").replace("\r", "")
    logger.info(f"Token ricevuto: '{token}'")

    if not db:
        await update.message.reply_text("⚠️ Database non disponibile.")
        return

    try:
        docs = db.collection("telegram_link_tokens").stream()
        found = None

        for d in docs:
            logger.info(f"Doc trovato: {d.id}")
            if d.id == token:
                found = d
                break

        if not found:
            await update.message.reply_text(f"❌ Token non trovato.")
            return

        data = found.to_dict()
        uid = data.get("uid")

        if data.get("used"):
            await update.message.reply_text("⚠️ Token già utilizzato.")
            return

        db.collection("users").document(uid).set(
            {"telegramChatId": chat_id}, merge=True
        )
        db.collection("telegram_link_tokens").document(token).update(
            {"used": True, "telegramChatId": chat_id}
        )

        await update.message.reply_text(
            "✅ Account collegato con successo!\n\n"
            "Da ora ti invierò promemoria personalizzati, "
            "notifiche sui badge e aggiornamenti settimanali 🙏"
        )

    except Exception as e:
        logger.error(f"Errore linking: {e}")
        await update.message.reply_text("⚠️ Errore durante il collegamento.")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories[chat_id] = []
    await update.message.reply_text("🔄 Cronologia cancellata. Ricominciamo da capo 🙏")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    chat_histories[chat_id].append({"role": "user", "content": text})

    # Limite cronologia
    if len(chat_histories[chat_id]) > 20:
        chat_histories[chat_id] = chat_histories[chat_id][-20:]

    reply = await call_ai(chat_histories[chat_id])
    chat_histories[chat_id].append({"role": "assistant", "content": reply})

    await update.message.reply_text(reply)

async def health(request):
    return web.Response(text="OK")

async def webhook(request, app):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

async def main():
    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("reset", cmd_reset))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    webhook_path = f"/webhook/{BOT_TOKEN}"
    web_app = web.Application()
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