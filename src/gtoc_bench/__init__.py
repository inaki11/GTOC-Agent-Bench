"""GTOC-Bench: the controller owns time, credentials and recorded results."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("GTOC_BENCH_ROOT", Path(__file__).resolve().parents[2]))
__version__ = "0.1.0"
