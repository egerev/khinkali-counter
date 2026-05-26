"""Khinkali Counter — Telegram bot + API backend.

Photo proof required. Vision counts khinkali.
API serves webapp for unified stats.
"""

import os
import json
import logging
import base64
import re
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from threading import Thread

import httpx
from aiohttp import web
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("khinkali")

BOT_TOKEN = os.environ["KHINKALI_BOT_TOKEN"]
WEBAPP_URL = os.environ.get("KHINKALI_WEBAPP_URL", "https://egerev.github.io/khinkali-counter/")
API_PORT = int(os.environ.get("KHINKALI_API_PORT", "5199"))

DATA_DIR = Path(os.environ.get("KHINKALI_DATA_DIR", "/Users/egoregerev/mcp-servers/khinkali-bot/data"))
PHOTOS_DIR = DATA_DIR / "photos"
STATS_FILE = DATA_DIR / "stats.json"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

TBILISI_TZ = timezone(timedelta(hours=4))

# Anthropic key
ANT_KEY = ""
try:
    auth = json.loads(Path("/opt/hermes-shared/auth.json").read_text())
    for cred in auth.get("credential_pool", {}).get("anthropic", []):
        ANT_KEY = cred.get("access_token", "")
        break
except Exception:
    pass


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


async def count_khinkali_vision(img_data_b64: str) -> int:
    if not ANT_KEY:
        return 0
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANT_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 20,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data_b64}},
                            {"type": "text", "text": "Look at this plate carefully. Count ALL individual khinkali (Georgian dumplings - large round pleated dumplings with a twisted top knot). Count every single one you can see, including partially hidden ones. Reply with ONLY the number."}
                        ]
                    }],
                },
            )
            if resp.status_code == 200:
                text = resp.json()["content"][0]["text"].strip()
                nums = re.findall(r'\d+', text)
                if nums:
                    return int(nums[0])
    except Exception as exc:
        log.warning(f"Vision failed: {exc}")
    return 0


def add_khinkali(user_id: str, user_name: str, count: int, photo_path: str = "") -> dict:
    stats = load_stats()
    s = get_user_stats(stats, user_id)
    s["name"] = user_name
    s["total"] += count
    s["today"] += count
    s["session"] += count
    if s["session"] > s["record"]:
        s["record"] = s["session"]
    if photo_path:
        ts = datetime.now(TBILISI_TZ).strftime("%Y%m%d_%H%M%S")
        s["photos"].append({"path": photo_path, "count": count, "ts": ts})
    save_stats(stats)
    return s


def get_leaderboard() -> list:
    stats = load_stats()
    entries = sorted(
        [(uid, s) for uid, s in stats.items() if s.get("total", 0) > 0],
        key=lambda x: x[1]["total"],
        reverse=True,
    )[:10]
    return [{"rank": i+1, "name": s["name"], "total": s["total"], "today": s["today"], "record": s["record"]} for i, (uid, s) in enumerate(entries)]


# ─── Telegram Bot Handlers ───

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

    photo = update.message.photo[-1]
    file = await photo.get_file()
    ts = datetime.now(TBILISI_TZ).strftime("%Y%m%d_%H%M%S")
    photo_path = str(PHOTOS_DIR / f"{user_id}_{ts}.jpg")
    await file.download_to_drive(photo_path)

    with open(photo_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode()

    count = await count_khinkali_vision(img_data)

    if count == 0:
        await update.message.reply_text("🤔 Не вижу хинкалей на фото! Попробуй сфоткать ближе.")
        return

    s = add_khinkali(user_id, user_name, count, photo_path)

    await update.message.reply_text(
        f"🥟 *+{count} хинкали!*\n\n"
        f"📊 Сессия: *{s['session']}*\n"
        f"📅 Сегодня: *{s['today']}*\n"
        f"🏆 Рекорд: *{s['record']}*\n"
        f"📈 Всего: *{s['total']}*",
        parse_mode="Markdown",
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lb = get_leaderboard()
    if not lb:
        await update.message.reply_text("Пока никто не ел хинкали 😢")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏅 *Топ хинкалеедов FFF*\n"]
    for e in lb:
        medal = medals[e["rank"]-1] if e["rank"] <= 3 else f"{e['rank']}."
        lines.append(f"{medal} {e['name']} — *{e['total']}* шт")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
    await update.message.reply_text("✅ Сессия сброшена! 🥟")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📸 Отправь фото хинкалей! Без пруфа не считается 😉")


# ─── API Server ───

async def api_stats(request):
    user_id = request.match_info["user_id"]
    stats = load_stats()
    s = get_user_stats(stats, user_id)
    return web.json_response(s, headers={"Access-Control-Allow-Origin": "*"})


async def api_leaderboard(request):
    return web.json_response(get_leaderboard(), headers={"Access-Control-Allow-Origin": "*"})


async def api_upload(request):
    try:
        reader = await request.multipart()
        user_id = None
        user_name = "Гость"
        img_data = None

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "user_id":
                user_id = (await part.text()).strip()
            elif part.name == "user_name":
                user_name = (await part.text()).strip()
            elif part.name == "photo":
                raw = await part.read()
                img_data = base64.b64encode(raw).decode()
                ts = datetime.now(TBILISI_TZ).strftime("%Y%m%d_%H%M%S")
                photo_path = str(PHOTOS_DIR / f"{user_id or 'anon'}_{ts}.jpg")
                with open(photo_path, "wb") as f:
                    f.write(raw)

        if not img_data:
            return web.json_response({"error": "no photo"}, status=400, headers={"Access-Control-Allow-Origin": "*"})

        count = await count_khinkali_vision(img_data)
        if count == 0:
            return web.json_response({"error": "no khinkali found", "count": 0}, headers={"Access-Control-Allow-Origin": "*"})

        s = add_khinkali(user_id or "anon", user_name, count, photo_path)
        return web.json_response({"count": count, "stats": s}, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500, headers={"Access-Control-Allow-Origin": "*"})


async def api_cors(request):
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


def run_api():
    app = web.Application()
    app.router.add_get("/api/stats/{user_id}", api_stats)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_post("/api/upload", api_upload)
    app.router.add_route("OPTIONS", "/api/{path:.*}", api_cors)
    web.run_app(app, host="0.0.0.0", port=API_PORT, print=None)


# ─── Main ───

def main():
    # Start API in background thread
    api_thread = Thread(target=run_api, daemon=True)
    api_thread.start()
    log.info(f"API server on port {API_PORT}")

    # Start bot
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
