from config import time_to_seconds


class TestTimeToSeconds:
    def test_minutes_seconds(self):
        assert time_to_seconds("3:30") == 210

    def test_hours_minutes_seconds(self):
        assert time_to_seconds("1:00:00") == 3600

    def test_seconds_only(self):
        assert time_to_seconds("45") == 45

    def test_zero(self):
        assert time_to_seconds("0:00") == 0

    def test_duration_limit(self):
        assert time_to_seconds("469:00") == 469 * 60

    def test_integer_input(self):
        assert time_to_seconds(30) == 30

    def test_string_single_digit(self):
        assert time_to_seconds("5") == 5

    def test_large_hours(self):
        assert time_to_seconds("2:30:00") == 9000
