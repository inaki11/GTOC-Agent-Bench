"""Rebuild host-only leaderboard references from captured official sources.

This tool makes no network requests and does not run or modify any problem judge.
Run `python references/leaderboards/rebuild.py --check` to verify the artifacts.
"""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent
UTC = UTC
MONTHS = {
    name: i
    for i, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}


def number(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError(f"Expected a finite non-negative score: {value!r}")
    return result


def integer(value):
    result = number(value)
    if result != result.to_integral_value():
        raise ValueError(f"Expected integer: {value!r}")
    return int(result)


def iso(epoch):
    return epoch.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def epoch(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("UTC offset required")
    return result.astimezone(UTC)


def published_epoch(value):
    """Parse JPL's English, minute-resolution timestamps without locale dependence."""
    match = re.fullmatch(
        r"([A-Za-z]{3})\.?\s+(\d{1,2}),\s+(\d{4}),\s+"
        r"(\d{1,2})(?::(\d{2}))?\s+([ap])\.?m\.?",
        value,
    )
    if not match:
        raise ValueError(f"Unrecognized JPL timestamp: {value!r}")
    month, day, year, hour, minute, ampm = match.groups()
    if not 1 <= int(hour) <= 12:
        raise ValueError(f"Invalid 12-hour timestamp: {value!r}")
    hour24 = int(hour) % 12 + (12 if ampm == "p" else 0)
    return datetime(int(year), MONTHS[month], int(day), hour24, int(minute or 0), tzinfo=UTC)


def time_bonus(submitted, start):
    days = Decimal(str((submitted - start).total_seconds())) / Decimal(86400)
    if days < 0:
        raise ValueError("Submission precedes competition start")
    return Decimal("1.13") if days <= 7 else Decimal("1.165") - Decimal("0.005") * days


def rounded(value, digits=8):
    return float(value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def table(path, columns):
    rows = []
    for line in path.read_text().splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if not any(cells) or all(not c or set(c) <= {"-", ":"} for c in cells):
            continue
        if len(cells) != columns:
            raise ValueError(f"Unexpected number of columns in {path.name}: {line!r}")
        rows.append(cells)
    if len(rows) < 2:
        raise ValueError(f"Missing leaderboard in {path}")
    return rows[0], rows[1:]


def team_key(name):
    return name.replace("’", "'").strip()


def gtoc12(base, sources):
    api = json.loads((base / "gtoc12/source-tsinghua-api.json").read_text())
    if api["code"] != 200:
        raise ValueError("Official GTOC12 API did not return success")
    _, mirror = table(base / "gtoc12/source-esa-table.txt", 3)
    entries = []
    for rank, raw in enumerate(api["data"], 1):
        ships, asteroids = (integer(s.strip()) for s in raw["mMin"].split(","))
        entries.append(
            {
                "official_rank": rank,
                "team": raw["teamName"],
                "ships": ships,
                "asteroids": asteroids,
                "official_score": float(number(raw["score"])),
                "published_score_text": raw["score"],
                "best_submission_utc": iso(epoch(raw["bestSubmitTime"])),
                "last_submission_utc": iso(epoch(raw["lastSubmitTime"])),
                "submissions": integer(raw["submitTimes"]),
                "benchmark_score_without_activity_coefficients": None,
            }
        )
    actual = [
        (team_key(r["team"]), r["ships"], r["asteroids"], number(r["official_score"]))
        for r in entries
    ]
    expected = [
        (team_key(name), *[integer(x.strip()) for x in counts.split(",")], number(score))
        for name, counts, score in mirror
    ]
    if actual != expected:
        raise ValueError("Official GTOC12 API and ESA final table disagree")
    return {
        "schema_version": 1,
        "problem": "gtoc12",
        "kind": "official_final",
        "captured_at_utc": sources["captured_at_utc"],
        "source_url": sources["sources"]["gtoc12/source-tsinghua-api.json"]["url"],
        "cross_check_url": sources["sources"]["gtoc12/source-esa-table.txt"]["url"],
        "score_unit": "kg",
        "score_definition": "Historical asteroid-activity-weighted returned mass",
        "benchmark_score_definition": "Returned mass with every activity coefficient fixed to 1",
        "benchmark_comparable": False,
        "normalization_note": "Per-asteroid competitive coefficients cannot be inverted from an aggregate score. Raw returned mass is not published in these tables. No corrected score is invented.",
        "entries": entries,
    }


def gtoc11_bonus(submitted, start, end):
    if submitted < start or end <= start:
        raise ValueError("Invalid GTOC11 submission window")
    # One historical row is displayed four minutes after closure. Preserve its
    # timestamp, but do not extrapolate an early-submission bonus below one.
    remaining = Decimal(str(max(0, (end - submitted).total_seconds())))
    duration = Decimal(str((end - start).total_seconds()))
    return Decimal(2) - (Decimal(1) - (remaining / duration) ** 3).sqrt()


def gtoc11(base, sources):
    _, rows = table(base / "gtoc11/source-esa-table.txt", 4)
    if len(rows) != 25:
        raise ValueError("Expected the complete 25-team GTOC11 final table")
    rules = sources["gtoc11_normalization"]
    start, end = epoch(rules["submission_open_utc"]), epoch(rules["competition_end_utc"])
    entries, corrected = [], []
    for rank, (name, date, quantities, score_text) in enumerate(rows, 1):
        match = re.fullmatch(
            r"([A-Z][a-z]{2}) (\d{2}), (\d{4}) (\d{1,2}):(\d{2}) (AM|PM) UTC", date
        )
        if not match:
            raise ValueError(f"Unrecognized GTOC11 UTC timestamp: {date}")
        month, day, year, hour, minute, ampm = match.groups()
        submitted = datetime(
            int(year),
            MONTHS[month],
            int(day),
            int(hour) % 12 + (12 if ampm == "PM" else 0),
            int(minute),
            tzinfo=UTC,
        )
        mass, count = (s.strip() for s in quantities.split(","))
        score = number(score_text)
        entry = {
            "official_rank": rank,
            "team": name,
            "best_submission_utc": iso(submitted),
            "best_submission_as_published": date,
            "minimum_station_mass_kg_as_published": mass,
            "minimum_station_mass_kg": float(number(mass)),
            "transferred_asteroids": integer(count),
            "official_score": float(score),
            "published_score_text": score_text,
        }
        entries.append(entry)
        bonus = gtoc11_bonus(submitted, start, end)
        later_bonus = gtoc11_bonus(submitted + timedelta(seconds=60), start, end)
        corrected.append(
            entry
            | {
                "score_b1_estimate": rounded(score / bonus),
                "time_bonus_estimate": rounded(bonus, 12),
                "normalization_method": "official_score_divided_by_bonus_at_published_best_submission",
                "score_b1_display_uncertainty_interval": [
                    rounded(max(Decimal(0), score - Decimal("0.0005")) / bonus),
                    rounded((score + Decimal("0.0005")) / later_bonus),
                ],
                "displayed_after_competition_close": submitted > end,
            }
        )
    corrected.sort(key=lambda r: -r["score_b1_estimate"])
    for rank, row in enumerate(corrected, 1):
        row["approximate_rank"] = rank
    common = {
        "schema_version": 1,
        "problem": "gtoc11",
        "score_unit": "points",
        "captured_at_utc": sources["sources"]["gtoc11/source-esa-table.txt"]["captured_at_utc"],
        "source_url": sources["sources"]["gtoc11/source-esa-table.txt"]["url"],
    }
    return (
        common
        | {
            "kind": "official_final",
            "benchmark_comparable": False,
            "score_definition": "Historical GTOC11 score including submission-time bonus",
            "entries": entries,
        },
        common
        | {
            "kind": "derived_without_time_bonus_approximate",
            "time_bonus": 1,
            "normalization": rules,
            "precision_note": "Displayed scores have three decimals and timestamps have minute resolution. These are the same retained final entries, not each team's best among all attempts at B=1. No exact team submissions are asserted. The Iowa State timestamp is four minutes beyond closure; it is retained and B is clamped to 1 for that approximate row.",
            "entries": corrected,
        },
    )


def gtoc13(base, sources):
    _, rows = table(base / "gtoc13/source-jpl-table.txt", 6)
    rules = sources["gtoc13_normalization"]
    start, end = epoch(rules["competition_start_utc"]), epoch(rules["competition_end_utc"])
    entries, corrected = [], []
    for rank, (name, submissions, last, best, metrics, score_text) in enumerate(rows, 1):
        count, vx, tof = [v.strip() for v in metrics.split(",")]
        submitted, last_submitted = published_epoch(best), published_epoch(last)
        if not start <= submitted <= last_submitted <= end:
            raise ValueError(f"Inconsistent final submission dates for {name}")
        entry = {
            "official_rank": rank,
            "team": name,
            "submissions": integer(submissions),
            "best_submission_utc": iso(submitted),
            "last_submission_utc": iso(last_submitted),
            "best_submission_as_published": best,
            "last_submission_as_published": last,
            "science_flybys": integer(count),
            "initial_vx_km_s": float(number(vx)),
            "time_of_flight_years": float(number(tof)),
            "official_score": float(number(score_text)),
            "published_score_text": score_text,
        }
        entries.append(entry)
        c = time_bonus(submitted, start)
        if c <= 0:
            raise ValueError("Invalid calendar coefficient")
        later_c = time_bonus(min(submitted + timedelta(seconds=60), end), start)
        score = number(score_text)
        # JPL's display omits seconds. The actual epoch can fall anywhere in that
        # displayed minute; the published score is also rounded to three decimals.
        lower = max(Decimal(0), score - Decimal("0.0005")) / c
        upper = (score + Decimal("0.0005")) / later_c
        if score == 0 and entry["science_flybys"] == 0:
            lower = upper = Decimal(0)
        corrected.append(
            entry
            | {
                "elapsed_days_estimate": rounded(
                    Decimal(str((submitted - start).total_seconds())) / Decimal(86400), 12
                ),
                "time_bonus_estimate": rounded(c, 12),
                "score_c1_estimate": rounded(score / c),
                "score_c1_display_uncertainty_interval": [rounded(lower), rounded(upper)],
            }
        )
    corrected.sort(
        key=lambda r: (
            -r["score_c1_estimate"],
            -r["science_flybys"],
            -r["initial_vx_km_s"],
            r["official_rank"],
        )
    )
    for rank, entry in enumerate(corrected, 1):
        entry["approximate_rank"] = rank
    common = {
        "schema_version": 1,
        "problem": "gtoc13",
        "score_unit": "points",
        "captured_at_utc": sources["captured_at_utc"],
        "source_url": sources["sources"]["gtoc13/source-jpl-table.txt"]["url"],
        "timestamp_resolution_seconds": 60,
        "timestamp_timezone": "UTC",
    }
    official = common | {
        "kind": "official_final",
        "score_definition": "Official J = b*c*sum(w*S*F)",
        "benchmark_comparable": False,
        "entries": entries,
    }
    approximate = common | {
        "kind": "derived_without_time_bonus_approximate",
        "score_definition": "Estimated J/c = b*sum(w*S*F), with calendar bonus set to 1",
        "normalization": rules,
        "comparison_scope": "Approximate scoring reference; official trajectories have not been certified by the benchmark judge",
        "uncertainty_note": "The interval only reflects assumed rounding to 0.001 points and omitted seconds within the displayed UTC minute. It does not cover unobserved host conventions or unavailable submissions.",
        "selection_note": "The same official best entry per team is rescored. This need not be that team's best possible c=1 submission.",
        "ranking_note": "Sorted by estimated J/c, then scientific flybys and initial Vx. This is not an official ranking.",
        "entries": corrected,
    }
    return official, approximate


class HTMLTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, _):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []

    def handle_data(self, value):
        if self.cell is not None:
            self.cell.append(value)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = None


def gtocx_bonus(submitted, start, end):
    if not start <= submitted <= end:
        raise ValueError("GTOCX submission outside the official window")
    return (
        Decimal(1)
        + (
            Decimal(str((end - submitted).total_seconds()))
            / Decimal(str((end - start).total_seconds()))
        )
        ** 4
    )


def gtocx(base, sources):
    rules = sources["gtocx_normalization"]
    checks_path = base / rules["reference_checks_path"]
    manifest_path = base / rules["reference_manifest_path"]
    for path, wanted in [
        (checks_path, rules["reference_checks_sha256"]),
        (manifest_path, rules["reference_manifest_sha256"]),
    ]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            raise ValueError(
                "GTOCX public-file validation provenance changed; rerun/update the audit"
            )
    checks = json.loads(checks_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    code_hash = hashlib.sha256()
    for path in sorted((base.parents[1] / "src/gtoc_bench/gtocx").glob("*.py")):
        code_hash.update(path.name.encode() + b"\0" + path.read_bytes())
    mapping = {
        "NUDT&XSCC": "NUDT-XSCC",
        "Tsinghua LAD - XINGYI": "TsinghuaLAD-XINGYI",
        "ESA-ACT": "ESA-ACT",
        "The Aerospace Corporation": "AerospaceCorp",
        "HIT_BACC": "HIT_BACC",
        "CSU": "CSU",
        "worhp2orb": "worhp2orb",
        "1-2-B-#1": "1-2-B-num1",
        "Team Kataskopoi": "TeamKataskopoi",
        "Team Jena": "TeamJena",
        "MSU-RAS-RUDN": "MSU-RAS-RUDN",
        "The Citadel": "TheCitadel",
    }
    parser = HTMLTable()
    parser.feed((base / "gtocx/source-jpl-leaderboard.html").read_text())
    start, end = epoch(rules["submission_start_utc"]), epoch(rules["submission_end_utc"])
    entries, corrected = [], []
    for rank, row in enumerate(parser.rows[1:], 1):
        if len(row) != 6:
            raise ValueError("Incomplete official GTOCX table row")
        name, submissions, last, best, metrics, score_text = row
        count, dv_used, dv_max = [v.strip() for v in metrics.split(",")]
        submitted = published_epoch(best.replace("June", "Jun")) - timedelta(
            minutes=rules["displayed_timezone_offset_minutes_assumed"]
        )
        b = gtocx_bonus(submitted, start, end)
        score = number(score_text)
        entry = {
            "official_rank": rank,
            "team": name,
            "submissions": integer(submissions),
            "best_submission_as_published": best,
            "last_submission_as_published": last,
            "best_submission_utc_estimate": iso(submitted),
            "settled_stars": integer(count),
            "delta_v_used_km_s_as_published": float(number(dv_used)),
            "delta_v_max_km_s_as_published": float(number(dv_max)),
            "official_score": float(score),
            "published_score_text": score_text,
        }
        entries.append(entry)
        normalized = entry | {
            "time_bonus_from_date_estimate": rounded(b, 12),
            "score_without_time_bonus": rounded(score / b),
            "normalization_method": "estimated_from_best_submission_date",
        }
        if name in mapping:
            filename = mapping[name] + "-gtocx-leaderboard-soln"
            verdict = checks[filename]
            path = manifest_path.parent / "submissions" / filename
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if (
                digest != manifest["files"]["submissions/" + filename]["sha256"]
                or digest != verdict["artifact_sha256"]
            ):
                raise ValueError("GTOCX public submission bytes changed")
            if (
                verdict["validator_revision"] != code_hash.hexdigest()
                or verdict["status"] != "valid"
            ):
                raise ValueError("GTOCX validated-file score is not current/accepted")
            detail = verdict["score_breakdown"]
            # Printed scientific notation has rounded Delta-V, so compare at its
            # published precision instead of requiring exact printed equality.
            quantum = Decimal(1).scaleb(Decimal(dv_used).as_tuple().exponent)
            if (
                detail["settled_stars"] != integer(count)
                or detail["delta_v_max_km_s"] != float(number(dv_max))
                or abs(Decimal(str(detail["delta_v_used_km_s"])) - number(dv_used)) > quantum / 2
            ):
                raise ValueError("Public GTOCX file does not match its final leaderboard entry")
            normalized.update(
                {
                    "score_without_time_bonus": verdict["score"],
                    "normalization_method": "recomputed_from_physically_validated_public_submission",
                    "reference_submission": "../../gtocx/submissions/" + filename,
                    "reference_submission_sha256": digest,
                    "reference_validator_revision": verdict["validator_revision"],
                    "delta_v_used_km_s_recomputed": detail["delta_v_used_km_s"],
                    "time_bonus_inferred_from_official_score": float(
                        score / Decimal(str(verdict["score"]))
                    ),
                }
            )
        corrected.append(normalized)
    corrected.sort(
        key=lambda r: (
            -r["score_without_time_bonus"],
            -r["settled_stars"],
            r["delta_v_used_km_s_as_published"],
        )
    )
    for rank, entry in enumerate(corrected, 1):
        entry["rank_without_time_bonus"] = rank
    common = {
        "schema_version": 1,
        "problem": "gtocx",
        "score_unit": "points",
        "captured_at_utc": sources["sources"]["gtocx/source-jpl-leaderboard.html"][
            "captured_at_utc"
        ],
        "source_url": sources["sources"]["gtocx/source-jpl-leaderboard.html"]["url"],
    }
    return (
        common | {"kind": "official_final", "entries": entries},
        common
        | {
            "kind": "derived_without_time_bonus",
            "normalization": rules,
            "score_definition": "J with B=1; all other merit terms unchanged",
            "recomputed_entries": len(mapping),
            "estimated_entries": len(entries) - len(mapping),
            "date_note": "Archive row timezone is inferred, not labelled. Best submission dates omit seconds, and the host used checking time rather than exact upload time. Date-based values are approximations.",
            "selection_note": "The same final official best entry per team is rescored, not all historical attempts.",
            "entries": corrected,
        },
    )


def md_table(headers, rows):
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    return (
        "\n".join(
            [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join(["---"] * len(headers)) + " |",
                *("| " + " | ".join(map(escape, row)) + " |" for row in rows),
            ]
        )
        + "\n"
    )


def utc_label(value):
    return value.replace("T", " ").removesuffix("Z")


def build(base=BASE):
    sources = json.loads((base / "sources.json").read_text())
    for name, source in sources["sources"].items():
        if hashlib.sha256((base / name).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"Source checksum mismatch: {name}")
    document = base / sources["gtoc13_normalization"]["local_statement_path"]
    if (
        hashlib.sha256(document.read_bytes()).hexdigest()
        != sources["gtoc13_normalization"]["local_statement_sha256"]
    ):
        raise ValueError("Referenced competition statement changed")
    with localcontext() as context:
        context.prec = 40
        twelve = gtoc12(base, sources)
        eleven, eleven_approximate = gtoc11(base, sources)
        official, approximate = gtoc13(base, sources)
        x_official, x_without_bonus = gtocx(base, sources)
    outputs = {}
    for name, data in [
        ("gtoc11/official-final", eleven),
        ("gtoc11/without-time-bonus-approx", eleven_approximate),
        ("gtoc12/official-final", twelve),
        ("gtoc13/official-final", official),
        ("gtoc13/without-time-bonus-approx", approximate),
    ]:
        outputs[name + ".json"] = (
            json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
    outputs["gtoc11/official-final.md"] = (
        "# GTOC11 — leaderboard oficial final\n\n"
        f"Fuente: [GTOC Portal de ESA]({eleven['source_url']}). "
        "Se conservan los 25 equipos, las puntuaciones históricas con bonus y las fechas UTC publicadas.\n\n"
        + md_table(
            ["Puesto", "Equipo", "M mínimo (kg)", "Asteroides", "J oficial", "Mejor entrega UTC"],
            [
                [
                    r["official_rank"],
                    r["team"],
                    r["minimum_station_mass_kg_as_published"],
                    r["transferred_asteroids"],
                    r["published_score_text"],
                    utc_label(r["best_submission_utc"]),
                ]
                for r in eleven["entries"]
            ],
        )
    )
    outputs["gtoc11/without-time-bonus-approx.md"] = (
        "# GTOC11 — leaderboard aproximado sin bonus temporal (B=1)\n\n"
        "Se estima J(B=1) = J_oficial/B(fecha_mejor_entrega). "
        "B = 2 - sqrt(1 - ((t_fin-t)/(t_fin-t_inicio))^3), con apertura de "
        "submissions el **17-10-2021 12:00 UTC** y cierre el **07-11-2021 12:00 UTC**. "
        "No se utiliza la fecha de publicación del enunciado (10 de octubre).\n\n"
        "Las 25 filas se estiman por fecha: no se dispone de sus archivos exactos identificados. "
        "Faltan segundos y los scores están redondeados. Se reordenan las mismas entregas "
        "retenidas, sin inferir el mejor intento histórico bajo B=1. La fila de Iowa State "
        "muestra 12:04, cuatro minutos después del cierre: se preserva esa fecha y se "
        "fija B=1 en su aproximación. Los intervalos por precisión están en el JSON.\n\n"
        + md_table(
            [
                "Puesto aprox.",
                "Puesto oficial",
                "Equipo",
                "J sin bonus aprox.",
                "J oficial",
                "B estimado",
            ],
            [
                [
                    r["approximate_rank"],
                    r["official_rank"],
                    r["team"],
                    f"{r['score_b1_estimate']:.2f}",
                    r["published_score_text"],
                    f"{r['time_bonus_estimate']:.9f}",
                ]
                for r in eleven_approximate["entries"]
            ],
        )
    )
    outputs["gtoc12/official-final.md"] = (
        "# GTOC12 — leaderboard oficial final\n\n"
        f"Fuente: [API oficial Tsinghua]({twelve['source_url']}), contrastada con [GTOC Portal de ESA]({twelve['cross_check_url']}).\n\n"
        "Las puntuaciones históricas incluyen los coeficientes competitivos por asteroide. "
        "El benchmark los fija a 1; esta tabla histórica no se convierte en masa sin ponderar.\n\n"
        + md_table(
            [
                "Puesto",
                "Equipo",
                "Naves",
                "Asteroides",
                "J oficial (kg ponderados)",
                "Mejor entrega UTC",
            ],
            [
                [
                    r["official_rank"],
                    r["team"],
                    r["ships"],
                    r["asteroids"],
                    f"{r['official_score']:.3f}",
                    utc_label(r["best_submission_utc"]),
                ]
                for r in twelve["entries"]
            ],
        )
    )
    outputs["gtoc13/official-final.md"] = (
        "# GTOC13 — leaderboard oficial final\n\n"
        f"Fuente: [JPL]({official['source_url']}). Puntuaciones con bonus temporal. Fechas UTC publicadas con resolución de un minuto.\n\n"
        + md_table(
            [
                "Puesto",
                "Equipo",
                "Envíos",
                "J oficial",
                "N ciencia",
                "Vx (km/s)",
                "TOF (años)",
                "Mejor entrega UTC",
                "Última entrega UTC",
            ],
            [
                [
                    r["official_rank"],
                    r["team"],
                    r["submissions"],
                    f"{r['official_score']:.3f}",
                    r["science_flybys"],
                    f"{r['initial_vx_km_s']:.3f}",
                    f"{r['time_of_flight_years']:.3f}",
                    utc_label(r["best_submission_utc"])[:-3],
                    utc_label(r["last_submission_utc"])[:-3],
                ]
                for r in official["entries"]
            ],
        )
    )
    outputs["gtoc13/without-time-bonus-approx.md"] = (
        "# GTOC13 — leaderboard aproximado sin bonus temporal\n\n"
        "Se estima `J(c=1) = J_oficial / c(fecha_mejor_entrega)`, tomando como origen "
        "**2025-10-20 17:00 UTC**. `c=1.13` durante los primeros siete días; después "
        "`c=1.165-0.005*días`. Se conserva el bonus de gran tour.\n\n"
        "Se utiliza **Best Submission**, que corresponde a la puntuación publicada, "
        "y se reordenan las mismas entregas finales de cada equipo. Las fechas no "
        "incluyen segundos y los scores están redondeados. No se puede recuperar "
        "de aquí la mejor entrega de cada equipo bajo c=1 entre todos sus intentos. "
        "Los intervalos orientativos por precisión de pantalla están en el JSON.\n\n"
        + md_table(
            [
                "Puesto aprox.",
                "Puesto oficial",
                "Equipo",
                "J sin bonus aprox.",
                "J oficial",
                "c estimado",
                "Mejor entrega UTC",
            ],
            [
                [
                    r["approximate_rank"],
                    r["official_rank"],
                    r["team"],
                    f"{r['score_c1_estimate']:.2f}",
                    f"{r['official_score']:.3f}",
                    f"{r['time_bonus_estimate']:.9f}",
                    utc_label(r["best_submission_utc"])[:-3],
                ]
                for r in approximate["entries"]
            ],
        )
    )
    for name, value in [
        ("gtocx/official-final", x_official),
        ("gtocx/without-time-bonus", x_without_bonus),
    ]:
        outputs[name + ".json"] = (
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
    outputs["gtocx/official-final.md"] = (
        "# GTOCX — leaderboard oficial final\n\n"
        f"Fuente: [archivo de JPL conservado por ESA]({x_official['source_url']}). "
        "Las 42 entradas conservan el bonus temporal y las fechas tal como se publicaron. "
        "La tabla estática no indica la zona horaria de las filas; el JSON conserva además una conversión UTC estimada.\n\n"
        + md_table(
            [
                "Puesto",
                "Equipo",
                "N",
                "ΔV usado (km/s)",
                "ΔV máximo (km/s)",
                "J oficial",
                "Mejor entrega publicada",
            ],
            [
                [
                    r["official_rank"],
                    r["team"],
                    r["settled_stars"],
                    r["delta_v_used_km_s_as_published"],
                    r["delta_v_max_km_s_as_published"],
                    r["published_score_text"],
                    r["best_submission_as_published"],
                ]
                for r in x_official["entries"]
            ],
        )
    )
    outputs["gtocx/without-time-bonus.md"] = (
        "# GTOCX — leaderboard sin bonus temporal (B=1)\n\n"
        "Se conservan las mismas entregas finales y se reordena por J con B=1. "
        "**12 puntuaciones se recalculan desde los archivos públicos, tras validar su física completa; "
        "30 se estiman por fecha como J_oficial/B.** Los valores de archivo no dependen de una fecha estimada.\n\n"
        "B = 1 + ((2019-06-12 20:00 UTC - t_check)/(22 días))^4. La apertura fue el "
        "2019-05-21 20:00 UTC. Para las filas sin archivo se utiliza Best Submission y un "
        "desfase UTC−05:00 inferido de las referencias públicas tempranas. La zona de las filas "
        "no está rotulada; faltan segundos y el host podía empezar a comprobar tras la subida. "
        "Por ello las filas marcadas «Fecha» son aproximadas.\n\n"
        + md_table(
            ["Puesto B=1", "Puesto oficial", "Equipo", "J sin bonus", "J oficial", "Procedencia"],
            [
                [
                    r["rank_without_time_bonus"],
                    r["official_rank"],
                    r["team"],
                    f"{r['score_without_time_bonus']:.2f}",
                    r["published_score_text"],
                    "Archivo validado"
                    if r["normalization_method"].startswith("recomputed")
                    else "Fecha (aprox.)",
                ]
                for r in x_without_bonus["entries"]
            ],
        )
    )
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check sources and generated files without rewriting"
    )
    args = parser.parse_args()
    outputs = build()
    for name, content in outputs.items():
        target = BASE / name
        if args.check:
            if not target.exists() or target.read_text() != content:
                parser.exit(1, f"Outdated leaderboard artifact: {name}\n")
        else:
            target.write_text(content)
    print(f"{'Verified' if args.check else 'Wrote'} {len(outputs)} leaderboard artifacts")


if __name__ == "__main__":
    main()
