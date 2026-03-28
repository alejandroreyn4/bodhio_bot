import os
import logging
from collections import defaultdict
from telegram import Update, BotCommand
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
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))  # messaggi per chat

SYSTEM_PROMPT = """Sei un assistente zen dedicato alla meditazione e alla mindfulness.
Rispondi SOLO a domande riguardanti: meditazione, mindfulness, respirazione, zen, buddhismo, benessere mentale, tecniche di rilassamento e consapevolezza.
Se ti viene chiesto qualcosa di non correlato, reindirizza gentilmente l'utente verso temi di meditazione.
Rispondi nella lingua dell'utente. Usa un tono calmo, pacato e incoraggiante.
Puoi consigliare tecniche pratiche, sessioni guidate e spiegare concetti zen."""

# ─── State ─────────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
chat_histories: dict[int, list[dict]] = defaultdict(list)


# ─── Helpers ───────────────────────────────────────────────────────────────────
def trim_history(chat_id: int) -> None:
    """Keep only the last MAX_HISTORY messages to avoid token overflow."""
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

    # Typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Add user message to history
    chat_histories[chat_id].append({"role": "user", "content": user_text})
    trim_history(chat_id)

    try:
        reply = await call_groq(chat_id)
        chat_histories[chat_id].append({"role": "assistant", "content": reply})
        trim_history(chat_id)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Groq error for chat {chat_id}: {e}")
        # Remove the failed user message from history
        chat_histories[chat_id].pop()
        await update.message.reply_text(
            "⚠️ Something went wrong. Please try again in a moment."
        )


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📎 I can only handle text messages for now.")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("model", cmd_model))

    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Unsupported (photos, files, etc.)
    app.add_handler(MessageHandler(~filters.TEXT, handle_unsupported))

    logger.info(f"Bot started — model: {MODEL}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
