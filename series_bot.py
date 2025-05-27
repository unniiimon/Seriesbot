import logging
import os
import json
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
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
&nbsp;
&nbsp;

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)
&nbsp;
&nbsp;

# Environment variables for bot token and MongoDB connection string
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
&nbsp;
&nbsp;

if not BOT_TOKEN or not MONGO_URI:
    logger.error("Missing BOT_TOKEN or MONGO_URI environment variables")
    exit(1)
&nbsp;
&nbsp;

# Initialize MongoDB client and db
client = MongoClient(MONGO_URI)
db = client.series_bot_db
series_collection = db.series
&nbsp;
&nbsp;

# Set your admin Telegram user IDs here (set your own admin ID here)
ADMIN_IDS = {5387919847}  # Replace with actual admin user IDs
&nbsp;
&nbsp;

# Helper function: Check if user is admin
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS
&nbsp;
&nbsp;

# Start command handler
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "Welcome to the Series Bot!\n\n"
        "Send the name of a series in any chat to get started.\n"
        "Admins: Use /addseries command in PM to add new series."
    )
&nbsp;
&nbsp;

# Admin command to add series - usage: /addseries followed by a JSON string or send JSON after it
def addseries_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("You are not authorized to add series.")
        return
&nbsp;
&nbsp;

    # Accept JSON data inline or as next message
    if context.args:
        json_text = " ".join(context.args)
        try:
            data = json.loads(json_text)
            response = save_series_to_db(data)
            update.message.reply_text(response)
        except Exception as e:
            update.message.reply_text(f"Failed to add series: {e}")
    else:
        update.message.reply_text(
            "Please send the series data as a JSON text message with this format:\n\n"
            "{\n"
            '  "name": "Friends",\n'
            '  "seasons": {\n'
            '    "S1": {\n'
            '      "episodes": {\n'
            '        "E1": {\n'
            '          "qualities": {\n'
            '            "1080p": "file_id_or_url",\n'
            '            "720p": "file_id_or_url"\n'
            '          }\n'
            '        }\n'
            '      }\n'
            '    }\n'
            '  }\n'
            "}\n\n"
            "Send this JSON now."
        )
        # Save state to expect next message for JSON data
        context.user_data["awaiting_series_json"] = True
&nbsp;
&nbsp;

# Handle admin JSON payload for adding series
def handle_admin_json(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    if context.user_data.get("awaiting_series_json"):
        text = update.message.text
        try:
            data = json.loads(text)
            response = save_series_to_db(data)
            update.message.reply_text(response)
        except Exception as e:
            update.message.reply_text(f"Failed to add series: {e}")
        context.user_data["awaiting_series_json"] = False
&nbsp;
&nbsp;

# Save or update series data to MongoDB
def save_series_to_db(data: dict) -> str:
    if "name" not in data or "seasons" not in data:
        return "Invalid data format. 'name' and 'seasons' fields are required."
&nbsp;
&nbsp;

    # Normalize series name to lowercase for querying
    series_name = data["name"].strip().lower()
&nbsp;
&nbsp;

    # Upsert the series by name
    result = series_collection.update_one(
        {"name": series_name},
        {"$set": data},
        upsert=True,
    )
    if result.upserted_id or result.modified_count:
        return f"Series '{data['name']}' added/updated successfully."
    else:
        return "No changes made to the database."
&nbsp;
&nbsp;

# When user sends text message (series name) in group or PM
def handle_series_query(update: Update, context: CallbackContext) -> None:
    # Ignore commands
    if update.message.text.startswith('/'):
        return
&nbsp;
&nbsp;

    text = update.message.text.strip().lower()
    series = series_collection.find_one({"name": text})
    if not series:
        update.message.reply_text("Sorry, series not found in database.")
        return
&nbsp;
&nbsp;

    # Show season buttons
    seasons = series.get("seasons", {})
    if not seasons:
        update.message.reply_text("No seasons found for this series.")
        return
&nbsp;
&nbsp;

    keyboard = []
    for season_name in sorted(seasons.keys()):
        keyboard.append([InlineKeyboardButton(season_name, callback_data=f"season|{series['name']}|{season_name}")])
&nbsp;
&nbsp;

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"Select Season for {series['name']}:", reply_markup=reply_markup)
&nbsp;
&nbsp;

# Handle all button callbacks
def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = query.data
    components = data.split("|")
&nbsp;
&nbsp;

    if len(components) < 3:
        query.edit_message_text(text="Invalid action.")
        return
