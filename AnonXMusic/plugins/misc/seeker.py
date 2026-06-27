"""
seeker.py — Track playback position timer.
Ticks every second and increments the "played" counter for active streams.
Uses direct in-memory access (no async DB lookups) for minimum overhead.
"""
import asyncio

from AnonXMusic.misc import db
from AnonXMusic.utils.database import active, pause


async def timer():
    while True:
        await asyncio.sleep(1)
        # Iterate a snapshot of active chats to avoid set-changed-during-iteration errors
        for chat_id in list(active):
            # Check in-memory pause state directly (no async call needed)
            if not pause.get(chat_id):
                continue
            playing = db.get(chat_id)
            if not playing:
                continue
            duration = int(playing[0].get("seconds", 0))
            if duration == 0:
                continue
            played = db[chat_id][0].get("played", 0)
            if played >= duration:
                continue
            db[chat_id][0]["played"] = played + 1


asyncio.get_event_loop().create_task(timer())
