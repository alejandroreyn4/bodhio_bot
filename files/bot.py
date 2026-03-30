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

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_URL  = os.environ["WEBHOOK_URL"].rstrip("/")
MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HISTORY  = int(os.getenv("MAX_HISTORY", "20"))
PORT         = int(os.getenv("PORT", "8000"))

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://bodhio.life,https://www.bodhio.life"
).split(",")

# ─── Firebase Init ─────────────────────────────────────────────────────────────
try:
    firebase_key = os.environ.get("FIREBASE_KEY")

    if not firebase_key:
        raise ValueError("FIREBASE_KEY non trovata")

    cred = credentials.Certificate(json.loads(firebase_key))
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    logger.info("✅ Firebase inizializzato")

except Exception as e:
    logger.error(f"❌ Firebase init error: {e}")
    db = None

# ─── Prompt ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Sei Bodhi, assistente di meditazione.
Rispondi solo su mindfulness, meditazione, respirazione.
Tono calmo e gentile."""

# ─── State ─────────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
chat_histories: dict[int, list[dict]] = defaultdict(list)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def trim_history(chat_id: int):
    if len(chat_histories[chat_id]) > MAX_HISTORY:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY:]

async def call_groq(messages):
    completion = groq_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        temperature=0.7,
        max_tokens=1024,
    )
    return completion.choices[0].message.content

def get_cors_headers(origin: str):
    allowed = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin": allowed,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

# ─── Telegram Commands ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.effective_user.first_name or "there"

    logger.info(f"/start ricevuto: {update.message.text}")

    args = context.args

    # 👉 START NORMALE
    if not args:
        await update.message.reply_text(
            f"Ciao {name}! Sono Bodhi 🪷\n\n"
            "Posso aiutarti con meditazione e mindfulness.\n\n"
            "Per collegare il tuo account usa il pulsante sul sito."
        )
        return

    # 👉 START CON TOKEN
    token = args[0]

    if not db:
        await update.message.reply_text("⚠️ Database non disponibile.")
        return

    try:
        doc_ref = db.collection("telegram_link_tokens").document(token)
        doc = doc_ref.get()

        if not doc.exists:
            await update.message.reply_text("❌ Token non valido.")
            return

        data = doc.to_dict()

        if data.get("used"):
            await update.message.reply_text("⚠️ Token già utilizzato.")
            return

        uid = data.get("uid")

        # 🔥 salva collegamento
        db.collection("users").document(uid).set({
            "telegramChatId": chat_id
        }, merge=True)

        # 🔥 marca token usato
        doc_ref.update({
            "used": True,
            "telegramChatId": chat_id
        })

        await update.message.reply_text(
            "✅ Collegamento completato con successo!"
        )

    except Exception as e:
        logger.error(f"Errore linking: {e}")
        await update.message.reply_text("⚠️ Errore durante il collegamento.")

# ─── Reset ─────────────────────────────────────────────────────────────────────
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories[chat_id].clear()
    await update.message.reply_text("🗑️ Conversazione resettata.")

# ─── Chat normale ──────────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text

    chat_histories[chat_id].append({"role": "user", "content": user_text})
    trim_history(chat_id)

    try:
        reply = await call_groq(chat_histories[chat_id])
        chat_histories[chat_id].append({"role": "assistant", "content": reply})
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Errore AI: {e}")
        await update.message.reply_text("⚠️ Errore temporaneo.")

# ─── Web Handlers ──────────────────────────────────────────────────────────────
async def health(request):
    return web.Response(text="OK")

async def webhook(request, app):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

async def chat_handler(request):
    origin = request.headers.get("Origin", "")
    cors = get_cors_headers(origin)

    if request.method == "OPTIONS":
        return web.Response(status=204, headers=cors)

    body = await request.json()
    messages = body.get("messages", [])[-10:]

    reply = await call_groq(messages)

    return web.Response(
        text=json.dumps({"reply": reply}),
        headers=cors,
        content_type="application/json"
    )

# ─── Main ──────────────────────────────────────────────────────────────────────
async def main():
    tg_app = ApplicationBuilder().token(BOT_TOKEN).updater(None).build()

    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("reset", cmd_reset))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    webhook_path = f"/webhook/{BOT_TOKEN}"

    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_post(webhook_path, lambda r: webhook(r, tg_app))
    web_app.router.add_post("/chat", chat_handler)
    web_app.router.add_route("OPTIONS", "/chat", chat_handler)

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