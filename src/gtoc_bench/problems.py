"""Trusted problem selection; submission data cannot select a judge or policy."""

SPECS = {
    "gtocx": {"score_unit": "points", "max_bytes": 100_000_000, "timeout": 600},
    "gtoc11": {"score_unit": "points", "max_bytes": 100_000_000, "timeout": 600},
    "gtoc12": {"score_unit": "kg", "max_bytes": 16 * 1024 * 1024, "timeout": 120},
    "gtoc13": {"score_unit": "points", "max_bytes": 100_000_000, "timeout": 600},
}


def specification(problem="gtoc12"):
    if problem not in SPECS:
        raise ValueError(f"Unknown problem {problem!r}; choose {', '.join(SPECS)}")
    return SPECS[problem]


def supervisor_timeout(problem):
    return specification(problem)["timeout"] + 15


def transport_timeout(problem):
    return supervisor_timeout(problem) + 15


def client_timeout(problem):
    return transport_timeout(problem) + 30
