"""Unit tests for the semantic alert macro (``_alert.html``).

The macro is the single place that pairs each daisyUI alert variant with a
distinct Lucide icon (a non-color cue) and the soft style the accent bar relies
on. These tests pin that contract and the autoescaping of the message.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import whisper_ui

_TEMPLATES = Path(whisper_ui.__file__).parent / "web" / "templates"


@pytest.fixture
def alert_macro():
    # autoescape mirrors Jinja2Templates' default for .html templates.
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    return env.get_template("_alert.html").module.alert


@pytest.mark.parametrize("variant", ["info", "success", "warning", "error"])
def test_alert_renders_variant_class_icon_and_message(alert_macro, variant):
    html = str(alert_macro(variant, "測試訊息"))
    assert f"alert-{variant}" in html
    assert "alert-soft" in html  # soft style — the accent bar reads against it
    assert "<svg" in html  # distinct per-variant icon (colorblind-safe cue)
    assert 'role="alert"' in html
    assert "測試訊息" in html


def test_alert_variants_render_distinct_icons(alert_macro):
    markup = {v: str(alert_macro(v, "x")) for v in ["info", "success", "warning", "error"]}
    # Each variant must produce a different SVG icon, not just a different class.
    svgs = {v: h[h.index("<svg") : h.index("</svg>")] for v, h in markup.items()}
    assert len(set(svgs.values())) == 4


def test_alert_escapes_message(alert_macro):
    html = str(alert_macro("error", "<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_alert_appends_extra_class(alert_macro):
    html = str(alert_macro("info", "x", "mb-4"))
    assert "mb-4" in html
