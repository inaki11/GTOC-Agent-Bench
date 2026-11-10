"""Strict parsing: no scoring is possible before all rows and arcs are checked."""

import math
import re
from dataclasses import dataclass

import numpy as np

from .model import END

MAX_BYTES = 100_000_000
MAX_ROWS = 500_000


class Rejection(Exception):
    def __init__(self, code, message, line=None, status="invalid"):
        self.code, self.message, self.line, self.status = code, message, line, status
        super().__init__(f"{code}" + (f" [line {line}]" if line else "") + f": {message}")


@dataclass
class Row:
    line: int
    body: int
    flag: int
    t: float
    state: np.ndarray
    control: np.ndarray


@dataclass
class Arc:
    kind: str
    rows: list[Row]


def same(a, b, velocity=True):
    """Allow only bounded floating-point rounding at duplicated state rows."""

    def matches(x, y, floor, cap):
        allowance = np.minimum(cap, floor + 16 * np.spacing(np.maximum(np.abs(x), np.abs(y))))
        return np.all(np.abs(x - y) <= allowance)

    if a.t != b.t or not matches(a.state[:3], b.state[:3], 1e-7, 1e-3):
        raise Rejection(
            "CONTINUITY", f"Epoch/position differs from line {a.line} at arc boundary", b.line
        )
    if velocity and not matches(a.state[3:], b.state[3:], 1e-12, 1e-10):
        raise Rejection(
            "CONTINUITY", f"Velocity differs from line {a.line} without a planetary flyby", b.line
        )


def parse(data, bodies):
    if not data or len(data) > MAX_BYTES:
        raise Rejection("FILE_SIZE", "Expected a non-empty file of at most 100,000,000 bytes")
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise Rejection("ENCODING", "Submission must be ASCII text") from exc
    rows = []
    for number, text in enumerate(lines, 1):
        text = text.strip()
        if not text or text.startswith(("#", "!")):
            continue
        # Empty comma-separated cells are errors, not silently discarded fields.
        if any(not part.strip() for part in text.split(",")):
            raise Rejection("COLUMNS", "Empty field between commas", number)
        fields = re.split(r"[,\s]+", text, flags=re.ASCII)
        if len(fields) != 12:
            raise Rejection("COLUMNS", f"Expected 12 numeric fields; found {len(fields)}", number)
        try:
            values = [float(x) for x in fields]
        except ValueError as exc:
            raise Rejection("NUMBER", "Invalid numeric field", number) from exc
        if not all(math.isfinite(x) for x in values):
            raise Rejection("NONFINITE", "NaN and infinity are forbidden", number)
        if not values[0].is_integer() or not values[1].is_integer():
            raise Rejection("IDENTIFIER", "body_id and flag must be exact integers", number)
        body, flag = int(values[0]), int(values[1])
        if body != 0 and body not in bodies:
            raise Rejection("BODY_ID", f"Unknown body {body}", number)
        if flag not in (0, 1):
            raise Rejection("FLAG", "flag must equal 0 or 1", number)
        if not 0 <= values[2] <= END:
            raise Rejection("TIME_WINDOW", "Every epoch must be within [0, 200 years]", number)
        if rows and values[2] < rows[-1].t:
            raise Rejection("TIME_ORDER", "Epochs must be non-decreasing", number)
        row = Row(number, body, flag, values[2], np.array(values[3:9]), np.array(values[9:12]))
        if body == 0 and flag == 0 and np.any(row.control != 0):
            raise Rejection("CONIC_CONTROL", "Conic control must be exactly [0, 0, 0]", number)
        if body == 0 and flag == 1:
            norm = np.linalg.norm(row.control)
            if not math.isfinite(norm) or abs(norm - 1) > 1e-10:
                raise Rejection(
                    "SAIL_NORMAL", f"Sail control must be a unit normal (norm={norm:.12g})", number
                )
            if np.dot(row.control, row.state[:3]) > 0:
                raise Rejection(
                    "SAIL_CONE",
                    "Sail normal points away from Altaira (cone angle >90 degrees)",
                    number,
                )
        if np.linalg.norm(row.state[:3]) < 1:
            raise Rejection("RADIUS", "Position is at or near the stellar centre", number)
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise Rejection(
                "RESOURCE_LIMIT",
                "More than 500,000 data rows; reduce file size",
                number,
                "unverified",
            )
    if not rows or rows[0].body:
        raise Rejection("INITIAL_ARC", "Trajectory must begin with a heliocentric arc")
    arcs, i = [], 0
    while i < len(rows):
        first = rows[i]
        if first.body:
            kind, size = "flyby", 2
            if i == len(rows) - 1:
                size = 1
            else:
                other = rows[i + 1]
                if other.body != first.body or other.flag != first.flag:
                    raise Rejection(
                        "FLYBY_STRUCTURE",
                        "Flyby must have two rows with identical body and flag",
                        first.line,
                    )
                same(first, other, velocity=False)
        elif first.flag == 0:
            kind, size = "conic", 2
            if i + 1 == len(rows) or rows[i + 1].body or rows[i + 1].flag:
                raise Rejection(
                    "CONIC_STRUCTURE",
                    "A conic needs exactly two heliocentric flag=0 rows",
                    first.line,
                )
        else:
            kind, size = "sail", 1
            while i + size < len(rows) and rows[i + size].body == 0 and rows[i + size].flag == 1:
                size += 1
            if size < 2:
                raise Rejection(
                    "SAIL_STRUCTURE", "A propagated arc needs at least two rows", first.line
                )
        selected = rows[i : i + size]
        if kind != "flyby":
            if selected[-1].t <= first.t:
                raise Rejection(
                    "ARC_DURATION", "Heliocentric arcs must have positive duration", first.line
                )
            if kind == "sail":
                for a, b in zip(selected, selected[1:]):
                    if b.t == a.t:
                        same(a, b)
                    elif b.t - a.t < 60:
                        raise Rejection(
                            "SAIL_TIMESTEP",
                            "Positive propagated steps must be at least 60 seconds",
                            b.line,
                        )
        if arcs:
            previous = arcs[-1]
            if previous.kind == kind == "flyby":
                raise Rejection(
                    "ARC_TRANSITION", "Flybys must be separated by a heliocentric arc", first.line
                )
            same(previous.rows[-1], first)
        arcs.append(Arc(kind, selected))
        i += size
    return arcs
