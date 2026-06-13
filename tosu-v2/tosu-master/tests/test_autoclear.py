from unittest.mock import patch

import pytest

import config
from AnonXMusic.utils.stream.autoclear import auto_clean


@pytest.fixture(autouse=True)
def reset_autoclean():
    config.autoclean.clear()
    yield
    config.autoclean.clear()


class TestAutoClean:
    @pytest.mark.asyncio
    async def test_removes_file_from_autoclean(self):
        config.autoclean.extend(["song.mp3", "song.mp3"])
        popped = {"file": "song.mp3"}
        with patch("os.remove"):
            await auto_clean(popped)
        assert config.autoclean.count("song.mp3") == 1

    @pytest.mark.asyncio
    async def test_deletes_file_when_last_reference(self):
        config.autoclean.append("song.mp3")
        popped = {"file": "song.mp3"}
        with patch("os.remove") as mock_remove:
            await auto_clean(popped)
        mock_remove.assert_called_once_with("song.mp3")

    @pytest.mark.asyncio
    async def test_does_not_delete_when_still_referenced(self):
        config.autoclean.extend(["song.mp3", "song.mp3"])
        popped = {"file": "song.mp3"}
        with patch("os.remove") as mock_remove:
            await auto_clean(popped)
        mock_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_vid_file_still_deleted_due_to_or_condition(self):
        """The source uses `or` not `and`, so vid/live/index files
        are still deleted when they are the last reference."""
        config.autoclean.append("vid_song.mp4")
        popped = {"file": "vid_song.mp4"}
        with patch("os.remove") as mock_remove:
            await auto_clean(popped)
        mock_remove.assert_called_once_with("vid_song.mp4")

    @pytest.mark.asyncio
    async def test_handles_missing_file_key(self):
        popped = {"title": "test"}
        await auto_clean(popped)

    @pytest.mark.asyncio
    async def test_handles_empty_popped(self):
        await auto_clean({})

    @pytest.mark.asyncio
    async def test_handles_file_not_in_autoclean(self):
        popped = {"file": "nonexistent.mp3"}
        await auto_clean(popped)

    @pytest.mark.asyncio
    async def test_os_remove_failure_is_swallowed(self):
        config.autoclean.append("song.mp3")
        popped = {"file": "song.mp3"}
        with patch("os.remove", side_effect=OSError("permission denied")):
            await auto_clean(popped)
