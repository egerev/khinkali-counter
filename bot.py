"""Khinkali Counter — Telegram Mini App bot with photo proof.

Users send photos of khinkali, vision model counts them,
adds to counter, saves photos for collage.
"""

import os
import json
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import httpx
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("khinkali")

BOT_TOKEN = os.environ["KHINKALI_BOT_TOKEN"]
WEBAPP_URL = os.environ.get("KHINKALI_WEBAPP_URL", "https://egerev.github.io/khinkali-counter/")

# Storage
DATA_DIR = Path(os.environ.get("KHINKALI_DATA_DIR", "/Users/egoregerev/mcp-servers/khinkali-bot/data"))
PHOTOS_DIR = DATA_DIR / "photos"
STATS_FILE = DATA_DIR / "stats.json"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

TBILISI_TZ = timezone(timedelta(hours=4))


def load_stats() -> dict:
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text())
    return {}


def save_stats(stats: dict):
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2))


def get_user_stats(stats: dict, user_id: str) -> dict:
    if user_id not in stats:
        stats[user_id] = {"name": "", "total": 0, "today": 0, "record": 0, "session": 0, "today_date": "", "photos": []}
    s = stats[user_id]
    today = datetime.now(TBILISI_TZ).strftime("%Y-%m-%d")
    if s.get("today_date") != today:
        s["today"] = 0
        s["today_date"] = today
    return s


async def count_khinkali_vision(photo_path: str) -> int:
    """Use local vision model to count khinkali in photo."""
    try:
        import base64
        with open(photo_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()

        # Try using Hermes auxiliary vision (mlx)
        # Fallback: use OpenAI API if available
        auth_file = Path("/opt/hermes-shared/auth.json")
        if auth_file.exists():
            auth = json.loads(auth_file.read_text())
            pool = auth.get("credential_pool", {}).get("openai-codex", [])
            token = None
            for cred in pool:
                if cred.get("access_token"):
                    token = cred["access_token"]

            if token:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [
                                {"role": "system", "content": "You are a khinkali counter. Look at the photo and count the number of khinkali (Georgian dumplings). Reply with ONLY a number. If you see a plate with khinkali, count them. If no khinkali visible, reply 0."},
                                {"role": "user", "content": [
                                    {"type": "text", "text": "How many khinkali are in this photo?"},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}}
                                ]}
                            ],
                            "max_tokens": 10,
                        },
                    )
                    if resp.status_code == 200:
                        text = resp.json()["choices"][0]["message"]["content"].strip()
                        # Extract number
                        import re
                        nums = re.findall(r'\d+', text)
                        if nums:
                            return int(nums[0])
    except Exception as exc:
        log.warning(f"Vision count failed: {exc}")
    return 0


def format_leaderboard(stats: dict) -> str:
    entries = sorted(
        [(uid, s) for uid, s in stats.items() if s.get("total", 0) > 0],
        key=lambda x: x[1]["total"],
        reverse=True,
    )[:10]
    if not entries:
        return "Пока никто не ел хинкали 😢"

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏅 *Топ хинкалеедов FFF*\n"]
    for i, (uid, s) in enumerate(entries):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {s['name']} — *{s['total']}* шт")
    return "\n".join(lines)


# Handlers

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[KeyboardButton("🥟 Счётчик хинкалей", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "🥟 *Счётчик хинкалей FFF*\n\n"
        "Отправь фото своих хинкалей — я посчитаю!\n"
        "Без фото-пруфа не считается 📸\n\n"
        "/top — лидерборд\n"
        "/my — моя статистика\n"
        "/reset — сбросить сессию",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown",
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = str(user.id)
    user_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Гость"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Download photo
    photo = update.message.photo[-1]  # highest resolution
    file = await photo.get_file()

    ts = datetime.now(TBILISI_TZ).strftime("%Y%m%d_%H%M%S")
    photo_path = str(PHOTOS_DIR / f"{user_id}_{ts}.jpg")
    await file.download_to_drive(photo_path)

    # Count khinkali
    count = await count_khinkali_vision(photo_path)

    if count == 0:
        await update.message.reply_text(
            "🤔 Не вижу хинкалей на фото! Сфоткай тарелку с хинкалями и попробуй ещё раз.",
        )
        return

    # Update stats
    stats = load_stats()
    s = get_user_stats(stats, user_id)
    s["name"] = user_name
    s["total"] += count
    s["today"] += count
    s["session"] += count
    if s["session"] > s["record"]:
        s["record"] = s["session"]
    s["photos"].append({"path": photo_path, "count": count, "ts": ts})
    save_stats(stats)

    await update.message.reply_text(
        f"🥟 *+{count} хинкали!*\n\n"
        f"📊 Сессия: *{s['session']}*\n"
        f"📅 Сегодня: *{s['today']}*\n"
        f"🏆 Рекорд: *{s['record']}*\n"
        f"📈 Всего: *{s['total']}*",
        parse_mode="Markdown",
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = load_stats()
    await update.message.reply_text(format_leaderboard(stats), parse_mode="Markdown")


async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    stats = load_stats()
    s = get_user_stats(stats, user_id)
    photos_count = len(s.get("photos", []))
    await update.message.reply_text(
        f"📊 *Твоя статистика*\n\n"
        f"🥟 Сессия: *{s['session']}*\n"
        f"📅 Сегодня: *{s['today']}*\n"
        f"🏆 Рекорд: *{s['record']}*\n"
        f"📈 Всего: *{s['total']}*\n"
        f"📸 Фото: *{photos_count}*",
        parse_mode="Markdown",
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    stats = load_stats()
    s = get_user_stats(stats, user_id)
    s["session"] = 0
    save_stats(stats)
    await update.message.reply_text("✅ Сессия сброшена. Начинай новый заход! 🥟")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📸 Отправь фото хинкалей! Без пруфа не считается 😉")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("my", my_stats))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Khinkali bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
