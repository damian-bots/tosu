import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, IndexModel

from config import DB_NAME, MONGO_DB_URI
from ..logging import LOGGER

_LOG = LOGGER(__name__)

_LOG.info("Connecting to MongoDB...")
try:
    _mongo_async_ = AsyncIOMotorClient(
        MONGO_DB_URI,
        # Connection pool tuning for high-load bots on dedicated servers
        maxPoolSize=20,
        minPoolSize=2,
        maxIdleTimeMS=30_000,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
        socketTimeoutMS=20_000,
    )
    mongodb = _mongo_async_[DB_NAME]
    _LOG.info("Connected to MongoDB.")
except Exception as e:
    _LOG.error(f"Failed to connect to MongoDB: {e}")
    exit()


async def ensure_indexes():
    """
    Create indexes for frequently-queried fields.
    Reduces full-collection scans which are a major source of lag.
    """
    try:
        # chat_id is the most-queried field across almost every collection
        _chat_id_index = IndexModel([("chat_id", ASCENDING)], background=True)

        collections_with_chat_id = [
            "adminauth", "authuser", "assistants", "blacklistChat",
            "cplaymode", "language", "playmode", "playtypedb",
            "skipmode", "upcount",
        ]
        for col_name in collections_with_chat_id:
            try:
                await mongodb[col_name].create_indexes([_chat_id_index])
            except Exception:
                pass  # Index may already exist — that's fine

        # search_cache: query field
        await mongodb["search_cache"].create_indexes([
            IndexModel([("query", ASCENDING)], background=True),
            IndexModel([("video_id", ASCENDING)], background=True),
        ])

        _LOG.info("MongoDB indexes ensured.")
    except Exception as e:
        _LOG.warning(f"Could not ensure MongoDB indexes: {e}")
