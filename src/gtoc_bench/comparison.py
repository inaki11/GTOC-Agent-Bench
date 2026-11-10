"""Canonical cross-run analysis. Continuations are one logical evaluation."""

import bisect
import hashlib
import json
import math
import os
import re
import shlex
import statistics
from collections import defaultdict
from pathlib import Path

from . import ROOT
from .comparison_recovery import load_recoveries, recovered_summary
from .pricing import PriceLedger, load_prices
from .problems import SPECS
from .storage import read_events, write_json

PRUNED = {
    "archive",
    "analysis",
    "comparisons",
    "exports",
    "workspace",
    "snapshots",
    "requests",
    "submissions",
    "downloads",
    "agent",
    "native-client",
    "node_modules",
    "__pycache__",
}
TERMINAL = {"completed", "failed", "interrupted", "cancelled"}
FINISHED = {"time_limit", "token_limit", "agent_finished", "agent_error", "output_limit"}


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing a symlink artifact: {path}")
    return json.loads(path.read_text())


def digest(path):
    if path.is_symlink():
        raise ValueError(f"Refusing a symlink artifact: {path}")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def discover(results):
    """Prune run internals before traversal; deduplicate copies by canonical hashes."""
    headers, excluded, duplicates = {}, [], []
    recoveries = load_recoveries(results)
    for directory, dirs, files in os.walk(Path(results).resolve(), followlinks=False):
        path = Path(directory)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in PRUNED and not d.startswith(".") and not (path / d).is_symlink()
        )
        if "manifest.json" not in files or not {
            "summary.json",
            "status.json",
            "events.jsonl",
        }.intersection(files):
            continue
        dirs.clear()
        manifest = read_json(path / "manifest.json")
        if manifest.get("config", {}).get("problem") not in SPECS:
            raise ValueError(f"Unknown problem in run manifest: {path}")
        rid = manifest["run_id"]
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", rid):
            raise ValueError(f"Invalid run identifier: {path}")
        recovery = None
        if "summary.json" in files:
            summary = read_json(path / "summary.json")
        elif path in recoveries:
            summary, recovery = recovered_summary(path, manifest, recoveries[path])
        else:
            excluded.append({"path": str(path), "run_id": rid, "reason": "missing_final_summary"})
            continue
        if summary.get("run_id") != rid:
            raise ValueError(f"Run identifiers disagree: {path}")
        if (summary.get("state") not in TERMINAL and not recovery) or summary.get(
            "elapsed_seconds", 0
        ) <= 0:
            excluded.append(
                {"path": str(path), "run_id": rid, "reason": "not_a_finalized_started_evaluation"}
            )
            continue
        continuation = (
            read_json(path / "continuation.json") if (path / "continuation.json").is_file() else {}
        )
        parent = continuation.get("parent_run_id", summary.get("parent_run_id"))
        if summary.get("parent_run_id", parent) != parent:
            raise ValueError(f"Conflicting continuation parent: {path}")
        hashes = {
            name: digest(path / name)
            for name in ("manifest.json", "summary.json", "events.jsonl")
            if name in files
        }
        if recovery:
            hashes["comparison_recovery"] = recovery["record_sha256"]
        if continuation:
            hashes["continuation.json"] = digest(path / "continuation.json")
        header = {
            "path": path,
            "run_id": rid,
            "manifest": manifest,
            "summary": summary,
            "continuation": continuation,
            "parent": parent,
            "hashes": hashes,
            "copies": [],
            "post_run_recovery": recovery,
        }
        if rid in headers:
            if headers[rid]["hashes"] != hashes:
                raise ValueError(f"Conflicting copies of run {rid}")
            headers[rid]["copies"].append(str(path))
            duplicates.append({"run_id": rid, "path": str(path), "same_canonical_hashes": True})
        else:
            headers[rid] = header
    return headers, excluded, duplicates


def request_metadata(run, identifier):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
        raise ValueError("Invalid controller request identifier")
    path = run / "requests" / identifier / "metadata.json"
    if any(p.is_symlink() for p in (path, path.parent, path.parent.parent)):
        raise ValueError("Request metadata cannot follow symlinks")
    return read_json(path) if path.is_file() else {}