&nbsp;
&nbsp;

    action = components[0]
    series_name = components[1]
    # Normalize series name for DB lookup
    series = series_collection.find_one({"name": series_name.lower()})
    if not series:
        query.edit_message_text(text="Series data not found.")
        return
&nbsp;
&nbsp;

    if action == "season":
        season_name = components[2]
        seasons = series.get("seasons", {})
        season = seasons.get(season_name)
        if not season:
            query.edit_message_text(text="Season not found.")
            return
&nbsp;
&nbsp;

        episodes = season.get("episodes", {})
        if not episodes:
            query.edit_message_text(text="No episodes found in this season.")
            return
&nbsp;
&nbsp;

        keyboard = []
        for ep_name in sorted(episodes.keys()):
            keyboard.append(
                [InlineKeyboardButton(ep_name, callback_data=f"episode|{series['name']}|{season_name}|{ep_name}")]
            )
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text=f"Select Episode for {season_name}:", reply_markup=reply_markup)
&nbsp;
&nbsp;

    elif action == "episode":
        if len(components) < 4:
            query.edit_message_text(text="Invalid episode action.")
            return
        season_name = components[2]
        ep_name = components[3]
        seasons = series.get("seasons", {})
        season = seasons.get(season_name)
        if not season:
            query.edit_message_text(text="Season not found.")
            return
        episodes = season.get("episodes", {})
        episode = episodes.get(ep_name)
        if not episode:
            query.edit_message_text(text="Episode not found.")
            return
&nbsp;
&nbsp;

        qualities = episode.get("qualities", {})
        if not qualities:
            query.edit_message_text(text="No qualities found for this episode.")
            return
&nbsp;
&nbsp;

        keyboard = []
        for quality_name in sorted(qualities.keys()):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        quality_name,
                        callback_data=f"quality|{series['name']}|{season_name}|{ep_name}|{quality_name}",
                    )
                ]
            )
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text=f"Select Quality for {ep_name}:", reply_markup=reply_markup)
&nbsp;
&nbsp;

    elif action == "quality":
        if len(components) < 5:
            query.edit_message_text(text="Invalid quality action.")
            return
        season_name = components[2]
        ep_name = components[3]
        quality_name = components[4]
&nbsp;
&nbsp;

        seasons = series.get("seasons", {})
        season = seasons.get(season_name)
        if not season:
            query.edit_message_text(text="Season not found.")
            return
        episodes = season.get("episodes", {})
        episode = episodes.get(ep_name)
        if not episode:
            query.edit_message_text(text="Episode not found.")
            return
        qualities = episode.get("qualities", {})
        file_id_or_url = qualities.get(quality_name)
        if not file_id_or_url:
            query.edit_message_text(text="File not found for selected quality.")
            return
&nbsp;
&nbsp;

        # Try sending file by file_id or URL:
        try:
            # If file_id_or_url looks like a Telegram file_id (only digits and letters)
            if file_id_or_url.startswith("http://") or file_id_or_url.startswith("https://"):
                # It's a URL - send as message with link or with inline button
                keyboard = [
                    [InlineKeyboardButton(f"Download {ep_name} in {quality_name}", url=file_id_or_url)]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                query.edit_message_text(
                    text=f"Download link for {ep_name} in {quality_name}:",
                    reply_markup=reply_markup,
                )
            else:
                # Assume it's a Telegram file_id - send file privately (if possible)
                context.bot.send_document(chat_id=query.from_user.id, document=file_id_or_url)
                query.edit_message_text(text=f"Sent {ep_name} in {quality_name} to your private chat.")
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            query.edit_message_text(text="Failed to send the file. Please try again later.")
    else:
        query.edit_message_text(text="Unknown action.")
&nbsp;
&nbsp;

def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
&nbsp;
&nbsp;

def main():
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher
&nbsp;
&nbsp;

    # Command handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("addseries", addseries_command))
    # Message handlers
    dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), handle_series_query))
    dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), handle_admin_json))
&nbsp;
&nbsp;

    # Callback query handler
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
&nbsp;
&nbsp;

    # Error handler
    dispatcher.add_error_handler(error_handler)
&nbsp;
&nbsp;

    # Start the Bot
    updater.start_polling()
    logger.info("Bot started.")
    updater.idle()
&nbsp;
&nbsp;

if __name__ == '__main__':
    main()
&nbsp;
&nbsp;
