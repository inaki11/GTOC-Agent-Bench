"""Dispatch a trusted problem judge; preserve the unchanged GTOC12 binary."""

import hashlib
import json
import math
import os
import re
import resource
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import ROOT
from .problems import specification

PROBLEM = ROOT / "problems/gtoc12"
MAX_BYTES = 16 * 1024 * 1024
TIMEOUT = 120
BRIDGE_TIMEOUT = TIMEOUT + 15
TRANSPORT_TIMEOUT = BRIDGE_TIMEOUT + 15
CLIENT_TIMEOUT = TRANSPORT_TIMEOUT + 30


def verify_materials(problem="gtoc12"):
    specification(problem)
    directory = ROOT / "problems" / problem
    provenance = json.loads((directory / "provenance.json").read_text())
    for relative, entry in provenance["files"].items():
        if hashlib.sha256((directory / relative).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Problem material checksum mismatch: {relative}")
    for relative, entry in provenance.get("code_files", {}).items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Validator code checksum mismatch: {relative}")
    return provenance


def check_input(data):
    if not data or len(data) > MAX_BYTES:
        return "Solution is empty or exceeds 16 MiB"
    try:
        lines = data.decode("ascii").splitlines()
        count = 0
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            cols = line.split()
            if (
                len(cols) < 2
                or not re.fullmatch(r"[0-9]+", cols[0])
                or not re.fullmatch(r"-?[0-9]+", cols[1])
            ):
                return f"Malformed ship/event IDs on line {number}"
            ship, event = int(cols[0]), int(cols[1])
            if not 1 <= ship <= 100 or not -4 <= event <= 60000:
                return f"Ship/event ID out of range on line {number}"
            if len(cols) != (6 if event == -1 else 10):
                return f"Wrong number of columns on line {number}"
            if any(not math.isfinite(float(x)) for x in cols[2:]):
                return f"Non-finite number on line {number}"
            count += 1
        if not count:
            return "Solution contains no events"
    except (UnicodeError, ValueError, OverflowError):
        return "Solution must contain finite ASCII numbers"
    return None


def parse_output(returncode, stdout, stderr):
    result = {"status": "validator_error", "score": None, "diagnostics": []}
    errors = re.findall(r"Error[0-9A-Z]+[^\n]*", stdout + "\n" + stderr)
    if returncode < 0:
        result["diagnostics"] = [f"Validator terminated by signal {-returncode}"]
    elif errors:
        result.update(status="invalid", diagnostics=errors)
    elif returncode != 0:
        result["diagnostics"] = [f"Unexpected validator exit code {returncode}"]
    else:
        matches = re.findall(r"The total resource mass is\s+([\d.eE+-]+)\s+kg\.", stdout)
        if "Check successfully!" in stdout and len(matches) == 1:
            score = float(matches[0])
            if math.isfinite(score) and score >= 0:
                result.update(status="valid", score=score)
                return result
        result["diagnostics"] = ["Missing or ambiguous success marker / resource mass"]
    return result


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT + 1))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024**2, 4 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def evaluate(data, temp_root=None, problem="gtoc12"):
    specification(problem)
    if problem == "gtoc11":
        from .gtoc11 import evaluate as evaluate_gtoc11

        verify_materials(problem)
        return evaluate_gtoc11(data, temp_root)
    if problem == "gtocx":
        from .gtocx import evaluate as evaluate_gtocx

        verify_materials(problem)
        return evaluate_gtocx(data)
    if problem == "gtoc13":
        from .gtoc13 import evaluate as evaluate_gtoc13

        verify_materials(problem)
        return evaluate_gtoc13(data)
    start = time.monotonic()
    common = {
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "score_unit": "kg",
        "problem_revision": "gtoc12-static-v1",
    }
    error = check_input(data)
    if error:
        return common | {
            "status": "invalid",
            "score": None,
            "diagnostics": [error],
            "stdout": "",
            "stderr": "",
            "validation_seconds": time.monotonic() - start,
        }
    with tempfile.TemporaryDirectory(dir=temp_root, prefix="gtoc-judge-") as directory:
        directory = Path(directory)
        (directory / "Result.txt").write_bytes(data)
        shutil.copyfile(PROBLEM / "validator/ScoreData.txt", directory / "ScoreData.txt")
        (directory / "GTOC12_Asteroids_Data.txt").symlink_to(
            PROBLEM / "data/GTOC12_Asteroids_Data.txt"
        )
        command = [str(PROBLEM / "validator/GTOC12_Verify")]
        try:
            # Files, rather than PIPE, bound native output even for hostile input.
            with (
                (directory / "stdout").open("w+b") as out,
                (directory / "stderr").open("w+b") as err,
            ):
                process = subprocess.run(
                    command,
                    cwd=directory,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    timeout=TIMEOUT,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                    preexec_fn=_limits,
                )
                out.seek(0)
                err.seek(0)
                stdout, stderr = (
                    out.read().decode(errors="replace"),
                    err.read().decode(errors="replace"),
                )
            result = parse_output(process.returncode, stdout, stderr)
            result.update(stdout=stdout, stderr=stderr, returncode=process.returncode)
        except subprocess.TimeoutExpired:
            result = {
                "status": "validator_error",
                "score": None,
                "diagnostics": ["Validator timeout"],
                "timeout_phase": "native_validator",
                "timeout_seconds": TIMEOUT,
            }
        except OSError as exc:
            result = {
                "status": "validator_error",
                "score": None,
                "diagnostics": [f"Validator could not run: {exc.strerror}"],
            }
    return (
        common
        | result
        | {
            "validation_seconds": time.monotonic() - start,
            "validator_revision": hashlib.sha256(
                (PROBLEM / "validator/GTOC12_Verify").read_bytes()
            ).hexdigest(),
        }
    )


if __name__ == "__main__":
    import sys

    problem = sys.argv[1] if len(sys.argv) > 1 else "gtoc12"
    print(
        json.dumps(
            evaluate(
                sys.stdin.buffer.read(specification(problem)["max_bytes"] + 1),
                os.environ.get("GTOC_JUDGE_TMP"),
                problem,
            ),
            allow_nan=False,
        )
    )