def segment_data(header, prices):
    config, summary, path = header["manifest"]["config"], header["summary"], header["path"]
    ledger, candidates, usage_rows = PriceLedger(prices), {}, []
    cost, tokens, missing, partial, priced = 0.0, 0, 0, 0, 0
    missing_models = set()
    native_pending, timeline_issues = set(), set()
    native_totals_reconciled = False
    for event in read_events(path / "events.jsonl"):
        kind = event["type"]
        if kind == "native_usage_counter_regression":
            timeline_issues.add("native_usage_counter_regression")
        if kind in {"submission_evaluated", "validation_late_verdict"}:
            verdict = event.get("verdict")
            if isinstance(verdict, dict):
                candidates[event["submission_id"]] = verdict
        elif kind == "model_request_finished":
            usage = event.get("usage") or {}
            metadata = request_metadata(path, event["request_id"])
            native = usage.get("source") == "official_claude_cli"
            native_v2 = native and usage.get("accounting_schema") == 2
            if (
                native
                and metadata.get("cumulative_reconciliation")
                and usage.get("quality") == "reported"
            ):
                native_totals_reconciled = True
            if native and not native_v2:
                timeline_issues.add("legacy_native_provisional_output")
            if native_v2 and usage.get("scope") == "response":
                if usage.get("quality") == "reported":
                    native_pending.discard(event["request_id"])
                else:
                    native_pending.add(event["request_id"])
            if native_v2 and usage.get("temporal_quality") == "unattributed":
                timeline_issues.add("native_cumulative_usage_without_response_attribution")
            model = metadata.get("returned_model") or config["model"]["id"]
            amount = ledger.charge(model, event["request_id"], usage, metadata)
            if (
                amount is None
                and prices.get("aliases", {}).get(model, model) not in prices["models"]
            ):
                missing_models.add(model)
            # A provisional zero delta is not unpriced consumption. Its pending
            # state is resolved by the final observation of the same response.
            if native_v2 and not (usage.get("input_tokens") or usage.get("output_tokens")):
                amount = 0.0
            missing += amount is None
            if not native_v2:
                partial += usage.get("quality") not in {"reported", "not_billed"}
            cost += amount or 0.0
            incoming, outgoing = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0
            if any(type(v) is not int or v < 0 for v in (incoming, outgoing)):
                raise ValueError(f"Invalid recorded usage: {header['run_id']}")
            tokens += incoming + outgoing
            priced += amount is not None and incoming + outgoing > 0
            usage_rows.append(
                {"seconds": event["elapsed_seconds"], "known_tokens": tokens, "known_usd": cost}
            )
    usage_rows.sort(key=lambda q: q["seconds"])
    # Canonical events are ordered. Sorting above must not change the accounting.
    if any(b["known_tokens"] < a["known_tokens"] for a, b in zip(usage_rows, usage_rows[1:])):
        raise ValueError("Usage timestamps disagree with the canonical event order")
    notes = set(ledger.notes)
    if tokens != summary.get("known_tokens", tokens):
        notes.add("El total de uso del resumen difiere del detalle disponible; coste parcial")
        missing += 1
    ends = [q["seconds"] for q in usage_rows]
    points = []
    for sid, verdict in candidates.items():
        received = verdict.get("received_elapsed_seconds")
        if received is None:
            raise ValueError(f"Candidate has no receipt time: {sid}")
        index = bisect.bisect_right(ends, received) - 1
        value = verdict.get("score")
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value)):
            raise ValueError(f"Nonfinite candidate score: {sid}")
        points.append(
            {
                "submission_id": sid,
                "status": verdict["status"],
                "score": value,
                "seconds": received,
                "known_tokens": verdict.get("known_tokens", 0),
                "known_usd": usage_rows[index]["known_usd"] if index >= 0 else 0.0,
                "usage_pending": verdict.get("usage_pending", False),
                "pending_usage_responses": verdict.get("pending_usage_responses"),
                "unknown_usage_requests": verdict.get("unknown_usage_requests", 0),
            }
        )
    return {
        "points": sorted(points, key=lambda p: p["seconds"]),
        "known_usd": cost,
        "unpriced_observations": missing,
        "partial_usage_observations": partial,
        "pending_native_usage_responses": len(native_pending),
        "unreconciled_native_usage_responses": 0
        if native_totals_reconciled
        else len(native_pending),
        "usage_timeline_issues": sorted(timeline_issues),
        "priced_observations": priced,
        "price_notes": sorted(notes),
        "usage_event_count": len(usage_rows),
        "missing_price_models": sorted(missing_models),
    }


