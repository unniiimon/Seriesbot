import logging
import os
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext
from pymongo import MongoClient
from typing import Optional, Dict, Any


# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


# Constants
DEFAULT_PORT = 8443
ADMIN_IDS = {5387919847}  # Replace with your real Telegram user ID(s)


# Environment variable validation
def validate_env_vars() -> None:
    bot_token = os.environ.get("BOT_TOKEN")
    mongo_uri = os.environ.get("MONGO_URI")
    if not bot_token or not mongo_uri:
        logger.error("Missing BOT_TOKEN or MONGO_URI environment variables")
        exit(1)
    return bot_token, mongo_uri


# Initialize MongoDB client
def init_mongo_client(mongo_uri: str) -> MongoClient:
    return MongoClient(mongo_uri, tls=True, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)


# Check if user is admin
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


# Add series command
def addseries_command(update: Update, context: CallbackContext) -> None:
    if not is_admin(update.effective_user.id):
        update.message.reply_text("You are not authorized to add series.")
        return


    if context.args:
        json_text = " ".join(context.args)
        try:
            data = json.loads(json_text)
            response = save_series_to_db(data)
            update.message.reply_text(response)
        except json.JSONDecodeError:
            update.message.reply_text("Invalid JSON format. Please check your input.")
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


# Handle admin JSON payload
def handle_admin_json(update: Update, context: CallbackContext) -> None:
    if not is_admin(update.effective_user.id):
        return
    if context.user_data.get("awaiting_series_json"):
        text = update.message.text
        try:
            data = json.loads(text)
            response = save_series_to_db(data)
            update.message.reply_text(response)
        except json.JSONDecodeError:
            update.message.reply_text("Invalid JSON format. Please check your input.")
        except Exception as e:
            update.message.reply_text(f"Failed to add series: {e}")
        finally:
            context.user_data["awaiting_series_json"] = False


# Save or update series data to MongoDB
def save_series_to_db(data: Dict[str, Any]) -> str:
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

# Parse caption
def parse_caption(caption: str) -> Optional[tuple]:
    parts = [p.strip() for p in caption.split("|")]
    if len(parts) != 3:
        return None
    series_name, season, quality = parts
    season = season.upper() if season.startswith("S") else "S" + season.lstrip("Season").strip()
    return series_name, season, quality


# Handle admin file upload
def handle_admin_file(update: Update, context: CallbackContext) -> None:
    if not is_admin(update.effective_user.id):
        return

    message = update.message
    file_obj = message.document or message.video
    if not file_obj:
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


    # Determine the next episode number
    series = series_collection.find_one({"name": series_name_key})
    episodes_key = determine_next_episode_key(series, season_key)


    # Prepare update to MongoDB
    update_query = {
        f"seasons.{season_key}.episodes.{episodes_key}.qualities.{quality_key}": file_id,
        "name": series_name_key
    }

    series_collection.update_one(
        {"name": series_name_key},
        {"$set": update_query},
        upsert=True
    )

    update.message.reply_text(
        f"Added/updated episode {episodes_key} of {series_name} season {season_key} quality {quality_key} successfully."
    )

# Determine the next episode key
def determine_next_episode_key(series: Optional[Dict], season_key: str) -> str:
    if series and "seasons" in series and season_key in series["seasons"]:
        existing_episodes = series["seasons"][season_key].get("episodes", {})
        episodes_list = sorted(existing_episodes.keys()) if existing_episodes else []
        next_ep_num = max((int(ep[1:]) for ep in episodes_list if ep.startswith("E")), default=0) + 1
        return f"E{next_ep_num}"
    return "E1"

