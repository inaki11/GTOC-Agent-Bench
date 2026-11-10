"""Verify every publicly archived GTOCX final file; never installed in agent images."""

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

BASE = Path(__file__).resolve().parent


def check(path):
    from gtoc_bench.gtocx import evaluate

    return path.name, evaluate(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--output", type=Path, default=BASE / "reference-checks.json")
    args = parser.parse_args()
    from gtoc_bench.validator import verify_materials

    verify_materials("gtocx")
    manifest = json.loads((BASE / "manifest.json").read_text())
    paths = []
    for name, source in manifest["files"].items():
        path = BASE / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"Reference submission checksum mismatch: {name}")
        paths.append(path)
    results = {}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        pending = [pool.submit(check, path) for path in paths]
        for future in as_completed(pending):
            name, verdict = future.result()
            results[name] = verdict
            print(name, verdict["status"], verdict["score"], verdict["diagnostics"], flush=True)
            args.output.write_text(
                json.dumps(dict(sorted(results.items())), indent=2, allow_nan=False) + "\n"
            )
    print(
        f"Verified {len(results)} reference files: {sum(r['status'] == 'valid' for r in results.values())} accepted"
    )


if __name__ == "__main__":
    main()
