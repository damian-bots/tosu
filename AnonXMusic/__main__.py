import asyncio
import importlib
import sys

# ── Use uvloop for significantly better async performance ────────────────────
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

from pyrogram import idle
from pytgcalls.exceptions import NoActiveGroupCall

import config
from AnonXMusic import LOGGER, app, userbot
from AnonXMusic.core.call import Anony
from AnonXMusic.misc import sudo
from AnonXMusic.plugins import ALL_MODULES
from AnonXMusic.utils.database import get_banned_users, get_gbanned
from AnonXMusic.utils.sys import cleanup_downloads, cleanup_playback_cache
from AnonXMusic.core.mongo import ensure_indexes
from config import BANNED_USERS

_LOG = LOGGER(__name__)


async def init():
    if not any([config.STRING1, config.STRING2, config.STRING3, config.STRING4, config.STRING5]):
        _LOG.error("No assistant client string session defined — exiting.")
        sys.exit(1)

    await sudo()

    # Clean up stale downloads from previous runs
    cleanup_downloads()
    cleanup_playback_cache()

    # Create MongoDB indexes for faster queries
    await ensure_indexes()

    try:
        users = await get_gbanned()
        for user_id in users:
            BANNED_USERS.add(user_id)
        users = await get_banned_users()
        for user_id in users:
            BANNED_USERS.add(user_id)
    except Exception as e:
        _LOG.warning(f"Could not load ban lists: {e}")

    await app.start()

    for all_module in ALL_MODULES:
        try:
            importlib.import_module("AnonXMusic.plugins" + all_module)
        except Exception as e:
            _LOG.error(f"Failed to import module {all_module}: {e}")

    LOGGER("AnonXMusic.plugins").info("Modules imported.")
    await userbot.start()
    await Anony.start()

    try:
        await Anony.stream_call("https://te.legra.ph/file/29f784eb49d230ab62e9e.mp4")
    except NoActiveGroupCall:
        _LOG.error(
            "Please turn on the video chat of your log group/channel.\n\nStopping Bot..."
        )
        sys.exit(1)
    except Exception:
        pass

    await Anony.decorators()
    _LOG.info("Bot is fully started and ready.")
    await idle()
    await app.stop()
    await userbot.stop()
    _LOG.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(init())