# Handle series query
def handle_series_query(update: Update, context: CallbackContext) -> None:
    if update.message is None or update.message.text.startswith("/"):  # Ignore commands and non-message updates
        return

    text = update.message.text.strip().lower()
    series = series_collection.find_one({"name": text})
    if not series:
        update.message.reply_text("Sorry, series not found in database.")
        return

    seasons = series.get("seasons", {})
    if not seasons:
        update.message.reply_text("No seasons found for this series.")
        return

    keyboard = [
        [InlineKeyboardButton(season_name, callback_data=f"season|{series['name']}|{season_name}")]
        for season_name in sorted(seasons.keys())
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"Select Season for {series['name']}:", reply_markup=reply_markup)

    # Redirect user to PM
    context.bot.send_message(chat_id=update.effective_user.id, text="Please check your PM for further actions.")

# Handle button callbacks
def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = query.data.split("|")

    if len(data) < 3:
        query.edit_message_text(text="Invalid action.")
        return

    action, series_name = data[0], data[1]
    series = series_collection.find_one({"name": series_name.lower()})
    if not series:
        query.edit_message_text(text="Series data not found.")
        return

    if action == "season":
        season_name = data[2]
        handle_season_selection(query, series, season_name)
    elif action == "episode":
        season_name = data[2]
        ep_name = data[3]
        handle_episode_selection(query, series, season_name, ep_name)
    elif action == "quality":
        season_name = data[2]
        ep_name = data[3]
        quality_name = data[4]
        handle_quality_selection(query, series, season_name, ep_name, quality_name)
    else:
        query.edit_message_text(text="Unknown action.")

def handle_season_selection(query, series, season_name):
    season = series.get("seasons", {}).get(season_name)
    if not season:
        query.edit_message_text(text="Season not found.")
        return

    episodes = season.get("episodes", {})
    if not episodes:
        query.edit_message_text(text="No episodes found in this season.")
        return

    keyboard = [
        [InlineKeyboardButton(ep_name, callback_data=f"episode|{series['name']}|{season_name}|{ep_name}")]
        for ep_name in sorted(episodes.keys())
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text=f"Select Episode for {season_name}:", reply_markup=reply_markup)

def handle_episode_selection(query, series, season_name, ep_name):
    season = series.get("seasons", {}).get(season_name)
    if not season:
        query.edit_message_text(text="Season not found.")
        return

    episode = season.get("episodes", {}).get(ep_name)
    if not episode:
        query.edit_message_text(text="Episode not found.")
        return

    qualities = episode.get("qualities", {})
    if not qualities:
        query.edit_message_text(text="No qualities found for this episode.")
        return

    # Show only 1080p and 720p options
    keyboard = [
        [InlineKeyboardButton(quality_name, callback_data=f"quality|{series['name']}|{season_name}|{ep_name}|{quality_name}")]
        for quality_name in ["1080p", "720p"] if quality_name in qualities
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text=f"Select Quality for {ep_name}:", reply_markup=reply_markup)

def handle_quality_selection(query, series, season_name, ep_name, quality_name):
    season = series.get("seasons", {}).get(season_name)
    if not season:
        query.edit_message_text(text="Season not found.")
        return

    episode = season.get("episodes", {}).get(ep_name)
    if not episode:
        query.edit_message_text(text="Episode not found.")
        return

    qualities = episode.get("qualities", {})
    file_id_or_url = qualities.get(quality_name)
    if not file_id_or_url:
        query.edit_message_text(text="File not found for selected quality.")
        return

    try:
        if isinstance(file_id_or_url, list):  # If there are multiple files for the episode
            for file in file_id_or_url:
                context.bot.send_document(chat_id=query.from_user.id, document=file)
            query.edit_message_text(text=f"Sent all files for {ep_name} in {quality_name} to your private chat.")
        else:
            context.bot.send_document(chat_id=query.from_user.id, document=file_id_or_url)
            query.edit_message_text(text=f"Sent {ep_name} in {quality_name} to your private chat.")
    except Exception as e:
        logger.error(f"Failed to send file: {e}")
        query.edit_message_text(text="Failed to send the file. Please try again later.")

def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main() -> None:
    bot_token, mongo_uri = validate_env_vars()
    global series_collection
    client = init_mongo_client(mongo_uri)
    db = client.series_bot_db
    series_collection = db.series

    updater = Updater(bot_token)
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
