"""Compact comparative report and CSV exports, built only from canonical run data."""

import csv
import html
import io
import json
import os
from urllib.parse import quote

from .comparison_interactive import CSS as INTERACTIVE_CSS
from .comparison_interactive import SCRIPT, SCRIPT_HASH, interactive_figure
from .comparison_plots import AGENTS, label, render_figures
from .storage import atomic_write, utc_now


def write_csv(path, rows, fields):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(path, stream.getvalue().encode())


def render_report(report, output):
    render_figures(report, output)
    report["created_at_utc"] = utc_now()

    def escape(value):
        return html.escape(str(value), quote=True)

    def table(headers, rows):
        return (
            "<table><thead><tr>"
            + "".join(f"<th>{escape(v)}</th>" for v in headers)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in rows
            )
            + "</tbody></table>"
        )

    def num(value, digits=1):
        return "—" if value is None else f"{value:,.{digits}f}".replace(",", " ")

    def panel_title(panel):
        tokens = panel["token_limit"] if panel["token_limit"] is not None else "sin límite"
        return f"{panel['problem'].upper()} · {panel['wall_seconds'] / 3600:g} h · tokens: {tokens}"

    axes = {"seconds": "Tiempo", "known_tokens": "Tokens", "known_usd": "Precio"}
    body = "<h1>Comparación de soluciones GTOC-Bench</h1>"
    body += "<p>Generación automática local desde los resultados guardados y una plantilla fija de precios. No requiere un agente LLM, credenciales ni llamadas a modelos. Las ejecuciones remotas aún no copiadas no forman parte de estas entradas.</p>"
    body += f"<details><summary>Reproducir este informe</summary><p>Directorio: {escape(report['reproduce_workdir'])}</p><pre>{escape(report['reproduce_command'])}</pre></details>"
    body += f"<p>{len(report['evaluations'])} evaluaciones lógicas · {len(report['duplicate_copies'])} copias excluidas · plantilla de precios: {escape(report['prices']['as_of'])}.</p>"
    body += "<p>La puntuación de cada evaluación es su máximo válido observado. Cada continuación se une a su padre, acumulando tiempo activo, tokens y coste; las pausas quedan fuera. Los segmentos originales y sus auditorías se conservan.</p>"
    body += "<p>Las medias y la dispersión se calculan entre raíces independientes. Una sola evaluación no permite estimar dispersión. Las curvas medias usan el horizonte común observado del par; no extrapolan consumo de tokens ni dólares.</p>"
    body += "<p>Los problemas, presupuestos originales, versiones del agente y esfuerzos de razonamiento se distinguen. Diferencias de máquina, proveedor, imagen y recuperación pueden confundir una comparación: estas evaluaciones de desarrollo describen los sistemas observados.</p>"
    body += "<p>Los botones de cada gráfica permiten mostrar u ocultar pares agente–modelo de forma independiente. «Mostrar todos» y «Ocultar todos» afectan solo a esa gráfica; no cambian los datos, las estadísticas ni las tablas.</p><noscript><p>Activa JavaScript para usar los botones. Las gráficas completas y sus exportaciones siguen disponibles sin JavaScript.</p></noscript>"
    body += '<p><a href="comparison.json">Datos y procedencia</a> · <a href="summary.csv">Resumen CSV</a> · <a href="harnesses.csv">Comparación de harness CSV</a> · <a href="coverage.csv">Disponibilidad CSV</a> · <a href="progress.csv">Candidatos unidos</a> · <a href="curves.csv">Curvas medias y dispersión</a> · <a href="prices.json">Plantilla aplicada</a></p>'
    body += "<nav aria-label=Paneles><ul>"
    for panel in report["panels"]:
        body += f'<li><a href="#{escape(panel["id"])}">{escape(panel_title(panel))}</a></li>'
        if panel["harness_comparisons"]:
            body += f'<li><a href="#{escape(panel["id"])}-harnesses">{escape(panel["problem"].upper())} · mismo modelo con distintos harness</a></li>'
    body += "</ul></nav>"
    summaries, candidates, curves, coverage_rows = [], [], [], []
    harness_rows = []
    for panel in report["panels"]:
        unit = "kg" if panel["problem"] == "gtoc12" else "puntos"
        body += f'<h2 id="{escape(panel["id"])}">{escape(panel_title(panel))}</h2>'
        body += "<p>Todos los pares de este problema y presupuesto se comparan juntos. El eje temporal usa el tiempo activo registrado; las diferencias de hardware y tratamiento de esperas permanecen en las condiciones de cada ejecución.</p>"
        body += "<h3>Disponibilidad de las gráficas</h3>"
        availability = []
        for group in panel["groups"]:
            n = group["maximum_scores"]["n"]
            row = [label(group), f"{n}/{n}"]
            coverage_rows.append(
                {
                    "panel": panel["id"],
                    "agent": group["agent"],
                    "model": group["model"],
                    "axis": "maximum",
                    "available": True,
                    "n": n,
                    "n_total": n,
                    "partial": False,
                    "reason": "",
                }
            )
            for axis in axes:
                data = group["curves"][axis]
                reason = "; ".join(
                    sorted(
                        {
                            item["reason"]
                            for item in data["excluded_evaluations"] + data["limitations"]
                            if item["reason"]
                        }
                    )
                )
                row.append(
                    f"{data['n']}/{n}" + (" · parcial" if data["partial"] else "")
                    if data["available"]
                    else "No: " + data["reason"]
                )
                coverage_rows.append(
                    {
                        "panel": panel["id"],
                        "agent": group["agent"],
                        "model": group["model"],
                        "axis": axis,
                        "available": data["available"],
                        "n": data["n"],
                        "n_total": n,
                        "partial": data["partial"],
                        "reason": reason,
                    }
                )
            availability.append(row)
        body += table(["Par agente–modelo", "Máximo", "Tiempo", "Tokens", "Precio"], availability)
        body += "<p>Cada celda indica evaluaciones representadas / evaluaciones del par. «Parcial» señala interrupciones o consumo incompleto; las causas se detallan bajo cada gráfica. Una serie sin datos suficientes permanece identificada, sin asignarle un coste cero.</p>"
        recovered = [label(g) for g in panel["groups"] if g["recovered_evaluations"]]
        if recovered:
            body += (
                "<p class=note>Cierre recuperado: "
                + escape("; ".join(recovered))
                + ". Presupuesto temporal agotado y mejor solución revalidada. Falló el cierre del controlador; falta el resumen y sello final originales. Se conservan la incidencia y las pruebas de recuperación.</p>"
            )
        if not panel.get("homogeneous_resources", True):
            body += "<p>Hay continuaciones con cambios de recursos; sus condiciones se conservan en el detalle de las evaluaciones.</p>"
        for name, axis in (
            ("maximum", "maximum"),
            ("time", "seconds"),
            ("tokens", "known_tokens"),
            ("price", "known_usd"),
        ):
            coverage = panel["coverage"][axis]
            body += interactive_figure(
                panel,
                name,
                output,
                f"{coverage['plotted_series']}/{coverage['total_series']} series · "
                f"{coverage['plotted_evaluations']}/{coverage['total_evaluations']} evaluaciones.",
            )
            if coverage["missing_series"]:
                body += "<p class=note>Series sin curva en esta gráfica:</p>"
                body += table(
                    ["Par agente–modelo", "Motivo"],
                    [[s["label"], s["reason"]] for s in coverage["missing_series"]],
                )
            if coverage["partial_series"]:
                body += "<details><summary>Limitaciones de los datos representados</summary>"
                body += table(
                    ["Par agente–modelo", "Evaluación", "Motivo"],
                    [
                        [s["label"], item["evaluation_id"], item["reason"]]
                        for s in coverage["partial_series"]
                        for item in s["details"]
                    ],
                )
                body += "</details>"
        rows = []
        for group in panel["groups"]:
            s = group["maximum_scores"]
            rows.append(
                [
                    label(group),
                    s["n"],
                    num(s["mean"]),
                    num(s["stddev"]) if s["stddev"] is not None else "No estimable (n=1)",
                    num(s["max"]),
                    f"{group['valid_evaluations']}/{s['n']}",
                    f"{group['completed_evaluations']}/{s['n']}",
                    f"{group['recovered_evaluations']}/{s['n']}",
                ]
            )
            summaries.append(
                {
                    "problem": panel["problem"],
                    "wall_seconds": panel["wall_seconds"],
                    "clock_policies": "|".join(group["clock_policies"]),
                    "homogeneous_resources": panel.get("homogeneous_resources", True),
                    "token_limit": panel["token_limit"],
                    "agent": group["agent"],
                    "agent_version": group["agent_version"],
                    "model": group["model"],
                    "reasoning": group["reasoning"],
                    "n": s["n"],
                    "mean_maximum_score": s["mean"],
                    "sample_stddev": s["stddev"],
                    "best_observed_score": s["max"],
                    "valid_evaluations": group["valid_evaluations"],
                    "completed_evaluations": group["completed_evaluations"],
                    "recovered_evaluations": group["recovered_evaluations"],
                    "historical_rank_mean": group.get("historical_rank_mean"),
                    "historical_rank_best": group.get("historical_rank_best"),
                }
            )
            for axis, data in group["curves"].items():
                for point in data.get("rows", []):
                    curves.append(
                        {
                            "panel": panel["id"],
                            "pair": group["label"],
                            "axis": axis,
                            "x": point[axis],
                            "n": point["n"],
                            "mean": point["mean"],
                            "sample_stddev": point["stddev"],
                        }
                    )
        body += table(
            [
                "Par agente–modelo",
                "n",
                f"Media máximos ({unit})",
                "SD muestral",
                "Mejor observado",
                "Con solución válida",
                "Ejecución terminada",
                "Cierre recuperado",
            ],
            rows,
        )
        body += "<p>Una interrupción de infraestructura sin resolver conserva su máximo observado, pero no se considera un protocolo finalizado ni extiende su curva temporal. Las salidas voluntarias y los fallos del agente conservan el último máximo hasta su presupuesto temporal.</p>"
        body += "<p>Una ejecución terminada agotó su presupuesto o terminó el agente. La columna de cierre recuperado identifica los casos incorporados mediante pruebas posteriores por un fallo de finalización.</p>"
        body += f'<h3 id="{escape(panel["id"])}-harnesses">Mismo modelo con distintos harness</h3>'
        if not panel["harness_comparisons"]:
            body += "<p>No hay un mismo modelo y esfuerzo de razonamiento ejecutado con al menos dos harness distintos para este problema y presupuesto.</p>"
        else:
            body += "<p>Se compara la media de los máximos válidos por evaluación independiente, para el mismo modelo y razonamiento. Cada continuación cuenta una sola vez. Las versiones del harness mantienen filas separadas. Con n=1 la media es el resultado de esa única evaluación y la dispersión no se puede estimar.</p>"
            body += interactive_figure(
                panel,
                "harnesses",
                output,
                "Media y ±1 desviación estándar muestral; cada modelo tiene su propia escala horizontal.",
            )
            for comparison in panel["harness_comparisons"]:
                body += (
                    f"<h4>{escape(comparison['model'])} · {escape(comparison['reasoning'])}</h4>"
                )
                rows = []
                for harness in comparison["harnesses"]:
                    stats = harness["maximum_scores"]
                    interrupted = stats["n"] - harness["completed_evaluations"]
                    notes = []
                    if interrupted:
                        notes.append(f"{interrupted} interrumpida(s): máximo observado")
                    if harness["recovered_evaluations"]:
                        notes.append(f"{harness['recovered_evaluations']} cierre(s) recuperado(s)")
                    rows.append(
                        [
                            f"{AGENTS.get(harness['agent'], harness['agent'])} · {harness['agent_version']}",
                            stats["n"],
                            num(stats["mean"], 3),
                            num(stats["stddev"], 3)
                            if stats["stddev"] is not None
                            else "No estimable (n=1)",
                            num(harness["observed_seconds"]["mean"] / 3600, 3),
                            f"{harness['completed_evaluations']}/{stats['n']}",
                            "; ".join(notes) or "—",
                        ]
                    )
                    harness_rows.append(
                        {
                            "problem": panel["problem"],
                            "wall_seconds": panel["wall_seconds"],
                            "token_limit": panel["token_limit"],
                            "model": comparison["model"],
                            "reasoning": comparison["reasoning"],
                            "agent": harness["agent"],
                            "agent_version": harness["agent_version"],
                            "n": stats["n"],
                            "mean_maximum_score": stats["mean"],
                            "sample_stddev": stats["stddev"],
                            "min": stats["min"],
                            "max": stats["max"],
                            "mean_observed_seconds": harness["observed_seconds"]["mean"],
                            "completed_evaluations": harness["completed_evaluations"],
                            "recovered_evaluations": harness["recovered_evaluations"],
                            "evaluation_ids": "|".join(harness["evaluation_ids"]),
                        }
                    )
                body += table(
                    [
                        "Harness · versión",
                        "n",
                        f"Media de máximos ({unit})",
                        "SD muestral",
                        "Tiempo activo medio (h)",
                        "Terminadas",
                        "Observaciones",
                    ],
                    rows,
                )
            body += "<p>Las ejecuciones interrumpidas aportan su máximo observado y conservan su duración parcial. Diferencias de hardware, pausas, imágenes y recuperación siguen visibles en el detalle; esta comparación no aísla por sí sola el efecto del harness.</p>"
        history = panel["history"]
        body += "<h3>Posición en el ranking histórico</h3>"
        if not history["available"]:
            body += f"<p>{escape(history['reason'])}</p>"
        else:
            body += "<p>Se insertan las medias de máximos de cada par; también se indica el puesto de su mejor evaluación. Cada puesto cuenta exclusivamente equipos históricos con puntuación estrictamente superior. Los otros pares agénticos no desplazan ninguna posición.</p>"
            if not history["benchmark_comparable"]:
                body += (
                    "<p class=note>Inserción numérica contextual: la función objetivo histórica conserva términos que el benchmark elimina. No representa equivalencia de objetivos ni de recursos. "
                    + escape(history["note"])
                    + "</p>"
                )
            source = history.get("source")
            if source and source.startswith("https://"):
                body += f'<p><a href="{escape(source)}">Fuente histórica oficial</a> · captura {escape(history.get("captured_at_utc"))}.</p>'
            body += "<table><thead><tr><th>Puesto histórico</th><th>Equipo o par</th><th>Puntuación</th><th>Mejor evaluación / puesto</th><th>Repeticiones / SD</th></tr></thead><tbody>"
            for row in history["rows"]:
                agent = row["kind"] == "agent_model"
                body += '<tr class="agent">' if agent else "<tr>"
                fields = [
                    row["historical_rank"],
                    row["name"],
                    num(row["score"], 3),
                    f"{num(row['best_score'], 3)} / #{row['best_historical_rank']}"
                    if agent
                    else "—",
                    f"n={row['n']} · SD {num(row['stddev']) if row['stddev'] is not None else 'no estimable'}"
                    if agent
                    else "—",
                ]
                body += "".join(f"<td>{escape(v)}</td>" for v in fields) + "</tr>"
            body += "</tbody></table>"
    body += "<h2>Evaluaciones y condiciones</h2>"
    for e in report["evaluations"]:
        body += f"<details><summary>{escape(e['agent']['id'] + ' · ' + e['model']['id'])} · {escape(e['evaluation_id'])} · {len(e['segments'])} segmento(s)</summary>"
        body += table(
            [
                "Máximo",
                "Tiempo activo (h)",
                "Tokens conocidos",
                "Coste equivalente conocido (USD)",
                "Uso desconocido",
                "Respuestas sin uso definitivo",
                "Observaciones sin precio",
            ],
            [
                [
                    num(e["best_score"]),
                    num(e["observed_seconds"] / 3600, 3),
                    num(e["known_tokens"], 0),
                    num(e["known_usd"], 3),
                    e["unknown_usage_requests"],
                    e.get("pending_native_usage_responses", 0),
                    e["unpriced_observations"],
                ]
            ],
        )
        body += (
            "<p>Tokens y coste por candidato pueden excluir uso de una solicitud todavía en vuelo. "
            + ("El coste total es parcial. " if e["price_partial"] else "")
            + "Los costes son equivalentes API, no la factura de suscripción.</p>"
        )
        body += "<ul>" + "".join(f"<li>{escape(note)}</li>" for note in e["price_notes"]) + "</ul>"
        for segment in e["segments"]:
            audit = os.path.join(segment["path"], "exports/audit.html")
            href = quote(os.path.relpath(audit, output), safe="/")
            audit_label = (
                f'<a href="{escape(href)}">Auditoría {escape(segment["run_id"])}</a>'
                if os.path.isfile(audit)
                else f"Auditoría individual no disponible en esta copia: {escape(segment['run_id'])}"
            )
            body += f"<p>{audit_label} · {escape(segment['stop_reason'])} · recuperación: {escape(segment['recovery_mode'] or 'inicio limpio')}.</p>"
            recovery = segment["post_run_recovery"]
            if recovery:
                href = quote(os.path.relpath(recovery["record_path"], output), safe="/")
                body += f'<p class=note>Cierre incompleto recuperado. <a href="{escape(href)}">Registro de recuperación y hashes de las pruebas</a>. {escape(recovery["note"])}</p>'
            body += (
                "<pre>"
                + escape(
                    json.dumps(
                        {
                            "image_id": segment["image_id"],
                            "hardware": segment["hardware"],
                            "configured_hardware": segment["configured_hardware"],
                            "configuration_id": segment["configuration_id"],
                            "provider": segment["provider"],
                            "post_run_recovery": recovery,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                + "</pre>"
            )
        href = "evaluations/" + quote(e["evaluation_id"], safe="") + ".json"
        body += f'<p><a href="{href}">Evaluación unida y procedencia</a></p></details>'
        candidates.extend(
            {
                "evaluation_id": e["evaluation_id"],
                "agent": e["agent"]["id"],
                "model": e["model"]["id"],
                **p,
            }
            for p in e["points"]
        )
    body += "<h2>Evaluaciones excluidas</h2>"
    reasons = {
        "missing_final_summary": "Falta el resumen final y no hay recuperación verificada",
        "not_a_finalized_started_evaluation": "Ejecución sin inicio y cierre final confirmados",
        "outside_selected_problem_or_original_budget": "Fuera del problema o presupuesto seleccionado",
    }
    body += table(
        ["Ejecución", "Motivo", "Ruta"],
        [
            [
                item.get("run_id", item.get("root_run_id", "—")),
                reasons.get(item["reason"], item["reason"]),
                item.get("path", "—"),
            ]
            for item in report["excluded"]
        ],
    )
    body += "<h2>Tarifas aplicadas</h2><p>Las tarifas se fijan antes de generar el informe y se conservan con su hash. No se consulta ningún proveedor ni se ejecuta código del agente. Los alias de precio son equivalencias explícitas de la plantilla, no prueba del modelo upstream.</p>"
    price_rows = []
    for model, price in report["prices"]["models"].items():
        price_rows.append(
            [
                model,
                price["input"],
                price["cached_input"],
                price["output"],
                price.get("cache_write", "—"),
                price.get("cache_write_long", "—"),
            ]
        )
        body += f'<p><a href="{escape(price["source"])}">{escape(model)}: fuente de tarifa</a></p>'
    body += table(
        [
            "Modelo",
            "Entrada / M",
            "Caché lectura / M",
            "Salida / M",
            "Escritura por defecto / M",
            "Escritura larga / M",
        ],
        price_rows,
    )
    write_csv(output / "summary.csv", summaries, list(summaries[0]))
    write_csv(
        output / "harnesses.csv",
        harness_rows,
        [
            "problem",
            "wall_seconds",
            "token_limit",
            "model",
            "reasoning",
            "agent",
            "agent_version",
            "n",
            "mean_maximum_score",
            "sample_stddev",
            "min",
            "max",
            "mean_observed_seconds",
            "completed_evaluations",
            "recovered_evaluations",
            "evaluation_ids",
        ],
    )
    write_csv(
        output / "coverage.csv",
        coverage_rows,
        ["panel", "agent", "model", "axis", "available", "n", "n_total", "partial", "reason"],
    )
    write_csv(
        output / "progress.csv",
        candidates,
        [
            "evaluation_id",
            "agent",
            "model",
            "segment_run_id",
            "submission_id",
            "status",
            "score",
            "seconds",
            "known_tokens",
            "known_usd",
            "usage_pending",
            "pending_usage_responses",
            "unknown_usage_requests",
        ],
    )
    write_csv(
        output / "curves.csv", curves, ["panel", "pair", "axis", "x", "n", "mean", "sample_stddev"]
    )
    document = (
        '<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        f"script-src 'sha256-{SCRIPT_HASH}'; "
        "img-src 'self'; style-src 'unsafe-inline'; connect-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'\">"
        "<title>Comparación GTOC-Bench</title><style>body{font:16px system-ui;max-width:1250px;margin:40px auto;padding:0 24px;color:#172338}table{border-collapse:collapse;width:100%;font-size:14px;margin:20px 0}td,th{text-align:left;padding:10px;border-bottom:1px solid #ddd}th{background:#f1f5f9}.agent{background:#dff4ea;font-weight:600}.note{background:#fff3d6;padding:14px;border-left:4px solid #d99b20}img{display:block;width:100%;height:auto}figure{margin:24px 0}figcaption{font-size:12px;color:#556}a{color:#164dc0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f3f5f8;padding:12px}details{border:1px solid #ddd;border-radius:6px;padding:14px;margin:12px 0}summary{cursor:pointer}"
        + INTERACTIVE_CSS
        + "</style>"
        + body
        + "<script>"
        + SCRIPT
        + "</script></html>"
    )
    atomic_write(output / "index.html", document.encode())
