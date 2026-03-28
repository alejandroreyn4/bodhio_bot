import os
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)
from groq import Groq

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WEBHOOK_URL  = os.environ["WEBHOOK_URL"]   # es. https://spicy-suzi-xxx.koyeb.app
MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HISTORY  = int(os.getenv("MAX_HISTORY", "20"))
PORT         = int(os.getenv("PORT", "8000"))

SYSTEM_PROMPT = """You are a helpful, knowledgeable, and friendly assistant.
You can answer questions on any topic: science, technology, history, culture, coding, math, travel, philosophy, and more.
Always respond in the same language the user is writing in — automatically detect and match it.
Be concise but thorough. When needed, use bullet points or numbered lists for clarity.
If you don't know something, say so honestly instead of guessing."""

# ─── State ─────────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
chat_histories: dict[int, list[dict]] = defaultdict(list)


# ─── Helpers ───────────────────────────────────────────────────────────────────
def trim_history(chat_id: int) -> None:
    if len(chat_histories[chat_id]) > MAX_HISTORY:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY:]


async def call_groq(chat_id: int) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_histories[chat_id]
    completion = groq_client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=1024,
    )
    return completion.choices[0].message.content


# ─── Handlers ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "there"
    await update.message.reply_text(
        f"👋 Hello {name}! I'm your AI assistant powered by LLaMA 3.\n\n"
        "Ask me anything — I'll reply in your language.\n\n"
        "Commands:\n"
        "  /start — show this message\n"
        "  /reset — clear conversation history\n"
        "  /model — show current AI model"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    chat_histories[chat_id].clear()
    await update.message.reply_text("🗑️ Conversation cleared. Fresh start!")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🤖 Current model: `{MODEL}`", parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    chat_histories[chat_id].append({"role": "user", "content": user_text})
    trim_history(chat_id)

    try:
        reply = await call_groq(chat_id)
        chat_histories[chat_id].append({"role": "assistant", "content": reply})
        trim_history(chat_id)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Groq error for chat {chat_id}: {e}")
        chat_histories[chat_id].pop()
        await update.message.reply_text(
            "⚠️ Something went wrong. Please try again in a moment."
        )


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📎 I can only handle text messages for now.")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(~filters.TEXT, handle_unsupported))

    webhook_path = f"/webhook/{BOT_TOKEN}"
    logger.info(f"Bot started in webhook mode — {WEBHOOK_URL}{webhook_path}")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        webhook_url=f"{WEBHOOK_URL}{webhook_path}",
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()