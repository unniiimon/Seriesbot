import logging
import os
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)
from pymongo import MongoClient
from telegram.error import TelegramError

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN',"7318650217:AAEXr17lLVfhXGBKgnMLgmtYjV1kJ_pAdmQ" )
MONGO_URI = os.environ.get('MONGO_URI', "mongodb+srv://Testmon:testmon@cluster0.flh9i33.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0" )
ADMIN_IDS = {int(x) for x in os.environ.get('ADMIN_IDS', '5387919847').split(',') if x}

if not BOT_TOKEN or not MONGO_URI:
    logger.error('Missing required environment variables')
    exit(1)

# Database connection with error handling
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()  # Test connection
    db = client.series_bot
    series_collection = db.series
    logger.info('Database connection established')
except Exception as e:
    logger.error(f'Database connection failed: {e}')
    exit(1)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def extract_season_number(season_str: str) -> str:
    """Convert various season formats to S01 style"""
    season_str = season_str.upper().replace('SEASON', '').replace('S', '').strip()
    try:
        return f'S{int(season_str):02d}'
    except ValueError:
        return f'S{season_str}'

def get_next_episode(series_name: str, season: str) -> int:
    """Get next episode number safely"""
    try:
        series = series_collection.find_one(
            {'name': series_name.lower()},
            {f'seasons.{season}.episodes': 1}
        )
        if not series or not series.get('seasons', {}).get(season, {}).get('episodes'):
            return 1
        episodes = series['seasons'][season]['episodes'].keys()
        return max([int(ep[1:]) for ep in episodes if ep.startswith('E') and ep[1:].isdigit()], default=0) + 1
    except Exception as e:
        logger.error(f'Episode number error: {e}')
        return 1

def add_series_command(update: Update, context: CallbackContext):
    """Handle /add command with robust parsing"""
    if not is_admin(update.effective_user.id):
        update.message.reply_text('🚫 Admin only', quote=True)
        return

    try:
        # Get the full command text
        full_text = update.message.text
        
        # Extract parts after /add
        parts = re.split(r'\s*\|\s*', full_text[len('/add'):].strip(), maxsplit=2)
        if len(parts) != 3:
            raise ValueError('Invalid format')
        
        series, season, quality = [x.strip() for x in parts]
        if not all([series, season, quality]):
            raise ValueError('Empty values')
        
        season = extract_season_number(season)
        series_lower = series.lower()

        # Store in context
        context.user_data.update({
            'upload_series': series_lower,
            'upload_season': season,
            'upload_quality': quality.lower(),
            'upload_episode': get_next_episode(series_lower, season)
        })

        update.message.reply_text(
            f'⚡ Ready to upload:\n'
            f'Series: {series}\n'
            f'Season: {season}\n'
            f'Quality: {quality}\n'
            f'Starting from: E{context.user_data["upload_episode"]:02d}\n\n'
            '📤 Send files now (documents/videos)',
            quote=True
        )
    except Exception as e:
        logger.warning(f'Add command error: {e}')
        update.message.reply_text(
            '❌ Invalid format! Use:\n'
            '/add series | season | quality\n'
            'Examples:\n'
            '/add Breaking Bad | S1 | 720p\n'
            '/add Stranger Things | Season 3 | 1080p\n'
            '/add The Boys | 2 | 480p',
            quote=True
        )

def handle_file(update: Update, context: CallbackContext):
    """Process uploaded files with error handling"""
    try:
        update.message.reply_chat_action('typing')
        
        # Verify setup
        if not all(k in context.user_data for k in ['upload_series', 'upload_season', 'upload_quality']):
            update.message.reply_text(
                '⚠️ First setup with /add series | season | quality',
                quote=True
            )
            return

        # Get file
        file = update.message.document or update.message.video
        if not file:
            update.message.reply_text('📛 Please send document or video files only', quote=True)
            return

        # Prepare data
        series = context.user_data['upload_series']
        season = context.user_data['upload_season']
        quality = context.user_data['upload_quality']
        ep_num = context.user_data.get('upload_episode', 1)
        ep_key = f'E{ep_num:02d}'

        # Database operation
        result = series_collection.update_one(
            {'name': series},
            {'$set': {
                f'seasons.{season}.episodes.{ep_key}.qualities.{quality}': file.file_id,
                'last_updated': datetime.now()
            }},
            upsert=True
        )

        # Determine if this was new episode
        if result.upserted_id or not series_collection.find_one({
            'name': series,
            f'seasons.{season}.episodes.{ep_key}': {'$exists': True}
        }):
            context.user_data['upload_episode'] = ep_num + 1
            action = '✨ NEW'
        else:
            action = '🔄 Updated'

        update.message.reply_text(
            f'{action} {series.title()} {season}{ep_key} ({quality})\n'
            f'Next: E{context.user_data.get("upload_episode", ep_num + 1):02d}',
            quote=True
        )
    except TelegramError as e:
        logger.error(f'Telegram error: {e}')
    except Exception as e:
        logger.error(f'File handling error: {e}')
        update.message.reply_text('⚠️ Error processing file. Try again.', quote=True)

def main():
    """Start the bot with proper error handling"""
    try:
        updater = Updater(BOT_TOKEN, use_context=True)
        dp = updater.dispatcher

        dp.add_handler(CommandHandler('add', add_series_command))
        dp.add_handler(MessageHandler(Filters.document | Filters.video, handle_file))

        updater.start_polling()
        logger.info('Bot is running...')
        updater.idle()
    except Exception as e:
        logger.critical(f'Bot failed: {e}')

if __name__ == '__main__':
    main()
