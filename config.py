import re
from os import getenv

from dotenv import load_dotenv
from pyrogram import filters

load_dotenv()

# Get this value from my.telegram.org/apps
API_ID = "24620300"
API_HASH = "9a098f01aa56c836f2e34aee4b7ef963"

# Get your token from @BotFather on Telegram.
BOT_TOKEN = getenv("BOT_TOKEN")

# Get your mongo url from cloud.mongodb.com
MONGO_DB_URI = getenv("MONGO_DB_URI")
DB_URI = getenv("DB_URI")
DB_NAME = getenv("DB_NAME")

DURATION_LIMIT_MIN = int(getenv("DURATION_LIMIT", 469))

# Chat id of a group for logging bot's activities
LOGGER_ID = int(getenv("LOGGER_ID"))
MEDIA_CHANNEL_ID = int(getenv("MEDIA_CHANNEL_ID"))

#Get API_KEY from deadlinetech.site or @smaugxd
API_URL = getenv("API_URL", "https://api.arcmusic.fun")
API_KEY = getenv("API_KEY")

API_URL2 = getenv("API_URL2", "https://api.onegrab.fun")
API_KEY2 = getenv("API_KEY2")

# Get this value from @FallenxBot on Telegram by /id
OWNER_ID = 603536072

## Fill these variables if you're deploying on heroku.
# Your heroku app name
HEROKU_APP_NAME = getenv("HEROKU_APP_NAME")
# Get it from http://dashboard.heroku.com/account
HEROKU_API_KEY = getenv("HEROKU_API_KEY")

UPSTREAM_REPO = getenv(
    "UPSTREAM_REPO",
    "https://github.com/damian-bots/tosu",
)
UPSTREAM_BRANCH = getenv("UPSTREAM_BRANCH", "master")
GIT_TOKEN = getenv(
    "GIT_TOKEN", None
)  # Fill this variable if your upstream repository is private

SUPPORT_CHANNEL = getenv("SUPPORT_CHANNEL", "https://t.me/Arcupdates")
SUPPORT_CHAT = getenv("SUPPORT_CHAT", "https://t.me/ArcChatz")

# Set this to True if you want the assistant to automatically leave chats after an interval
AUTO_LEAVING_ASSISTANT = bool(getenv("AUTO_LEAVING_ASSISTANT", True))


# Get this credentials from https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_ID = getenv("SPOTIFY_CLIENT_ID", "6be9f0b34c384ad097cc71b1c1fc5e8b")
SPOTIFY_CLIENT_SECRET = getenv("SPOTIFY_CLIENT_SECRET", "2607415f99944cc6b24fa98018fb8c09")


# Maximum limit for fetching playlist's track from youtube, spotify, apple links.
PLAYLIST_FETCH_LIMIT = int(getenv("PLAYLIST_FETCH_LIMIT", 50))


# ── Platform Toggles ──────────────────────────────────────────────────────────
# Set to "false" (case-insensitive) to disable a platform.
# Users will see a "temporarily disabled" message with a support link.
def _bool(key: str, default: bool = True) -> bool:
    val = getenv(key, "").strip().lower()
    if val == "false":
        return False
    if val == "true":
        return True
    return default

ENABLE_YOUTUBE    = _bool("ENABLE_YOUTUBE",    True)
ENABLE_SPOTIFY    = _bool("ENABLE_SPOTIFY",    True)
ENABLE_APPLE      = _bool("ENABLE_APPLE",      True)
ENABLE_SOUNDCLOUD = _bool("ENABLE_SOUNDCLOUD", True)
ENABLE_DEEZER     = _bool("ENABLE_DEEZER",     True)
ENABLE_GAANA      = _bool("ENABLE_GAANA",      True)
ENABLE_TIDAL      = _bool("ENABLE_TIDAL",      False)
ENABLE_JIOSAAVN   = _bool("ENABLE_JIOSAAVN",   True)
ENABLE_TWITCH     = _bool("ENABLE_TWITCH",     True)
ENABLE_KICK       = _bool("ENABLE_KICK",       True)
ENABLE_MXPLAYER   = _bool("ENABLE_MXPLAYER",   True)


