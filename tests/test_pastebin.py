from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from AnonXMusic.utils.pastebin import BASE, AnonyBin, post


class TestPost:
    @pytest.mark.asyncio
    async def test_post_json_response(self):
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"success": True, "message": "abc123"})
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock()))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_session), __aexit__=AsyncMock())):
            result = await post("https://example.com/api", data="test")
            assert result == {"success": True, "message": "abc123"}

    @pytest.mark.asyncio
    async def test_post_text_fallback(self):
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(side_effect=Exception("not json"))
        mock_resp.text = AsyncMock(return_value="plain text response")
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock()))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_session), __aexit__=AsyncMock())):
            result = await post("https://example.com/api", data="test")
            assert result == "plain text response"


class TestAnonyBin:
    @pytest.mark.asyncio
    async def test_successful_paste(self):
        with patch("AnonXMusic.utils.pastebin.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"success": True, "message": "abc123"}
            result = await AnonyBin("some text")
            assert result == f"{BASE}abc123"
            mock_post.assert_called_once_with(f"{BASE}api/v2/paste", data="some text")

    @pytest.mark.asyncio
    async def test_failed_paste(self):
        with patch("AnonXMusic.utils.pastebin.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"success": False, "message": "error"}
            result = await AnonyBin("some text")
            assert result is None

    @pytest.mark.asyncio
    async def test_base_url(self):
        assert BASE == "https://batbin.me/"
