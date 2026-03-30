import os
import re
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
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
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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

INACTIVITY_DAYS = int(os.getenv("INACTIVITY_DAYS", "3"))

# ─── i18n ────────────────────────────────────────────────────
STRINGS = {
    "start_welcome": {
        "it": lambda name: (
            f"Ciao {name}! Sono Bodhi 🪷\n\n"
            "Sono il tuo assistente di meditazione su Bodhio.life. "
            "Scrivimi nella lingua che preferisci!\n\n"
            "Comandi:\n"
            "/start — mostra questo messaggio\n"
            "/reset — cancella cronologia chat\n"
            "/remind HH:MM — imposta reminder giornaliero (es. /remind 08:00)\n"
            "/remindoff — disattiva reminder giornaliero\n"
            "/settings — mostra le tue preferenze notifiche"
        ),
        "en": lambda name: (
            f"Hi {name}! I'm Bodhi 🪷\n\n"
            "I'm your meditation assistant on Bodhio.life. "
            "Write to me in whichever language you prefer!\n\n"
            "Commands:\n"
            "/start — show this message\n"
            "/reset — clear chat history\n"
            "/remind HH:MM — set daily reminder (e.g. /remind 08:00)\n"
            "/remindoff — disable daily reminder\n"
            "/settings — show your notification preferences"
        ),
        "es": lambda name: (
            f"¡Hola {name}! Soy Bodhi 🪷\n\n"
            "Soy tu asistente de meditación en Bodhio.life. "
            "¡Escríbeme en el idioma que prefieras!\n\n"
            "Comandos:\n"
            "/start — mostrar este mensaje\n"
            "/reset — borrar historial del chat\n"
            "/remind HH:MM — configurar recordatorio diario (ej. /remind 08:00)\n"
            "/remindoff — desactivar recordatorio diario\n"
            "/settings — ver tus preferencias de notificaciones"
        ),
    },
    "db_unavailable": {
        "it": "⚠️ Database non disponibile.",
        "en": "⚠️ Database unavailable.",
        "es": "⚠️ Base de datos no disponible.",
    },
    "token_not_found": {
        "it": "❌ Token non trovato.",
        "en": "❌ Token not found.",
        "es": "❌ Token no encontrado.",
    },
    "token_already_used": {
        "it": "⚠️ Token già utilizzato.",
        "en": "⚠️ Token already used.",
        "es": "⚠️ Token ya utilizado.",
    },
    "account_linked": {
        "it": (
            "✅ Account collegato con successo!\n\n"
            "🪷 Da ora ti invierò promemoria personalizzati, "
            "notifiche sui badge e aggiornamenti settimanali.\n\n"
            "Usa /remind HH:MM per impostare il tuo reminder giornaliero!"
        ),
        "en": (
            "✅ Account successfully linked!\n\n"
            "🪷 I'll now send you personalized reminders, "
            "badge notifications and weekly updates.\n\n"
            "Use /remind HH:MM to set your daily reminder!"
        ),
        "es": (
            "✅ ¡Cuenta vinculada con éxito!\n\n"
            "🪷 Ahora te enviaré recordatorios personalizados, "
            "notificaciones de insignias y actualizaciones semanales.\n\n"
            "¡Usa /remind HH:MM para configurar tu recordatorio diario!"
        ),
    },
    "link_error": {
        "it": "⚠️ Errore durante il collegamento.",
        "en": "⚠️ Error during linking.",
        "es": "⚠️ Error durante la vinculación.",
    },
    "remind_usage": {
        "it": "Per impostare un reminder scrivi:\n/remind HH:MM  (es. /remind 08:00)",
        "en": "To set a reminder write:\n/remind HH:MM  (e.g. /remind 08:00)",
        "es": "Para configurar un recordatorio escribe:\n/remind HH:MM  (ej. /remind 08:00)",
    },
    "remind_invalid_format": {
        "it": "Formato non valido. Usa HH:MM, ad esempio /remind 08:00",
        "en": "Invalid format. Use HH:MM, e.g. /remind 08:00",
        "es": "Formato inválido. Usa HH:MM, por ejemplo /remind 08:00",
    },
    "remind_not_linked": {
        "it": "Account non collegato. Usa prima il link da Bodhio.life.",
        "en": "Account not linked. Use the link from Bodhio.life first.",
        "es": "Cuenta no vinculada. Usa primero el enlace de Bodhio.life.",
    },
    "remind_set": {
        "it": lambda h, m: (
            f"✅ Reminder impostato ogni giorno alle {h:02d}:{m:02d} UTC 🪷\n"
            f"Usa /remindoff per disattivarlo."
        ),
        "en": lambda h, m: (
            f"✅ Reminder set every day at {h:02d}:{m:02d} UTC 🪷\n"
            f"Use /remindoff to disable it."
        ),
        "es": lambda h, m: (
            f"✅ Recordatorio configurado cada día a las {h:02d}:{m:02d} UTC 🪷\n"
            f"Usa /remindoff para desactivarlo."
        ),
    },
    "remind_off": {
        "it": "🔕 Reminder giornaliero disattivato.",
        "en": "🔕 Daily reminder disabled.",
        "es": "🔕 Recordatorio diario desactivado.",
    },
    "not_linked": {
        "it": "Account non collegato.",
        "en": "Account not linked.",
        "es": "Cuenta no vinculada.",
    },
    "settings": {
        "it": lambda reminder_status, inactivity, weekly, days: (
            f"Le tue preferenze notifiche:\n\n"
            f"📅 Reminder giornaliero: {reminder_status}\n"
            f"💤 Alert inattività ({days}gg): {inactivity}\n"
            f"📊 Report settimanale: {weekly}\n\n"
            f"Comandi:\n"
            f"/remind HH:MM — imposta reminder\n"
            f"/remindoff — disattiva reminder"
        ),
        "en": lambda reminder_status, inactivity, weekly, days: (
            f"Your notification settings:\n\n"
            f"📅 Daily reminder: {reminder_status}\n"
            f"💤 Inactivity alert ({days}d): {inactivity}\n"
            f"📊 Weekly report: {weekly}\n\n"
            f"Commands:\n"
            f"/remind HH:MM — set reminder\n"
            f"/remindoff — disable reminder"
        ),
        "es": lambda reminder_status, inactivity, weekly, days: (
            f"Tus preferencias de notificaciones:\n\n"
            f"📅 Recordatorio diario: {reminder_status}\n"
            f"💤 Alerta de inactividad ({days}d): {inactivity}\n"
            f"📊 Informe semanal: {weekly}\n\n"
            f"Comandos:\n"
            f"/remind HH:MM — configurar recordatorio\n"
            f"/remindoff — desactivar recordatorio"
        ),
    },
    "settings_reminder_active": {
        "it": lambda h, m: f"attivo alle {h:02d}:{m:02d} UTC",
        "en": lambda h, m: f"active at {h:02d}:{m:02d} UTC",
        "es": lambda h, m: f"activo a las {h:02d}:{m:02d} UTC",
    },
    "settings_reminder_off": {
        "it": "disattivato",
        "en": "disabled",
        "es": "desactivado",
    },
    "settings_active": {
        "it": "attivo",
        "en": "active",
        "es": "activo",
    },
    "settings_disabled": {
        "it": "disattivato",
        "en": "disabled",
        "es": "desactivado",
    },
    "reset_done": {
        "it": "🔄 Cronologia cancellata 🙏",
        "en": "🔄 History cleared 🙏",
        "es": "🔄 Historial borrado 🙏",
    },
}

