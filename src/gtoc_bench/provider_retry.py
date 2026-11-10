"""Provider retry hints are minimum delays, never shortened to fit a local budget."""

import math
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime


def retry_seconds(value, *, milliseconds=False, now=None):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        if milliseconds or not isinstance(value, str):
            return None
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            return max(0.0, (date - (now or datetime.now(UTC))).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds / 1000 if milliseconds else seconds


def retry_hint(headers=None, error=None, *, now=None):
    """Read HTTP and structured error hints; honor the largest valid minimum."""
    sources = [("http", headers or {})]
    if isinstance(error, dict):
        sources.extend([("error.headers", error.get("headers", {})), ("error", error)])
    hints = []
    for source, values in sources:
        if not hasattr(values, "items"):
            continue
        for name, value in values.items():
            field = str(name).lower().replace("_", "-")
            if field not in {"retry-after", "retry-after-ms", "retry-after-seconds"}:
                continue
            seconds = retry_seconds(value, milliseconds=field.endswith("-ms"), now=now)
            if seconds is not None:
                hints.append(
                    {"seconds": seconds, "source": f"{source}.{field}", "value": str(value)}
                )
    return max(hints, key=lambda hint: hint["seconds"], default=None)


def retry_not_before(seconds):
    try:
        return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    except OverflowError:
        # An enormous valid delay must not cause an immediate retry or a crash.
        return datetime.max.replace(tzinfo=UTC).isoformat()
