"""Standalone comparative PNG/SVG figures; plotting is an optional host dependency."""

from textwrap import wrap

from .comparison import curve

COLORS = (
    "#2563eb",
    "#0d9488",
    "#d97706",
    "#7c3aed",
    "#db2777",
    "#64748b",
    "#92400e",
    "#0891b2",
    "#65a30d",
    "#dc2626",
    "#a21caf",
    "#0f172a",
)
AGENTS = {
    "codex": "Codex",
    "claude_code": "Claude Code",
    "antigravity": "Antigravity",
    "openhands": "OpenHands",
    "swe_agent": "SWE-agent",
    "mini_swe_agent": "mini-SWE-agent",
}


def label(group):
    recovered = " *" if group.get("recovered_evaluations") else ""
    return f"{AGENTS.get(group['agent'], group['agent'])} · {group['model']} · {group['reasoning']}{recovered}"


def tag(artist, series, part):
    """Stable SVG layers let the local report toggle all marks of one series."""
    artist.set_gid(f"series-{series}-{part}")
    return artist


def tag_bar(bars, series):
    for index, patch in enumerate(bars.patches):
        tag(patch, series, f"bar-{index}")
    if bars.errorbar:
        line, caps, collections = bars.errorbar.lines
        if line is not None:
            tag(line, series, "error-line")
        for index, artist in enumerate((*caps, *collections)):
            tag(artist, series, f"error-{index}")


def harness_figure(panel, plt, number, save):
    comparisons = panel["harness_comparisons"]
    if not comparisons:
        return None
    rows = max(len(c["harnesses"]) for c in comparisons)
    height = max(5.2, 2 + len(comparisons) * max(2.6, rows * 0.5 + 1))
    fig, axes = plt.subplots(len(comparisons), 1, figsize=(12.5, height), squeeze=False)
    fig.subplots_adjust(left=0.33, right=0.87, bottom=0.15, top=0.81, hspace=0.9)
    fig.suptitle(
        "Mismo modelo con distintos harness",
        x=0.08,
        y=0.965,
        ha="left",
        fontsize=17,
        weight="bold",
        color="#172c46",
    )
    fig.text(
        0.08,
        0.90,
        f"{panel['problem'].upper()} · {panel['wall_seconds'] / 3600:g} h · media del máximo por evaluación",
        color="#55657b",
    )
    unit = "kg" if panel["problem"] == "gtoc12" else "puntos"
    agents = sorted({h["agent"] for c in comparisons for h in c["harnesses"]})
    colors = {agent: COLORS[index % len(COLORS)] for index, agent in enumerate(agents)}
    series_ids = {
        (g["model"], g["reasoning"], g["agent"], g["agent_version"]): index
        for index, g in enumerate(panel["groups"])
    }
    for ax, comparison in zip(axes.flat, comparisons):
        names = []
        ids = []
        largest = (
            max(
                max(
                    h["maximum_scores"]["max"],
                    h["maximum_scores"]["mean"] + (h["maximum_scores"]["stddev"] or 0),
                )
                for h in comparison["harnesses"]
            )
            or 1
        )
        for index, row in enumerate(comparison["harnesses"]):
            series = series_ids[
                comparison["model"], comparison["reasoning"], row["agent"], row["agent_version"]
            ]
            ids.append(series)
            stats = row["maximum_scores"]
            marks = (" *" if row["recovered_evaluations"] else "") + (
                " †" if row["completed_evaluations"] < stats["n"] else ""
            )
            names.append(
                f"{AGENTS.get(row['agent'], row['agent'])} · {row['agent_version']}{marks}"
            )
            bars = ax.barh(
                index,
                stats["mean"],
                xerr=stats["stddev"],
                capsize=4,
                height=0.55,
                color=colors[row["agent"]],
                alpha=0.85,
            )
            tag_bar(bars, series)
            tag(
                ax.text(
                    stats["mean"] + (stats["stddev"] or 0) + largest * 0.025,
                    index,
                    f"{stats['mean']:,.1f}".replace(",", " ") + f" · n={stats['n']}",
                    va="center",
                    fontsize=9,
                ),
                series,
                "score",
            )
        ax.set_yticks(range(len(names)), names)
        for series, tick in zip(ids, ax.get_yticklabels()):
            tag(tick, series, "tick")
        ax.invert_yaxis()
        ax.set_title(f"{comparison['model']} · {comparison['reasoning']}", loc="left", fontsize=11)
        ax.set(xlabel=f"Media de máximos válidos ({unit})", xlim=(0, largest * 1.2))
        ax.xaxis.set_major_formatter(number)
        ax.grid(axis="x", alpha=0.15)
        ax.set_axisbelow(True)
    return save(
        fig,
        panel["id"] + "-harnesses",
        "Media y ±1 SD muestral entre evaluaciones independientes; con n=1 no se estima dispersión.\n"
        "Cada modelo usa su propia escala horizontal. * Cierre recuperado; † incluye una ejecución interrumpida.\n"
        "Las condiciones de máquina y ejecución permanecen en el informe; comparación descriptiva.",
        {"partial_series": [], "missing_series": []},
    )


