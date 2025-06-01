import logging
import os
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
from telegram.error import BadRequest

# ===== Configuration =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "5387919847").split(",")}  # Comma-separated IDs
PIC_URL = os.environ.get("PIC_URL", "")

# Database setup
client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
db = client.series_bot_db
series_collection = db.series

# ===== Core Functions =====
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def force_subscribe_check(update: Update, context: CallbackContext) -> bool:
    if not FORCE_SUB_CHANNEL:
        return True
    try:
        member = context.bot.get_chat_member(
            chat_id=FORCE_SUB_CHANNEL,
            user_id=update.effective_user.id
        )
        return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
    except BadRequest as e:
        logger.error(f"Force sub check failed: {e}")
        return False

def get_next_episode_number(series_name: str, season: str) -> int:
    """Get the next available episode number for a season"""
    series = series_collection.find_one(
        {"name": series_name.lower(), f"seasons.{season}": {"$exists": True}}
    )
    if not series:
        return 1
    
    episodes = series["seasons"][season].get("episodes", {})
    if not episodes:
        return 1
    
    last_ep = max(
        [int(k[1:]) for k in episodes.keys() if k.startswith("E") and k[1:].isdigit()],
        default=0
    )
    return last_ep + 1

# ===== Command Handlers =====
def start(update: Update, context: CallbackContext):
    if not force_subscribe_check(update, context):
        update.message.reply_text(
            f"⚠️ Please join @{FORCE_SUB_CHANNEL} to use this bot.",
            disable_web_page_preview=True
        )
        return
    
    update.message.reply_text(
        "🎬 *Series Bot*\n\n"
        "🔹 _Admins_: Use /add `<series>|<season>|<quality>` then upload files\n"
        "🔹 _Users_: Send series name to browse\n\n"
        "📌 Example: `/add Breaking Bad|S1|720p`",
        parse_mode="Markdown"
    )

def add_series_command(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("🚫 Admin access required")
        return

    if not context.args:
        update.message.reply_text(
            "❌ Usage: /add `<series>|<season>|<quality>`\n"
            "Example: `/add Game of Thrones|S1|1080p`",
            parse_mode="Markdown"
        )
        return

    try:
        series_name, season, quality = [x.strip() for x in " ".join(context.args).split("|")]
        if not all([series_name, season, quality]):
            raise ValueError
        
        # Standardize season format (S01 → S1)
        season = f"S{int(season.upper().replace('S', ''))}" if season.upper().replace('S', '').isdigit() else season.upper()
        
        context.user_data.update({
            "upload_series": series_name.lower(),
            "upload_season": season,
            "upload_quality": quality.lower(),
            "upload_episode": get_next_episode_number(series_name.lower(), season)
        })

        update.message.reply_text(
            f"📥 *Ready to upload*:\n"
            f"Series: `{series_name}`\n"
            f"Season: `{season}`\n"
            f"Quality: `{quality}`\n"
            f"Starting from: `E{context.user_data['upload_episode']}`\n\n"
            "📤 Just send files now!",
            parse_mode="Markdown"
        )
    except:
        update.message.reply_text(
            "❌ Invalid format! Use: /add `<series>|<season>|<quality>`\n"
            "Example: `/add Stranger Things|S3|720p`",
            parse_mode="Markdown"
        )

def handle_admin_file(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return

    # Validate upload context
    required_keys = ["upload_series", "upload_season", "upload_quality", "upload_episode"]
    if not all(k in context.user_data for k in required_keys):
        update.message.reply_text(
            "❌ Use /add command first to set series/season/quality!"
        )
        return

    # Get file
    file = update.message.document or update.message.video
    if not file:
        update.message.reply_text("📛 Please send a video or document file")
        return

    # Prepare database update
    series_name = context.user_data["upload_series"]
    season = context.user_data["upload_season"]
    quality = context.user_data["upload_quality"]
    episode_num = context.user_data["upload_episode"]
    episode_key = f"E{episode_num}"

    # Check if episode exists (regardless of quality)
    episode_exists = bool(series_collection.find_one({
        "name": series_name,
        f"seasons.{season}.episodes.{episode_key}": {"$exists": True}
    }))

    # Build update query
    update_query = {
        f"seasons.{season}.episodes.{episode_key}.qualities.{quality}": file.file_id,
        "name": series_name
    }

    # Execute update
    series_collection.update_one(
        {"name": series_name},
        {"$set": update_query},
        upsert=True
    )

    # Only increment episode if this was NEW episode
    if not episode_exists:
        context.user_data["upload_episode"] += 1
        action = "Saved NEW episode"
    else:
        action = "Updated existing episode"

    update.message.reply_text(
        f"✅ {action}:\n"
        f"`{series_name.title()} {season}{episode_key} ({quality})`\n\n"
        f"Next episode: `E{context.user_data['upload_episode']}`",
        parse_mode="Markdown"
    )

# ===== User Interaction =====
def handle_series_query(update: Update, context: CallbackContext):
    if update.message.text.startswith('/'):
        return

    series_name = update.message.text.strip().lower()
    series = series_collection.find_one({"name": series_name})
    
    if not series:
        update.message.reply_text("🔍 Series not found in database")
        return

    # Send photo if available
    if PIC_URL:
        update.message.reply_photo(
            PIC_URL,
            caption=f"📺 {series_name.title()} - Select season:",
            reply_markup=get_seasons_keyboard(series_name, series)
        )
    else:
        update.message.reply_text(
            f"📺 {series_name.title()} - Select season:",
            reply_markup=get_seasons_keyboard(series_name, series)
        )

def get_seasons_keyboard(series_name: str, series_data: dict) -> InlineKeyboardMarkup:
    buttons = []
    seasons = series_data.get("seasons", {})
    
    for season in sorted(seasons.keys()):
        buttons.append([
            InlineKeyboardButton(
                f"🎬 {season}",
                callback_data=f"season:{series_name}:{season}")
        ])
    
    return InlineKeyboardMarkup(buttons)

# ===== Main Execution =====
def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher

    # Command handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("add", add_series_command))
    
    # Message handlers
    dp.add_handler(MessageHandler(
        Filters.text & ~Filters.command,
        handle_series_query
    ))
    dp.add_handler(MessageHandler(
        Filters.document | Filters.video,
        handle_admin_file
    ))

    # Error handling
    dp.add_error_handler(lambda u, c: logger.error(c.error))

    updater.start_polling()
    logger.info("Bot is running...")
    updater.idle()

if __name__ == "__main__":
    main()