def t(key: str, lang: str, *args):
    """Recupera la stringa tradotta per la lingua data.
    Se la stringa è un callable (lambda), la chiama con *args.
    Fallback su 'en' se la lingua non è disponibile."""
    lang = lang if lang in ("it", "en", "es") else "en"
    value = STRINGS.get(key, {}).get(lang) or STRINGS.get(key, {}).get("en", "")
    if callable(value):
        return value(*args)
    return value

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

def ai_message(prompt: str, language: str = "it") -> str:
    """Genera un messaggio con Groq dato un prompt interno."""
    lang_map = {"it": "Italian", "en": "English", "es": "Spanish"}
    lang = lang_map.get(language, "Italian")
    try:
        completion = groq_client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are Bodhi 🪷, a warm and mindful meditation assistant. "
                        f"Reply ONLY in {lang}. "
                        f"Never use Markdown. Plain text only. Be concise and caring."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        return strip_markdown(completion.choices[0].message.content)
    except Exception as e:
        logger.error(f"Errore AI: {e}")
        return ""

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

def get_all_telegram_users_sync() -> list:
    if not db:
        return []
    try:
        users = (
            db.collection("users")
            .where("telegramChatId", "!=", None)
            .stream()
        )
        result = []
        for u in users:
            d = u.to_dict()
            if d.get("telegramChatId"):
                result.append({"uid": u.id, **d})
        return result
    except Exception as e:
        logger.error(f"Errore lettura utenti: {e}")
        return []

