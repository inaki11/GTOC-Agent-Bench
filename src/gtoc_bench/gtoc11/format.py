"""Check the complete numeric envelope before handing it to the C++ executable.

This is not a replacement trajectory validator. Only the organizer's executable
can award points. In particular, mode 1 intermediate states and the mass column
are left to the original checker (the supplied example repeats arrival mass).
"""

import io
import math
import re
from dataclasses import dataclass

MAX_BYTES = 100_000_000
MAX_ROWS = 1_000_000
MAX_LINE = 4096
ASTEROIDS = 83453
INTEGER = re.compile(rb"[+-]?[0-9]+\Z")
NUMBER = re.compile(rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


class Rejection(ValueError):
    def __init__(self, code, message, line=None):
        self.code, self.line, self.message = code, line, message
        super().__init__(f"{code}{f' at line {line}' if line else ''}: {message}")


@dataclass(frozen=True)
class Summary:
    radius_au: float
    continuous_thrust_type: int
    delta_v_km_s: tuple
    station_mass_kg: tuple
    transferred_asteroids: int
    flybys: int
    rows: int

    @property
    def score(self):
        return (1e-10 * min(self.station_mass_kg)) / (
            self.radius_au**2 * math.fsum((1 + dv / 50) ** 2 for dv in self.delta_v_km_s)
        )


class Reader:
    def __init__(self, data):
        if not data or len(data) > MAX_BYTES:
            raise Rejection("FILE_SIZE", f"Expected 1..{MAX_BYTES} bytes")
        self.stream, self.line = io.BytesIO(data), 0

    def row(self, columns=None, optional=False):
        raw = self.stream.readline(MAX_LINE + 2)
        if not raw:
            if optional:
                return None
            raise Rejection("TRUNCATED", "A declared record is missing", self.line + 1)
        self.line += 1
        if self.line > MAX_ROWS:
            raise Rejection("ROW_LIMIT", f"At most {MAX_ROWS} rows are supported", self.line)
        if len(raw) > MAX_LINE:
            raise Rejection("LINE_LENGTH", f"A row exceeds {MAX_LINE} bytes", self.line)
        if any(x not in (9, 10, 13, 32) and not 33 <= x <= 126 for x in raw):
            raise Rejection("ENCODING", "Use ASCII numeric rows, spaces and tabs", self.line)
        fields = raw.split()
        if not fields:
            # Empty lines are accepted only at EOF, never inside declared blocks.
            if optional and not self.stream.read().strip():
                return None
            raise Rejection("BLANK_LINE", "Unexpected blank data row", self.line)
        if columns is not None and len(fields) != columns:
            raise Rejection("COLUMNS", f"Expected {columns} columns, got {len(fields)}", self.line)
        return fields

    def integer(self, token, low, high, label):
        if not INTEGER.fullmatch(token) or len(token) > 20:
            raise Rejection("INTEGER", f"{label} must be a decimal integer", self.line)
        value = int(token)
        if not low <= value <= high:
            raise Rejection("RANGE", f"{label} must be in [{low}, {high}]", self.line)
        return value

    def numbers(self, fields):
        result = []
        for field in fields:
            if not NUMBER.fullmatch(field):
                raise Rejection("NUMBER", "Expected finite ASCII numbers", self.line)
            value = float(field)
            if not math.isfinite(value):
                raise Rejection(
                    "NONFINITE", "NaN, infinity and overflowing numbers are forbidden", self.line
                )
            if abs(value) > 1e30:
                raise Rejection(
                    "NUMERIC_RANGE",
                    "Numeric magnitude exceeds the checker safety bound (1e30)",
                    self.line,
                )
            result.append(value)
        return result


def parse(data):
    reader = Reader(data)
    delta_v, flybys = [], set()
    for ship in range(1, 11):
        header = reader.row(3)
        identifier = reader.integer(header[0], 1, 10, "Mother ship ID")
        if identifier != ship:
            raise Rejection("SHIP_ORDER", f"Expected mother ship header {ship}", reader.line)
        impulses = reader.integer(header[1], 0, 4 * (ASTEROIDS + 1), "Impulse count")
        encounters = reader.integer(header[2], 0, ASTEROIDS, "Flyby count")
        if impulses + encounters > MAX_ROWS - reader.line:
            raise Rejection("ROW_LIMIT", "Declared ship block exceeds the row budget", reader.line)
        costs, actual_flybys, actual_impulses = [], 0, 0
        previous = -math.inf
        for index in range(impulses + encounters):
            fields = reader.row(11)
            values = reader.numbers(fields[:10])
            target = reader.integer(fields[10], -1, ASTEROIDS, "Target ID")
            if (index == 0 and target != -1) or (index > 0 and target == -1):
                raise Rejection(
                    "DEPARTURE",
                    "The first ship event must be its only Earth departure (-1)",
                    reader.line,
                )
            if values[0] < previous:
                raise Rejection("CHRONOLOGY", "Mother ship epochs must not decrease", reader.line)
            previous = values[0]
            if target <= 0:
                actual_impulses += 1
                if target == 0:
                    costs.append(math.hypot(*values[7:10]))
            else:
                actual_flybys += 1
                if target in flybys:
                    raise Rejection(
                        "DUPLICATE_FLYBY", f"Asteroid {target} was already visited", reader.line
                    )
                flybys.add(target)
                if any(values[7:10]):
                    raise Rejection(
                        "FLYBY_IMPULSE", "Flyby rows must have zero dvx, dvy and dvz", reader.line
                    )
        if (actual_impulses, actual_flybys) != (impulses, encounters):
            raise Rejection(
                "COUNTS", "Declared impulse/flyby counts disagree with the events", reader.line
            )
        delta_v.append(math.fsum(costs))

    ring = reader.numbers(reader.row(4))
    if ring[0] < 0.65:
        raise Rejection(
            "DYSON_RADIUS", "The Dyson ring radius must be at least 0.65 AU", reader.line
        )
    mode = reader.integer(reader.row(1)[0], 0, 1, "Continuous-thrust type")
    masses, transferred = [[] for _ in range(12)], set()
    while (header := reader.row(optional=True)) is not None:
        if len(header) != 3:
            raise Rejection(
                "COLUMNS", "Expected asteroid ID, station ID and row count", reader.line
            )
        asteroid = reader.integer(header[0], 1, ASTEROIDS, "Asteroid ID")
        station = reader.integer(header[1], 1, 12, "Station ID")
        count = reader.integer(header[2], 1, MAX_ROWS - reader.line, "Trajectory row count")
        if asteroid not in flybys:
            raise Rejection(
                "UNVISITED_ASTEROID", f"Asteroid {asteroid} has no mother ship flyby", reader.line
            )
        if asteroid in transferred:
            raise Rejection(
                "DUPLICATE_TRANSFER",
                f"Asteroid {asteroid} is transferred more than once",
                reader.line,
            )
        transferred.add(asteroid)
        previous = -math.inf
        for _ in range(count):
            values = reader.numbers(reader.row(11))
            if values[0] <= previous:
                raise Rejection("CHRONOLOGY", "Asteroid epochs must increase strictly", reader.line)
            previous = values[0]
        masses[station - 1].append(values[10])
    return Summary(
        ring[0],
        mode,
        tuple(delta_v),
        tuple(map(math.fsum, masses)),
        len(transferred),
        len(flybys),
        reader.line,
    )
