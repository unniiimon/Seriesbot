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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL")  # Optional for force subscribe
CUSTOM_FILE_CAPTION = os.environ.get("CUSTOM_FILE_CAPTION")  # Optional caption
PIC_URL = os.environ.get("PIC_URL")

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

def build_button_rows(buttons, row_size=3):
    return [buttons[i:i + row_size] for i in range(0, len(buttons), row_size)]

def start(update: Update, context: CallbackContext) -> None:
    if not force_subscribe_check(update, context):
        update.message.reply_text(f"Please join our channel {FORCE_SUB_CHANNEL} to use this bot.")
        return
    update.message.reply_text(
        "Welcome to the Series Bot!\n\n"
        "Admins: Use /add series_name|season|quality to set the context.\n"
        "Use /n series_name|quality to change quality and reset episode count.\n"
        "Then upload files without captions.\n"
        "Users: Send series name to browse."
    )

def add_series_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("You are not authorized to add series.")
        return
    if context.args:
        parts = " ".join(context.args).split("|")
        if len(parts) != 3:
            update.message.reply_text("Use format: /add series_name|season|quality")
            return
        series_name, season, quality = [p.strip() for p in parts]
        season = season.upper()
        if not season.startswith("S"):
            season = "S" + season.lstrip("Season").strip()
        context.user_data['upload_series'] = series_name.lower()
        context.user_data['upload_season'] = season
        context.user_data['upload_quality'] = quality
        context.user_data['upload_episode'] = get_next_episode_number(series_name.lower(), season)
        update.message.reply_text(
            f"Context set to {series_name} - {season} - {quality}. "
            f"Upload files now. Episode will auto-increment from E{context.user_data['upload_episode']}."
        )
    else:
        update.message.reply_text("Use format: /add series_name|season|quality")

def next_quality_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        update.message.reply_text("You are not authorized.")
        return
    if context.args:
        parts = " ".join(context.args).split("|")
        if len(parts) == 2:
            series_name, quality = [p.strip() for p in parts]
            series_name_key = series_name.lower()
        elif len(parts) == 1:
            quality = parts[0].strip()
            series_name_key = context.user_data.get('upload_series')
            if not series_name_key:
                update.message.reply_text("Current series not set. Use /add first with series_name|season|quality")
                return
        else:
            update.message.reply_text("Use format: /n series_name|quality or /n quality")
            return
        
        season_key = context.user_data.get('upload_season')
        if not season_key:
            update.message.reply_text("Season not set. Use /add with series_name|season|quality to set the season.")
            return

        context.user_data['upload_series'] = series_name_key
        context.user_data['upload_quality'] = quality
        context.user_data['upload_episode'] = 1  # Reset episode count for new quality

        update.message.reply_text(f"Quality changed to {quality} for series {series_name_key}. Episode counter reset to 1. Upload files now.")
    else:
        update.message.reply_text("Use format: /n series_name|quality or /n quality")

def get_next_episode_number(series_name, season):
    series = series_collection.find_one({"name": series_name})
    if not series or "seasons" not in series or season not in series["seasons"]:
        return 1
    episodes = series["seasons"][season].get("episodes", {})
    max_ep = 0
    for ep in episodes.keys():
        try:
            ep_num = int(ep.lstrip("E"))
            if ep_num > max_ep:
                max_ep = ep_num
        except:
            continue
    return max_ep + 1

