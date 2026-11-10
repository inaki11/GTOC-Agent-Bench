"""Bounded parsing and settlement-tree validation before any scoring."""

import math
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

import numpy as np

from .model import END, TIME_TOL

MAX_BYTES = 100_000_000
MAX_ROWS = 100_100
ORDER_EPS = 1e-12  # Floating point output roundoff, about 32 seconds.


class Rejection(Exception):
    def __init__(self, code, message, line=None, status="invalid"):
        self.code, self.message, self.line, self.status = code, message, line, status
        super().__init__(code + (f" [line {line}]" if line else "") + ": " + message)


@dataclass
class Pod:
    target: int
    time: float
    impulse: np.ndarray
    line: int


@dataclass
class Vessel:
    kind: str
    ident: int
    source: int
    target: int | None
    times: np.ndarray
    impulses: np.ndarray
    line: int
    pods: list[Pod] = field(default_factory=list)


@dataclass
class Tree:
    mothers: list[Vessel]
    fast: list[Vessel]
    settlers: list[Vessel]
    arrivals: dict[int, tuple[float, int]]
    format_notes: list[str]


def integral(value, line, label):
    if not value.is_integer():
        raise Rejection("INTEGER", f"{label} must be an exact integer", line)
    return int(value)


def star(value, line):
    ident = integral(value, line, "star ID")
    if not 1 <= ident <= 100000:
        raise Rejection(
            "STAR_ID", "Star IDs must be in [1,100000]; Sol (0) cannot be a settlement", line
        )
    return ident


def timetable(times, line):
    times = np.array(times)
    if not np.all((times >= 0) & (times <= END + TIME_TOL)):
        raise Rejection(
            "TIME_WINDOW",
            "Every event must occur in [0,90 Myr] (1 year upper-limit tolerance)",
            line,
        )
    if len(times) > 1 and np.any(np.diff(times) < 1 - TIME_TOL):
        raise Rejection(
            "MANEUVER_SPACING",
            "Consecutive impulses of a ship must be at least 1 Myr apart (1 year tolerance)",
            line,
        )
    return times


