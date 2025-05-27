import logging
import os
import re
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

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables for bot token, MongoDB connection string, and port
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
PORT = int(os.environ.get("PORT", "8443"))  # default 8443 for HTTPS webhook use if needed
PIC_URL = os.environ.get("PIC_URL")  # URL or file_id of the picture to send

if not BOT_TOKEN or not MONGO_URI:
    logger.error("Missing BOT_TOKEN or MONGO_URI environment variables")
    exit(1)

# Initialize MongoDB client and db with best practice URI options (use TLS, timeout, etc.)
client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)
db = client.series_bot_db
series_collection = db.series

# Set your admin Telegram user IDs here (replace with actual admin user IDs)
ADMIN_IDS = {5387919847}  # Replace with your real Telegram user ID(s)

# Helper function: Check if user is admin
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Start command handler
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "Welcome to the Series Bot!\n\n"
        "Send the name of a series in any chat to get started.\n"
        "Admins: Send files with caption 'SeriesName | SeasonNumber | Quality' to add episodes.\n"
        "Example caption: Stranger Things | S1 | 720p\n"
        "Or use /addseries command with JSON payload."
    )

# Admin command - add series with JSON
def addseries_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("You are not authorized to add series.")
        return

    if context.args:
        json_text = " ".join(context.args)
        try:
            import json
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
        context.user_data["awaiting_series_json"] = True

