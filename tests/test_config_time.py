import pytest

from src.config import AppSettings


def _hm(value: str):
    return AppSettings(daily_digest_time=value).digest_hh_mm()


def test_colon_format():
    assert _hm("08:30") == (8, 30)
    assert _hm("8:5") == (8, 5)
    assert _hm("23:59") == (23, 59)


def test_four_digit_format():
    assert _hm("0830") == (8, 30)
    assert _hm("0000") == (0, 0)
    assert _hm("2359") == (23, 59)


def test_three_digit_format():
    assert _hm("830") == (8, 30)


def test_invalid_time_raises():
    with pytest.raises(ValueError):
        _hm("2560")
    with pytest.raises(ValueError):
        _hm("2400")
