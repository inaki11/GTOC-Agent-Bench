"""Audit the organizer example and invalid mutations with the protected judge.

Run from bench: uv run python references/gtoc11/check_reference.py --image gtoc-bench:gtoc11-cpu
No participant program is executed; only the supplied official Linux checker.
"""

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from gtoc_bench import ROOT
from gtoc_bench.cli import validate_file
from gtoc_bench.validator import verify_materials


def change(data, row, column, value):
    lines = data.decode().splitlines()
    fields = lines[row - 1].split()
    fields[column] = str(value)
    lines[row - 1] = " ".join(fields)
    return ("\n".join(lines) + "\n").encode()


def asteroid_state(identifier, epoch):
    """Analytic Kepler state used only to construct an isolated negative test."""
    catalog = ROOT / "problems/gtoc11/data/Candidate Asteroids.txt"
    with catalog.open() as stream:
        entry = next(line.split() for line in stream if int(line.split()[0]) == identifier)
    _, reference, a, e, inclination, node, perihelion, anomaly, _ = map(float, entry)
    a *= 1.49597870691e8
    inclination, node, perihelion, anomaly = map(
        math.radians, (inclination, node, perihelion, anomaly)
    )
    motion = math.sqrt(1.32712440018e11 / a**3)
    mean = (anomaly + motion * (epoch - reference) * 86400) % (2 * math.pi)
    eccentric = mean
    for _ in range(20):
        eccentric -= (eccentric - e * math.sin(eccentric) - mean) / (1 - e * math.cos(eccentric))
    x, y = a * (math.cos(eccentric) - e), a * math.sqrt(1 - e * e) * math.sin(eccentric)
    vx = -a * motion * math.sin(eccentric) / (1 - e * math.cos(eccentric))
    vy = a * motion * math.sqrt(1 - e * e) * math.cos(eccentric) / (1 - e * math.cos(eccentric))
    co, so, ci, si, cw, sw = (
        math.cos(node),
        math.sin(node),
        math.cos(inclination),
        math.sin(inclination),
        math.cos(perihelion),
        math.sin(perihelion),
    )
    p = (co * cw - so * sw * ci, so * cw + co * sw * ci, sw * si)
    q = (-co * sw - so * cw * ci, -so * sw + co * cw * ci, cw * si)
    return [x * p[j] + y * q[j] for j in range(3)] + [vx * p[j] + vy * q[j] for j in range(3)]


def early_activation(data):
    # The small fixture's first flyby is 95939 MJD. Move activation to +29 days,
    # updating r/v as well, so the official checker reaches its actual wait test
    # instead of rejecting an unrelated mismatch with the catalogue ephemeris.
    lines = data.decode().splitlines()
    fields = lines[43].split()
    values = [95968.0, *asteroid_state(81885, 95968.0)]
    fields[:7] = [format(x, ".17g") for x in values]
    lines[43] = " ".join(fields)
    return ("\n".join(lines) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="gtoc-bench:gtoc11-cpu")
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("reference-checks.json")
    )
    args = parser.parse_args()
    verify_materials("gtoc11")
    base = Path(__file__).resolve().parent
    manifest = json.loads((base / "manifest.json").read_text())
    for name, entry in manifest["files"].items():
        data = (ROOT / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Reference checksum mismatch: {name}")
    small = (ROOT / "tests/fixtures/gtoc11/single-asteroid.txt").read_bytes()
    cases = [
        (
            "complete_organizer_example",
            (base / "received/Solution.txt").read_bytes(),
            "valid",
            1556.5911,
        ),
        ("derived_single_asteroid", small, "valid", 0),
    ]
    for mode in (0, 1):
        empty = "\n".join([f"{i} 0 0" for i in range(1, 11)] + ["1.1 0 0 0", str(mode)]) + "\n"
        cases.append((f"empty_fleet_type_{mode}", empty.encode(), "valid", 0))
    for name, row, column, value in [
        ("wrong_earth_position", 2, 1, "100000000"),
        ("departure_excess_over_6", 2, 7, "100"),
        ("broken_impulsive_dynamics", 3, 7, "10"),
        ("wrong_atd_acceleration", 44, 7, "0"),
        ("wrong_arrival_mass", 1303, 10, "1000000000000000"),
        ("wrong_station_arrival_position", 1303, 1, "100000000"),
        ("wrong_station_arrival_velocity", 1303, 4, "100"),
        ("nonfinite_control", 44, 7, "NaN"),
        ("invalid_station", 43, 1, "13"),
        ("invalid_ring_radius", 41, 0, "0.5"),
        ("invalid_control_type", 42, 0, "2"),
        ("missing_trajectory_row", 43, 2, "1261"),
    ]:
        cases.append((name, change(small, row, column, value), "invalid", None))
    cases.extend(
        [
            ("activation_before_30_days", early_activation(small), "invalid", None),
            ("truncated_file", small[:-1000], "invalid", None),
            ("extra_trailing_record", small + b"unexpected\n", "invalid", None),
            ("duplicate_transfer", small + b"81885 2 1\n", "invalid", None),
        ]
    )
    records = []
    with tempfile.TemporaryDirectory(prefix="gtoc11-audit-") as temp:
        target = Path(temp) / "candidate.txt"
        for name, data, status, score in cases:
            target.write_bytes(data)
            verdict = validate_file(target, args.image, "gtoc11")
            if verdict["status"] != status or verdict["score"] != score:
                raise AssertionError(f"Unexpected verdict for {name}: {verdict}")
            records.append({"case": name, "expected_status": status, "verdict": verdict})
            print(f"{name}: {verdict['status']} {verdict['score']}", flush=True)
    image = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    report = {
        "problem": "gtoc11",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "image": args.image,
        "image_id": image,
        "validator_revision": records[0]["verdict"]["validator_revision"],
        "adapter_revision": records[0]["verdict"]["adapter_revision"],
        "valid_cases": sum(r["expected_status"] == "valid" for r in records),
        "invalid_cases": sum(r["expected_status"] == "invalid" for r in records),
        "cases": records,
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