def logical_evaluations(headers, prices, minimum_seconds=3600, problem=None, wall_seconds=None):
    children = defaultdict(list)
    for rid, header in headers.items():
        if header["parent"]:
            if header["parent"] not in headers:
                raise ValueError(
                    f"Missing parent {header['parent']} for {rid}; include the full chain"
                )
            children[header["parent"]].append(rid)
    if any(len(v) > 1 for v in children.values()):
        raise ValueError(
            "Branched continuations share prior work; select one branch instead of counting repetitions"
        )
    evaluations, excluded, visited = [], [], set()
    for rid, root in sorted(headers.items()):
        if root["parent"]:
            continue
        chain, current = [], rid
        while current:
            if current in visited:
                raise ValueError("Cyclic continuation chain")
            visited.add(current)
            chain.append(headers[current])
            current = next(iter(children.get(current, [])), None)
        config = root["manifest"]["config"]
        budget = config["budget"]
        if (
            budget["wall_seconds"] < minimum_seconds
            or (problem and config["problem"] != problem)
            or (
                wall_seconds is not None
                and not math.isclose(budget["wall_seconds"], wall_seconds, abs_tol=0.1)
            )
        ):
            excluded.append(
                {"root_run_id": rid, "reason": "outside_selected_problem_or_original_budget"}
            )
            continue
        seconds, tokens, usd, unknown, missing, partial, priced = 0.0, 0, 0.0, 0, 0, 0, 0
        points, segments, notes, missing_models = [], [], set(), set()
        timeline_issues, native_pending, native_unreconciled = set(), 0, 0
        for header in chain:
            cfg, summary, continuation = (
                header["manifest"]["config"],
                header["summary"],
                header["continuation"],
            )
            if any(cfg[k] != config[k] for k in ("problem", "agent", "model")):
                raise ValueError(
                    f"Continuation changed problem, agent or model: {header['run_id']}"
                )
            if continuation:
                if (
                    not math.isclose(continuation["prior_elapsed_seconds"], seconds, abs_tol=0.1)
                    or continuation["prior_known_tokens"] != tokens
                ):
                    raise ValueError(f"Continuation offsets disagree: {header['run_id']}")
                parent = headers[header["parent"]]
                seal = parent["path"] / "integrity.json"
                if (
                    continuation.get("parent_integrity_sha256")
                    and digest(seal) != continuation["parent_integrity_sha256"]
                ):
                    raise ValueError(f"Continuation parent seal does not match: {header['run_id']}")
            data = segment_data(header, prices)
            for point in data["points"]:
                points.append(
                    point
                    | {
                        "segment_run_id": header["run_id"],
                        "seconds": seconds + point["seconds"],
                        "known_tokens": tokens + point["known_tokens"],
                        "known_usd": usd + point["known_usd"],
                        "unknown_usage_requests": unknown + point["unknown_usage_requests"],
                    }
                )
            segments.append(
                {
                    "run_id": header["run_id"],
                    "path": str(header["path"]),
                    "duplicate_copies": header["copies"],
                    "start_seconds": seconds,
                    "start_tokens": tokens,
                    "start_usd": usd,
                    "elapsed_seconds": summary["elapsed_seconds"],
                    "state": summary["state"],
                    "stop_reason": summary.get("stop_reason"),
                    "recovery_mode": continuation.get(
                        "recovery_mode", "native_session" if continuation else None
                    ),
                    "hashes": header["hashes"],
                    "post_run_recovery": header["post_run_recovery"],
                    "image_id": header["manifest"].get("image_id"),
                    "hardware": header["manifest"]
                    .get("resolved_conditions", {})
                    .get("hardware", {}),
                    "configured_hardware": cfg.get("hardware", {}),
                    "resource_changes": continuation.get("resource_changes", {}),
                    "clock_policy": cfg.get("recovery", {}).get("clock_policy", "continuous_wall"),
                    "configuration_id": header["manifest"].get("configuration_id"),
                    "provider": {
                        k: cfg.get("provider", {}).get(k) for k in ("id", "protocol", "auth_method")
                    },
                }
            )
            seconds += summary["elapsed_seconds"]
            tokens += summary.get("known_tokens", 0)
            usd += data["known_usd"]
            unknown += summary.get("unknown_usage_requests", 0)
            missing += data["unpriced_observations"]
            partial += data["partial_usage_observations"]
            native_pending += data["pending_native_usage_responses"]
            native_unreconciled += data["unreconciled_native_usage_responses"]
            timeline_issues.update(data["usage_timeline_issues"])
            priced += data["priced_observations"]
            notes.update(data["price_notes"])
            missing_models.update(data["missing_price_models"])
            if header["post_run_recovery"]:
                notes.add(header["post_run_recovery"]["note"])
        points.sort(key=lambda p: p["seconds"])
        if any(
            b["known_tokens"] < a["known_tokens"] or b["known_usd"] + 1e-9 < a["known_usd"]
            for a, b in zip(points, points[1:])
        ):
            raise ValueError(f"Nonmonotonic accounting along chain {rid}")
        best, improvements = 0.0, []
        for point in points:
            if point["seconds"] > budget["wall_seconds"] + 0.1:
                raise ValueError(f"Candidate received after the original time budget: {rid}")
            if point["status"] == "valid" and point["score"] > best:
                best = point["score"]
                improvements.append(point | {"best_score": best})
        expected = chain[-1]["summary"].get("best_score")
        if expected is not None and not math.isclose(best, expected, abs_tol=1e-6):
            raise ValueError(f"Candidate best score disagrees with summary: {rid}")
        finished = chain[-1]["summary"].get("stop_reason") in FINISHED
        if len(chain) > 1:
            notes.add("Continuaciones unidas; la pausa sin ejecución no consume el eje temporal")
        if any(s["resource_changes"] for s in segments):
            notes.add(
                "Continuación de investigación con cambio explícito de RAM; recursos heterogéneos"
            )
        clock_policies = sorted({s["clock_policy"] for s in segments})
        if len(clock_policies) > 1:
            notes.add("La política de pausas cambió entre segmentos; perfil temporal mixto")
        if any(s["recovery_mode"] == "workspace_only" for s in segments):
            notes.add(
                "Una continuación restauró archivos e historial observable, sin sesión nativa"
            )
        price_available = priced > 0 or (tokens == 0 and missing == 0 and unknown == 0)
        price_partial = bool(unknown or missing or partial or native_unreconciled)
        if missing_models:
            price_reason = "Falta tarifa en la plantilla para: " + ", ".join(sorted(missing_models))
        elif not price_available:
            price_reason = "No hay detalle de consumo suficiente para calcular el coste"
        elif price_partial:
            price_reason = "Coste conocido parcial: hay consumo desconocido, parcial o sin tarifa"
        else:
            price_reason = None
        axis_status = {
            "seconds": {
                "available": True,
                "partial": not finished,
                "reason": None
                if finished
                else f"Ejecución interrumpida; curva hasta {seconds / 3600:.3f} h activas",
            },
            "known_tokens": {
                "available": tokens > 0 or unknown == 0,
                "partial": bool(unknown or partial or native_unreconciled),
                "reason": "Consumo parcialmente desconocido; se muestran los tokens conocidos"
                if unknown or partial or native_unreconciled
                else None,
            },
            "known_usd": {
                "available": price_available,
                "partial": price_partial,
                "reason": price_reason,
            },
        }
        if not axis_status["known_tokens"]["available"]:
            axis_status["known_tokens"]["reason"] = (
                "El proveedor no comunicó ningún consumo de tokens"
            )
        if timeline_issues:
            reason = "El uso de Claude solo se conoce parcialmente por respuesta; un total al cierre no permite localizarlo en la curva"
            for axis in ("known_tokens", "known_usd"):
                axis_status[axis] = {"available": False, "partial": True, "reason": reason}
            notes.add(reason)
        evaluations.append(
            {
                "evaluation_id": rid,
                "problem": config["problem"],
                "agent": config["agent"],
                "model": config["model"],
                "budget": budget,
                "clock_policy": clock_policies[0]
                if len(clock_policies) == 1
                else "mixed:" + "+".join(clock_policies),
                "homogeneous_resources": not any(s["resource_changes"] for s in segments),
                "segments": segments,
                "protocol_finished": finished,
                "recovered_finalization": any(s["post_run_recovery"] for s in segments),
                "observed_seconds": min(seconds, budget["wall_seconds"]),
                "time_horizon": budget["wall_seconds"]
                if finished
                else min(seconds, budget["wall_seconds"]),
                "known_tokens": tokens,
                "known_usd": usd if price_available else None,
                "unknown_usage_requests": unknown,
                "unpriced_observations": missing,
                "partial_usage_observations": partial,
                "pending_native_usage_responses": native_pending,
                "unreconciled_native_usage_responses": native_unreconciled,
                "usage_timeline_quality": "partial"
                if timeline_issues or native_unreconciled or unknown or partial
                else "observed",
                "usage_timeline_issues": sorted(timeline_issues),
                "price_partial": price_partial,
                "axis_status": axis_status,
                "missing_price_models": sorted(missing_models),
                "price_notes": sorted(notes),
                "best_score": best,
                "has_valid_solution": any(p["status"] == "valid" for p in points),
                "candidate_count": len(points),
                "verdict_counts": {
                    s: sum(p["status"] == s for p in points)
                    for s in sorted({p["status"] for p in points})
                },
                "improvements": improvements,
                "points": points,
            }
        )
    if len(visited) != len(headers):
        raise ValueError("Continuation graph contains a cycle without a root")
    return evaluations, excluded