# Telegram audio and video file size limit (in bytes)
TG_AUDIO_FILESIZE_LIMIT = int(getenv("TG_AUDIO_FILESIZE_LIMIT", 104857600))
TG_VIDEO_FILESIZE_LIMIT = int(getenv("TG_VIDEO_FILESIZE_LIMIT", 1073741824))
# Checkout https://www.gbmb.org/mb-to-bytes for converting mb to bytes


# Get your pyrogram v2 session from @StringFatherBot on Telegram
STRING1 = getenv("STRING_SESSION", None)
STRING2 = getenv("STRING_SESSION2", None)
STRING3 = getenv("STRING_SESSION3", None)
STRING4 = getenv("STRING_SESSION4", None)
STRING5 = getenv("STRING_SESSION5", None)


BANNED_USERS = filters.user()
adminlist = {}
lyrical = {}
votemode = {}
autoclean = []
confirmer = {}


START_IMG_URL = getenv(
    "START_IMG_URL", "https://files.catbox.moe/67fpo2.jpg"
)
PING_IMG_URL = getenv(
    "PING_IMG_URL", "https://files.catbox.moe/4q5mlx.jpg"
)
PLAYLIST_IMG_URL = "https://files.catbox.moe/hyfiyc.jpg"
STATS_IMG_URL = "https://files.catbox.moe/4q5mlx.jpg"
TELEGRAM_AUDIO_URL = "https://files.catbox.moe/hyfiyc.jpg"
TELEGRAM_VIDEO_URL = "https://files.catbox.moe/viv1hy.jpg"
STREAM_IMG_URL = "https://files.catbox.moe/he87u5.jpg"
SOUNCLOUD_IMG_URL = "https://files.catbox.moe/hyfiyc.jpg"
YOUTUBE_IMG_URL = "https://files.catbox.moe/viv1hy.jpg"
SPOTIFY_ARTIST_IMG_URL = "https://graph.org/file/97669c286e18c2eddc72d.jpg"
SPOTIFY_ALBUM_IMG_URL = "https://graph.org/file/97669c286e18c2eddc72d.jpg"
SPOTIFY_PLAYLIST_IMG_URL = "https://files.catbox.moe/viv1hy.jpg"
APPLE_IMG_URL = getenv("APPLE_IMG_URL", "https://graph.org/file/e528bda04666ba055b7dc-4e329c61fb4c76075e.jpg")
DEEZER_IMG_URL = getenv("DEEZER_IMG_URL", "https://graph.org/file/7cd62e72a920f16d8fda3-732e322c7ae578639b.jpg")
TIDAL_IMG_URL = getenv("TIDAL_IMG_URL", "https://graph.org/file/16ece0c0a5ce175e59d82-61ce1455217b67a6fb.jpg")
GAANA_IMG_URL = getenv("GAANA_IMG_URL", "https://graph.org/file/d8e84b377613036662aaf-706e3522833f0c8362.jpg")
JIOSAAVN_IMG_URL = getenv("JIOSAAVN_IMG_URL", "https://graph.org/file/4b8567ce6ea17529e46c7-847417c56f94a0eb7d.jpg")
TWITCH_IMG_URL = getenv("TWITCH_IMG_URL", "https://graph.org/file/3a29b6bdfe13d14e60631-dbb00927f3a06b2da5.jpg")
KICK_IMG_URL = getenv("KICK_IMG_URL", "https://graph.org/file/585897ed58476ed81961b-c1654b83be3f910e58.jpg")
MXPLAYER_IMG_URL = getenv("MXPLAYER_IMG_URL", "https://graph.org/file/87de6205beb117608dea4-103175004f7191f9d2.jpg")


def time_to_seconds(time):
    stringt = str(time)
    return sum(int(x) * 60**i for i, x in enumerate(reversed(stringt.split(":"))))


DURATION_LIMIT = int(time_to_seconds(f"{DURATION_LIMIT_MIN}:00"))


if SUPPORT_CHANNEL:
    if not re.match("(?:http|https)://", SUPPORT_CHANNEL):
        raise SystemExit(
            "[ERROR] - Your SUPPORT_CHANNEL url is wrong. Please ensure that it starts with https://"
        )

if SUPPORT_CHAT:
    if not re.match("(?:http|https)://", SUPPORT_CHAT):
        raise SystemExit(
            "[ERROR] - Your SUPPORT_CHAT url is wrong. Please ensure that it starts with https://"
    )
