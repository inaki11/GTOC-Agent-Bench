"""Derived views; deleting exports never deletes experimental evidence."""

import csv
import html
import io
import json
from itertools import islice
from pathlib import Path

from .audit import read_records
from .storage import atomic_write, read_events


def export_progress(run):
    run = Path(run)
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "submission_id",
            "elapsed_seconds",
            "known_tokens",
            "usage_pending",
            "pending_usage_responses",
            "usage_quality",
            "score",
            "best_score",
            "cumulative_elapsed_seconds",
            "cumulative_known_tokens",
            "inherited_best_score",
        ],
    )
    writer.writeheader()
    latest = {}
    if (run / "events.jsonl").exists():
        for event in read_events(run / "events.jsonl"):
            if event["type"] == "submission_evaluated":
                latest[event["submission_id"]] = event["verdict"]
    points = [v for v in latest.values() if v["status"] == "valid"]
    continuation_path = run / "continuation.json"
    continuation = json.loads(continuation_path.read_text()) if continuation_path.exists() else {}
    inherited_best = continuation.get("inherited_best_score")
    best = inherited_best or 0
    for point in sorted(points, key=lambda p: p["received_elapsed_seconds"]):
        best = max(best, point["score"])
        writer.writerow(
            {
                "submission_id": point["submission_id"],
                "elapsed_seconds": point["received_elapsed_seconds"],
                "known_tokens": point["known_tokens"],
                "usage_pending": point["usage_pending"],
                "pending_usage_responses": point.get("pending_usage_responses"),
                "usage_quality": "partial"
                if point["usage_pending"]
                or point.get("unknown_usage_requests", 0)
                or continuation.get("prior_unknown_usage_requests", 0)
                else "reported",
                "score": point["score"],
                "best_score": best,
                "cumulative_elapsed_seconds": point.get(
                    "cumulative_elapsed_seconds", point["received_elapsed_seconds"]
                ),
                "cumulative_known_tokens": point.get(
                    "cumulative_known_tokens", point["known_tokens"]
                ),
                "inherited_best_score": inherited_best,
            }
        )
    atomic_write(run / "exports/progress.csv", buffer.getvalue().encode())
    return run / "exports/progress.csv"


# Bounded derived views: raw JSON/JSONL and snapshot manifests remain the evidence.
AUDIT_ROWS = 200
AUDIT_PREVIEW = 1200


def _preview(value, limit=AUDIT_PREVIEW):
    """Clip structures before serializing, including a single enormous tool result."""

    def clip(item, depth=0):
        if isinstance(item, str):
            return item[:limit] + (" … [abreviado]" if len(item) > limit else "")
        if depth >= 5:
            return "[estructura abreviada]"
        if isinstance(item, dict):
            result = {str(k)[:120]: clip(v, depth + 1) for k, v in islice(item.items(), 20)}
            if len(item) > 20:
                result["…"] = f"{len(item) - 20} campos adicionales"
            return result
        if isinstance(item, list):
            return [clip(v, depth + 1) for v in item[:15]] + (["…"] if len(item) > 15 else [])
        return item

    text = json.dumps(clip(value), indent=2, ensure_ascii=False)
    return html.escape(text[:limit] + (" … [ver evidencia completa]" if len(text) > limit else ""))