def summary_stats(values):
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "stddev": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "max": max(values),
    }


def curve(evaluation, axis):
    horizon = {
        "seconds": evaluation["time_horizon"],
        "known_tokens": evaluation["known_tokens"],
        "known_usd": evaluation["known_usd"],
    }[axis]
    if horizon is None or not evaluation.get("axis_status", {}).get(axis, {}).get(
        "available", True
    ):
        return None
    rows = [(0, 0.0)]
    for p in evaluation["improvements"]:
        x = min(p[axis], horizon)
        if x == rows[-1][0]:
            rows[-1] = x, max(rows[-1][1], p["score"])
        else:
            rows.append((x, p["score"]))
    if rows[-1][0] < horizon:
        rows.append((horizon, rows[-1][1]))
    return rows


def aggregate_curve(evaluations, axis):
    curves, included, excluded, limitations = [], [], [], []
    for evaluation in evaluations:
        data = curve(evaluation, axis)
        status = evaluation.get("axis_status", {}).get(axis, {})
        item = {"evaluation_id": evaluation["evaluation_id"], "reason": status.get("reason")}
        if data is None:
            excluded.append(
                item | {"reason": item["reason"] or "No hay datos suficientes para este eje"}
            )
        else:
            curves.append(data)
            included.append(evaluation["evaluation_id"])
            if status.get("partial"):
                limitations.append(item)
    coverage = {
        "n": len(included),
        "n_total": len(evaluations),
        "included_evaluation_ids": included,
        "excluded_evaluations": excluded,
        "limitations": limitations,
        "partial": bool(excluded or limitations),
    }
    if not curves:
        return {
            "available": False,
            "reason": "; ".join(sorted({e["reason"] for e in excluded})),
            **coverage,
        }
    horizon = min(c[-1][0] for c in curves)
    grid = sorted({0, horizon} | {x for c in curves for x, _ in c if x <= horizon})
    coordinates = [[p[0] for p in c] for c in curves]
    rows = []
    for x in grid:
        values = [c[bisect.bisect_right(xs, x) - 1][1] for c, xs in zip(curves, coordinates)]
        rows.append({axis: x, **summary_stats(values)})
    return {"available": True, "common_horizon": horizon, "rows": rows, **coverage}


