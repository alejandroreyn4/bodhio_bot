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

# 🔥 Firebase Admin
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

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://bodhio.life,https://www.bodhio.life").split(",")

# 🔑 Firebase init (una sola volta)
if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(os.environ["FIREBASE_KEY"]))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ─── Prompt ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_TELEGRAM = """Sei Bodhi, l'assistente ufficiale di Bodhio.life dedicato alla meditazione e mindfulness.
Rispondi SOLO su temi di meditazione, zen, mindfulness, respirazione, benessere mentale e consapevolezza.
Se ti viene chiesto altro, reindirizza gentilmente verso temi di meditazione.
Tono calmo, pacato, incoraggiante. Rispondi nella lingua dell'utente.
Firma le risposte più lunghe con — Bodhi 🪷"""

SYSTEM_PROMPT_WEB = SYSTEM_PROMPT_TELEGRAM

# ─── State ─────────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
chat_histories: dict[int, list[dict]] = defaultdict(list)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def trim_history(chat_id: int) -> None:
    if len(chat_histories[chat_id]) > MAX_HISTORY:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY:]


async def call_groq_telegram(chat_id: int) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT_TELEGRAM}] + chat_histories[chat_id]
    completion = groq_client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=1024,
    )
    return completion.choices[0].message.content


async def call_groq_web(messages: list) -> str:
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT_WEB}] + messages
    completion = groq_client.chat.completions.create(
        model=MODEL,
        messages=full_messages,
        temperature=0.7,
        max_tokens=1024,
    )
    return completion.choices[0].message.content


def get_cors_headers(origin: str) -> dict:
    allowed = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin": allowed,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

# ─── 🔥 TELEGRAM HANDLERS ──────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "there"
    chat_id = update.effective_chat.id

    token = context.args[0] if context.args else None
    print("TOKEN:", token)

    if token:
        try:
            token_ref = db.collection("telegram_link_tokens").document(token)
            token_doc = token_ref.get()

            if not token_doc.exists or token_doc.to_dict().get("used"):
                await update.message.reply_text("❌ Link non valido o già usato.")
                return

            uid = token_doc.to_dict()["uid"]

            # 🔗 collega utente
            db.collection("users").document(uid).update({
                "telegramChatId": chat_id
            })

            # ✅ segna token usato
            token_ref.update({"used": True})

            await update.message.reply_text("✅ Account collegato con successo! 🙏")
            return

        except Exception as e:
            print("ERROR LINK:", e)
            await update.message.reply_text("⚠️ Errore durante il collegamento.")
            return

    # fallback normale
    await update.message.reply_text(
        f"Ciao {name}! Sono Bodhi 🪷, il tuo assistente di meditazione su Bodhio.life.\n\n"
        "Posso aiutarti con tecniche di meditazione, mindfulness e benessere mentale.\n\n"
        "Comandi:\n"
        "  /start — mostra questo messaggio\n"
        "  /reset — cancella la cronologia\n"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    chat_histories[chat_id].clear()
    await update.message.reply_text("🗑️ Conversazione cancellata. Nuovo inizio!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    chat_histories[chat_id].append({"role": "user", "content": user_text})
    trim_history(chat_id)

    try:
        reply = await call_groq_telegram(chat_id)
        chat_histories[chat_id].append({"role": "assistant", "content": reply})
        trim_history(chat_id)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Groq error for chat {chat_id}: {e}")
        chat_histories[chat_id].pop()
        await update.message.reply_text("⚠️ Qualcosa è andato storto. Riprova tra un momento.")


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📎 Per ora gestisco solo messaggi di testo.")

# ─── Web Endpoints ─────────────────────────────────────────────────────────────
async def health_handler(request):
    return web.Response(text="OK")


async def telegram_webhook_handler(request, app):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")


async def chat_handler(request):
    origin = request.headers.get("Origin", "")
    cors = get_cors_headers(origin)

    if request.method == "OPTIONS":
        return web.Response(status=204, headers=cors)

    try:
        body = await request.json()
        messages = body.get("messages", [])

        if not messages:
            return web.Response(
                status=400,
                text=json.dumps({"error": "messages array required"}),
                content_type="application/json",
                headers=cors,
            )

        messages = messages[-10:]
        reply = await call_groq_web(messages)

        return web.Response(
            text=json.dumps({"reply": reply}),
            content_type="application/json",
            headers=cors,
        )

    except Exception as e:
        logger.error(f"Web chat error: {e}")
        return web.Response(
            status=500,
            text=json.dumps({"error": "Internal server error"}),
            content_type="application/json",
            headers=cors,
        )

# ─── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    tg_app = ApplicationBuilder().token(BOT_TOKEN).updater(None).build()

    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("reset", cmd_reset))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    tg_app.add_handler(MessageHandler(~filters.TEXT, handle_unsupported))

    webhook_path = f"/webhook/{BOT_TOKEN}"

    web_app = web.Application()
    web_app.router.add_get("/", health_handler)
    web_app.router.add_get("/health", health_handler)
    web_app.router.add_post(webhook_path, lambda req: telegram_webhook_handler(req, tg_app))
    web_app.router.add_post("/chat", chat_handler)
    web_app.router.add_route("OPTIONS", "/chat", chat_handler)

    await tg_app.initialize()
    await tg_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}{webhook_path}",
        drop_pending_updates=True,
    )
    await tg_app.start()

    logger.info(f"Bot started — webhook: {WEBHOOK_URL}{webhook_path}")

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Web server listening on port {PORT}")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())