def render_figures(report, output):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except ImportError as exc:
        raise RuntimeError(
            "Plotting requires the analysis extra: uv sync --extra analysis"
        ) from exc

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "#fbfcfe",
            "axes.facecolor": "#fbfcfe",
            "savefig.facecolor": "#fbfcfe",
            "svg.hashsalt": "gtoc-bench",
        }
    )
    figures = output / "figures"
    figures.mkdir(exist_ok=True)
    evaluations = {e["evaluation_id"]: e for e in report["evaluations"]}

    def number(value, _position=None):
        return f"{value:,.0f}".replace(",", ".")

    def save(fig, name, note, coverage):
        if coverage["partial_series"]:
            note += "\n† Datos parciales o repeticiones sin datos; causas y cobertura por serie en el informe y coverage.csv."
        missing = []
        for item in coverage["missing_series"]:
            missing.extend(wrap(item["label"] + ": " + item["reason"], width=145))
        if missing:
            note += "\nSin curva: " + "\n".join(missing)
        fig.set_figheight(fig.get_figheight() + max(0, len(note.splitlines()) - 2) * 0.22)
        fig.text(0.08, 0.025, note, fontsize=8.5, color="#55657b", va="bottom", linespacing=1.5)
        for extension in ("png", "svg"):
            fig.savefig(figures / f"{name}.{extension}", dpi=170)
        plt.close(fig)
        return f"figures/{name}.png"

    for panel in report["panels"]:
        groups, unit = panel["groups"], "kg" if panel["problem"] == "gtoc12" else "puntos"
        title = f"{panel['problem'].upper()} · {panel['wall_seconds'] / 3600:g} h"
        if any(g["recovered_evaluations"] for g in groups):
            title += " · * cierre recuperado"
        if panel["token_limit"] is not None:
            title += f" · límite {panel['token_limit']:,} tokens"
        panel["figures"] = {}
        fig, ax = plt.subplots(figsize=(12.5, max(5.2, 0.65 * len(groups) + 2.5)))
        fig.subplots_adjust(left=0.35, right=0.87, bottom=0.18, top=0.81)
        fig.suptitle(
            "Máxima puntuación por par agente–modelo",
            x=0.08,
            y=0.965,
            ha="left",
            fontsize=17,
            weight="bold",
            color="#172c46",
        )
        coverage = panel["coverage"]["maximum"]
        fig.text(0.08, 0.90, title, color="#55657b")
        fig.text(
            0.08,
            0.865,
            f"{coverage['plotted_series']}/{coverage['total_series']} series · media de los máximos de cada evaluación",
            color="#55657b",
        )
        largest = (
            max(
                max(
                    g["maximum_scores"]["max"],
                    g["maximum_scores"]["mean"] + (g["maximum_scores"]["stddev"] or 0),
                )
                for g in groups
            )
            or 1
        )
        for index, group in enumerate(groups):
            stats = group["maximum_scores"]
            color = COLORS[index % len(COLORS)]
            bars = ax.barh(
                index,
                stats["mean"],
                color=color,
                alpha=0.85,
                height=0.55,
                xerr=stats["stddev"],
                capsize=4,
            )
            tag_bar(bars, index)
            trials = [evaluations[rid] for rid in group["evaluation_ids"]]
            if len(trials) > 1:
                offsets = [
                    (i - (len(trials) - 1) / 2) * min(0.05, 0.32 / len(trials))
                    for i in range(len(trials))
                ]
                tag(
                    ax.scatter(
                        [e["best_score"] for e in trials],
                        [index + off for off in offsets],
                        s=20,
                        color="#172338",
                        zorder=4,
                        alpha=0.7,
                    ),
                    index,
                    "samples",
                )
            tag(
                ax.text(
                    stats["mean"] + (stats["stddev"] or 0) + largest * 0.025,
                    index,
                    f"{stats['mean']:,.1f}".replace(",", " ") + f"  · n={stats['n']}",
                    va="center",
                    fontsize=9,
                ),
                index,
                "score",
            )
        ax.set_yticks(range(len(groups)), [label(g).replace(" · ", "\n", 1) for g in groups])
        for index, tick in enumerate(ax.get_yticklabels()):
            tag(tick, index, "tick")
        ax.invert_yaxis()
        ax.set(xlabel=f"Media de máximos observados ({unit})", xlim=(0, largest * 1.1))
        ax.xaxis.set_major_formatter(FuncFormatter(number))
        ax.grid(axis="x", alpha=0.15)
        ax.set_axisbelow(True)
        panel["figures"]["maximum"] = save(
            fig,
            panel["id"] + "-maximum",
            "Barras de error: ±1 desviación estándar muestral entre evaluaciones independientes.\nCon n=1 no se estima dispersión. Las continuaciones y las copias no añaden repeticiones.",
            coverage,
        )
        for axis, name, xlabel, scale in (
            ("seconds", "time", "Tiempo activo acumulado (horas)", 3600),
            (
                "known_tokens",
                "tokens",
                "Consumo conocido: entrada + salida (millones de tokens)",
                1e6,
            ),
            ("known_usd", "price", "Coste API equivalente conocido (USD)", 1),
        ):
            legend_rows = (len(groups) + 1) // 2
            extra_rows = max(0, legend_rows - 5)
            fig, ax = plt.subplots(figsize=(12.5, 7.5 + 0.3 * extra_rows))
            fig.subplots_adjust(
                left=0.09, right=0.96, bottom=min(0.55, 0.32 + 0.05 * extra_rows), top=0.83
            )
            fig.suptitle(
                "Progreso comparativo por "
                + {"time": "tiempo", "tokens": "tokens", "price": "precio"}[name],
                x=0.08,
                y=0.965,
                ha="left",
                fontsize=17,
                weight="bold",
                color="#172c46",
            )
            coverage = panel["coverage"][axis]
            fig.text(
                0.08,
                0.90,
                title + f" · {coverage['plotted_series']}/{coverage['total_series']} series",
                color="#55657b",
            )
            available = 0
            legend_series = []
            for index, group in enumerate(groups):
                aggregate = group["curves"][axis]
                if not aggregate["available"]:
                    continue
                available += 1
                legend_series.append(index)
                color = COLORS[index % len(COLORS)]
                rows = aggregate["rows"]
                xs, ys = [r[axis] / scale for r in rows], [r["mean"] for r in rows]
                trials = [evaluations[rid] for rid in aggregate["included_evaluation_ids"]]
                if len(trials) > 1:
                    for trial_index, trial in enumerate(trials):
                        individual = curve(trial, axis)
                        tag(
                            ax.step(
                                [x / scale for x, _ in individual],
                                [y for _, y in individual],
                                where="post",
                                color=color,
                                alpha=0.17,
                                linewidth=0.7,
                            )[0],
                            index,
                            f"trial-{trial_index}",
                        )
                    tag(
                        ax.fill_between(
                            xs,
                            [max(0, r["mean"] - r["stddev"]) for r in rows],
                            [r["mean"] + r["stddev"] for r in rows],
                            step="post",
                            color=color,
                            alpha=0.13,
                        ),
                        index,
                        "band",
                    )
                n_label = (
                    str(len(trials))
                    if len(trials) == aggregate["n_total"]
                    else f"{len(trials)}/{aggregate['n_total']}"
                )
                tag(
                    ax.step(
                        xs,
                        ys,
                        where="post",
                        color=color,
                        linewidth=2,
                        label=f"{label(group)} (n={n_label})"
                        + (" †" if aggregate["partial"] else ""),
                    )[0],
                    index,
                    "mean",
                )
            ax.set(
                xlabel=xlabel,
                ylabel=f"Mejor puntuación validada ({unit})",
                xlim=(0, None),
                ylim=(0, None),
            )
            ax.yaxis.set_major_formatter(FuncFormatter(number))
            ax.grid(alpha=0.15)
            if available:
                legend = ax.legend(
                    loc="upper center",
                    bbox_to_anchor=(0.5, -0.20),
                    ncol=2,
                    frameon=False,
                    fontsize=9,
                )
                for index, handle, text in zip(
                    legend_series, legend.legend_handles, legend.get_texts()
                ):
                    tag(handle, index, "legend-handle")
                    tag(text, index, "legend-text")
            else:
                ax.text(
                    0.5,
                    0.5,
                    "No hay datos suficientes para este eje; causas al pie de la gráfica",
                    transform=ax.transAxes,
                    ha="center",
                )
            note = "Media escalonada y banda ±1 SD entre repeticiones; con n=1 solo hay una curva.\nHorizonte común observado por par; líneas finas: evaluaciones individuales."
            if name == "time":
                note += " Tiempo activo registrado; interrupciones sin extrapolar."
            elif name == "tokens":
                note += "\nLa caché ya está incluida en la entrada. El consumo pendiente o desconocido se detalla en el informe."
            else:
                note = f"Plantilla fija {report['prices']['as_of']} · tarifas estándar · equivalente API, no factura de suscripción.\nEl coste conocido puede ser parcial; no incluye hardware, almacenamiento de caché ni herramientas de pago."
            panel["figures"][name] = save(fig, panel["id"] + "-" + name, note, coverage)
        harnesses = harness_figure(panel, plt, number, save)
        if harnesses:
            panel["figures"]["harnesses"] = harnesses
