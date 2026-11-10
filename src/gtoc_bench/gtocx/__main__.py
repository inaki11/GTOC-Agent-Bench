"""Standalone offline GTOCX judge: python -m gtoc_bench.gtocx FILE [--json]."""

import argparse
import json
import sys
from pathlib import Path

from . import evaluate
from .format import MAX_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Submission file, or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.file == "-":
            data = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with Path(args.file).open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        result = evaluate(data)
    except OSError as exc:
        result = {"status": "invalid", "score": None, "diagnostics": [str(exc)]}
    if args.json:
        print(json.dumps(result, indent=2, allow_nan=False))
    elif result["status"] == "valid":
        print(f"Check successfully! GTOCX score: {result['score']:.2f} points (B=1).")
    else:
        print(f"GTOCX {result['status']}: no score awarded.")
        for message in result["diagnostics"]:
            print(message)
    return 0 if result["status"] == "valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
