import os
import re
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://bodhio.life,https://www.bodhio.life"
).split(",")

# ─── i18n ─────────────────────────────────────────────────────────────────────
STRINGS = {
    "start_welcome": {
        "it": lambda name: (
            f"Ciao {name}! Sono Bodhi 🪷\n\n"
            "Sono il tuo assistente di meditazione su Bodhio.life. "
            "Scrivimi nella lingua che preferisci!\n\n"
            "Comandi:\n"
            "/start — mostra questo messaggio\n"
            "/reset — cancella cronologia chat\n"
            "/timezone Continente/Città — imposta il tuo fuso orario (es. /timezone Europe/Rome)\n"
            "/remind HH:MM — imposta reminder giornaliero (es. /remind 08:00)\n"
            "/remindoff — disattiva reminder giornaliero\n"
            "/settings — mostra le tue preferenze notifiche\n"
            "/notifiche off — disattiva TUTTE le notifiche"
        ),
        "en": lambda name: (
            f"Hi {name}! I'm Bodhi 🪷\n\n"
            "I'm your meditation assistant on Bodhio.life. "
            "Write to me in whichever language you prefer!\n\n"
            "Commands:\n"
            "/start — show this message\n"
            "/reset — clear chat history\n"
            "/timezone Continent/City — set your timezone (e.g. /timezone America/New_York)\n"
            "/remind HH:MM — set daily reminder (e.g. /remind 08:00)\n"
            "/remindoff — disable daily reminder\n"
            "/settings — show your notification preferences\n"
            "/notifiche off — disable ALL notifications"
        ),
        "es": lambda name: (
            f"¡Hola {name}! Soy Bodhi 🪷\n\n"
            "Soy tu asistente de meditación en Bodhio.life. "
            "¡Escríbeme en el idioma que prefieras!\n\n"
            "Comandos:\n"
            "/start — mostrar este mensaje\n"
            "/reset — borrar historial del chat\n"
            "/timezone Continente/Ciudad — configura tu zona horaria (ej. /timezone America/Mexico_City)\n"
            "/remind HH:MM — configurar recordatorio diario (ej. /remind 08:00)\n"
            "/remindoff — desactivar recordatorio diario\n"
            "/settings — ver tus preferencias de notificaciones\n"
            "/notifiche off — desactivar TODAS las notificaciones"
        ),
    },
    "db_unavailable":     {"it": "⚠️ Database non disponibile.", "en": "⚠️ Database unavailable.", "es": "⚠️ Base de datos no disponible."},
    "token_not_found":    {"it": "❌ Token non trovato.", "en": "❌ Token not found.", "es": "❌ Token no encontrado."},
    "token_already_used": {"it": "⚠️ Token già utilizzato.", "en": "⚠️ Token already used.", "es": "⚠️ Token ya utilizado."},
    "account_linked": {
        "it": "✅ Account collegato!\n\n🪷 Da ora ti invierò promemoria personalizzati.\n\nPrima imposta il tuo fuso orario:\n/timezone Europe/Rome\n\nPoi usa /remind HH:MM per il reminder giornaliero!",
        "en": "✅ Account linked!\n\n🪷 I'll now send you personalized reminders.\n\nFirst set your timezone:\n/timezone America/New_York\n\nThen use /remind HH:MM to set your daily reminder!",
        "es": "✅ ¡Cuenta vinculada!\n\n🪷 Ahora te enviaré recordatorios personalizados.\n\nPrimero configura tu zona horaria:\n/timezone America/Mexico_City\n\n¡Luego usa /remind HH:MM para tu recordatorio diario!",
    },
    "link_error": {"it": "⚠️ Errore durante il collegamento.", "en": "⚠️ Error during linking.", "es": "⚠️ Error durante la vinculación."},

    # /timezone
    "timezone_usage": {
        "it": (
            "Usa: /timezone Continente/Città\n\n"
            "Esempi:\n"
            "🇮🇹 /timezone Europe/Rome\n"
            "🇬🇧 /timezone Europe/London\n"
            "🇺🇸 /timezone America/New_York\n"
            "🇲🇽 /timezone America/Mexico_City\n"
            "🇧🇷 /timezone America/Sao_Paulo\n"
            "🇦🇷 /timezone America/Argentina/Buenos_Aires\n"
            "🇯🇵 /timezone Asia/Tokyo\n"
            "🇮🇳 /timezone Asia/Kolkata\n"
            "🇦🇺 /timezone Australia/Sydney\n\n"
            "Lista completa: en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        ),
        "en": (
            "Use: /timezone Continent/City\n\n"
            "Examples:\n"
            "🇮🇹 /timezone Europe/Rome\n"
            "🇬🇧 /timezone Europe/London\n"
            "🇺🇸 /timezone America/New_York\n"
            "🇲🇽 /timezone America/Mexico_City\n"
            "🇧🇷 /timezone America/Sao_Paulo\n"
            "🇦🇷 /timezone America/Argentina/Buenos_Aires\n"
            "🇯🇵 /timezone Asia/Tokyo\n"
            "🇮🇳 /timezone Asia/Kolkata\n"
            "🇦🇺 /timezone Australia/Sydney\n\n"
            "Full list: en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        ),
        "es": (
            "Usa: /timezone Continente/Ciudad\n\n"
            "Ejemplos:\n"
            "🇮🇹 /timezone Europe/Rome\n"
            "🇬🇧 /timezone Europe/London\n"
            "🇺🇸 /timezone America/New_York\n"
            "🇲🇽 /timezone America/Mexico_City\n"
            "🇧🇷 /timezone America/Sao_Paulo\n"
            "🇦🇷 /timezone America/Argentina/Buenos_Aires\n"
            "🇯🇵 /timezone Asia/Tokyo\n"
            "🇮🇳 /timezone Asia/Kolkata\n"
            "🇦🇺 /timezone Australia/Sydney\n\n"
            "Lista completa: en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        ),
    },
    "timezone_invalid": {
        "it": lambda tz: f"❌ Fuso orario non valido: {tz}\n\nUsa /timezone per vedere gli esempi.",
        "en": lambda tz: f"❌ Invalid timezone: {tz}\n\nUse /timezone to see examples.",
        "es": lambda tz: f"❌ Zona horaria no válida: {tz}\n\nUsa /timezone para ver ejemplos.",
    },
    "timezone_set": {
        "it": lambda tz, offset: f"✅ Fuso orario impostato: {tz} (UTC{offset:+.0f}h) 🌍\n\nOra puoi usare /remind HH:MM con la tua ora locale!",
        "en": lambda tz, offset: f"✅ Timezone set: {tz} (UTC{offset:+.0f}h) 🌍\n\nNow you can use /remind HH:MM with your local time!",
        "es": lambda tz, offset: f"✅ Zona horaria configurada: {tz} (UTC{offset:+.0f}h) 🌍\n\n¡Ahora puedes usar /remind HH:MM con tu hora local!",
    },
    "timezone_not_set": {
        "it": "⚠️ Prima imposta il tuo fuso orario:\n/timezone Europe/Rome\n\n(oppure la città più vicina a te)",
        "en": "⚠️ First set your timezone:\n/timezone America/New_York\n\n(or the city closest to you)",
        "es": "⚠️ Primero configura tu zona horaria:\n/timezone America/Mexico_City\n\n(o la ciudad más cercana a ti)",
    },

    "remind_usage":          {"it": "Scrivi:\n/remind HH:MM  (es. /remind 08:00)", "en": "Write:\n/remind HH:MM  (e.g. /remind 08:00)", "es": "Escribe:\n/remind HH:MM  (ej. /remind 08:00)"},
    "remind_invalid_format": {"it": "Formato non valido. Usa HH:MM, es. /remind 08:00", "en": "Invalid format. Use HH:MM, e.g. /remind 08:00", "es": "Formato inválido. Usa HH:MM, ej. /remind 08:00"},
    "remind_not_linked":     {"it": "Account non collegato. Usa prima il link da Bodhio.life.", "en": "Account not linked. Use the link from Bodhio.life first.", "es": "Cuenta no vinculada. Usa primero el enlace de Bodhio.life."},
    "remind_set": {
        "it": lambda h, m, tz: f"✅ Reminder impostato ogni giorno alle {h:02d}:{m:02d} ({tz}) 🪷\nUsa /remindoff per disattivarlo.",
        "en": lambda h, m, tz: f"✅ Reminder set every day at {h:02d}:{m:02d} ({tz}) 🪷\nUse /remindoff to disable it.",
        "es": lambda h, m, tz: f"✅ Recordatorio configurado cada día a las {h:02d}:{m:02d} ({tz}) 🪷\nUsa /remindoff para desactivarlo.",
    },
    "remind_off":   {"it": "🔕 Reminder giornaliero disattivato.", "en": "🔕 Daily reminder disabled.", "es": "🔕 Recordatorio diario desactivado."},
    "not_linked":   {"it": "Account non collegato.", "en": "Account not linked.", "es": "Cuenta no vinculada."},
    "settings": {
        "it": lambda rs, ia, wr, days, tz: (
            f"Le tue preferenze notifiche:\n\n"
            f"🌍 Fuso orario: {tz}\n"
            f"📅 Reminder giornaliero: {rs}\n"
            f"💤 Alert inattività ({days}gg): {ia}\n"
            f"📊 Report settimanale: {wr}\n\n"
            f"Comandi:\n"
            f"/timezone Continente/Città — cambia fuso orario\n"
            f"/remind HH:MM — imposta reminder\n"
            f"/remindoff — disattiva reminder"
        ),
        "en": lambda rs, ia, wr, days, tz: (
            f"Your notification settings:\n\n"
            f"🌍 Timezone: {tz}\n"
            f"📅 Daily reminder: {rs}\n"
            f"💤 Inactivity alert ({days}d): {ia}\n"
            f"📊 Weekly report: {wr}\n\n"
            f"Commands:\n"
            f"/timezone Continent/City — change timezone\n"
            f"/remind HH:MM — set reminder\n"
            f"/remindoff — disable reminder"
        ),
        "es": lambda rs, ia, wr, days, tz: (
            f"Tus preferencias:\n\n"
            f"🌍 Zona horaria: {tz}\n"
            f"📅 Recordatorio diario: {rs}\n"
            f"💤 Alerta inactividad ({days}d): {ia}\n"
            f"📊 Informe semanal: {wr}\n\n"
            f"Comandos:\n"
            f"/timezone Continente/Ciudad — cambiar zona horaria\n"
            f"/remind HH:MM — configurar\n"
            f"/remindoff — desactivar"
        ),
    },
    "settings_reminder_active": {
        "it": lambda h, m: f"attivo alle {h:02d}:{m:02d} (ora locale)",
        "en": lambda h, m: f"active at {h:02d}:{m:02d} (local time)",
        "es": lambda h, m: f"activo a las {h:02d}:{m:02d} (hora local)",
    },
    "settings_reminder_off": {"it": "disattivato", "en": "disabled", "es": "desactivado"},
    "settings_active":       {"it": "attivo",      "en": "active",   "es": "activo"},
    "settings_disabled":     {"it": "disattivato", "en": "disabled", "es": "desactivado"},
    "reset_done":            {"it": "🔄 Cronologia cancellata 🙏", "en": "🔄 History cleared 🙏", "es": "🔄 Historial borrado 🙏"},
    "notifiche_off": {
        "it": "🔕 Tutte le notifiche disattivate.\n\nPuoi riattivarle in qualsiasi momento con /remind HH:MM o scrivendo /settings.",
        "en": "🔕 All notifications disabled.\n\nYou can re-enable them anytime with /remind HH:MM or /settings.",
        "es": "🔕 Todas las notificaciones desactivadas.\n\nPuedes reactivarlas en cualquier momento con /remind HH:MM o /settings.",
    },
    "notifiche_already_off": {
        "it": "ℹ️ Le notifiche sono già disattivate.",
        "en": "ℹ️ Notifications are already disabled.",
        "es": "ℹ️ Las notificaciones ya están desactivadas.",
    },
}

def t(key, lang, *args):
    lang  = lang if lang in ("it", "en", "es") else "en"
    value = STRINGS.get(key, {}).get(lang) or STRINGS.get(key, {}).get("en", "")
    if callable(value):
        return value(*args)
    return value

# ─── Timezone helpers ──────────────────────────────────────────────────────────

def local_to_utc(hour, minute, tz_name):
    """
    Converte ora locale (HH:MM) in UTC usando il timezone tz_name.
    Restituisce (hour_utc, minute_utc).
    Solleva ZoneInfoNotFoundError se il timezone non è valido.
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    dt_local  = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    dt_utc    = dt_local.astimezone(timezone.utc)
    return dt_utc.hour, dt_utc.minute

def get_utc_offset_hours(tz_name):
    """Restituisce l'offset UTC corrente in ore (es. +2.0 per Europe/Rome in estate)."""
    try:
        tz  = ZoneInfo(tz_name)
        now = datetime.now(tz)
        return now.utcoffset().total_seconds() / 3600
    except Exception:
        return 0

# ─── Firebase ─────────────────────────────────────────────────────────────────
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

# ─── AI ───────────────────────────────────────────────────────────────────────
groq_client    = Groq(api_key=GROQ_API_KEY)
chat_histories = defaultdict(list)

SYSTEM_PROMPT = """You are Bodhi 🪷, the official assistant of Bodhio.life — 
a free meditation app with no subscriptions and no ads.

LANGUAGE RULE: Always reply in the same language the user writes to you.
FORMATTING RULE: Never use Markdown. Plain text only.

Your personality: warm, calm, encouraging, present and mindful.

MOOD SCALE (internal only — NEVER show numbers to user):
1=Molto Stressato 2=Stressato 3=Neutro 4=Calmo 5=Molto Calmo

IMPORTANT RULES:
- Use [USER DATA] section for personalized responses.
- Always refer to Bodhio.life for tracking.
- Never mention other apps (Headspace, Calm, etc.).
- NEVER show numeric mood scores to users.
- Keep responses concise and warm.
"""

# ─── Utility ──────────────────────────────────────────────────────────────────
def strip_markdown(text):
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__',     r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'_(.*?)_',       r'\1', text)
    text = re.sub(r'`(.*?)`',       r'\1', text)
    text = re.sub(r'#{1,6}\s',      '',    text)
    return text.strip()

def mood_label(level):
    return {1:"Molto Stressato",2:"Stressato",3:"Neutro",4:"Calmo",5:"Molto Calmo"}.get(level,"Sconosciuto")

def ai_message(prompt, language="it"):
    lang_map = {"it":"Italian","en":"English","es":"Spanish"}
    lang = lang_map.get(language, "Italian")
    try:
        c = groq_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role":"system","content":f"You are Bodhi 🪷, a warm meditation assistant. Reply ONLY in {lang}. No Markdown. Be concise and caring."},
                {"role":"user","content":prompt},
            ],
        )
        return strip_markdown(c.choices[0].message.content)
    except Exception as e:
        logger.error(f"AI error: {e}")
        return ""

# ─── Firebase helpers ─────────────────────────────────────────────────────────
def get_user_data_sync(chat_id):
    if not db: return {}
    try:
        for u in db.collection("users").where("telegramChatId","==",chat_id).limit(1).stream():
            return {"uid": u.id, **u.to_dict()}
        return {}
    except Exception as e:
        logger.error(f"get_user_data: {e}"); return {}

def get_all_telegram_users_sync():
    if not db: return []
    try:
        return [
            {"uid":u.id,**u.to_dict()}
            for u in db.collection("users").where("telegramChatId","!=",None).stream()
            if u.to_dict().get("telegramChatId")
        ]
    except Exception as e:
        logger.error(f"get_all_users: {e}"); return []

def get_mood_data_sync(uid):
    if not db or not uid: return []
    try:
        docs = (
            db.collection("users")
            .document(uid)
            .collection("moods")
            .limit(20)
            .stream()
        )
        result = []
        for m in docs:
            d = m.to_dict()
            result.append({
                "date":            d.get("createdAt"),
                "moodLevel":       int(d.get("moodLevel", 0)),
                "sessionDuration": int(d.get("sessionDuration", 0)),
                "note":            d.get("note"),
            })
        def safe_date_key(x):
            d = x.get("date")
            if d is None: return ""
            if hasattr(d, "strftime"): return d.strftime("%Y-%m-%d %H:%M:%S")
            return str(d)
        result.sort(key=safe_date_key, reverse=True)
        return result[:10]
    except Exception as e:
        logger.error(f"get_mood: {e}"); return []

def get_latest_session_sync(uid):
    if not db or not uid: return None
    try:
        result = [
            {"id":s.id,**s.to_dict()}
            for s in db.collection("sessions").where("userId","==",uid).limit(20).stream()
        ]
        result.sort(key=lambda x: x.get("createdAt") or "", reverse=True)
        return result[0] if result else None
    except Exception as e:
        logger.error(f"get_session: {e}"); return None

def save_notification_prefs_sync(uid, prefs):
    if not db or not uid: return
    try:
        db.collection("users").document(uid).set({"notificationPrefs": prefs}, merge=True)
    except Exception as e:
        logger.error(f"save_prefs: {e}")

def get_notification_prefs_sync(uid):
    if not db or not uid: return {}
    try:
        doc = db.collection("users").document(uid).get()
        return doc.to_dict().get("notificationPrefs", {}) if doc.exists else {}
    except Exception as e:
        logger.error(f"get_prefs: {e}"); return {}

def save_timezone_sync(uid, tz_name):
    """Salva il timezone in users/{uid}/timezone."""
    if not db or not uid: return
    try:
        db.collection("users").document(uid).set({"timezone": tz_name}, merge=True)
    except Exception as e:
        logger.error(f"save_timezone: {e}")

def get_timezone_sync(uid):
    """Legge il timezone da users/{uid}/timezone. Restituisce None se non impostato."""
    if not db or not uid: return None
    try:
        doc = db.collection("users").document(uid).get()
        return doc.to_dict().get("timezone") if doc.exists else None
    except Exception as e:
        logger.error(f"get_timezone: {e}"); return None

def has_meditated_today_sync(user_data):
    today_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_minutes = user_data.get("dailyMinutes", {})
    today_min     = int(user_data.get("todayMin", 0))
    return today_min > 0 or int(daily_minutes.get(today_str, 0)) > 0

def build_user_context(user_data, mood_data):
    if not user_data: return ""
    name            = user_data.get("displayName","")
    today_min       = int(user_data.get("todayMin",0))
    total_min       = int(user_data.get("totalMinutes",0))
    streak          = int(user_data.get("streak",0))
    sessions        = int(user_data.get("sessions",0))
    daily_goal      = int(user_data.get("dailyGoal",0))
    max_session     = int(user_data.get("maxSessionDuration",0))
    is_donator      = user_data.get("isDonator",False)
    donation_tier   = user_data.get("donationTier","")
    unlocked_badges = user_data.get("unlockedBadges",[])
    language        = user_data.get("language","it")
    daily_minutes   = user_data.get("dailyMinutes",{})

    daily_history = ""
    if daily_minutes:
        daily_history = "\n- Meditation history by day:\n" + "".join(
            f"  {d}: {int(v)} min\n" for d,v in sorted(daily_minutes.items())
        )

    mood_history = ""
    if mood_data:
        mood_history = "\n- Recent mood history:\n"
        for m in mood_data:
            date = m.get("date")
            try: date_str = date.strftime("%Y-%m-%d %H:%M") if hasattr(date,'strftime') else str(date)
            except: date_str = str(date)
            mood_history += f"  {date_str}: mood {m.get('moodLevel',0)}/5 ({mood_label(m.get('moodLevel',0))}), session {m.get('sessionDuration',0)}s"
            if m.get("note"): mood_history += f", note: {m['note']}"
            mood_history += "\n"
        levels = [m.get("moodLevel",0) for m in mood_data if m.get("moodLevel")]
        if levels:
            avg = sum(levels)/len(levels)
            mood_history += f"  Average: {avg:.1f}/5 ({mood_label(round(avg))})\n"
        latest = mood_data[0]
        mood_history += f"\n- MOST RECENT MOOD: {latest.get('moodLevel',0)}/5 ({mood_label(latest.get('moodLevel',0))}) — NEVER show this number to user.\n"
    else:
        mood_history = "\n- Mood history: no data yet.\n"

    badges_str = f"\n- Unlocked badges: {', '.join(str(b) for b in unlocked_badges)}" if unlocked_badges else ""
    donor_str  = f"\n- Supporter tier: {donation_tier} 💛" if is_donator and donation_tier else ""

    return f"""
[USER DATA from Bodhio.life]
- Name: {name}
- Language: {language}
- Minutes today: {today_min} / goal: {daily_goal}
- Total minutes: {total_min}
- Longest session: {max_session} min
- Streak: {streak} days
- Total sessions: {sessions}
{badges_str}{donor_str}
{daily_history}
{mood_history}
Use ALL this data when user asks about their practice.
NEVER reveal numeric mood scores to the user.
"""

# ─── Notification jobs ────────────────────────────────────────────────────────

async def send_daily_reminders(tg_app):
    """
    Invia il reminder giornaliero.
    L'ora è salvata in UTC (convertita al momento del /remind).
    Ordine check:
      1. reminder abilitato?
      2. ora UTC impostata?
      3. ora UTC corrente == ora UTC salvata?
      4. finestra 2 minuti
      5. già inviato oggi?
    """
    loop      = asyncio.get_event_loop()
    now_utc   = datetime.now(timezone.utc)
    now_h     = now_utc.hour
    now_m     = now_utc.minute
    today_str = now_utc.strftime("%Y-%m-%d")

    logger.info(f"⏰ send_daily_reminders chiamato — {now_utc.strftime('%H:%M')} UTC")

    users = await loop.run_in_executor(None, get_all_telegram_users_sync)
    logger.info(f"👥 Utenti Telegram trovati: {len(users)}")

    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        name    = user.get("displayName", "?")
        if not chat_id or not uid:
            continue

        prefs = await loop.run_in_executor(None, lambda u=uid: get_notification_prefs_sync(u))

        logger.info(
            f"🔍 {name}: enabled={prefs.get('reminderEnabled')}, "
            f"hour_utc={prefs.get('reminderHourUTC')}, min_utc={prefs.get('reminderMinuteUTC')}, "
            f"lastSent={prefs.get('lastReminderSent')}, now={now_h}:{now_m:02d} UTC"
        )

        # 1. Reminder abilitato?
        if not prefs.get("reminderEnabled", False):
            logger.info(f"  ⏭️ {name}: reminder disabilitato")
            continue

        # 2. Ora UTC impostata?
        if prefs.get("reminderHourUTC") is None:
            logger.info(f"  ⏭️ {name}: ora UTC non impostata (usa /timezone poi /remind)")
            continue

        # 3. Ora corrente == ora UTC salvata?
        if int(prefs["reminderHourUTC"]) != now_h:
            logger.info(f"  ⏭️ {name}: ora UTC {prefs['reminderHourUTC']} != {now_h}")
            continue

        # 4. Finestra di 2 minuti
        target_m = int(prefs.get("reminderMinuteUTC", 0))
        diff = (now_m - target_m) % 60
        if diff > 2:
            logger.info(f"  ⏭️ {name}: fuori finestra (diff={diff} min)")
            continue

        # 5. Già inviato oggi?
        if prefs.get("lastReminderSent") == today_str:
            logger.info(f"  ⏭️ {name}: già inviato oggi")
            continue

        language = user.get("language", "it")
        streak   = int(user.get("streak", 0))
        logger.info(f"📬 Invio reminder a {chat_id} ({name})")

        prompt = (
            f"Send a warm, short daily meditation reminder to {name}. "
            f"Their current streak is {streak} days. "
            f"Encourage them to keep it going. Max 3 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"✅ Reminder inviato a {chat_id}")
                await loop.run_in_executor(
                    None,
                    lambda u=uid, p=prefs: save_notification_prefs_sync(
                        u, {**p, "lastReminderSent": today_str}
                    )
                )
            except Exception as e:
                logger.error(f"Reminder error {chat_id}: {e}")


async def send_inactivity_alerts(tg_app):
    """Max 1 alert ogni 3 giorni per utente."""
    loop      = asyncio.get_event_loop()
    now       = datetime.now(timezone.utc)
    cutoff    = now - timedelta(days=INACTIVITY_DAYS)
    today_str = now.strftime("%Y-%m-%d")

    users = await loop.run_in_executor(None, get_all_telegram_users_sync)
    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        prefs = await loop.run_in_executor(None, lambda u=uid: get_notification_prefs_sync(u))
        if prefs.get("inactivityAlertDisabled", False):
            continue

        last_sent = prefs.get("lastInactivitySent")
        if last_sent:
            try:
                last_sent_date = datetime.strptime(last_sent, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if (now - last_sent_date).days < 3:
                    continue
            except Exception:
                pass

        if has_meditated_today_sync(user):
            continue

        session   = await loop.run_in_executor(None, lambda u=uid: get_latest_session_sync(u))
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
            f"Last streak was {streak} days. "
            f"Send a gentle, warm message to invite them back. "
            f"Don't be pushy. Max 3 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"💤 Inactivity alert inviato a {chat_id}")
                await loop.run_in_executor(
                    None,
                    lambda u=uid, p=prefs: save_notification_prefs_sync(
                        u, {**p, "lastInactivitySent": today_str}
                    )
                )
            except Exception as e:
                logger.error(f"Inactivity error {chat_id}: {e}")


async def send_weekly_reports(tg_app):
    loop  = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, get_all_telegram_users_sync)

    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        prefs = await loop.run_in_executor(None, lambda u=uid: get_notification_prefs_sync(u))
        if prefs.get("weeklyReportDisabled", False):
            continue

        mood_data = await loop.run_in_executor(None, lambda u=uid: get_mood_data_sync(u))
        name      = user.get("displayName","")
        language  = user.get("language","it")
        total_min = int(user.get("totalMinutes",0))
        streak    = int(user.get("streak",0))
        sessions  = int(user.get("sessions",0))
        now       = datetime.now(timezone.utc)
        week_ago  = now - timedelta(days=7)
        daily_min = user.get("dailyMinutes",{})
        week_min  = sum(int(v) for k,v in daily_min.items() if k >= week_ago.strftime("%Y-%m-%d"))

        recent_moods = []
        for m in mood_data:
            raw = m.get("date")
            if raw:
                try:
                    md = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw
                    if md > week_ago: recent_moods.append(m.get("moodLevel",0))
                except: pass

        avg_mood_str = ""
        if recent_moods:
            avg = sum(recent_moods)/len(recent_moods)
            avg_mood_str = f"Average mood this week: {mood_label(round(avg))}."

        prompt = (
            f"Generate a warm weekly meditation report for {name}. "
            f"This week: {week_min} minutes. Sessions ever: {sessions}. "
            f"Streak: {streak} days. Total: {total_min} min. {avg_mood_str} "
            f"Celebrate progress, mention one highlight, gentle encouragement. Max 5 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"📊 Weekly report inviato a {chat_id}")
            except Exception as e:
                logger.error(f"Weekly report error {chat_id}: {e}")


async def check_post_session_mood(tg_app):
    loop  = asyncio.get_event_loop()
    now   = datetime.now(timezone.utc)
    users = await loop.run_in_executor(None, get_all_telegram_users_sync)

    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        mood_data = await loop.run_in_executor(None, lambda u=uid: get_mood_data_sync(u))
        if not mood_data:
            continue

        latest    = mood_data[0]
        mood_date = latest.get("date")
        if not mood_date:
            continue

        try:
            md = mood_date.replace(tzinfo=timezone.utc) if mood_date.tzinfo is None else mood_date
            if (now - md).total_seconds() > 600:
                continue
        except: continue

        prefs = await loop.run_in_executor(None, lambda u=uid: get_notification_prefs_sync(u))
        try:    mood_date_str = mood_date.strftime("%Y-%m-%d %H:%M") if hasattr(mood_date,'strftime') else str(mood_date)
        except: mood_date_str = str(mood_date)

        if prefs.get("lastMoodNotified") == mood_date_str:
            continue

        level    = latest.get("moodLevel",0)
        name     = user.get("displayName","")
        language = user.get("language","it")
        duration = latest.get("sessionDuration",0)
        note     = latest.get("note","")
        note_str = f'They left a note: "{note}".' if note else ""

        prompt = (
            f"User {name} finished a {duration}s meditation session. "
            f"Mood: {mood_label(level)}. {note_str} "
            f"Send a short warm empathetic message. "
            f"If stressed: show concern, offer breathing exercise. "
            f"If calm: celebrate warmly. NEVER mention numeric scores. Max 3 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"🧘 Post-session mood inviato a {chat_id}")
                await loop.run_in_executor(
                    None,
                    lambda u=uid, d=mood_date_str, p=prefs: save_notification_prefs_sync(
                        u, {**p, "lastMoodNotified": d}
                    )
                )
            except Exception as e:
                logger.error(f"Post-session error {chat_id}: {e}")


async def send_stress_mood_alerts(tg_app):
    """Max 1 stress alert al giorno per utente."""
    loop      = asyncio.get_event_loop()
    now       = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    users = await loop.run_in_executor(None, get_all_telegram_users_sync)
    for user in users:
        chat_id = user.get("telegramChatId")
        uid     = user.get("uid")
        if not chat_id or not uid:
            continue

        prefs = await loop.run_in_executor(None, lambda u=uid: get_notification_prefs_sync(u))
        if prefs.get("inactivityAlertDisabled", False):
            continue
        if prefs.get("lastStressAlertSent") == today_str:
            continue

        mood_data = await loop.run_in_executor(None, lambda u=uid: get_mood_data_sync(u))
        if not mood_data:
            continue

        stressed_today = False
        for m in mood_data:
            mood_date = m.get("date")
            if not mood_date:
                continue
            try:
                md = mood_date.replace(tzinfo=timezone.utc) if mood_date.tzinfo is None else mood_date
                if md.strftime("%Y-%m-%d") == today_str and m.get("moodLevel", 0) <= 2:
                    stressed_today = True
                    break
            except:
                continue

        if not stressed_today:
            continue

        name     = user.get("displayName", "")
        language = user.get("language", "it")

        prompt = (
            f"The user {name} recorded a stressed or very stressed mood today. "
            f"Send a short, warm, caring message asking how they are doing. "
            f"Show genuine concern without being dramatic. "
            f"Gently invite them to try a short meditation or breathing exercise. "
            f"NEVER mention numeric mood scores. Max 3 sentences."
        )
        message = ai_message(prompt, language)
        if message:
            try:
                await tg_app.bot.send_message(chat_id=chat_id, text=message)
                logger.info(f"😟 Stress mood alert inviato a {chat_id}")
                await loop.run_in_executor(
                    None,
                    lambda u=uid, p=prefs: save_notification_prefs_sync(
                        u, {**p, "lastStressAlertSent": today_str}
                    )
                )
            except Exception as e:
                logger.error(f"Stress alert error {chat_id}: {e}")


# ─── Telegram handlers ────────────────────────────────────────────────────────

async def get_lang(chat_id, loop):
    user_data = await loop.run_in_executor(None, lambda: get_user_data_sync(chat_id))
    return user_data.get("uid",""), user_data.get("language","it")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name    = update.effective_user.first_name or ""
    args    = context.args
    loop    = asyncio.get_event_loop()
    logger.info(f"/start da {name}, args: {args}")

    if not args:
        _, lang = await get_lang(chat_id, loop)
        await update.message.reply_text(t("start_welcome", lang, name))
        return

    token = args[0].strip().replace("\n","").replace("\r","")
    lang  = "it"

    if not db:
        await update.message.reply_text(t("db_unavailable", lang))
        return

    try:
        found = None
        for d in db.collection("telegram_link_tokens").stream():
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

        db.collection("users").document(uid).set({"telegramChatId": chat_id}, merge=True)
        db.collection("telegram_link_tokens").document(token).update(
            {"used": True, "telegramChatId": chat_id}
        )

        user_doc = db.collection("users").document(uid).get()
        if user_doc.exists:
            lang = user_doc.to_dict().get("language","it")

        await update.message.reply_text(t("account_linked", lang))
    except Exception as e:
        logger.error(f"Link error: {e}")
        await update.message.reply_text(t("link_error", lang))


async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /timezone Continente/Città
    Valida e salva il timezone in Firebase. Usato da /remind per la conversione UTC.
    """
    chat_id   = update.effective_chat.id
    loop      = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    # Nessun argomento → mostra esempi
    if not context.args:
        await update.message.reply_text(t("timezone_usage", lang))
        return

    if not uid:
        await update.message.reply_text(t("not_linked", lang))
        return

    tz_name = context.args[0].strip()

    # Valida
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        await update.message.reply_text(t("timezone_invalid", lang, tz_name))
        return

    offset = get_utc_offset_hours(tz_name)
    await loop.run_in_executor(None, lambda: save_timezone_sync(uid, tz_name))
    logger.info(f"🌍 Timezone impostato per {uid}: {tz_name} (UTC{offset:+.0f}h)")
    await update.message.reply_text(t("timezone_set", lang, tz_name, offset))


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remind HH:MM
    Richiede che l'utente abbia già impostato il timezone con /timezone.
    Converte l'ora locale in UTC e salva entrambe in Firebase.
    """
    chat_id   = update.effective_chat.id
    loop      = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    if not context.args:
        await update.message.reply_text(t("remind_usage", lang))
        return

    try:
        hour, minute = map(int, context.args[0].strip().split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text(t("remind_invalid_format", lang))
        return

    if not uid:
        await update.message.reply_text(t("remind_not_linked", lang))
        return

    # Controlla timezone salvato
    tz_name = await loop.run_in_executor(None, lambda: get_timezone_sync(uid))
    if not tz_name:
        await update.message.reply_text(t("timezone_not_set", lang))
        return

    # Converti ora locale → UTC
    try:
        hour_utc, minute_utc = local_to_utc(hour, minute, tz_name)
    except ZoneInfoNotFoundError:
        await update.message.reply_text(t("timezone_invalid", lang, tz_name))
        return

    logger.info(f"🕐 {uid}: {hour:02d}:{minute:02d} {tz_name} → {hour_utc:02d}:{minute_utc:02d} UTC")

    prefs = await loop.run_in_executor(None, lambda: get_notification_prefs_sync(uid))
    prefs.update({
        "reminderEnabled":   True,
        "reminderHour":      hour,         # ora locale (per display in /settings)
        "reminderMinute":    minute,
        "reminderHourUTC":   hour_utc,     # ora UTC (usata dal job scheduler)
        "reminderMinuteUTC": minute_utc,
        "reminderTimezone":  tz_name,
    })
    prefs.pop("lastReminderSent", None)    # reset per attivare subito al prossimo match
    await loop.run_in_executor(None, lambda: save_notification_prefs_sync(uid, prefs))
    await update.message.reply_text(t("remind_set", lang, hour, minute, tz_name))


async def cmd_remindoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    loop      = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    if not uid:
        await update.message.reply_text(t("not_linked", lang))
        return

    prefs = await loop.run_in_executor(None, lambda: get_notification_prefs_sync(uid))
    prefs["reminderEnabled"] = False
    await loop.run_in_executor(None, lambda: save_notification_prefs_sync(uid, prefs))
    await update.message.reply_text(t("remind_off", lang))


async def cmd_notifiche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disattiva TUTTE le notifiche con /notifiche off"""
    chat_id   = update.effective_chat.id
    loop      = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    args = context.args
    if not args or args[0].lower() != "off":
        await update.message.reply_text(
            "Usa /notifiche off per disattivare tutte le notifiche." if lang == "it"
            else "Use /notifiche off to disable all notifications."
        )
        return

    if not uid:
        await update.message.reply_text(t("not_linked", lang))
        return

    prefs = await loop.run_in_executor(None, lambda: get_notification_prefs_sync(uid))

    already_off = (
        not prefs.get("reminderEnabled", False) and
        prefs.get("inactivityAlertDisabled", False) and
        prefs.get("weeklyReportDisabled", False)
    )
    if already_off:
        await update.message.reply_text(t("notifiche_already_off", lang))
        return

    prefs["reminderEnabled"]         = False
    prefs["inactivityAlertDisabled"] = True
    prefs["weeklyReportDisabled"]    = True
    await loop.run_in_executor(None, lambda: save_notification_prefs_sync(uid, prefs))
    await update.message.reply_text(t("notifiche_off", lang))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    loop      = asyncio.get_event_loop()
    uid, lang = await get_lang(chat_id, loop)

    if not uid:
        await update.message.reply_text(t("not_linked", lang))
        return

    prefs      = await loop.run_in_executor(None, lambda: get_notification_prefs_sync(uid))
    tz_name    = await loop.run_in_executor(None, lambda: get_timezone_sync(uid))
    tz_display = tz_name if tz_name else (
        "non impostato" if lang == "it" else ("not set" if lang == "en" else "no configurado")
    )

    if prefs.get("reminderEnabled"):
        rs = t("settings_reminder_active", lang,
               prefs.get("reminderHour", 0),
               prefs.get("reminderMinute", 0))
    else:
        rs = t("settings_reminder_off", lang)

    ia = t("settings_active" if not prefs.get("inactivityAlertDisabled") else "settings_disabled", lang)
    wr = t("settings_active" if not prefs.get("weeklyReportDisabled") else "settings_disabled", lang)
    await update.message.reply_text(t("settings", lang, rs, ia, wr, INACTIVITY_DAYS, tz_display))


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

    user_data = await loop.run_in_executor(None, lambda: get_user_data_sync(chat_id))
    uid       = user_data.get("uid","")
    mood_data = await loop.run_in_executor(None, lambda: get_mood_data_sync(uid)) if uid else []

    full_system = SYSTEM_PROMPT + build_user_context(user_data, mood_data)
    chat_histories[chat_id].append({"role":"user","content":text})
    if len(chat_histories[chat_id]) > 20:
        chat_histories[chat_id] = chat_histories[chat_id][-20:]

    try:
        c = groq_client.chat.completions.create(
            model=MODEL,
            messages=[{"role":"system","content":full_system}] + chat_histories[chat_id],
        )
        reply = strip_markdown(c.choices[0].message.content)
        chat_histories[chat_id].append({"role":"assistant","content":reply})
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"handle_message error: {e}")
        await update.message.reply_text("⚠️ Qualcosa è andato storto. Riprova 🙏")


# ─── Web endpoints ────────────────────────────────────────────────────────────
async def health_handler(request):
    return web.Response(text="OK")


async def tick_handler(request, tg_app):
    logger.info("🔔 /tick ricevuto")
    await send_daily_reminders(tg_app)
    return web.Response(text="tick OK")


async def telegram_webhook_handler(request, tg_app):
    data   = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return web.Response(text="OK")


def get_cors_headers(origin):
    allowed = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin":  allowed,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


async def chat_handler(request):
    """Secure proxy for Bodhio.life web chat widget."""
    origin = request.headers.get("Origin","")
    cors   = get_cors_headers(origin)

    if request.method == "OPTIONS":
        return web.Response(status=204, headers=cors)

    try:
        body     = await request.json()
        messages = body.get("messages", [])[-10:]

        if not messages:
            return web.Response(
                status=400,
                text=json.dumps({"error":"messages required"}),
                content_type="application/json",
                headers=cors,
            )

        full_messages = [{"role":"system","content":SYSTEM_PROMPT}] + messages
        c = groq_client.chat.completions.create(
            model=MODEL,
            messages=full_messages,
            temperature=0.7,
            max_tokens=1024,
        )
        reply = strip_markdown(c.choices[0].message.content)
        return web.Response(
            text=json.dumps({"reply":reply}),
            content_type="application/json",
            headers=cors,
        )
    except Exception as e:
        logger.error(f"Web chat error: {e}")
        return web.Response(
            status=500,
            text=json.dumps({"error":"Internal server error"}),
            content_type="application/json",
            headers=cors,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    tg_app = ApplicationBuilder().token(BOT_TOKEN).updater(None).build()
    tg_app.add_handler(CommandHandler("start",     cmd_start))
    tg_app.add_handler(CommandHandler("reset",     cmd_reset))
    tg_app.add_handler(CommandHandler("timezone",  cmd_timezone))
    tg_app.add_handler(CommandHandler("remind",    cmd_remind))
    tg_app.add_handler(CommandHandler("remindoff", cmd_remindoff))
    tg_app.add_handler(CommandHandler("settings",  cmd_settings))
    tg_app.add_handler(CommandHandler("notifiche", cmd_notifiche))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(send_daily_reminders,    CronTrigger(minute="*"),                           args=[tg_app], id="daily_reminders")
    scheduler.add_job(send_inactivity_alerts,  CronTrigger(hour=10, minute=0),                   args=[tg_app], id="inactivity_alerts")
    scheduler.add_job(send_weekly_reports,     CronTrigger(day_of_week="mon", hour=9, minute=0),  args=[tg_app], id="weekly_reports")
    scheduler.add_job(check_post_session_mood, CronTrigger(minute="*/5"),                         args=[tg_app], id="post_session_mood")
    scheduler.add_job(send_stress_mood_alerts, CronTrigger(minute="*/10"),                        args=[tg_app], id="stress_mood_alerts")
    scheduler.start()
    logger.info("✅ Scheduler avviato")

    webhook_path = f"/webhook/{BOT_TOKEN}"
    web_app      = web.Application()
    web_app.router.add_get("/",           health_handler)
    web_app.router.add_get("/health",     health_handler)
    web_app.router.add_get("/tick",       lambda r: tick_handler(r, tg_app))
    web_app.router.add_post(webhook_path, lambda r: telegram_webhook_handler(r, tg_app))
    web_app.router.add_post("/chat",      chat_handler)
    web_app.router.add_route("OPTIONS", "/chat", chat_handler)

    await tg_app.initialize()
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}{webhook_path}", drop_pending_updates=True)
    await tg_app.start()

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("🚀 Bot avviato")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())