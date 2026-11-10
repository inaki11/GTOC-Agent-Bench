"""Unmodified organizer's GTOC11 judge, with bounded input/output handling."""

REVISION = "gtoc11-official-b1-v1"


def evaluate(data, temp_root=None):
    from .judge import evaluate as check

    return check(data, temp_root)