def get_mood_data_sync(uid: str) -> list:
    if not db or not uid:
        return []
    try:
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
        result.sort(
            key=lambda x: x["date"] if x["date"] else "",
            reverse=True
        )
        return result[:10]
    except Exception as e:
        logger.error(f"Errore lettura mood: {e}")
        return []

def get_latest_session_sync(uid: str) -> dict | None:
    if not db or not uid:
        return None
    try:
        sessions = (
            db.collection("sessions")
            .where("userId", "==", uid)
            .limit(20)
            .stream()
        )
        result = []
        for s in sessions:
            d = s.to_dict()
            result.append({"id": s.id, **d})
        result.sort(
            key=lambda x: x.get("createdAt") if x.get("createdAt") else "",
            reverse=True
        )
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Errore lettura sessioni: {e}")
        return None

def save_notification_prefs_sync(uid: str, prefs: dict):
    if not db or not uid:
        return
    try:
        db.collection("users").document(uid).set(
            {"notificationPrefs": prefs}, merge=True
        )
    except Exception as e:
        logger.error(f"Errore salvataggio prefs: {e}")

def get_notification_prefs_sync(uid: str) -> dict:
    if not db or not uid:
        return {}
    try:
        doc = db.collection("users").document(uid).get()
        if doc.exists:
            return doc.to_dict().get("notificationPrefs", {})
        return {}
    except Exception as e:
        logger.error(f"Errore lettura prefs: {e}")
        return {}

def build_user_context(user_data: dict, mood_data: list) -> str:
    if not user_data:
        return ""

    name            = user_data.get("displayName", "")
    today_min       = int(user_data.get("todayMin", 0))
    total_min       = int(user_data.get("totalMinutes", 0))
    streak          = int(user_data.get("streak", 0))
    sessions        = int(user_data.get("sessions", 0))
    daily_goal      = int(user_data.get("dailyGoal", 0))
    max_session     = int(user_data.get("maxSessionDuration", 0))
    is_donator      = user_data.get("isDonator", False)
    donation_tier   = user_data.get("donationTier", "")
    unlocked_badges = user_data.get("unlockedBadges", [])
    language        = user_data.get("language", "it")

    daily_minutes = user_data.get("dailyMinutes", {})
    daily_history = ""
    if daily_minutes:
        sorted_days = sorted(daily_minutes.items())
        daily_history = "\n- Meditation history by day:\n"
        for date, minutes in sorted_days:
            daily_history += f"  {date}: {int(minutes)} min\n"

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

        levels = [m.get("moodLevel", 0) for m in mood_data if m.get("moodLevel")]
        if levels:
            avg = sum(levels) / len(levels)
            mood_history += (
                f"  Average mood: {avg:.1f}/5 "
                f"({mood_label(round(avg))})\n"
            )

        latest       = mood_data[0]
        latest_level = latest.get("moodLevel", 0)
        mood_history += (
            f"\n- MOST RECENT MOOD: {latest_level}/5 "
            f"({mood_label(latest_level)}) — "
            f"use this to respond empathetically. "
            f"NEVER show this number to the user.\n"
        )
    else:
        mood_history = "\n- Mood history: no data yet. Invite the user to record their mood after the next session on Bodhio.life.\n"

    badges_str = ""
    if unlocked_badges:
        badges_str = f"\n- Unlocked badges: {', '.join(str(b) for b in unlocked_badges)}"

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