def figure_coverage(groups, axis):
    """Shared by JSON, HTML and plots so a missing series is always explained."""
    missing, partial = [], []
    plotted, trials = 0, 0
    for group in groups:
        total = group["maximum_scores"]["n"]
        data = group["curves"].get(axis) if axis != "maximum" else None
        if data is None:
            plotted += 1
            trials += total
        elif data["available"]:
            plotted += 1
            trials += data["n"]
            if data["partial"]:
                partial.append(
                    {
                        "label": group["label"],
                        "n": data["n"],
                        "n_total": total,
                        "details": data["excluded_evaluations"] + data["limitations"],
                    }
                )
        else:
            missing.append(
                {
                    "label": group["label"],
                    "reason": data["reason"],
                    "evaluation_ids": group["evaluation_ids"],
                }
            )
    return {
        "total_series": len(groups),
        "plotted_series": plotted,
        "total_evaluations": sum(g["maximum_scores"]["n"] for g in groups),
        "plotted_evaluations": trials,
        "missing_series": missing,
        "partial_series": partial,
    }


def historical_table(problem, groups, base):
    path = Path(base) / problem / "official-final.json"
    if not path.is_file():
        return {"available": False, "reason": "No frozen historical leaderboard"}
    reference = read_json(path)
    originals = reference["entries"]

    def rank(score):
        return 1 + sum(row["official_score"] > score for row in originals)

    rows = [
        {
            "kind": "historical",
            "name": r["team"],
            "score": r["official_score"],
            "historical_rank": r["official_rank"],
        }
        for r in originals
    ]
    for group in groups:
        stats = group["maximum_scores"]
        group["historical_rank_mean"] = rank(stats["mean"])
        group["historical_rank_best"] = rank(stats["max"])
        rows.append(
            {
                "kind": "agent_model",
                "name": group["label"],
                "score": stats["mean"],
                "historical_rank": rank(stats["mean"]),
                "best_score": stats["max"],
                "best_historical_rank": rank(stats["max"]),
                "n": stats["n"],
                "stddev": stats["stddev"],
            }
        )
    return {
        "available": True,
        "source": reference.get("source_url"),
        "captured_at_utc": reference.get("captured_at_utc"),
        "sha256": digest(path),
        "reference_file": str(path.resolve()),
        "benchmark_comparable": reference.get("benchmark_comparable", False),
        "note": reference.get(
            "normalization_note",
            "Official historical scores may retain bonuses absent from the benchmark; this is a contextual numerical insertion.",
        ),
        "rank_rule": "1 + number of strictly higher historical scores; other agentic results never count",
        "rows": sorted(rows, key=lambda r: (-r["score"], r["kind"] != "historical", r["name"])),
    }