def handle_admin_file(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    message = update.message
    file_obj = message.document or message.video
    if not file_obj:
        update.message.reply_text("Please send a document or video file.")
        return

    series = context.user_data.get("upload_series")
    season = context.user_data.get("upload_season")
    quality = context.user_data.get("upload_quality")
    episode = context.user_data.get("upload_episode")

    if not all([series, season, quality, episode]):
        update.message.reply_text("Use /add to set series, season, and quality before uploading files.")
        return

    file_id = file_obj.file_id
    episode_key = f"E{episode}"

    update_query = {
        f"seasons.{season}.episodes.{episode_key}.qualities.{quality}": file_id,
        "name": series
    }

    series_collection.update_one({"name": series}, {"$set": update_query}, upsert=True)
    update.message.reply_text(f"Saved: Series {series}, Season {season}, Episode {episode_key}, Quality {quality}.")
    context.user_data["upload_episode"] = episode + 1

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
        update.message.reply_text("No seasons found for this series.")
        return

    keyboard = [[InlineKeyboardButton("All Seasons", callback_data=f"all_seasons|{series['name']}")]]
    for season_name in sorted(seasons.keys()):
        keyboard.append([InlineKeyboardButton(season_name, callback_data=f"season|{series['name']}|{season_name}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"Select Season for {series['name']}:", reply_markup=reply_markup)

def button_handler(update: Update, context: CallbackContext):
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

    if action == "season":
        if len(parts) < 3:
            query.edit_message_text(text="Invalid season action.")
            return
        season_name = parts[2]
        season = series["seasons"].get(season_name, {})
        episodes = season.get("episodes", {})
        if not episodes:
            query.edit_message_text(text="No episodes found in this season.")
            return

        buttons = [InlineKeyboardButton("All Episodes", callback_data=f"all_episodes|{series['name']}|{season_name}")]
        buttons += [InlineKeyboardButton(ep_name, callback_data=f"episode|{series['name']}|{season_name}|{ep_name}") for ep_name in sorted(episodes.keys())]
        
        button_rows = build_button_rows(buttons, row_size=3)
        reply_markup = InlineKeyboardMarkup(button_rows)
        query.edit_message_text(text=f"Select Episode for {season_name}:", reply_markup=reply_markup)

    elif action == "episode":
        if len(parts) < 4:
            query.edit_message_text(text="Invalid episode action.")
            return
        season_name = parts[2]
        ep_name = parts[3]
        season = series.get("seasons", {}).get(season_name, {})
        episode = season.get("episodes", {}).get(ep_name, {})
        qualities = episode.get("qualities", {})
        if not qualities:
            query.edit_message_text(text="No qualities found for this episode.")
            return

        buttons = [InlineKeyboardButton(q, callback_data=f"quality|{series['name']}|{season_name}|{ep_name}|{q}") for q in sorted(qualities.keys())]
        buttons.append(InlineKeyboardButton("Back to Seasons", callback_data=f"season|{series['name']}|{season_name}"))

        button_rows = build_button_rows(buttons, row_size=3)
        reply_markup = InlineKeyboardMarkup(button_rows)
        query.edit_message_text(text=f"Select Quality for {ep_name}:", reply_markup=reply_markup)

    elif action == "quality":
        if len(parts) < 5:
            query.edit_message_text(text="Invalid quality action.")
            return
        season_name = parts[2]
        ep_name = parts[3]
        quality_name = parts[4]
        season = series.get("seasons", {}).get(season_name, {})
        episode = season.get("episodes", {}).get(ep_name, {})
        qualities = episode.get("qualities", {})
        file_id_or_url = qualities.get(quality_name)
        if not file_id_or_url:
            query.edit_message_text(text="File not found for selected quality.")
            return

        try:
            if file_id_or_url.startswith("http://") or file_id_or_url.startswith("https://"):
                keyboard = [InlineKeyboardButton(f"Download {ep_name} in {quality_name}", url=file_id_or_url),
                            InlineKeyboardButton("Back to Episodes", callback_data=f"episode|{series['name']}|{season_name}|{ep_name}")]
                reply_markup = InlineKeyboardMarkup([keyboard])
                query.edit_message_text(text=f"Download link for {ep_name} in {quality_name}:", reply_markup=reply_markup)
            else:
                context.bot.send_document(chat_id=query.from_user.id, document=file_id_or_url, caption=CUSTOM_FILE_CAPTION)
                keyboard = [InlineKeyboardButton("Back to Episodes", callback_data=f"episode|{series['name']}|{season_name}|{ep_name}")]
                reply_markup = InlineKeyboardMarkup([keyboard])
                query.edit_message_text(text=f"Sent {ep_name} in {quality_name} to your private chat.", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            query.edit_message_text(text="Failed to send the file. Please try again later.")

    elif action == "all_seasons":
        quality_set = set()
        for season_name, season in series.get("seasons", {}).items():
            episodes = season.get("episodes", {})
            for ep_data in episodes.values():
                quality_set.update(ep_data.get("qualities", {}).keys())
        if not quality_set:
            query.edit_message_text(text="No qualities found for this series.")
            return
        buttons = [InlineKeyboardButton(q, callback_data=f"all_seasons_quality|{series['name']}|{q}") for q in sorted(quality_set)]
        reply_markup = InlineKeyboardMarkup([[btn] for btn in buttons])
        query.edit_message_text(text="Select quality to send all episodes of all seasons:", reply_markup=reply_markup)

    elif action == "all_seasons_quality":
        user_id = query.from_user.id
        if len(parts) < 3:
            query.edit_message_text(text="Invalid action.")
            return
        quality = parts[2]
        query.edit_message_text(text=f"Sending all episodes in {quality} for all seasons to your private chat...")
        count_sent = 0
        for season_name, season in series.get("seasons", {}).items():
            episodes = season.get("episodes", {})
            for ep_name, ep_data in episodes.items():
                qualities = ep_data.get("qualities", {})
                file_id = qualities.get(quality)
                if file_id:
                    try:
                        context.bot.send_document(chat_id=user_id, document=file_id, caption=CUSTOM_FILE_CAPTION or f"{series_name} - {season_name} - {ep_name} - {quality}")
                        count_sent += 1
                    except Exception as e:
                        logger.error(f"Error sending file: {e}")
        if count_sent == 0:
            context.bot.send_message(chat_id=user_id, text=f"No episodes found for quality {quality}.")
        else:
            context.bot.send_message(chat_id=user_id, text=f"Sent {count_sent} episodes for quality {quality}.")

    elif action == "all_episodes":
        if len(parts) < 3:
            query.edit_message_text(text="Please select a season first.")
            return
        season_name = parts[2]
        season = series.get("seasons", {}).get(season_name, {})
        episodes = season.get("episodes", {})
        quality_set = set()
        for ep_data in episodes.values():
            quality_set.update(ep_data.get("qualities", {}).keys())
        buttons = [InlineKeyboardButton(q, callback_data=f"all_quality|{series['name']}|{season_name}|{q}") for q in sorted(quality_set)]
        reply_markup = InlineKeyboardMarkup([[btn] for btn in buttons])
        query.edit_message_text(text=f"Select quality to send all episodes in season {season_name}:", reply_markup=reply_markup)

    elif action == "all_quality":
        user_id = query.from_user.id
        if len(parts) < 4:
            query.edit_message_text(text="Invalid action.")
            return
        season_name = parts[2]
        quality = parts[3]
        season = series.get("seasons", {}).get(season_name, {})
        episodes = season.get("episodes", {})
        query.edit_message_text(text=f"Sending all episodes in {quality} for season {season_name} to your private chat...")
        for ep_name, ep_data in episodes.items():
            qualities = ep_data.get("qualities", {})
            file_id = qualities.get(quality)
            if file_id:
                try:
                    context.bot.send_document(chat_id=user_id, document=file_id, caption=CUSTOM_FILE_CAPTION or f"{series_name} - {season_name} - {ep_name} - {quality}")
                except Exception as e:
                    logger.error(f"Error sending file: {e}")

    else:
        query.edit_message_text(text="Unknown action.")

def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main():
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("add", add_series_command))
    dispatcher.add_handler(CommandHandler("n", next_quality_command))
    dispatcher.add_handler(MessageHandler(Filters.document | Filters.video, handle_admin_file))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_series_query))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_error_handler(error_handler)

    updater.start_polling()
    logger.info("Bot started.")
    updater.idle()

if __name__ == "__main__":
    main()
