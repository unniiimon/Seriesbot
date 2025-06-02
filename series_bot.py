import logging
import os
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
    CallbackContext,
)
from pymongo import MongoClient
from telegram.error import BadRequest, TelegramError

# ===== Configuration =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "5387919847").split(",")}
PIC_URL = os.environ.get("PIC_URL", "")

# Database setup
client = MongoClient(MONGO_URI, connectTimeoutMS=30000, socketTimeoutMS=30000)
db = client.series_bot
series_collection = db.series

# ===== Helper Functions =====
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def standardize_season(season_str: str) -> str:
    """Convert various season formats to S01 style"""
    season_str = season_str.upper().replace("SEASON", "").strip()
    if season_str.startswith("S"):
        season_str = season_str[1:]
    try:
        return f"S{int(season_str):02d}"
    except ValueError:
        return f"S{season_str}"

def get_next_episode(series_name: str, season: str) -> int:
    """Get next episode number with optimized query"""
    result = series_collection.find_one(
        {"name": series_name.lower()},
        {f"seasons.{season}.episodes": 1}
    )
    if not result or not result.get("seasons", {}).get(season, {}).get("episodes"):
        return 1
    existing = result["seasons"][season]["episodes"].keys()
    return max([int(ep[1:]) for ep in existing if ep.startswith("E") and ep[1:].isdigit()], default=0) + 1

# ===== Command Handlers =====
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🎬 *Series Manager Bot*\n\n"
        "🔹 Admins: `/add series | season | quality`\n"
        "🔹 Users: Send series name to browse\n\n"
        "📌 Example: `/add Breaking Bad | S1 | 720p`",
        parse_mode="Markdown"
    )

def add_series(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("🚫 Admin only", quote=True)
        return

    try:
        # Smart parsing that handles | and ｜ with spaces
        raw_text = " ".join(context.args)
        parts = re.split(r"\s*\|\s*|\s*\｜\s*", raw_text)
        
        if len(parts) != 3:
            raise ValueError
        
        series, season, quality = [x.strip() for x in parts]
        season = standardize_season(season)

        context.user_data.update({
            "upload_series": series.lower(),
            "upload_season": season,
            "upload_quality": quality.lower(),
            "upload_episode": get_next_episode(series.lower(), season)
        })

        update.message.reply_text(
            f"⚡ *Ready for Upload*:\n"
            f"• Series: `{series}`\n"
            f"• Season: `{season}`\n"
            f"• Quality: `{quality}`\n"
            f"• Next: `E{context.user_data['upload_episode']}`\n\n"
            "📤 Send files now (documents/videos)",
            parse_mode="Markdown",
            quote=True
        )
    except Exception as e:
        logger.warning(f"Add command error: {e}")
        update.message.reply_text(
            "❌ Usage: `/add series | season | quality`\n"
            "Examples:\n"
            "• `/add Stranger Things | S3 | 720p`\n"
            "• `/add Loki | Season 1 | 1080p`",
            parse_mode="Markdown",
            quote=True
        )

# ===== File Handler =====
def handle_file(update: Update, context: CallbackContext):
    try:
        # Immediate response
        update.message.reply_chat_action("typing")
        
        # Validate context
        if not all(k in context.user_data for k in ["upload_series", "upload_season", "upload_quality"]):
            update.message.reply_text(
                "⚠️ First use: `/add series | season | quality`",
                parse_mode="Markdown",
                quote=True
            )
            return

        # Get file
        file = update.message.document or update.message.video
        if not file:
            update.message.reply_text("📛 Send video/document files only", quote=True)
            return

        # Prepare data
        series = context.user_data["upload_series"]
        season = context.user_data["upload_season"]
        quality = context.user_data["upload_quality"]
        ep_num = context.user_data.get("upload_episode", 1)
        ep_key = f"E{ep_num:02d}"

        # Check if episode exists
        exists = bool(series_collection.find_one({
            "name": series,
            f"seasons.{season}.episodes.{ep_key}": {"$exists": True}
        }))

        # Database operation
        series_collection.update_one(
            {"name": series},
            {"$set": {
                f"seasons.{season}.episodes.{ep_key}.qualities.{quality}": file.file_id,
                "last_updated": datetime.now()
            }},
            upsert=True
        )

        # Update counter if new episode
        if not exists:
            context.user_data["upload_episode"] = ep_num + 1
            action = "✨ NEW"
        else:
            action = "🔄 Updated"

        update.message.reply_text(
            f"{action} `{series.title()} {season}{ep_key} ({quality})`\n"
            f"Next: E{context.user_data.get('upload_episode', ep_num + 1):02d}",
            parse_mode="Markdown",
            quote=True
        )
    except TelegramError as e:
        logger.error(f"Telegram API error: {e}")
    except Exception as e:
        logger.error(f"File handler error: {e}")
        update.message.reply_text("⚠️ Processing error. Try again.", quote=True)

# ===== Main =====
def main():
    updater = Updater(BOT_TOKEN, request_kwargs={
        'read_timeout': 20, 'connect_timeout': 20
    })
    dp = updater.dispatcher

    # Handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("add", add_series))
    dp.add_handler(MessageHandler(
        Filters.document | Filters.video,
        handle_file
    ))

    # Start bot
    updater.start_polling(
        poll_interval=0.5,
        timeout=20,
        drop_pending_updates=True
    )
    logger.info("Bot is running with optimized performance...")
    updater.idle()

if __name__ == "__main__":
    main()
