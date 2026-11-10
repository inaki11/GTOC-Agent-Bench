"""Local SVG controls. Only this fixed, hash-authorized script executes."""

import base64
import hashlib
import html
import re
import xml.etree.ElementTree as ET

from .comparison_plots import COLORS, label

SCRIPT = """(() => {
  document.querySelectorAll('.interactive-chart').forEach(chart => {
    const controls = chart.querySelector('.chart-controls');
    const buttons = [...controls.querySelectorAll('[data-series-button]')];
    const available = buttons.filter(button => !button.disabled);
    const layers = [...chart.querySelectorAll('[data-plot-series]')];
    const status = controls.querySelector('[role="status"]');
    const empty = chart.querySelector('.chart-empty');
    function update() {
      const visible = available.filter(button => button.getAttribute('aria-pressed') === 'true').length;
      status.textContent = `${visible} de ${available.length} series visibles`;
      empty.hidden = visible !== 0 || available.length === 0;
    }
    function setVisible(button, visible) {
      button.setAttribute('aria-pressed', String(visible));
      button.setAttribute('aria-label', `${visible ? 'Ocultar' : 'Mostrar'} ${button.dataset.label}`);
      layers.filter(layer => layer.dataset.plotSeries === button.dataset.seriesButton).forEach(layer => {
        if (layer.dataset.legend === 'true') layer.style.opacity = visible ? '' : '0.2';
        else layer.style.display = visible ? '' : 'none';
      });
    }
    available.forEach(button => button.addEventListener('click', () => {
      setVisible(button, button.getAttribute('aria-pressed') !== 'true');
      update();
    }));
    controls.querySelectorAll('[data-series-action]').forEach(button => {
      button.addEventListener('click', () => {
        available.forEach(series => setVisible(series, button.dataset.seriesAction === 'show'));
        update();
      });
    });
    controls.hidden = false;
    update();
  });
})();"""

SCRIPT_HASH = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
CSS = """
.chart-controls{margin:12px 0;padding:14px;background:#f3f5f8;border-radius:8px}
.chart-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.chart-actions [role=status]{margin-left:auto;font-size:14px;color:#43536a}
.series-buttons{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.chart-controls button{font:inherit;font-size:13px;cursor:pointer;border:1px solid #b8c4d2;
 border-radius:6px;padding:8px 10px;background:white;color:#172338}
.chart-controls button:focus-visible{outline:3px solid #2563eb;outline-offset:2px}
.series-buttons button{display:flex;gap:8px;align-items:center;text-align:left}
.series-buttons button[aria-pressed=false]{opacity:.55;background:#e8edf3}
.series-buttons button[aria-pressed=false] .series-name{text-decoration:line-through}
.series-buttons button:disabled{cursor:not-allowed;opacity:.55}
.series-swatch{display:inline-block;width:14px;height:14px;border-radius:3px;flex-shrink:0}
.chart-svg svg{display:block;width:100%;height:auto}
.chart-empty{padding:12px;border:1px dashed #b8c4d2;border-radius:6px}
@media(max-width:700px){.series-buttons{grid-template-columns:1fr}}
@media print{.chart-controls{display:none}}
"""


def inline_svg(path, prefix):
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root = ET.parse(path).getroot()
    for child in list(root):
        if child.tag.rsplit("}", 1)[-1] == "metadata":
            root.remove(child)
    ids = {e.attrib["id"]: prefix + "-" + e.attrib["id"] for e in root.iter() if "id" in e.attrib}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] in {"script", "foreignObject"}:
            raise ValueError("Executable SVG content is not allowed in comparison figures")
        original_id = element.get("id", "")
        series = re.fullmatch(r"series-(\d+)-(.+)", original_id)
        if series:
            element.set("data-plot-series", series[1])
            if series[2].startswith("legend-"):
                element.set("data-legend", "true")
        for key, value in list(element.attrib.items()):
            if key.rsplit("}", 1)[-1].startswith("on"):
                raise ValueError("SVG event handlers are not allowed")
            if key == "id":
                value = ids[value]
            elif key.rsplit("}", 1)[-1] == "href":
                if not value.startswith("#") or value[1:] not in ids:
                    raise ValueError("SVG references must stay inside the figure")
                value = "#" + ids[value[1:]]
            value = re.sub(r"url\(#([^)]*)\)", lambda m: "url(#" + ids[m[1]] + ")", value)
            element.set(key, value)
    return ET.tostring(root, encoding="unicode")


def interactive_figure(panel, name, output, caption):
    prefix = panel["id"] + "-" + name
    png = panel["figures"][name]
    svg = png.removesuffix(".png") + ".svg"

    def escape(value):
        return html.escape(str(value), quote=True)

    axis = {"time": "seconds", "tokens": "known_tokens", "price": "known_usd"}.get(name)
    selected = None
    if name == "harnesses":
        selected = {(c["model"], c["reasoning"]) for c in panel["harness_comparisons"]}
    agents = sorted({h["agent"] for c in panel["harness_comparisons"] for h in c["harnesses"]})
    buttons = []
    for index, group in enumerate(panel["groups"]):
        if selected is not None and (group["model"], group["reasoning"]) not in selected:
            continue
        available = axis is None or group["curves"][axis]["available"]
        text = label(group)
        reason = group["curves"][axis].get("reason") if not available else None
        color = COLORS[
            (agents.index(group["agent"]) if selected is not None else index) % len(COLORS)
        ]
        action = "Ocultar" if available else "Sin datos:"
        buttons.append(
            f'<button type="button" data-series-button="{index}" data-label="{escape(text)}" '
            f'aria-pressed="{str(available).lower()}" aria-label="{action} {escape(text)}" '
            + (f'disabled title="{escape(reason)}" ' if not available else "")
            + f'><span class="series-swatch" style="background:{color}" aria-hidden="true"></span>'
            f'<span class="series-name">{escape(text)}</span>'
            + (" · sin datos" if not available else "")
            + "</button>"
        )
    return (
        f'<figure class="interactive-chart" id="{escape(prefix)}-chart">'
        '<div class="chart-controls" hidden><div class="chart-actions">'
        '<button type="button" data-series-action="show">Mostrar todos</button>'
        '<button type="button" data-series-action="hide">Ocultar todos</button>'
        '<span role="status" aria-live="polite"></span></div>'
        '<div class="series-buttons" role="group" aria-label="Series de esta gráfica">'
        + "".join(buttons)
        + "</div></div>"
        '<p class="chart-empty" hidden>No hay series visibles. Activa un modelo o pulsa «Mostrar todos».</p>'
        '<div class="chart-svg">' + inline_svg(output / svg, prefix + "-svg") + "</div>"
        f"<figcaption>{escape(caption)} La escala se mantiene al ocultar series. "
        f'<a href="{escape(svg)}">SVG completo</a> · <a href="{escape(png)}">PNG completo</a>'
        "</figcaption></figure>"
    )
