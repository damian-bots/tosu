"""Tests for in-memory database functions in AnonXMusic.utils.database.

These functions operate purely on module-level dicts/lists and do not
require a MongoDB connection.
"""
from unittest.mock import AsyncMock

import pytest

from AnonXMusic.utils import database


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all in-memory state before each test."""
    database.active.clear()
    database.activevideo.clear()
    database.loop.clear()
    database.pause.clear()
    database.maintenance.clear()
    database.playmode.clear()
    database.playtype.clear()
    database.langm.clear()
    database.channelconnect.clear()
    database.count.clear()
    database.skipmode.clear()
    database.nonadmin.clear()
    database.assistantdict.clear()
    database.search_cache.clear()
    yield


class TestActiveChats:
    @pytest.mark.asyncio
    async def test_empty_active_chats(self):
        result = await database.get_active_chats()
        assert result == []

    @pytest.mark.asyncio
    async def test_add_active_chat(self):
        await database.add_active_chat(123)
        assert await database.is_active_chat(123)

    @pytest.mark.asyncio
    async def test_add_duplicate_active_chat(self):
        await database.add_active_chat(123)
        await database.add_active_chat(123)
        chats = await database.get_active_chats()
        assert chats.count(123) == 1

    @pytest.mark.asyncio
    async def test_remove_active_chat(self):
        await database.add_active_chat(123)
        await database.remove_active_chat(123)
        assert not await database.is_active_chat(123)

    @pytest.mark.asyncio
    async def test_remove_nonexistent_chat(self):
        await database.remove_active_chat(999)
        assert not await database.is_active_chat(999)

    @pytest.mark.asyncio
    async def test_is_active_chat_false(self):
        assert not await database.is_active_chat(999)


class TestActiveVideoChats:
    @pytest.mark.asyncio
    async def test_empty_active_video_chats(self):
        result = await database.get_active_video_chats()
        assert result == []

    @pytest.mark.asyncio
    async def test_add_active_video_chat(self):
        await database.add_active_video_chat(456)
        assert await database.is_active_video_chat(456)

    @pytest.mark.asyncio
    async def test_add_duplicate_video_chat(self):
        await database.add_active_video_chat(456)
        await database.add_active_video_chat(456)
        chats = await database.get_active_video_chats()
        assert chats.count(456) == 1

    @pytest.mark.asyncio
    async def test_remove_active_video_chat(self):
        await database.add_active_video_chat(456)
        await database.remove_active_video_chat(456)
        assert not await database.is_active_video_chat(456)

    @pytest.mark.asyncio
    async def test_is_active_video_false(self):
        assert not await database.is_active_video_chat(999)


class TestLoop:
    @pytest.mark.asyncio
    async def test_default_loop(self):
        result = await database.get_loop(123)
        assert result == 0

    @pytest.mark.asyncio
    async def test_set_and_get_loop(self):
        await database.set_loop(123, 5)
        result = await database.get_loop(123)
        assert result == 5

    @pytest.mark.asyncio
    async def test_update_loop(self):
        await database.set_loop(123, 3)
        await database.set_loop(123, 10)
        result = await database.get_loop(123)
        assert result == 10


class TestMusicPlaying:
    @pytest.mark.asyncio
    async def test_default_not_playing(self):
        result = await database.is_music_playing(123)
        assert result is False

    @pytest.mark.asyncio
    async def test_music_on(self):
        await database.music_on(123)
        assert await database.is_music_playing(123) is True

    @pytest.mark.asyncio
    async def test_music_off(self):
        await database.music_on(123)
        await database.music_off(123)
        assert await database.is_music_playing(123) is False


class TestSearchCache:
    @pytest.mark.asyncio
    async def test_get_cache_empty_query(self):
        result = await database.get_search_cache("")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cache_none_query(self):
        result = await database.get_search_cache(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cache_hit(self):
        data = {
            "video_id": "abc123",
            "title": "Test Song",
            "duration": "3:30",
            "thumbnail": "http://example.com/thumb.jpg",
            "url": "http://example.com/video",
        }
        database.search_cache["test query"] = data
        result = await database.get_search_cache("Test Query")
        assert result == data

    @pytest.mark.asyncio
    async def test_get_cache_miss_falls_to_db(self):
        database.searchdb = AsyncMock()
        database.searchdb.find_one = AsyncMock(return_value=None)
        result = await database.get_search_cache("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cache_miss_db_hit(self):
        doc = {
            "video_id": "xyz",
            "title": "From DB",
            "duration": "2:00",
            "thumbnail": "http://example.com/t.jpg",
            "url": "http://example.com/v",
        }
        database.searchdb = AsyncMock()
        database.searchdb.find_one = AsyncMock(return_value=doc)
        result = await database.get_search_cache("db query")
        assert result["video_id"] == "xyz"
        assert "db query" in database.search_cache

    @pytest.mark.asyncio
    async def test_set_cache_empty_query(self):
        await database.set_search_cache("", {"video_id": "abc"})
        assert database.search_cache == {}

    @pytest.mark.asyncio
    async def test_set_cache_empty_data(self):
        await database.set_search_cache("test", {})
        assert database.search_cache == {}
