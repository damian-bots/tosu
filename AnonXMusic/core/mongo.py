import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, IndexModel
from pymongo.errors import OperationFailure

from config import DB_NAME, MONGO_DB_URI
from ..logging import LOGGER

_LOG = LOGGER(__name__)

_LOG.info("Connecting to MongoDB...")
try:
    _mongo_async_ = AsyncIOMotorClient(
        MONGO_DB_URI,
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


async def _safe_create_index(collection, index_model: IndexModel) -> None:
    """
    Create an index, silently ignoring conflicts with existing indexes
    (MongoDB error code 85 = IndexOptionsConflict, 86 = IndexKeySpecsConflict).
    Any other OperationFailure is also swallowed — index creation is best-effort.
    """
    try:
        await collection.create_indexes([index_model])
    except OperationFailure as e:
        if e.code in (85, 86):
            pass  # Index already exists under a different name — that's fine
        else:
            _LOG.debug(f"[mongo] Index creation skipped for {collection.name}: {e}")
    except Exception as e:
        _LOG.debug(f"[mongo] Index creation skipped for {collection.name}: {e}")


async def ensure_indexes():
    """
    Create indexes for frequently-queried fields.
    Reduces full-collection scans which are a major source of lag.
    Uses background=True so the bot isn't blocked during index builds.
    """
    chat_id_index = IndexModel([("chat_id", ASCENDING)], background=True)

    for col_name in [
        "adminauth", "authuser", "assistants", "blacklistChat",
        "cplaymode", "language", "playmode", "playtypedb",
        "skipmode", "upcount",
    ]:
        await _safe_create_index(mongodb[col_name], chat_id_index)

    await _safe_create_index(
        mongodb["search_cache"],
        IndexModel([("query", ASCENDING)], background=True),
    )
    await _safe_create_index(
        mongodb["search_cache"],
        IndexModel([("video_id", ASCENDING)], background=True),
    )

    _LOG.info("MongoDB indexes ensured.")