# ─── Notification jobs ───────────────────────────────────────

async def send_daily_reminders(tg_app):
    loop     = asyncio.get_event_loop()
    now_hour = datetime.now(timezone.utc).hour

    users = await loop.run_in_executor(None, get_all_telegram_users_sync)
    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        prefs = await loop.run_in_executor(
            None, lambda u=uid: get_notification_prefs_sync(u)
        )
        if not prefs.get("reminderEnabled", False):
            continue
        if prefs.get("reminderHour") is None:
            continue
        if int(prefs["reminderHour"]) != now_hour:
            continue

        name     = user.get("displayName", "")
        language = user.get("language", "it")
        streak   = int(user.get("streak", 0))

        prompt = (
            f"Send a warm, short daily meditation reminder to {name}. "
            f"Their current streak is {streak} days. "
            f"Encourage them to keep the streak going. "
            f"Be motivating but gentle. Max 3 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"📬 Reminder giornaliero inviato a {chat_id}")
            except Exception as e:
                logger.error(f"Errore invio reminder a {chat_id}: {e}")


async def send_inactivity_alerts(tg_app):
    loop   = asyncio.get_event_loop()
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=INACTIVITY_DAYS)

    users = await loop.run_in_executor(None, get_all_telegram_users_sync)
    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        prefs = await loop.run_in_executor(
            None, lambda u=uid: get_notification_prefs_sync(u)
        )
        if prefs.get("inactivityAlertDisabled", False):
            continue

        session = await loop.run_in_executor(
            None, lambda u=uid: get_latest_session_sync(u)
        )

        last_date = None
        if session:
            raw = session.get("createdAt")
            if raw and hasattr(raw, "utcoffset"):
                last_date = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw

        if last_date and last_date > cutoff:
            continue

        name     = user.get("displayName", "")
        language = user.get("language", "it")
        streak   = int(user.get("streak", 0))

        prompt = (
            f"The user {name} hasn't meditated in {INACTIVITY_DAYS} days. "
            f"Their last streak was {streak} days. "
            f"Send a gentle, warm message to invite them back to their practice. "
            f"Don't be pushy. Be like a caring friend. Max 3 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"💤 Inactivity alert inviato a {chat_id}")
            except Exception as e:
                logger.error(f"Errore invio inactivity alert a {chat_id}: {e}")


async def send_weekly_reports(tg_app):
    loop  = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, get_all_telegram_users_sync)

    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        prefs = await loop.run_in_executor(
            None, lambda u=uid: get_notification_prefs_sync(u)
        )
        if prefs.get("weeklyReportDisabled", False):
            continue

        mood_data = await loop.run_in_executor(
            None, lambda u=uid: get_mood_data_sync(u)
        )

        name      = user.get("displayName", "")
        language  = user.get("language", "it")
        total_min = int(user.get("totalMinutes", 0))
        streak    = int(user.get("streak", 0))
        sessions  = int(user.get("sessions", 0))

        daily_minutes = user.get("dailyMinutes", {})
        now      = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        week_min = sum(
            int(v) for k, v in daily_minutes.items()
            if k >= week_ago.strftime("%Y-%m-%d")
        )

        recent_moods = []
        for m in mood_data:
            raw = m.get("date")
            if raw:
                try:
                    md = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw
                    if md > week_ago:
                        recent_moods.append(m.get("moodLevel", 0))
                except Exception:
                    pass

        avg_mood_str = ""
        if recent_moods:
            avg = sum(recent_moods) / len(recent_moods)
            avg_mood_str = f"Average mood this week: {mood_label(round(avg))}."

        prompt = (
            f"Generate a warm weekly meditation report for {name}. "
            f"This week they meditated for {week_min} minutes. "
            f"Total sessions ever: {sessions}. "
            f"Current streak: {streak} days. "
            f"Total minutes ever: {total_min}. "
            f"{avg_mood_str} "
            f"Celebrate their progress, mention one highlight, "
            f"and give a gentle encouragement for next week. "
            f"Keep it under 5 sentences. Warm and personal tone."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"📊 Report settimanale inviato a {chat_id}")
            except Exception as e:
                logger.error(f"Errore invio report a {chat_id}: {e}")