class _AuditRows:
    def __init__(self):
        from collections import deque

        self.first, self.last, self.count = [], deque(maxlen=AUDIT_ROWS // 2), 0

    def add(self, row):
        self.count += 1
        if len(self.first) < AUDIT_ROWS // 2:
            self.first.append(row)
        else:
            self.last.append(row)

    def render(self):
        note = ""
        if self.count > AUDIT_ROWS:
            note = f"<p>Vista de los primeros y últimos {AUDIT_ROWS // 2} registros de {self.count}. La evidencia enlazada conserva todos.</p>"
        return note + "<table>" + "".join(self.first) + "".join(self.last) + "</table>"


def export_audit(run):
    """Compact static index. Streams logs; never expands a workspace manifest in HTML."""
    from urllib.parse import quote

    run = Path(run).resolve()

    def link(relative, label=None):
        from pathlib import PurePosixPath

        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not (run / relative).resolve().is_relative_to(run)
        ):
            return html.escape(label or relative)
        if not (run / relative).exists():
            if (run / "evidence.tar.gz").is_file():
                return (
                    f'<a href="../evidence.tar.gz">{html.escape(label or relative)} (archivado)</a>'
                )
            return html.escape(label or relative) + " (no disponible)"
        return f'<a href="../{quote(relative, safe="/")}">{html.escape(label or relative)}</a>'

    def load(name):
        path = run / name
        return json.loads(path.read_text()) if path.exists() else {"available": False}

    def row(entry):
        title = entry.get("activity", {}).get("kind", entry.get("type", "event"))
        seconds = entry.get("elapsed_seconds", 0)
        when = f"{seconds:.3f}" if isinstance(seconds, (int, float)) else "?"
        return f"<tr><td>{when}</td><td>{html.escape(str(title)[:140])}</td><td><details><summary>Vista abreviada</summary><pre>{_preview(entry)}</pre></details></td></tr>"

    events_view, snapshots, activities, native, resources, models = [_AuditRows() for _ in range(6)]
    snapshot_count = coalesced = skipped = 0
    if (run / "events.jsonl").exists():
        for event in read_events(run / "events.jsonl"):
            events_view.add(row(event))
            if event["type"] == "observation_skipped":
                skipped += 1
            if event["type"] != "workspace_snapshot":
                continue
            snapshot_count += 1
            coalesced += max(0, event.get("coalesced_requests", 1) - 1)
            # Only the small event is inspected: no snapshot manifest or per-file glob.
            detail = f"{event.get('reason', '')} · {event.get('file_count', '?')} archivos · {event.get('changed_files', '?')} cambios"
            snapshots.add(
                f"<tr><td>{event.get('elapsed_seconds', 0):.3f}</td><td>{html.escape(detail)}</td><td>{link(event['artifact'], event.get('snapshot_id', 'Manifiesto'))}</td></tr>"
            )
    for entry in read_records(run / "agent/transcript.jsonl"):
        activities.add(row(entry))
    pending = set()
    for entry in read_records(run / "native-client/tools.jsonl"):
        if entry["type"] == "tool_started":
            pending.add(entry["call_id"])
        else:
            pending.discard(entry.get("call_id"))
        native.add(row(entry))
    peak = None
    for entry in read_records(run / "resources.jsonl"):
        value = entry.get("cgroup", {}).get("memory.current")
        if str(value or "").isdigit():
            peak = max(peak or 0, int(value))
        resources.add(row(entry))
    for path in sorted((run / "requests").glob("*/metadata.json")):
        refs = [
            link(str(p.relative_to(run)), p.name)
            for p in sorted(path.parent.iterdir())
            if p.is_file()
        ]
        models.add(f"<tr><td>{html.escape(path.parent.name)}</td><td>{' · '.join(refs)}</td></tr>")
    summary = load("summary.json")
    inventory = load("workspace-manifest.json")
    inventory_bytes = sum(v["bytes"] for v in inventory.get("files", {}).values())
    evidence = {
        "agent_records": activities.count,
        "native_tool_records": native.count,
        "pending_native_tool_count": len(pending),
        "pending_native_tools": sorted(pending)[:100],
        "resource_samples": resources.count,
        "sampled_peak_memory_bytes": peak,
        "workspace_snapshots": snapshot_count,
        "coalesced_snapshot_requests": coalesced,
        "skipped_observations": skipped,
        "model_requests": models.count,
        "reported_workspace_bytes": summary.get("workspace_bytes"),
        "final_inventory_regular_file_bytes": inventory_bytes if "files" in inventory else None,
        "final_inventory_truncated": inventory.get("truncated"),
        "scope": "Observable client activity and sampled state; no hidden reasoning.",
        "view": "compact",
        "max_rows_per_section": AUDIT_ROWS,
    }
    names = (
        "prompt.txt",
        "manifest.json",
        "audit-policy.json",
        "provider-model.json",
        "codex-model-catalog.json",
        "model-compatibility.json",
        "continuation.json",
        "checkpoint.json",
        "validator-preflight.json",
        "provider-recovery.json",
        "provider-transport.json",
        "gateway-stop.json",
        "native-usage.json",
        "storage-preflight.json",
        "storage-status.json",
        "supervision.json",
        "memory.jsonl",
        "freezer.json",
        "freezer.jsonl",
        "restored-workspace.json",
        "events.jsonl",
        "agent/transcript.jsonl",
        "agent/stdout.log",
        "agent/stderr.log",
        "native-client/tools.jsonl",
        "resources.jsonl",
        "workspace-manifest.json",
        "workspace-usage.json",
        "container-state.json",
        "integrity.json",
        "evidence.tar.gz",
    )
    links = "".join(f"<li>{link(name)}</li>" for name in names if (run / name).exists())
    prompt = (run / "prompt.txt").read_text() if (run / "prompt.txt").exists() else "No disponible"
    document = f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Auditoría {html.escape(run.name)}</title><style>
body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#172338}}
table{{width:100%;border-collapse:collapse}}td{{padding:8px;border-bottom:1px solid #ddd;vertical-align:top}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:12px}}a{{color:#164dc0}}
</style><h1>Auditoría de {html.escape(run.name)}</h1>
<h2>Resultado y cobertura</h2><pre>{_preview(summary, 8000)}</pre><pre>{_preview(evidence, 8000)}</pre>
<p>Vista compacta: los detalles extensos se consultan en los archivos enlazados. Los manifiestos
no se expanden en este HTML. Las observaciones coalescidas comparten una captura posterior;
no reconstruyen cada estado intermedio. Los datos originales no se modifican.</p>
<p>El tamaño registrado y la suma del inventario se muestran por separado: una diferencia
no corrige retroactivamente el resumen sellado. Un inventario truncado puede ser parcial.</p>
<h2>Archivos de evidencia</h2><ul>{links}</ul>
<h2>Instrucciones iniciales</h2><pre>{html.escape(prompt[:32000])}</pre>
<h2>Actividad del agente</h2>{activities.render()}
<h2>Herramientas del servidor MCP</h2>{native.render()}
<h2>Copias del workspace</h2>{snapshots.render()}
<h2>Llamadas al modelo</h2>{models.render()}
<h2>Cronología del controlador</h2>{events_view.render()}
<h2>Recursos</h2>{resources.render()}
</html>"""
    target = run / "exports/audit.html"
    atomic_write(target, document.encode())
    atomic_write(
        run / "exports/audit-summary.json", (json.dumps(evidence, indent=2) + "\n").encode()
    )
    return target
