import logging
import os
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext
from pymongo import MongoClient
from typing import Optional, Dict, Any
&nbsp;
&nbsp;

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
&nbsp;
&nbsp;

# Constants
DEFAULT_PORT = 8443
ADMIN_IDS = {5387919847}  # Replace with your real Telegram user ID(s)
&nbsp;
&nbsp;

# Environment variable validation
def validate_env_vars() -> None:
    bot_token = os.environ.get("BOT_TOKEN")
    mongo_uri = os.environ.get("MONGO_URI")
    if not bot_token or not mongo_uri:
        logger.error("Missing BOT_TOKEN or MONGO_URI environment variables")
        exit(1)
    return bot_token, mongo_uri
&nbsp;
&nbsp;

# Initialize MongoDB client
def init_mongo_client(mongo_uri: str) -> MongoClient:
    return MongoClient(mongo_uri, tls=True, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)
&nbsp;
&nbsp;

# Check if user is admin
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS
&nbsp;
&nbsp;

# Start command handler
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "Welcome to the Series Bot!\n\n"
        "Send the name of a series in any chat to get started.\n"
        "Admins: Send files with caption 'SeriesName | SeasonNumber | Quality' to add episodes.\n"
        "Example caption: Stranger Things | S1 | 720p\n"
        "Or use /addseries command with JSON payload."
    )
&nbsp;
&nbsp;

# Add series command
def addseries_command(update: Update, context: CallbackContext) -> None:
    if not is_admin(update.effective_user.id):
        update.message.reply_text("You are not authorized to add series.")
        return
&nbsp;
&nbsp;

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
&nbsp;
&nbsp;

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
&nbsp;
&nbsp;

# Save or update series data to MongoDB
def save_series_to_db(data: Dict[str, Any]) -> str:
    if "name" not in data or "seasons" not in data:
        return "Invalid data format. 'name' and 'seasons' fields are required."
&nbsp;
&nbsp;

    series_name = data["name"].strip().lower()
    result = series_collection.update_one(
        {"name": series_name},
        {"$set": data},
        upsert=True,
    )
&nbsp;
&nbsp;

    if result.upserted_id or result.modified_count:
        return f"Series '{data['name']}' added/updated successfully."
    else:
        return "No changes made to the database."
&nbsp;
&nbsp;

# Parse caption
def parse_caption(caption: str) -> Optional[tuple]:
    parts = [p.strip() for p in caption.split("|")]
    if len(parts) != 3:
        return None
    series_name, season, quality = parts
    season = season.upper() if season.startswith("S") else "S" + season.lstrip("Season").strip()
    return series_name, season, quality
&nbsp;
&nbsp;

# Handle admin file upload
def handle_admin_file(update: Update, context: CallbackContext) -> None:
    if not is_admin(update.effective_user.id):
        return
&nbsp;
&nbsp;

    message = update.message
    file_obj = message.document or message.video
    if not file_obj:
        update.message.reply_text("Please send a document or video file with caption.")
        return
&nbsp;
&nbsp;

    caption = message.caption
    if not caption:
        update.message.reply_text("Please add a caption in format: SeriesName | Season | Quality")
        return
&nbsp;
&nbsp;

    parsed = parse_caption(caption)
    if not parsed:
        update.message.reply_text("Caption format invalid. Use: SeriesName | Season | Quality")
        return
&nbsp;
&nbsp;

    series_name, season_key, quality_key = parsed
    file_id = file_obj.file_id
    series_name_key = series_name.strip().lower()
&nbsp;
&nbsp;

    # Determine the next episode number
    series = series_collection.find_one({"name": series_name_key})
    episodes_key = determine_next_episode_key(series, season_key)
&nbsp;
&nbsp;

    # Prepare update to MongoDB
    update_query = {
        f"seasons.{season_key}.episodes.{episodes_key}.qualities.{quality_key}": file_id,
        "name": series_name_key
    }
&nbsp;
&nbsp;

    series_collection.update_one(
        {"name": series_name_key},
        {"$set": update_query},
        upsert=True
    )
&nbsp;
&nbsp;

    update.message.reply_text(
        f"Added/updated episode {episodes_key} of {series_name} season {season_key} quality {quality_key} successfully."
    )
&nbsp;
&nbsp;

# Determine the next episode key
def determine_next_episode_key(series: Optional[Dict], season_key: str) -> str:
    if series and "seasons" in series and season_key in series["seasons"]:
        existing_episodes = series["seasons"][season_key].get("episodes", {})
        episodes_list = sorted(existing_episodes.keys()) if existing_episodes else []
        next_ep_num = max(int(ep[1:]) for ep in episodes_list if ep.startswith("E"), default=0) + 1
        return f"E{next_ep_num}"
    return "E1"
&nbsp;
&nbsp;

# Handle series query
def handle_series_query(update: Update, context: CallbackContext) -> None:
    if update.message.text.startswith("/"):  # Ignore commands
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

    seasons = series.get("seasons", {})
    if not seasons:
        update.message.reply_text("No seasons found for this series.")
        return
