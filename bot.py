import os
import sqlite3
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from google import genai

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

client = genai.Client(api_key=GEMINI_API_KEY)

DB_FILE = "/tmp/messages.db" if os.getenv("VERCEL") else "messages.db"


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_message(message):
    if not message.text:
        return

    user = message.from_user

    conn = db()

    conn.execute("""
        INSERT INTO messages (
            chat_id,
            message_id,
            user_id,
            username,
            full_name,
            text,
            timestamp
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        message.chat_id,
        message.message_id,
        user.id if user else None,
        user.username if user else None,
        user.full_name if user else "Unknown",
        message.text,
        message.date.isoformat(),
    ))

    conn.commit()
    conn.close()


def get_last_messages(chat_id, limit=100):
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM messages
        WHERE chat_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (chat_id, limit)).fetchall()

    conn.close()

    return list(reversed(rows))


def get_messages_since(chat_id, since):
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM messages
        WHERE chat_id = ?
        AND timestamp >= ?
        ORDER BY id
    """, (
        chat_id,
        since.isoformat(),
    )).fetchall()

    conn.close()

    return rows


def format_messages(rows):
    output = []

    for row in rows:
        name = (
            row["full_name"]
            or row["username"]
            or "Unknown"
        )

        output.append(
            f'{name}: {row["text"]}'
        )

    return "\n".join(output)


def gemini_summary(rows):
    conversation = format_messages(rows)

    prompt = f"""
Summarize the following Telegram group conversation.

Return:

📌 TELEGRAM CHAT SUMMARY

📝 Main Topics
• ...

✅ Decisions
• ...

👤 Action Items
• Person → Task

⏰ Deadlines / Dates
• ...

🔗 Important Links / Resources
• ...

❓ Unresolved Questions
• ...

Rules:
- Do not invent information.
- Preserve important names.
- Preserve dates and deadlines.
- Keep the summary concise.
- If a section has nothing relevant, write "None".

Conversation:

{conversation}
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )

    return response.text


def gemini_action_items(rows):
    conversation = format_messages(rows)

    prompt = f"""
Extract action items from this Telegram conversation.

Return:

✅ ACTION ITEMS

• Person:
  Task:
  Deadline:

Rules:
- Do not invent tasks.
- Use "Unassigned" if no owner is mentioned.
- Use "Not specified" if no deadline is mentioned.

Conversation:

{conversation}
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )

    return response.text


async def send_long(update, text):
    limit = 3900

    while len(text) > limit:
        pos = text.rfind("\n", 0, limit)

        if pos == -1:
            pos = limit

        await update.message.reply_text(
            text[:pos]
        )

        text = text[pos:].strip()

    if text:
        await update.message.reply_text(text)


async def collect(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.message

    if not message:
        return

    if message.from_user and message.from_user.is_bot:
        return

    save_message(message)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        """
🤖 NGEB Summary Bot

Commands:

/summary
Summarize last 100 messages.

/summary 50
Summarize last 50 messages.

/today
Summarize today's messages.

/last1h
Summarize last hour.

/actionitems
Extract action items.

/stats
Show stored-message statistics.
        """
    )


async def summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    limit = 100

    if context.args:
        try:
            limit = int(context.args[0])
            limit = max(1, min(limit, 500))
        except ValueError:
            await update.message.reply_text(
                "Example: /summary 50"
            )
            return

    rows = get_last_messages(chat_id, limit)

    if not rows:
        await update.message.reply_text(
            "No messages stored yet."
        )
        return

    await context.bot.send_chat_action(
        chat_id=chat_id,
        action=ChatAction.TYPING,
    )

    try:
        result = gemini_summary(rows)
        await send_long(update, result)

    except Exception as e:
        print("Gemini error:", e)

        await update.message.reply_text(
            "❌ Gemini summary failed. Check the terminal logs."
        )


async def today(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    now = datetime.now(timezone.utc)

    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    rows = get_messages_since(chat_id, start)

    if not rows:
        await update.message.reply_text(
            "No messages stored today."
        )
        return

    await context.bot.send_chat_action(
        chat_id=chat_id,
        action=ChatAction.TYPING,
    )

    try:
        result = gemini_summary(rows)
        await send_long(update, result)

    except Exception as e:
        print("Gemini error:", e)

        await update.message.reply_text(
            "❌ Gemini summary failed."
        )


async def last1h(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    start = (
        datetime.now(timezone.utc)
        - timedelta(hours=1)
    )

    rows = get_messages_since(chat_id, start)

    if not rows:
        await update.message.reply_text(
            "No messages during the last hour."
        )
        return

    try:
        result = gemini_summary(rows)
        await send_long(update, result)

    except Exception as e:
        print("Gemini error:", e)

        await update.message.reply_text(
            "❌ Gemini summary failed."
        )


async def actionitems(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    rows = get_last_messages(
        chat_id,
        100,
    )

    if not rows:
        await update.message.reply_text(
            "No messages available."
        )
        return

    try:
        result = gemini_action_items(rows)
        await send_long(update, result)

    except Exception as e:
        print("Gemini error:", e)

        await update.message.reply_text(
            "❌ Could not extract action items."
        )


async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    conn = db()

    row = conn.execute("""
        SELECT
            COUNT(*) AS count,
            COUNT(DISTINCT user_id) AS users
        FROM messages
        WHERE chat_id = ?
    """, (chat_id,)).fetchone()

    conn.close()

    await update.message.reply_text(
        f"""
📊 Chat Statistics

Messages stored: {row["count"]}
Users: {row["users"]}
        """
    )


def main():
    init_db()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("summary", summary)
    )

    app.add_handler(
        CommandHandler("today", today)
    )

    app.add_handler(
        CommandHandler("last1h", last1h)
    )

    app.add_handler(
        CommandHandler(
            "actionitems",
            actionitems,
        )
    )

    app.add_handler(
        CommandHandler("stats", stats)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            collect,
        )
    )


    app.run_polling()


if __name__ == "__main__":
    if os.getenv("VERCEL"):
        print("Running on Vercel - polling disabled")
    else:
        main()
