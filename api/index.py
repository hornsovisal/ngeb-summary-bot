from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import (
    TELEGRAM_TOKEN,
    init_db,
    start,
    summary,
    today,
    last1h,
    actionitems,
    stats,
    collect,
)

app = FastAPI()

telegram_app = (
    Application.builder()
    .token(TELEGRAM_TOKEN)
    .build()
)

telegram_app.add_handler(
    CommandHandler("start", start)
)

telegram_app.add_handler(
    CommandHandler("summary", summary)
)

telegram_app.add_handler(
    CommandHandler("today", today)
)

telegram_app.add_handler(
    CommandHandler("last1h", last1h)
)

telegram_app.add_handler(
    CommandHandler("actionitems", actionitems)
)

telegram_app.add_handler(
    CommandHandler("stats", stats)
)

telegram_app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        collect,
    )
)

initialized = False


async def initialize_bot():
    global initialized

    if not initialized:
        init_db()
        await telegram_app.initialize()
        initialized = True


@app.get("/")
async def home():
    return {
        "status": "online",
        "bot": "NGEB Summary Bot"
    }


@app.post("/api/telegram")
async def telegram_webhook(request: Request):
    await initialize_bot()

    data = await request.json()

    update = Update.de_json(
        data,
        telegram_app.bot,
    )

    await telegram_app.process_update(update)

    return {"ok": True}
