import os
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

# Admin user ID
ADMINS = [int(admin) for admin in os.getenv("ADMINS", "").split()]

# Pyrogram client
app = Client("series_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# MongoDB setup
mongo = MongoClient(MONGO_URL)
db = mongo["seriesbot"]
episodes = db["episodes"]
episode_counter = db["episode_counters"]

# Per-user session to track uploads
app.session_data = {}

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    await message.reply("👋 Welcome to the Series Bot!\nUse /add command to upload a new episode.")

@app.on_message(filters.command("add") & filters.private & filters.user(ADMINS))
async def add_episode(client, message: Message):
    try:
        args = message.text.split(" ", 1)[1]
        parts = [x.strip() for x in args.split("|")]
        if len(parts) != 3:
            return await message.reply("❗ Format: /add Series Name | Season Number | Quality")

        series_name, season_str, quality = parts
        season = int(season_str)

        counter = episode_counter.find_one({"series": series_name, "season": season})
        if not counter:
            episode = 1
            episode_counter.insert_one({"series": series_name, "season": season, "episode": episode})
        else:
            episode = counter["episode"]

        app.session_data[message.from_user.id] = {
            "series": series_name,
            "season": season,
            "episode": episode,
            "quality": quality
        }

        await message.reply(
            f"✅ Now send the file for:\n📺 {series_name}\n🎞 Season {season}, Episode {episode}\n🎚 Quality: {quality}"
        )

    except Exception as e:
        await message.reply(f"⚠️ Error: {e}")

@app.on_message(filters.document & filters.private & filters.user(ADMINS))
async def handle_file(client, message: Message):
    data = app.session_data.get(message.from_user.id)
    if not data:
        return await message.reply("❗ Use /add before uploading the file.")

    series, season, episode, quality = data["series"], data["season"], data["episode"], data["quality"]
    doc = message.document

    file_info = {
        "file_id": doc.file_id,
        "file_name": doc.file_name,
        "file_size": doc.file_size
    }

    existing = episodes.find_one({"series": series, "season": season, "episode": episode})

    if existing:
        existing["qualities"][quality] = file_info
        episodes.replace_one({"_id": existing["_id"]}, existing)
    else:
        new_doc = {
            "series": series,
            "season": season,
            "episode": episode,
            "title": f"{series} - S{season:02}E{episode:02}",
            "qualities": {
                quality: file_info
            }
        }
        episodes.insert_one(new_doc)

    await message.reply(f"✅ Uploaded {series} - S{season:02}E{episode:02} [{quality}]")

    episode_counter.update_one(
        {"series": series, "season": season},
        {"$inc": {"episode": 1}}
    )

    del app.session_data[message.from_user.id]

if __name__ == "__main__":
    print("Bot is running...")
    app.run()
