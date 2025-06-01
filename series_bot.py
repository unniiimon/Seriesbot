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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL")  # Optional: for force subscribe
CUSTOM_FILE_CAPTION = os.environ.get("CUSTOM_FILE_CAPTION")  # Optional file caption
PIC_URL = os.environ.get("PIC_URL")

if not BOT_TOKEN or not MONGO_URI:
    logger.error("Missing BOT_TOKEN or MONGO_URI environment variables")
    exit(1)

client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)
db = client.series_bot_db
series_collection = db.series

ADMIN_IDS = {5387919847}  # Replace with your Telegram user IDs

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def force_subscribe_check(update: Update, context: CallbackContext) -> bool:
    if not FORCE_SUB_CHANNEL:
        return True
    user_id = update.effective_user.id
    try:
        status = context.bot.get_chat_member(FORCE_SUB_CHANNEL, user_id).status
        return status in ('creator', 'administrator', 'member')
    except:
        return False

def start(update: Update, context: CallbackContext) -> None:
    if not force_subscribe_check(update, context):
        update.message.reply_text(f"Please join {FORCE_SUB_CHANNEL} to use this bot.")
        return
    update.message.reply_text(
        "Welcome to the Series Bot!\n"
        "Admins commands:\n"
        "/add series_name - Set current series to upload files.\n"
        "Then send files with names like 'SeriesName_S01_E01_720p.mkv'.\n"
        "Users: Send series name to browse."
    )

def add_series_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("You are not authorized to add series.")
        return
    if context.args:
        series_name = " ".join(context.args).strip().lower()
        context.user_data['upload_series'] = series_name
        context.user_data['upload_season'] = None  # Reset season
        update.message.reply_text(f"Set current series to '{series_name}'. Now upload files.")
    else:
        update.message.reply_text("Usage: /add series_name")

def handle_admin_file(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        return
    message = update.message
    file_obj = message.document or message.video
    if not file_obj:
        update.message.reply_text("Please send a document or video file.")
        return

    series_name = context.user_data.get('upload_series')
    if not series_name:
        update.message.reply_text("Set series first using /add series_name.")
        return

    file_id = file_obj.file_id
    file_name = file_obj.file_name

    # Extract season and episode from the file name
    match = re.search(r'_(S\d{2})_(E\d{2})_', file_name)
    if match:
        season = match.group(1).upper()
        episode = match.group(2).upper()
    else:
        update.message.reply_text("File name must include season and episode in format: _S01_E01_")
        return

    # Update the database
    update_query = {
        f"seasons.{season}.episodes.{episode}.qualities.default": file_id,
        "name": series_name
    }
    series_collection.update_one(
        {"name": series_name},
        {"$set": update_query},
        upsert=True
    )
    update.message.reply_text(
        f"Added file for {series_name} season {season} episode {episode}."
    )

def handle_message(update: Update, context: CallbackContext):
    if not force_subscribe_check(update, context):
        update.message.reply_text(f"Please join {FORCE_SUB_CHANNEL} to use this bot.")
        return
    if update.message.text and not update.message.text.startswith("/"):
        handle_series_query(update, context)

def handle_series_query(update: Update, context: CallbackContext):
    if update.message.text.startswith("/"):
        return
    text = update.message.text.strip().lower()
    series = series_collection.find_one({"name": text})
    if not series:
        update.message.reply_text("Series not found.")
        return
    if PIC_URL:
        user_mention = update.message.from_user.mention_html()
        caption = f"Hi {user_mention}, Select Season for {text.title()}"
        context.bot.send_photo(update.effective_chat.id, PIC_URL, caption=caption, parse_mode='HTML')
    seasons = series.get("seasons", {})
    if not seasons:
        update.message.reply_text("No seasons found.")
        return
    keyboard = [[InlineKeyboardButton("All Seasons", callback_data=f"all_seasons|{series['name']}")]]
    for season_name in sorted(seasons.keys()):
        keyboard.append([InlineKeyboardButton(season_name, callback_data=f"season|{series['name']}|{season_name}")])
    update.message.reply_text(f"Select Season for {series['name']}:", reply_markup=InlineKeyboardMarkup(keyboard))

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    parts = data.split("|")
    if len(parts) < 2:
        query.edit_message_text("Invalid action.")
        return
    action, series_name = parts[0], parts[1]
    series = series_collection.find_one({"name": series_name.lower()})
    if not series:
        query.edit_message_text("Series data not found.")
        return

    # Handle other actions (season, all seasons, etc.) as before...

def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main():
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("add", add_series_command))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dispatcher.add_handler(MessageHandler(Filters.document | Filters.video, handle_admin_file))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_error_handler(error_handler)

    updater.start_polling()
    logger.info("Bot started.")
    updater.idle()

if __name__ == "__main__":
    main()
