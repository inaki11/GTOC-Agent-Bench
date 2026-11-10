"""Independent GTOCX trajectory judge and scoring, with calendar bonus B=1."""

REVISION = "gtocx-independent-v1"


def evaluate(data, **kwargs):
    from .judge import evaluate as run

    return run(data, **kwargs)