def compare_harnesses(groups, evaluations):
    """Compare the same model/effort using independent trial maxima per harness."""
    by_model = defaultdict(list)
    trials = {e["evaluation_id"]: e for e in evaluations}
    for group in groups:
        by_model[group["model"], group["reasoning"]].append(group)
    comparisons = []
    for (model, reasoning), series in by_model.items():
        harnesses = {group["agent"] for group in series}
        if len(harnesses) < 2:
            continue
        rows = []
        for group in series:
            repetitions = [trials[rid] for rid in group["evaluation_ids"]]
            rows.append(
                {
                    "agent": group["agent"],
                    "agent_version": group["agent_version"],
                    "maximum_scores": group["maximum_scores"].copy(),
                    "evaluation_ids": group["evaluation_ids"],
                    "completed_evaluations": group["completed_evaluations"],
                    "recovered_evaluations": group["recovered_evaluations"],
                    "observed_seconds": summary_stats([e["observed_seconds"] for e in repetitions]),
                    "clock_policies": group["clock_policies"],
                }
            )
        comparisons.append(
            {
                "model": model,
                "reasoning": reasoning,
                "harness_count": len(harnesses),
                "evaluation_count": sum(row["maximum_scores"]["n"] for row in rows),
                "harnesses": rows,
            }
        )
    return comparisons


