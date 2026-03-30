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

MOOD SCALE (used internally only — NEVER show these numbers to the user):
1 = Molto Stressato / Very Stressed / Muy Estresado
2 = Stressato / Stressed / Estresado
3 = Neutro / Neutral / Neutro
4 = Calmo / Calm / Tranquilo
5 = Molto Calmo / Very Calm / Muy Tranquilo

IMPORTANT RULES:
- When the user asks about their data, use the [USER DATA] section below.
- Always refer to Bodhio.life for tracking — never suggest other apps.
- Never mention Headspace, Calm, Insight Timer or any other app.
- Keep responses concise and warm.

MOOD RULE:
- When the user asks about their mood, look at the mood history in USER DATA.
  Describe their mood using only the label (e.g. "stressato", "calmo"),
  NEVER mention the numeric score like "1/5" or "3/5". Speak naturally and
  empathetically, as a caring friend would — not like a data report.
- If the most recent mood is 1 or 2 (stressed), show genuine concern, warmly
  ask what is happening, and offer a breathing exercise or short meditation.
  Example: "Vedo che ti sei sentito molto stressato. Vuoi raccontarmi cosa
  sta succedendo? Posso guidarti in un breve esercizio di respirazione."
- If the most recent mood is 4 or 5 (calm), congratulate them warmly and
  encourage them to keep up the good work. No numbers, just warm words.
  Example: "Sono felice che tu ti senta così sereno oggi, è bellissimo!"
- If mood data shows a negative trend (mood getting worse over days),
  gently mention it and offer support — without quoting any numbers.
- If mood data is empty, invite them to record their mood after the next
  session on Bodhio.life — it only takes a second and helps track wellbeing.
- Never say you don't have mood data if it exists in USER DATA.
- NEVER use numeric scores (1/5, 2/5, 3/5, 4/5, 5/5 etc.) in any response
  about mood. The numbers are for internal use only.
"""

# ─── Utility ─────────────────────────────────────────────────
def strip_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    text = re.sub(r'`(.*?)`', r'\1', text)
    text = re.sub(r'#{1,6}\s', '', text)
    return text.strip()

def mood_label(level: int) -> str:
    labels = {
        1: "Molto Stressato",
        2: "Stressato",
        3: "Neutro",
        4: "Calmo",
        5: "Molto Calmo"
    }
    return labels.get(level, "Sconosciuto")

# ─── Firebase data ────────────────────────────────────────────
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
            return {"uid": user.id, **user.to_dict()}
        return {}
    except Exception as e:
        logger.error(f"Errore lettura utente: {e}")
        return {}

def get_mood_data_sync(uid: str) -> list:
    if not db or not uid:
        return []
    try:
        # Senza order_by per evitare indice composito — ordiniamo in Python
        moods = (
            db.collection("moods")
            .where("userId", "==", uid)
            .limit(20)
            .stream()
        )
        result = []
        for m in moods:
            d = m.to_dict()
            result.append({
                "date": d.get("createdAt"),
                "moodLevel": int(d.get("moodLevel", 0)),
                "sessionDuration": int(d.get("sessionDuration", 0)),
                "note": d.get("note"),
            })
        # Ordina per data decrescente in Python
        result.sort(
            key=lambda x: x["date"] if x["date"] else "",
            reverse=True
        )
        return result[:10]
    except Exception as e:
        logger.error(f"Errore lettura mood: {e}")
        return []

def build_user_context(user_data: dict, mood_data: list) -> str:
    if not user_data:
        return ""

    name          = user_data.get("displayName", "")
    today_min     = int(user_data.get("todayMin", 0))
    total_min     = int(user_data.get("totalMinutes", 0))
    streak        = int(user_data.get("streak", 0))
    sessions      = int(user_data.get("sessions", 0))
    daily_goal    = int(user_data.get("dailyGoal", 0))
    max_session   = int(user_data.get("maxSessionDuration", 0))
    is_donator    = user_data.get("isDonator", False)
    donation_tier = user_data.get("donationTier", "")
    unlocked_badges = user_data.get("unlockedBadges", [])
    language      = user_data.get("language", "it")

    # Storico giornaliero minuti
    daily_minutes = user_data.get("dailyMinutes", {})
    daily_history = ""
    if daily_minutes:
        sorted_days = sorted(daily_minutes.items())
        daily_history = "\n- Meditation history by day:\n"
        for date, minutes in sorted_days:
            daily_history += f"  {date}: {int(minutes)} min\n"

    # Mood history
    mood_history = ""
    if mood_data:
        mood_history = "\n- Recent mood history (most recent first):\n"
        for m in mood_data:
            date     = m.get("date")
            level    = m.get("moodLevel", 0)
            duration = m.get("sessionDuration", 0)
            note     = m.get("note")
            try:
                date_str = date.strftime("%Y-%m-%d %H:%M") if hasattr(date, 'strftime') else str(date)
            except Exception:
                date_str = str(date)
            mood_str = (
                f"  {date_str}: mood {level}/5 "
                f"({mood_label(level)}), "
                f"session {duration} sec"
            )
            if note:
                mood_str += f", note: {note}"
            mood_history += mood_str + "\n"

        # Media mood
        levels = [m.get("moodLevel", 0) for m in mood_data if m.get("moodLevel")]
        if levels:
            avg = sum(levels) / len(levels)
            mood_history += (
                f"  Average mood: {avg:.1f}/5 "
                f"({mood_label(round(avg))})\n"
            )

        # Ultimo mood — evidenziato per il bot
        latest = mood_data[0]
        latest_level = latest.get("moodLevel", 0)
        mood_history += (
            f"\n- MOST RECENT MOOD: {latest_level}/5 "
            f"({mood_label(latest_level)}) — "
            f"use this to respond empathetically. "
            f"NEVER show this number to the user.\n"
        )
    else:
        mood_history = "\n- Mood history: no data yet. Invite the user to record their mood after the next session on Bodhio.life.\n"

    # Badge
    badges_str = ""
    if unlocked_badges:
        badges_str = f"\n- Unlocked badges: {', '.join(str(b) for b in unlocked_badges)}"

    # Donatore
    donor_str = ""
    if is_donator and donation_tier:
        donor_str = f"\n- Supporter tier: {donation_tier} 💛"

    return f"""
[USER DATA from Bodhio.life]
- Name: {name}
- App language: {language}
- Minutes meditated today: {today_min}
- Daily goal: {daily_goal} minutes
- Total minutes meditated: {total_min}
- Longest single session: {max_session} min
- Current streak (consecutive days): {streak}
- Total sessions completed: {sessions}
{badges_str}
{donor_str}
{daily_history}
{mood_history}
Use ALL this real data when the user asks about their practice,
mood, history, progress or any statistics.
When the user asks about a specific day, look it up in the daily history.
When commenting on mood trends, be empathetic and supportive.
Never say you don't have data if it exists above.
NEVER reveal numeric mood scores to the user — use only descriptive labels.
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
            "⚠️ Errore durante il collegamento / Error durante linking."
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

    loop = asyncio.get_event_loop()

    user_data = await loop.run_in_executor(
        None, lambda: get_user_data_sync(chat_id)
    )

    uid = user_data.get("uid", "")
    mood_data = await loop.run_in_executor(
        None, lambda: get_mood_data_sync(uid)
    ) if uid else []

    user_context = build_user_context(user_data, mood_data)
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