# Handle admin JSON payload for adding series
def handle_admin_json(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    if context.user_data.get("awaiting_series_json"):
        text = update.message.text
        try:
            import json
            data = json.loads(text)
            response = save_series_to_db(data)
            update.message.reply_text(response)
        except Exception as e:
            update.message.reply_text(f"Failed to add series: {e}")
        context.user_data["awaiting_series_json"] = False

# Save or update series data to MongoDB
def save_series_to_db(data: dict) -> str:
    if "name" not in data or "seasons" not in data:
        return "Invalid data format. 'name' and 'seasons' fields are required."
    series_name = data["name"].strip().lower()
    result = series_collection.update_one(
        {"name": series_name},
        {"$set": data},
        upsert=True,
    )
    if result.upserted_id or result.modified_count:
        return f"Series '{data['name']}' added/updated successfully."
    else:
        return "No changes made to the database."

# Parse caption like "Stranger Things | S1 | 720p" to extract info
def parse_caption(caption: str):
    parts = [p.strip() for p in caption.split("|")]
    if len(parts) != 3:
        return None
    series_name, season, quality = parts
    season = season.upper()
    if not season.startswith("S"):
        season = "S" + season.lstrip("Season").strip()
    return series_name, season, quality

# Admin sends a file with caption to add episode info
def handle_admin_file(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    message = update.message

    file_obj = None
    if message.document:
        file_obj = message.document
    elif message.video:
        file_obj = message.video
    else:
        update.message.reply_text("Please send a document or video file with caption.")
        return

    caption = message.caption
    if not caption:
        update.message.reply_text("Please add a caption in format: SeriesName | Season | Quality")
        return

    parsed = parse_caption(caption)
    if not parsed:
        update.message.reply_text("Caption format invalid. Use: SeriesName | Season | Quality")
        return

    series_name, season_key, quality_key = parsed
    file_id = file_obj.file_id

    series_name_key = series_name.strip().lower()
    episodes_key = None
    series = series_collection.find_one({"name": series_name_key})
    if series and "seasons" in series and season_key in series["seasons"]:
        existing_episodes = series["seasons"][season_key].get("episodes", {})
        episodes_list = sorted(existing_episodes.keys()) if existing_episodes else []
        next_ep_num = 1
        for ep in episodes_list:
            if ep.startswith("E") and ep[1:].isdigit():
                n = int(ep[1:])
                if n >= next_ep_num:
                    next_ep_num = n + 1
        episodes_key = f"E{next_ep_num}"
    else:
        episodes_key = "E1"

    update_query = {
        f"seasons.{season_key}.episodes.{episodes_key}.qualities.{quality_key}": file_id,
        "name": series_name_key
    }
    result = series_collection.update_one(
        {"name": series_name_key},
        {"$set": update_query},
        upsert=True
    )

    update.message.reply_text(
        f"Added/updated episode {episodes_key} of {series_name} season {season_key} quality {quality_key} successfully."
    )

# When user sends text message (series name) in group or PM
def handle_series_query(update: Update, context: CallbackContext) -> None:
    if update.message.text.startswith("/"):  # Ignore commands
        return

    text = update.message.text.strip().lower()
    series = series_collection.find_one({"name": text})
    if not series:
        update.message.reply_text("Sorry, series not found in database.")
        return

    # Send a photo with a custom caption
    if PIC_URL:
        user_mention = update.message.from_user.mention_html()
        caption = f"Hi {user_mention}, Select Season for {text.title()}"
        context.bot.send_photo(chat_id=update.effective_chat.id, photo=PIC_URL, caption=caption, parse_mode='HTML')

    seasons = series.get("seasons", {})
    if not seasons:
        update.message.reply_text("No seasons found for this series.")
        return

    keyboard = []
    # Add "All Episodes" button at top
    keyboard.append([InlineKeyboardButton("All Episodes", callback_data=f"all_episodes|{series['name']}")])
    for season_name in sorted(seasons.keys()):
        keyboard.append(
            [InlineKeyboardButton(season_name, callback_data=f"season|{series['name']}|{season_name}")]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"Select Season for {series['name']}:", reply_markup=reply_markup)

# Handle button callbacks
def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = query.data
    parts = data.split("|")

    if len(parts)  2:
        query.edit_message_text(text="Invalid action.")
        return

    action = parts[0]
    series_name = parts[1]
    series = series_collection.find_one({"name": series_name.lower()})
    if not series:
        query.edit_message_text(text="Series data not found.")
        return

    if action == "season":
        if len(parts)  3:
            query.edit_message_text(text="Invalid season action.")
            return
        season_name = parts[2]
        seasons = series.get("seasons", {})
        season = seasons.get(season_name)
        if not season:
            query.edit_message_text(text="Season not found.")
            return
        episodes = season.get("episodes", {})
        if not episodes:
            query.edit_message_text(text="No episodes found in this season.")
            return

        keyboard = []
        # Add "All Episodes" button
        keyboard.append([InlineKeyboardButton("All Episodes", callback_data=f"all_episodes|{series['name']}|{season_name}")])
        for ep_name in sorted(episodes.keys()):
            keyboard.append(
                [InlineKeyboardButton(ep_name, callback_data=f"episode|{series['name']}|{season_name}|{ep_name}")]
            )
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text=f"Select Episode for {season_name}:", reply_markup=reply_markup)

    elif action == "episode":
        if len(parts)  4:
            query.edit_message_text(text="Invalid episode action.")
            return
        season_name = parts[2]
        ep_name = parts[3]
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
        if not qualities:
            query.edit_message_text(text="No qualities found for this episode.")
            return

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

    elif action == "all_episodes":
        chat_id = query.message.chat_id
        user_id = query.from_user.id

        if len(parts) == 3:
            season_name = parts[2]
            episodes = series["seasons"].get(season_name, {}).get("episodes", {})
            if not episodes:
                query.edit_message_text(text="No episodes found in this season.")
                return

            query.edit_message_text(text=f"Sending all episodes for {season_name}...")

            for ep_name, episode in episodes.items():
                qualities = episode.get("qualities", {})
                for quality_name, file_id_or_url in qualities.items():
                    try:
                        if file_id_or_url.startswith("http://") or file_id_or_url.startswith("https://"):
                            context.bot.send_message(
                                chat_id=user_id,
                                text=f"{ep_name} ({season_name}) - {quality_name}:\n{file_id_or_url}"
                            )
                        else:
                            context.bot.send_document(chat_id=user_id, document=file_id_or_url)
                    except Exception as e:
                        logger.error(f"Error sending file {ep_name} {quality_name}: {e}")

            context.bot.send_message(chat_id=chat_id, text=f"All episodes for {season_name} sent to your private chat.")

        else:
            query.edit_message_text(text="Sending all episodes for all seasons...")

            for season_name, season in series.get("seasons", {}).items():
                episodes = season.get("episodes", {})
                for ep_name, episode in episodes.items():
                    qualities = episode.get("qualities", {})
                    for quality_name, file_id_or_url in qualities.items():
                        try:
                            if file_id_or_url.startswith("http://") or file_id_or_url.startswith("https://"):
                                context.bot.send_message(
                                    chat_id=user_id,
                                    text=f"{ep_name} ({season_name}) - {quality_name}:\n{file_id_or_url}"
                                )
                            else:
                                context.bot.send_document(chat_id=user_id, document=file_id_or_url)
                        except Exception as e:
                            logger.error(f"Error sending file {ep_name} {quality_name}: {e}")

            context.bot.send_message(chat_id=chat_id, text="All episodes for all seasons sent to your private chat.")

    elif action == "quality":
        if len(parts)  5:
            query.edit_message_text(text="Invalid quality action.")
            return
        season_name = parts[2]
        ep_name = parts[3]
        quality_name = parts[4]

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

        try:
            if file_id_or_url.startswith("http://") or file_id_or_url.startswith("https://"):
                keyboard = [
                    [InlineKeyboardButton(f"Download {ep_name} in {quality_name}", url=file_id_or_url)]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                query.edit_message_text(
                    text=f"Download link for {ep_name} in {quality_name}:",
                    reply_markup=reply_markup,
                )
            else:
                context.bot.send_document(chat_id=query.from_user.id, document=file_id_or_url)
                query.edit_message_text(text=f"Sent {ep_name} in {quality_name} to your private chat.")
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            query.edit_message_text(text="Failed to send the file. Please try again later.")

    else:
        query.edit_message_text(text="Unknown action.")

def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main():
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("addseries", addseries_command))
    dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), handle_series_query))
    dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), handle_admin_json))
    dispatcher.add_handler(MessageHandler(Filters.document | Filters.video, handle_admin_file))

    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_error_handler(error_handler)

    updater.start_polling()
    logger.info("Bot started.")
    updater.idle()

if __name__ == '__main__':
    main()