def group_evaluations(evaluations, leaderboards, model_order=()):
    buckets = defaultdict(list)
    for e in evaluations:
        key = (
            e["problem"],
            e["budget"]["wall_seconds"],
            e["budget"].get("token_limit"),
        )
        buckets[key].append(e)
    panels = []
    for (problem, seconds, token_limit), trials in sorted(
        buckets.items(), key=lambda q: (q[0][0], q[0][1], str(q[0][2]))
    ):
        pairs = defaultdict(list)
        for e in trials:
            pairs[
                e["agent"]["id"],
                e["agent"]["version"],
                e["model"]["id"],
                e["model"].get("reasoning"),
            ].append(e)
        groups = []
        for (agent, version, model, reasoning), repetitions in sorted(pairs.items()):
            label = f"{agent} · {model} · {reasoning}"
            groups.append(
                {
                    "label": label,
                    "agent": agent,
                    "agent_version": version,
                    "model": model,
                    "reasoning": reasoning,
                    "evaluation_ids": [e["evaluation_id"] for e in repetitions],
                    "maximum_scores": summary_stats([e["best_score"] for e in repetitions]),
                    "completed_evaluations": sum(e["protocol_finished"] for e in repetitions),
                    "recovered_evaluations": sum(e["recovered_finalization"] for e in repetitions),
                    "valid_evaluations": sum(e["has_valid_solution"] for e in repetitions),
                    "clock_policies": sorted({e["clock_policy"] for e in repetitions}),
                    "curves": {
                        a: aggregate_curve(repetitions, a)
                        for a in ("seconds", "known_tokens", "known_usd")
                    },
                }
            )
        ranks = {model: index for index, model in enumerate(model_order)}
        groups.sort(
            key=lambda g: (
                g["agent"],
                ranks.get(g["model"], len(ranks)),
                g["model"],
                g["agent_version"],
                str(g["reasoning"]),
            )
        )
        panels.append(
            {
                "id": f"{problem}-{len(panels) + 1}",
                "problem": problem,
                "wall_seconds": seconds,
                "clock_policies": sorted({e["clock_policy"] for e in trials}),
                "homogeneous_resources": all(e["homogeneous_resources"] for e in trials),
                "token_limit": token_limit,
                "groups": groups,
                "harness_comparisons": compare_harnesses(groups, trials),
                "coverage": {
                    axis: figure_coverage(groups, axis)
                    for axis in ("maximum", "seconds", "known_tokens", "known_usd")
                },
                "history": historical_table(problem, groups, leaderboards),
            }
        )
    return panels


