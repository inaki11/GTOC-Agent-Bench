"""Local controls keep SVG references isolated and labels inert."""

import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import pytest

from gtoc_bench.comparison_interactive import inline_svg, interactive_figure


def test_inline_svg_prefixes_references_and_identifies_complete_series(tmp_path):
    path = tmp_path / "chart.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<metadata>Removed</metadata><defs><path id="glyph"/><clipPath id="clip"/></defs>'
        '<g id="series-2-mean" clip-path="url(#clip)"><use xlink:href="#glyph"/></g>'
        '<g id="series-2-legend-text"/></svg>'
    )
    roots = [ET.fromstring(inline_svg(path, prefix)) for prefix in ("one", "two")]
    ids = [{e.get("id") for e in root.iter() if e.get("id")} for root in roots]
    assert not ids[0] & ids[1]
    for prefix, root in zip(("one", "two"), roots):
        mean = root.find(f".//*[@id='{prefix}-series-2-mean']")
        assert mean.get("data-plot-series") == "2"
        assert mean.get("clip-path") == f"url(#{prefix}-clip)"
        assert mean[0].get("{http://www.w3.org/1999/xlink}href") == f"#{prefix}-glyph"
        legend = root.find(f".//*[@id='{prefix}-series-2-legend-text']")
        assert legend.get("data-legend") == "true"
        assert "Removed" not in ET.tostring(root, encoding="unicode")


@pytest.mark.parametrize(
    "content",
    [
        "<script/>",
        "<foreignObject/>",
        '<g onclick="bad()"/>',
        '<use href="https://example.com/x"/>',
    ],
)
def test_inline_svg_rejects_executable_or_external_content(tmp_path, content):
    path = tmp_path / "chart.svg"
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg">{content}</svg>')
    with pytest.raises(ValueError):
        inline_svg(path, "test")


def test_series_without_data_are_disabled_and_model_names_are_escaped(tmp_path):
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

    (tmp_path / "figure.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    model = '</button><script>alert("agent")</script>'
    panel = {
        "id": "test",
        "figures": {"price": "figure.png"},
        "harness_comparisons": [],
        "groups": [
            {
                "agent": "codex",
                "model": model,
                "reasoning": "max",
                "curves": {"known_usd": {"available": False, "reason": "Falta tarifa"}},
            }
        ],
    }
    markup = interactive_figure(panel, "price", tmp_path, "Price")
    parser = Elements()
    parser.feed(markup)
    assert not any(tag == "script" for tag, _ in parser.tags)
    series = [
        attrs for tag, attrs in parser.tags if tag == "button" and "data-series-button" in attrs
    ]
    assert len(series) == 1
    assert "disabled" in series[0] and series[0]["aria-pressed"] == "false"
    assert series[0]["title"] == "Falta tarifa"
    assert model in series[0]["data-label"]
    assert 'class="chart-controls" hidden' in markup  # No inert buttons without JavaScript.