&nbsp;
&nbsp;

    keyboard = [
        [InlineKeyboardButton(season_name, callback_data=f"season|{series['name']}|{season_name}")]
        for season_name in sorted(seasons.keys())
    ]
&nbsp;
&nbsp;

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"Select Season for {series['name']}:", reply_markup=reply_markup)
&nbsp;
&nbsp;

    # Redirect user to PM
    context.bot.send_message(chat_id=update.effective_user.id, text="Please check your PM for further actions.")
&nbsp;
&nbsp;

# Handle button callbacks
def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = query.data.split("|")
&nbsp;
&nbsp;

    if len(data) < 3:
        query.edit_message_text(text="Invalid action.")
        return
&nbsp;
&nbsp;

    action, series_name = data[0], data[1]
    series = series_collection.find_one({"name": series_name.lower()})
    if not series:
        query.edit_message_text(text="Series data not found.")
        return
&nbsp;
&nbsp;

    if action == "season":
        handle_season_selection(query, series, data[2])
    elif action == "episode":
        handle_episode_selection(query, series, data[2], data[3])
    elif action == "quality":
        handle_quality_selection(query, series, data[2], data[3], data[4])
    elif action == "send_all":
        handle_send_all_episodes(query, series, data[2])
    else:
        query.edit_message_text(text="Unknown action.")
&nbsp;
&nbsp;

def handle_season_selection(query, series, season_name):
    season = series.get("seasons", {}).get(season_name)
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

    keyboard = [
        [InlineKeyboardButton(ep_name, callback_data=f"episode|{series['name']}|{season_name}|{ep_name}")]
        for ep_name in sorted(episodes.keys())
    ]
    keyboard.append([InlineKeyboardButton("Send All Episodes", callback_data=f"send_all|{series['name']}|{season_name}")])
&nbsp;
&nbsp;

    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text=f"Select Episode for {season_name}:", reply_markup=reply_markup)
&nbsp;
&nbsp;

def handle_episode_selection(query, series, season_name, ep_name):
    season = series.get("seasons", {}).get(season_name)
    if not season:
        query.edit_message_text(text="Season not found.")
        return
&nbsp;
&nbsp;

    episode = season.get("episodes", {}).get(ep_name)
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

    keyboard = [
        [InlineKeyboardButton(quality_name, callback_data=f"quality|{series['name']}|{season_name}|{ep_name}|{quality_name}")]
        for quality_name in sorted(qualities.keys())
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text=f"Select Quality for {ep_name}:", reply_markup=reply_markup)
&nbsp;
&nbsp;

def handle_send_all_episodes(query, series, season_name):
    season = series.get("seasons", {}).get(season_name)
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

    keyboard = [
        [InlineKeyboardButton(quality_name, callback_data=f"quality|{series['name']}|{season_name}|{ep_name}|{quality_name}")]
        for ep_name in sorted(episodes.keys())
        for quality_name in sorted(episodes[ep_name].get("qualities", {}).keys())
    ]
&nbsp;
&nbsp;

    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text=f"Select Quality to send all episodes for {season_name}:", reply_markup=reply_markup)
&nbsp;
&nbsp;

def handle_quality_selection(query, series, season_name, ep_name, quality_name):
    season = series.get("seasons", {}).get(season_name)
    if not season:
        query.edit_message_text(text="Season not found.")
        return
&nbsp;
&nbsp;

    episode = season.get("episodes", {}).get(ep_name)
    if not episode:
        query.edit_message_text(text="Episode not found.")
        return
&nbsp;
&nbsp;

    qualities = episode.get("qualities", {})
    file_id_or_url = qualities.get(quality_name)
    if not file_id_or_url:
        query.edit_message_text(text="File not found for selected quality.")
        return
&nbsp;
&nbsp;

    try:
        if file_id_or_url.startswith(("http://", "https://")):
            keyboard = [[InlineKeyboardButton(f"Download {ep_name} in {quality_name}", url=file_id_or_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(text=f"Download link for {ep_name} in {quality_name}:", reply_markup=reply_markup)
        else:
            context.bot.send_document(chat_id=query.from_user.id, document=file_id_or_url)
            query.edit_message_text(text=f"Sent {ep_name} in {quality_name} to your private chat.")
    except Exception as e:
        logger.error(f"Failed to send file: {e}")
        query.edit_message_text(text="Failed to send the file. Please try again later.")
&nbsp;
&nbsp;

def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
&nbsp;
&nbsp;

def main() -> None:
    bot_token, mongo_uri = validate_env_vars()
    global series_collection
    client = init_mongo_client(mongo_uri)
    db = client.series_bot_db
    series_collection = db.series
&nbsp;
&nbsp;

    updater = Updater(bot_token)
    dispatcher = updater.dispatcher
&nbsp;
&nbsp;

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("addseries", addseries_command))
    dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), handle_series_query))
    dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), handle_admin_json))
    dispatcher.add_handler(MessageHandler(Filters.document | Filters.video, handle_admin_file))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_error_handler(error_handler)
&nbsp;
&nbsp;

    updater.start_polling()
    logger.info("Bot started.")
    updater.idle()
&nbsp;
&nbsp;

if __name__ == '__main__':
    main()
