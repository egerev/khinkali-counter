"""Khinkali Counter — Telegram Mini App bot.

Simple bot that opens a webapp for counting khinkali.
"""

import os
import logging
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["KHINKALI_BOT_TOKEN"]
WEBAPP_URL = os.environ.get("KHINKALI_WEBAPP_URL", "https://egerev.github.io/khinkali-counter/")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[KeyboardButton("🥟 Счётчик хинкалей", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "Привет! Нажми кнопку внизу чтобы открыть счётчик хинкалей 🥟",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("🥟 Открыть счётчик", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "Считай хинкали! 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("count", count))
    logging.info("Khinkali bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
