"""Independent GTOC13 judge with verified reconstruction of interior sail controls."""

REVISION = "gtoc13-reconstructed-controls-v2"


def evaluate(data, **kwargs):
    from .judge import evaluate as run

    return run(data, **kwargs)