def build_comparison(
    results, prices, *, minimum_seconds=3600, problem=None, wall_seconds=None, leaderboards=None
):
    if (
        not math.isfinite(minimum_seconds)
        or minimum_seconds < 0
        or (wall_seconds is not None and (not math.isfinite(wall_seconds) or wall_seconds <= 0))
    ):
        raise ValueError("Comparison time budgets must be finite and nonnegative")
    headers, excluded, duplicates = discover(results)
    evaluations, filtered = logical_evaluations(
        headers, prices, minimum_seconds, problem, wall_seconds
    )
    if not evaluations:
        raise ValueError("No finalized evaluations match the selected problem and original budget")
    return {
        "schema_version": 1,
        "generator_source_sha256": {
            name: digest(Path(__file__).with_name(name))
            for name in (
                "comparison.py",
                "comparison_recovery.py",
                "comparison_plots.py",
                "comparison_interactive.py",
                "comparison_report.py",
                "pricing.py",
            )
        },
        "prices": prices,
        "results_root": str(Path(results).resolve()),
        "minimum_wall_seconds": minimum_seconds,
        "excluded": excluded + filtered,
        "duplicate_copies": duplicates,
        "evaluations": evaluations,
        "panels": group_evaluations(
            evaluations,
            leaderboards or ROOT / "references/leaderboards",
            prices.get("model_order", ()),
        ),
        "policies": {
            "continuations": "always one evaluation; offline gaps removed; original segments preserved",
            "recovery": "explicit operator records with frozen input hashes, confirmed stop and independent best revalidation; original missing summary/seal remain missing",
            "dispersion": "sample standard deviation across independent root evaluations; unavailable for n=1",
            "curve_average": "stepwise mean and sample SD on the common observed horizon of all repetitions in the pair",
            "time_extension": "finished protocol including voluntary/agent exit holds its best through assigned time; unresolved infrastructure interruptions do not",
            "token_axis": "reported input plus output; cached input and reasoning are subsets, not additional tokens",
            "price_axis": "known API-equivalent token cost at the fixed template; unknown consumption, hardware, tools, cache storage and subscription invoices excluded",
            "groups": "One panel per problem and original time/token budget; all clock policies and resource profiles compared together. Agent versions and reasoning efforts form distinct series; other conditions remain explicit metadata.",
            "coverage": "Every figure reports included and missing series and reasons; usable repetitions remain visible if other repetitions lack an axis. Interrupted curves stop at observed time.",
            "harness_comparison": "Same exact model and reasoning within each problem/budget; requires at least two distinct harness IDs. Means and sample SD use independent trial maxima per harness/version; continuations and copies are never additional repetitions.",
        },
    }


def export_comparison(results, output, prices=None, **options):
    from .comparison_report import render_report

    output = Path(output).resolve()
    price_path = Path(prices or ROOT / "analysis/prices.yaml").resolve()
    report = build_comparison(results, load_prices(price_path), **options)
    # All derived data stays outside original run directories and their seals.
    for e in report["evaluations"]:
        if any(output.is_relative_to(Path(s["path"])) for s in e["segments"]):
            raise ValueError("Comparison output must be outside original run directories")
    output.mkdir(parents=True, exist_ok=True)
    previous = (
        read_json(output / "comparison.json") if (output / "comparison.json").is_file() else {}
    )
    command = [
        "uv",
        "run",
        "--extra",
        "analysis",
        "gtoc",
        "compare",
        "--results",
        str(Path(results).resolve()),
        "--output",
        str(output),
        "--prices",
        str(price_path),
    ]
    for key, flag in (
        ("problem", "--problem"),
        ("wall_seconds", "--wall-seconds"),
        ("minimum_seconds", "--min-wall-seconds"),
        ("leaderboards", "--leaderboards"),
    ):
        if options.get(key) is not None:
            command.extend([flag, str(options[key])])
    report["reproduce_command"] = shlex.join(command)
    report["reproduce_workdir"] = str(ROOT)
    render_report(report, output)
    write_json(output / "comparison.json", report)
    write_json(output / "prices.json", report["prices"])
    for e in report["evaluations"]:
        write_json(output / "evaluations" / f"{e['evaluation_id']}.json", e)

    # Remove only obsolete generator-owned artifacts described by the previous
    # report. Unrelated files in the output directory are retained.
    def generated(data):
        names = set()
        for panel in data.get("panels", []):
            for figure in panel.get("figures", {}).values():
                if re.fullmatch(
                    r"figures/[A-Za-z0-9_-]+-(maximum|time|tokens|price|harnesses)\.png", figure
                ):
                    names.update((figure, figure.removesuffix(".png") + ".svg"))
        for e in data.get("evaluations", []):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", e["evaluation_id"]):
                names.add("evaluations/" + e["evaluation_id"] + ".json")
        return names

    for name in generated(previous) - generated(report):
        path = output / name
        if not path.parent.is_symlink():
            path.unlink(missing_ok=True)
    return output / "index.html"
