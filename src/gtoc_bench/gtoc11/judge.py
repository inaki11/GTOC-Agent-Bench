"""Execute the official Linux checker unchanged; never infer success from exit 0."""

import hashlib
import math
import platform
import re
import resource
import subprocess
import tempfile
import time
from pathlib import Path

from .. import ROOT
from . import REVISION
from .format import Rejection, parse

PROBLEM = ROOT / "problems/gtoc11"
TIMEOUT = 600
OUTPUT_BYTES = 1024 * 1024
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def _printed_matches(text, expected, significant_digits):
    """Account only for the original C++ stream's decimal output precision."""
    value = float(text)
    if not math.isfinite(value) or value < 0 or not math.isfinite(expected) or expected < 0:
        return False
    if expected == 0:
        return value == 0
    tolerance = 0.501 * 10 ** (math.floor(math.log10(expected)) - significant_digits + 1)
    return abs(value - expected) <= tolerance + 8 * math.ulp(expected)


def parse_output(returncode, stdout, stderr, report, summary):
    failure = {"status": "validator_error", "score": None, "diagnostics": []}
    if returncode < 0:
        return failure | {"diagnostics": [f"Official checker terminated by signal {-returncode}"]}
    statuses = re.findall(r"^Status:\s*(-?\d+)\s*$", stdout, re.M)
    scores = re.findall(rf"^Score:\s*({NUMBER})\s*$", stdout, re.M)
    if len(statuses) != 1 or len(scores) != 1:
        return failure | {"diagnostics": ["Missing or ambiguous official Status / Score markers"]}
    status = int(statuses[0])
    if status == -1:
        diagnostics = list(
            dict.fromkeys(
                line.strip()
                for line in (report + "\n" + stdout + "\n" + stderr).splitlines()
                if re.match(r"(?:Line \d+:|Asteroid \d+ |RV cannot match!)", line.strip())
            )
        )
        return {
            "status": "invalid",
            "score": None,
            "native_status": status,
            "diagnostics": diagnostics or ["Official checker rejected the solution (Status:-1)"],
        }
    if status != 0 or returncode != 0:
        return failure | {"diagnostics": [f"Unexpected checker exit/status: {returncode}/{status}"]}
    extra = re.findall(rf"^Extra Info:\s*({NUMBER})\s+(\d+)\s*$", stdout, re.M)
    lines = report.splitlines()
    if len(extra) != 1 or len(lines) != 3 or lines[-1].strip() != "0":
        return failure | {"diagnostics": ["Missing or malformed official Result.txt / Extra Info"]}
    counts = re.fullmatch(rf"\s*({NUMBER}),\s*(\d+)\s*", lines[0])
    if not counts or not re.fullmatch(rf"\s*{NUMBER}\s*", lines[1]):
        return failure | {"diagnostics": ["Malformed mass, asteroid count or score in Result.txt"]}
    native_score = float(scores[0])
    if float(lines[1]) != native_score or not _printed_matches(scores[0], summary.score, 8):
        return failure | {
            "diagnostics": [
                "Official score disagrees with Result.txt or independently computed B=1 merit"
            ]
        }
    if (
        int(extra[0][1]) != summary.transferred_asteroids
        or int(counts[2]) != summary.transferred_asteroids
        or not _printed_matches(extra[0][0], min(summary.station_mass_kg), 8)
        or not _printed_matches(counts[1], min(summary.station_mass_kg), 6)
    ):
        return failure | {
            "diagnostics": [
                "Official mass or asteroid count disagrees with the complete submission"
            ]
        }
    if stderr.strip():
        return failure | {
            "diagnostics": ["Unexpected diagnostic output on official checker stderr"]
        }
    return {
        "status": "valid",
        "score": native_score,
        "diagnostics": [],
        "native_status": 0,
        "score_breakdown": {
            "time_bonus": 1.0,
            "minimum_station_mass_kg": min(summary.station_mass_kg),
            "station_mass_kg": list(summary.station_mass_kg),
            "dyson_radius_au": summary.radius_au,
            "mother_ship_delta_v_km_s": list(summary.delta_v_km_s),
            "delta_v_penalty": math.fsum((1 + x / 50) ** 2 for x in summary.delta_v_km_s),
            "independent_score_b1": summary.score,
        },
        "metrics": {
            "transferred_asteroids": summary.transferred_asteroids,
            "flybys": summary.flybys,
            "continuous_thrust_type": summary.continuous_thrust_type,
            "rows": summary.rows,
        },
    }


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT + 1))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_BYTES, OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def evaluate(data, temp_root=None):
    start = time.monotonic()
    executable = PROBLEM / "validator/GTOC11Check_Linux"
    common = {
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "score_unit": "points",
        "problem_revision": REVISION,
        "validator_kind": "official_binary_with_input_output_guards",
        "validator_revision": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "adapter_revision": hashlib.sha256(
            b"".join(path.read_bytes() for path in sorted(Path(__file__).parent.glob("*.py")))
        ).hexdigest(),
    }

    def finish(result):
        return common | result | {"validation_seconds": time.monotonic() - start}

    try:
        summary = parse(data)
    except Rejection as exc:
        return finish(
            {
                "status": "invalid",
                "score": None,
                "diagnostics": [str(exc)],
                "errors": [{"code": exc.code, "line": exc.line, "message": exc.message}],
                "stdout": "",
                "stderr": "",
            }
        )
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        return finish(
            {
                "status": "validator_error",
                "score": None,
                "diagnostics": [
                    "The official GTOC11 executable requires Linux amd64; use gtoc validate --problem gtoc11 --image gtoc-bench:gtoc11-cpu FILE"
                ],
            }
        )
    # Check pins even when this module is called directly rather than via dispatch.
    from ..validator import verify_materials

    verify_materials("gtoc11")
    with tempfile.TemporaryDirectory(dir=temp_root, prefix="gtoc11-judge-") as name:
        directory = Path(name)
        (directory / "Solution.txt").write_bytes(data)
        (directory / "Candidate Asteroids.txt").symlink_to(PROBLEM / "data/Candidate Asteroids.txt")
        try:
            with (
                (directory / "stdout").open("w+b") as out,
                (directory / "stderr").open("w+b") as err,
            ):
                process = subprocess.run(
                    [str(executable)],
                    cwd=directory,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    timeout=TIMEOUT,
                    preexec_fn=_limits,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                )
                out.seek(0)
                err.seek(0)
                stdout = out.read(OUTPUT_BYTES + 1).decode(errors="replace")
                stderr = err.read(OUTPUT_BYTES + 1).decode(errors="replace")
            report_path = directory / "Result.txt"
            report = report_path.read_text(errors="replace") if report_path.exists() else ""
            result = parse_output(process.returncode, stdout, stderr, report, summary)
            result.update(
                stdout=stdout, stderr=stderr, native_report=report, returncode=process.returncode
            )
        except subprocess.TimeoutExpired:
            result = {
                "status": "validator_error",
                "score": None,
                "diagnostics": ["Official GTOC11 checker timeout"],
                "timeout_phase": "native_validator",
                "timeout_seconds": TIMEOUT,
            }
        except OSError as exc:
            result = {
                "status": "validator_error",
                "score": None,
                "diagnostics": [f"Official GTOC11 checker could not run: {exc.strerror}"],
            }
    return finish(result)
