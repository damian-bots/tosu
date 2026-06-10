# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

import asyncio

import pytest
import pytest_asyncio

from AnonXMusic.utils.formatters import (
    alpha_to_int,
    convert_bytes,
    get_readable_time,
    int_to_alpha,
    seconds_to_min,
    speed_converter,
    time_to_seconds,
)


class TestGetReadableTime:
    def test_zero_seconds(self):
        assert get_readable_time(0) == ""

    def test_seconds_only(self):
        assert get_readable_time(45) == "45s"

    def test_minutes_and_seconds(self):
        result = get_readable_time(125)
        assert result == "2\u1d0d:5s"

    def test_hours_minutes_seconds(self):
        result = get_readable_time(3661)
        assert result == "1\u029c:1\u1d0d:1s"

    def test_days(self):
        result = get_readable_time(90061)
        assert "1\u1d05\u1d00\u028fs" in result

    def test_one_second(self):
        assert get_readable_time(1) == "1s"

    def test_exact_minute(self):
        result = get_readable_time(60)
        assert result == "1\u1d0d:0s"

    def test_exact_hour(self):
        result = get_readable_time(3600)
        assert result == "1\u029c:0\u1d0d:0s"


class TestConvertBytes:
    def test_zero(self):
        assert convert_bytes(0) == ""

    def test_none(self):
        assert convert_bytes(None) == ""

    def test_bytes(self):
        result = convert_bytes(500)
        assert "500.00" in result
        assert "B" in result

    def test_kilobytes(self):
        result = convert_bytes(2048)
        assert "2.00" in result
        assert "Ki" in result

    def test_megabytes(self):
        result = convert_bytes(1048577)
        assert "Mi" in result

    def test_gigabytes(self):
        result = convert_bytes(1073741825)
        assert "Gi" in result

    def test_terabytes(self):
        result = convert_bytes(1099511627777)
        assert "Ti" in result


class TestIntToAlpha:
    @pytest.mark.asyncio
    async def test_single_digit(self):
        result = await int_to_alpha(5)
        assert result == "f"

    @pytest.mark.asyncio
    async def test_multi_digit(self):
        result = await int_to_alpha(123)
        assert result == "bcd"

    @pytest.mark.asyncio
    async def test_zero(self):
        result = await int_to_alpha(0)
        assert result == "a"

    @pytest.mark.asyncio
    async def test_all_digits(self):
        result = await int_to_alpha(1234567890)
        assert result == "bcdefghija"


class TestAlphaToInt:
    @pytest.mark.asyncio
    async def test_single_char(self):
        result = await alpha_to_int("f")
        assert result == 5

    @pytest.mark.asyncio
    async def test_multi_char(self):
        result = await alpha_to_int("bcd")
        assert result == 123

    @pytest.mark.asyncio
    async def test_roundtrip(self):
        original = 9876543210
        alpha = await int_to_alpha(original)
        back = await alpha_to_int(alpha)
        assert back == original


class TestTimeToSeconds:
    def test_minutes_seconds(self):
        assert time_to_seconds("3:30") == 210

    def test_hours_minutes_seconds(self):
        assert time_to_seconds("1:00:00") == 3600

    def test_seconds_only(self):
        assert time_to_seconds("45") == 45

    def test_zero(self):
        assert time_to_seconds("0:00") == 0

    def test_large_value(self):
        assert time_to_seconds("10:00") == 600


class TestSecondsToMin:
    def test_none(self):
        assert seconds_to_min(None) == "-"

    def test_zero(self):
        assert seconds_to_min(0) == "-"

    def test_seconds_only(self):
        assert seconds_to_min(30) == "00:30"

    def test_minutes_seconds(self):
        assert seconds_to_min(125) == "02:05"

    def test_hours(self):
        assert seconds_to_min(3661) == "01:01:01"

    def test_days(self):
        result = seconds_to_min(90061)
        assert result == "01:01:01:01"


class TestSpeedConverter:
    def test_half_speed(self):
        result = speed_converter(100, "0.5")
        converted, total = result
        assert total == 200

    def test_075_speed(self):
        result = speed_converter(100, "0.75")
        converted, total = result
        assert total == 150

    def test_15_speed(self):
        result = speed_converter(100, "1.5")
        converted, total = result
        assert total == 75

    def test_double_speed(self):
        result = speed_converter(100, "2.0")
        converted, total = result
        assert total == 50

    def test_normal_speed(self):
        result = speed_converter(100, "1.0")
        converted, total = result
        assert total == 100

    def test_zero_seconds(self):
        result = speed_converter(0, "2.0")
        assert result == "-"