def parse(data):
    if not data or len(data) > MAX_BYTES:
        raise Rejection("FILE_SIZE", "Expected a non-empty file of at most 100,000,000 bytes")
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise Rejection("ENCODING", "Submission must be ASCII text") from exc
    if len(lines) > MAX_ROWS + 1:
        raise Rejection("RESOURCE_LIMIT", "Too many data rows", status="unverified")
    rows = []
    # Exactly the first physical line is a mandatory header, including a blank header.
    for line, text in enumerate(lines[1:], 2):
        text = text.strip()
        if not text:
            raise Rejection("BLANK_LINE", "Only the first/header line may be blank", line)
        if len(text) > 16384:
            raise Rejection("ROW_SIZE", "Numeric row is too long", line)
        if text.startswith(",") or re.search(r",\s*,", text):
            raise Rejection("COLUMNS", "Empty comma-separated numeric field", line)
        # A single trailing comma occurs in the official AerospaceCorp file.
        fields = re.split(r"[,\s]+", text.removesuffix(",").rstrip(), flags=re.ASCII)
        if not 2 <= len(fields) <= 23:
            raise Rejection("COLUMNS", "Expected between 2 and 23 numeric fields", line)
        try:
            values = [float(value) for value in fields]
        except ValueError as exc:
            raise Rejection("NUMBER", "Invalid numeric field", line) from exc
        if not all(math.isfinite(v) for v in values):
            raise Rejection("NONFINITE", "NaN and infinity are forbidden", line)
        rows.append((line, values))
    if not rows:
        raise Rejection("EMPTY_TREE", "No settlement data after the mandatory header")
    mothers, fast, settlers, notes = [], [], [], []
    note_flags = set()
    stage, index = 0, 0
    seen_mothers, closed_groups = set(), set()
    previous_source, previous_departure = None, None
    while index < len(rows):
        line, x = rows[index]
        ident = integral(x[0], line, "ship/source ID")
        second = integral(x[1], line, "pod/target ID")
        if ident in (-1, -2, -3):
            if stage or second != 0 or ident in seen_mothers:
                raise Rejection(
                    "MOTHER_STRUCTURE",
                    "Mother ships and their pods must form unique, consecutive initial blocks",
                    line,
                )
            if len(x) < 4:
                raise Rejection("COLUMNS", "Incomplete mother-ship row", line)
            pods, maneuvers = integral(x[2], line, "Nsp"), integral(x[3], line, "M")
            if not 1 <= pods <= 10:
                raise Rejection(
                    "POD_COUNT",
                    "Each listed mother must contribute 1–10 pods to actual settlements",
                    line,
                )
            if not 1 <= maneuvers <= 3 or len(x) != 4 + 4 * maneuvers:
                raise Rejection(
                    "MOTHER_MANEUVERS",
                    "Mother ship needs 1–3 impulses and exactly 4+4*M fields",
                    line,
                )
            vessel = Vessel(
                "mother",
                ident,
                0,
                None,
                timetable(x[4 : 4 + maneuvers], line),
                np.array(x[4 + maneuvers :]).reshape(maneuvers, 3),
                line,
            )
            for pod_number in range(1, pods + 1):
                index += 1
                if index >= len(rows):
                    raise Rejection("MISSING_POD", "Missing declared Settlement Pod rows", line)
                pod_line, p = rows[index]
                if len(p) != 7 or p[0] != ident or p[1] != pod_number:
                    raise Rejection(
                        "POD_STRUCTURE",
                        f"Expected mother {ident}, pod {pod_number}, with 7 fields",
                        pod_line,
                    )
                target = star(p[2], pod_line)
                time = float(timetable([p[3]], pod_line)[0])
                vessel.pods.append(Pod(target, time, np.array(p[4:]), pod_line))
            if any(b.time < a.time - ORDER_EPS for a, b in zip(vessel.pods, vessel.pods[1:])):
                notes.append(
                    f"Line {line}: pod labels do not follow time order; releases are propagated chronologically"
                )
            seen_mothers.add(ident)
            mothers.append(vessel)
        elif ident in (-11, -12):
            if stage > 1 or len(fast) >= 2 or len(x) != 10:
                raise Rejection(
                    "FAST_STRUCTURE",
                    "At most two Fast Ships, each with 10 fields, precede Settler Ships",
                    line,
                )
            stage = 1
            if ident != -11 - len(fast):
                notes.append(
                    f"Line {line}: fast label {ident} is noncanonical; each row is counted as a distinct ship (maximum two), as in the official Citadel file"
                )
            fast.append(
                Vessel(
                    "fast",
                    ident,
                    0,
                    star(x[1], line),
                    timetable(x[2:4], line),
                    np.array(x[4:]).reshape(2, 3),
                    line,
                )
            )
        elif ident > 0:
            stage = 2
            source, target = star(x[0], line), star(x[1], line)
            if len(x) < 3:
                raise Rejection("COLUMNS", "Incomplete Settler Ship row", line)
            maneuvers = integral(x[2], line, "M")
            if not 2 <= maneuvers <= 5 or len(x) != 3 + 4 * maneuvers:
                raise Rejection(
                    "SETTLER_MANEUVERS",
                    "Settler Ship needs 2–5 impulses and exactly 3+4*M fields",
                    line,
                )
            times = timetable(x[3 : 3 + maneuvers], line)
            if source != previous_source:
                if source in closed_groups and "GROUP" not in note_flags:
                    notes.append(
                        f"Line {line}: nonconsecutive source groups are resolved by the full settlement graph; the three-ship limit is checked globally"
                    )
                    note_flags.add("GROUP")
                if previous_source is not None:
                    closed_groups.add(previous_source)
                previous_source, previous_departure = source, None
            if previous_departure is not None and times[0] < previous_departure - ORDER_EPS:
                if "ORDER" not in note_flags:
                    notes.append(
                        f"Line {line}: source departures are not sorted; dependencies and departure epochs are verified independently of row order, as required by published final files"
                    )
                    note_flags.add("ORDER")
            previous_departure = times[0]
            settlers.append(
                Vessel(
                    "settler",
                    len(settlers) + 1,
                    source,
                    target,
                    times,
                    np.array(x[3 + maneuvers :]).reshape(maneuvers, 3),
                    line,
                )
            )
        else:
            raise Rejection("SHIP_ID", "Unknown vessel/source identifier", line)
        index += 1
    if not mothers and not fast:
        raise Rejection("NO_ROOT", "At least one Mother Ship or Fast Ship must depart Sol")
    arrivals = {}
    for target, time, line in [(p.target, p.time, p.line) for m in mothers for p in m.pods] + [
        (v.target, float(v.times[-1]), v.line) for v in fast + settlers
    ]:
        if target in arrivals:
            raise Rejection(
                "DUPLICATE_SETTLEMENT",
                f"Star {target} was already assigned a settlement on line {arrivals[target][1]}",
                line,
            )
        arrivals[target] = (time, line)
    departures = Counter(v.source for v in settlers)
    children = defaultdict(list)
    for vessel in settlers:
        if vessel.source not in arrivals:
            raise Rejection(
                "UNSETTLED_SOURCE", f"No vessel settles departure star {vessel.source}", vessel.line
            )
        if departures[vessel.source] > 3:
            raise Rejection(
                "BRANCH_LIMIT",
                f"More than three Settler Ships leave star {vessel.source}",
                vessel.line,
            )
        if vessel.times[0] < arrivals[vessel.source][0] + 2 - TIME_TOL:
            raise Rejection(
                "COLONY_WAIT",
                f"Star {vessel.source} must be settled for at least 2 Myr before launching a Settler Ship",
                vessel.line,
            )
        children[vessel.source].append(vessel)
    queue = deque([p.target for m in mothers for p in m.pods] + [v.target for v in fast])
    ordered = []
    while queue:
        source = queue.popleft()
        for vessel in children[source]:
            ordered.append(vessel)
            queue.append(vessel.target)
    if len(ordered) != len(settlers):
        raise Rejection(
            "UNREACHABLE_TREE", "Settlement graph has an unreachable component or a cycle"
        )
    return Tree(mothers, fast, ordered, arrivals, notes)
