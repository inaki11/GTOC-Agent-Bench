from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from gtoc_bench.provider_retry import retry_hint, retry_seconds


@pytest.mark.parametrize(
    "value,milliseconds,expected",
    [("302", False, 302), ("302000", True, 302), (0, False, 0), ("0.25", False, 0.25)],
)
def test_provider_delay_units(value, milliseconds, expected):
    assert retry_seconds(value, milliseconds=milliseconds) == expected


@pytest.mark.parametrize("value", [None, True, [], {}, "bad", "NaN", "inf", "-inf", -1])
def test_invalid_hints_are_not_used_for_scheduling(value):
    assert retry_seconds(value) is None


def test_http_date_is_converted_to_a_delay():
    now = datetime(2026, 11, 4, tzinfo=UTC)
    future = format_datetime(now + timedelta(seconds=302), usegmt=True)
    assert retry_seconds(future, now=now) == 302
    assert retry_seconds(format_datetime(now - timedelta(seconds=2)), now=now) == 0
    assert retry_seconds(future, milliseconds=True, now=now) is None


def test_longest_hint_wins_across_http_and_structured_error_headers():
    hint = retry_hint(
        {"Retry-After": "120"},
        {"headers": {"Retry-After": "302", "retry-after-ms": "303000"}},
    )
    assert hint == {"seconds": 303, "source": "error.headers.retry-after-ms", "value": "303000"}


def test_structured_seconds_and_milliseconds_are_supported():
    assert retry_hint(error={"retry_after": 302})["seconds"] == 302
    assert retry_hint(error={"retry_after_ms": 302000})["seconds"] == 302
    assert retry_hint(error={"headers": None, "retry_after": "NaN"}) is None
