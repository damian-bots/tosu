# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

from AnonXMusic.mongo.readable_time import get_readable_time


class TestGetReadableTime:
    def test_zero(self):
        assert get_readable_time(0) == ""

    def test_seconds_only(self):
        assert get_readable_time(30) == "30s"

    def test_one_second(self):
        assert get_readable_time(1) == "1s"

    def test_minutes_and_seconds(self):
        result = get_readable_time(90)
        assert result == "1\u1d0d:30s"

    def test_exact_minute(self):
        result = get_readable_time(60)
        assert result == "1\u1d0d:0s"

    def test_hours(self):
        result = get_readable_time(3661)
        assert result == "1\u029c:1\u1d0d:1s"

    def test_exact_hour(self):
        result = get_readable_time(3600)
        assert result == "1\u029c:0\u1d0d:0s"

    def test_days(self):
        result = get_readable_time(90061)
        assert "1\u1d05\u1d00\u028fs" in result

    def test_large_seconds(self):
        result = get_readable_time(86400)
        assert "\u1d05\u1d00\u028fs" in result