async def check_post_session_mood(tg_app):
    loop  = asyncio.get_event_loop()
    now   = datetime.now(timezone.utc)
    users = await loop.run_in_executor(None, get_all_telegram_users_sync)

    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        mood_data = await loop.run_in_executor(
            None, lambda u=uid: get_mood_data_sync(u)
        )
        if not mood_data:
            continue

        latest_mood = mood_data[0]
        mood_date   = latest_mood.get("date")
        if not mood_date:
            continue

        try:
            md = mood_date.replace(tzinfo=timezone.utc) if mood_date.tzinfo is None else mood_date
            if (now - md).total_seconds() > 600:
                continue
        except Exception:
            continue

        prefs = await loop.run_in_executor(
            None, lambda u=uid: get_notification_prefs_sync(u)
        )
        try:
            mood_date_str = mood_date.strftime("%Y-%m-%d %H:%M") if hasattr(mood_date, 'strftime') else str(mood_date)
        except Exception:
            mood_date_str = str(mood_date)

        if prefs.get("lastMoodNotified") == mood_date_str:
            continue

        level    = latest_mood.get("moodLevel", 0)
        name     = user.get("displayName", "")
        language = user.get("language", "it")
        duration = latest_mood.get("sessionDuration", 0)
        note     = latest_mood.get("note", "")
        note_str = f'They also left a note: "{note}".' if note else ""

        prompt = (
            f"The user {name} just finished a meditation session of {duration} seconds "
            f"and recorded their mood as: {mood_label(level)}. {note_str} "
            f"Send a short, warm, empathetic message reacting to their mood. "
            f"If they felt stressed, show concern and offer support. "
            f"If they felt calm or very calm, celebrate with them warmly. "
            f"If neutral, acknowledge it kindly. "
            f"NEVER mention any numeric mood score. Max 3 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"🧘 Post-session mood message inviato a {chat_id}")
                await loop.run_in_executor(
                    None,
                    lambda u=uid, d=mood_date_str, p=prefs: save_notification_prefs_sync(
                        u, {**p, "lastMoodNotified": d}
                    )
                )
            except Exception as e:
                logger.error(f"Errore invio post-session a {chat_id}: {e}")


# ─── Helpers per lingua nei comandi ──────────────────────────
async def get_lang(chat_id: int, loop) -> tuple[str, str]:
    """Ritorna (uid, language) per un dato chat_id."""
    user_data = await loop.run_in_executor(
        None, lambda: get_user_data_sync(chat_id)
    )
    uid  = user_data.get("uid", "")
    lang = user_data.get("language", "it")
    return uid, lang


# ─── Handlers ────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name    = update.effective_user.first_name or ""
    args    = context.args
    loop    = asyncio.get_event_loop()

    logger.info(f"/start ricevuto da {name}, args: {args}")

    if not args:
        # Prova a leggere la lingua da Firebase; fallback su "it"
        _, lang = await get_lang(chat_id, loop)
        await update.message.reply_text(t("start_welcome", lang, name))
        return

    token = args[0].strip().replace("\n", "").replace("\r", "")
    logger.info(f"Token ricevuto: '{token}'")

    # Prima del linking non abbiamo ancora la lingua → usiamo "it" come default
    lang = "it"

    if not db:
        await update.message.reply_text(t("db_unavailable", lang))
        return

    try:
        docs  = db.collection("telegram_link_tokens").stream()
        found = None
        for d in docs:
            if d.id == token:
                found = d
                break

        if not found:
            await update.message.reply_text(t("token_not_found", lang))
            return

        data = found.to_dict()
        uid  = data.get("uid")

        if data.get("used"):
            await update.message.reply_text(t("token_already_used", lang))
            return

        db.collection("users").document(uid).set(
            {"telegramChatId": chat_id}, merge=True
        )
        db.collection("telegram_link_tokens").document(token).update(
            {"used": True, "telegramChatId": chat_id}
        )

        # Ora possiamo leggere la lingua dell'utente appena collegato
        user_doc = db.collection("users").document(uid).get()
        if user_doc.exists:
            lang = user_doc.to_dict().get("language", "it")

        await update.message.reply_text(t("account_linked", lang))

    except Exception as e:
        logger.error(f"Errore linking: {e}")
        await update.message.reply_text(t("link_error", lang))


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    loop    = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    if not context.args:
        await update.message.reply_text(t("remind_usage", lang))
        return

    time_str = context.args[0].strip()
    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text(t("remind_invalid_format", lang))
        return

    if not uid:
        await update.message.reply_text(t("remind_not_linked", lang))
        return

    prefs = await loop.run_in_executor(
        None, lambda: get_notification_prefs_sync(uid)
    )
    prefs["reminderEnabled"] = True
    prefs["reminderHour"]    = hour
    prefs["reminderMinute"]  = minute

    await loop.run_in_executor(
        None, lambda: save_notification_prefs_sync(uid, prefs)
    )
    await update.message.reply_text(t("remind_set", lang, hour, minute))


async def cmd_remindoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    loop    = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    if not uid:
        await update.message.reply_text(t("not_linked", lang))
        return

    prefs = await loop.run_in_executor(
        None, lambda: get_notification_prefs_sync(uid)
    )
    prefs["reminderEnabled"] = False
    await loop.run_in_executor(
        None, lambda: save_notification_prefs_sync(uid, prefs)
    )
    await update.message.reply_text(t("remind_off", lang))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    loop    = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    if not uid:
        await update.message.reply_text(t("not_linked", lang))
        return

    prefs = await loop.run_in_executor(
        None, lambda: get_notification_prefs_sync(uid)
    )

    if prefs.get("reminderEnabled"):
        h = prefs.get("reminderHour", 0)
        m = prefs.get("reminderMinute", 0)
        reminder_status = t("settings_reminder_active", lang, h, m)
    else:
        reminder_status = t("settings_reminder_off", lang)

    inactivity = t("settings_active", lang) if not prefs.get("inactivityAlertDisabled") else t("settings_disabled", lang)
    weekly     = t("settings_active", lang) if not prefs.get("weeklyReportDisabled") else t("settings_disabled", lang)

    await update.message.reply_text(
        t("settings", lang, reminder_status, inactivity, weekly, INACTIVITY_DAYS)
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    loop    = asyncio.get_event_loop()
    _, lang = await get_lang(chat_id, loop)
    chat_histories[chat_id] = []
    await update.message.reply_text(t("reset_done", lang))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text    = update.message.text
    loop    = asyncio.get_event_loop()

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
    reply = strip_markdown(completion.choices[0].message.content)
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
    tg_app.add_handler(CommandHandler("start",     cmd_start))
    tg_app.add_handler(CommandHandler("reset",     cmd_reset))
    tg_app.add_handler(CommandHandler("remind",    cmd_remind))
    tg_app.add_handler(CommandHandler("remindoff", cmd_remindoff))
    tg_app.add_handler(CommandHandler("settings",  cmd_settings))
    tg_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        send_daily_reminders,
        CronTrigger(minute=0),
        args=[tg_app],
        id="daily_reminders",
    )
    scheduler.add_job(
        send_inactivity_alerts,
        CronTrigger(hour="*/6", minute=30),
        args=[tg_app],
        id="inactivity_alerts",
    )
    scheduler.add_job(
        send_weekly_reports,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        args=[tg_app],
        id="weekly_reports",
    )
    scheduler.add_job(
        check_post_session_mood,
        CronTrigger(minute="*/5"),
        args=[tg_app],
        id="post_session_mood",
    )
    scheduler.start()
    logger.info("✅ Scheduler avviato")

    webhook_path = f"/webhook/{BOT_TOKEN}"
    web_app      = web.Application()
    web_app.router.add_get("/",       health)
    web_app.router.add_get("/health", health)
    web_app.router.add_post(webhook_path, lambda r: webhook(r, tg_app))

    await tg_app.initialize()
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}{webhook_path}")
    await tg_app.start()

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("🚀 Bot avviato con notifiche attive")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())