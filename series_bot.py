import logging
import os
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
)
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
import re

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
PIC_URL = os.environ.get("PIC_URL")
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL")  # e.g. @YourChannel
CUSTOM_FILE_CAPTION = os.environ.get("CUSTOM_FILE_CAPTION")  # Optional

if not BOT_TOKEN or not MONGO_URI:
    logger.error("Missing BOT_TOKEN or MONGO_URI environment variables")
    exit(1)

client = MongoClient(
    MONGO_URI, tls=True, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000
)
db = client.series_bot_db
series_collection = db.series

ADMIN_IDS = {5387919847}  # Replace with your Telegram user ID(s)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def force_subscribe_check(update: Update, context: CallbackContext) -> bool:
    if not FORCE_SUB_CHANNEL:
        return True
    user_id = update.effective_user.id
    try:
        member = context.bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
    except BadRequest as e:
        logger.error(f"Force subscribe error: {e}")
        return False

def start(update: Update, context: CallbackContext) -> None:
    if not force_subscribe_check(update, context):
        update.message.reply_text(
            f"Please join our channel {FORCE_SUB_CHANNEL} to use this bot."
        )
        return

    update.message.reply_text(
        "Welcome to the Series Bot!\n\n"
        "Admins: Use /add series_name|season|quality to add episodes.\n"
        "Example: /add Stranger Things|S1|720p\n"
        "Then upload the file with the same naming convention."
    )

def add_series_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("You are not authorized to add series.")
        return
    if context.args:
        series_info = " ".join(context.args)
        parts = [p.strip() for p in series_info.split("|")]
        if len(parts) != 3:
            update.message.reply_text("Use format: /add series_name|season|quality")
            return
        series_name, season, quality = parts
        season = season.upper()
        if not season.startswith("S"):
            season = "S" + season.lstrip("Season").strip()
        series_name_key = series_name.strip().lower()
        context.user_data['upload_series'] = series_name_key
        context.user_data['upload_season'] = season
        context.user_data['upload_quality'] = quality
        update.message.reply_text(
            f"Set current series to '{series_name}', season '{season}', quality '{quality}'. Now upload the file."
        )
    else:
        update.message.reply_text("Use format: /add series_name|season|quality")

def parse_caption(caption: str):
    parts = [p.strip() for p in caption.split("|")]
    if len(parts) != 3:
        return None
    series_name, season, quality = parts
    season = season.upper()
    if not season.startswith("S"):
        season = "S" + season.lstrip("Season").strip()
    return series_name, season, quality

def handle_admin_file(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    message = update.message
    file_obj = message.document or message.video
    if not file_obj:
        update.message.reply_text("Please send a document or video file.")
        return

    caption = message.caption
    if not caption:
        update.message.reply_text("Add caption: SeriesName | Season | Quality")
        return

    parsed = parse_caption(caption)
    if not parsed:
        update.message.reply_text("Use caption format: SeriesName | Season | Quality")
        return

    series_name, season_key, quality_key = parsed
    series_name_key = series_name.strip().lower()
    file_id = file_obj.file_id

    # Update the database
    update_query = {
        f"seasons.{season_key}.episodes.default.qualities.{quality_key}": file_id,
        "name": series_name_key,
    }
    series_collection.update_one(
        {"name": series_name_key}, {"$set": update_query}, upsert=True
    )

    update.message.reply_text(
        f"Added/updated {series_name} season {season_key} with quality {quality_key} successfully."
    )

def handle_series_query(update: Update, context: CallbackContext) -> None:
    if update.message.text.startswith("/"):
        return

    text = update.message.text.strip().lower()
    series = series_collection.find_one({"name": text})
    if not series:
        update.message.reply_text("Series not found in database.")
        return

    if PIC_URL:
        user_mention = update.message.from_user.mention_html()
        caption = f"Hi {user_mention}, Select Season for {text.title()}"
        context.bot.send_photo(
            chat_id=update.effective_chat.id, photo=PIC_URL, caption=caption, parse_mode="HTML"
        )

    seasons = series.get("seasons", {})
    if not seasons:
        update.message.reply_text("No seasons found for this series.")
        return

    keyboard = [
        [InlineKeyboardButton("All Seasons", callback_data=f"all_seasons|{series['name']}")]
    ]
    for season_name in sorted(seasons.keys()):
        keyboard.append(
            [InlineKeyboardButton(season_name, callback_data=f"season|{series['name']}|{season_name}")]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        f"Select Season for {series['name']}:", reply_markup=reply_markup
    )

def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = query.data
    parts = data.split("|")

    if len(parts) < 2:
        query.edit_message_text(text="Invalid action.")
        return

    action = parts[0]
    series_name = parts[1]
    series = series_collection.find_one({"name": series_name.lower()})
    if not series:
        query.edit_message_text(text="Series data not found.")
        return

    # Additional action handling (e.g., show season contents) can be added here

def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main():
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("add", add_series_command))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_series_query))
    dispatcher.add_handler(MessageHandler(Filters.document | Filters.video, handle_admin_file))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_error_handler(error_handler)

    updater.start_polling()
    logger.info("Bot started.")
    updater.idle()

if __name__ == "__main__":
    main()
