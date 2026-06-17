"""Unit tests for the sidebar partial (``_sidebar.html``) rendering contract.

Guards the icon-rail behaviour: every control that hides its label in the rail
must still carry an icon (so it does not become invisible), the collapse toggle
must exist, and rail centering must be driven by ``.sidebar-action`` rather than a
``justify-start`` utility that would left-align the lone icon.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader

import whisper_ui
from whisper_ui.ui import labels as labels_mod

_TEMPLATES = Path(whisper_ui.__file__).parent / "web" / "templates"


@pytest.fixture
def render_sidebar():
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    env.globals["labels"] = labels_mod
    env.globals["url_for"] = lambda *a, **k: "/static/x.svg"

    def _render(is_admin: bool = True, username: str = "alice") -> str:
        user = SimpleNamespace(is_admin=is_admin, username=username)
        request = SimpleNamespace(state=SimpleNamespace(user=user))
        return env.get_template("_sidebar.html").render(request=request)

    return _render


def test_logout_button_has_an_icon(render_sidebar):
    """The logout control must carry an icon so it stays visible in the icon rail
    (its label is sr-only there). Regression guard for the iconless logout."""
    html = render_sidebar()
    form = re.search(r'<form[^>]*action="/logout".*?</form>', html, re.S)
    assert form, "logout form not found"
    assert "<svg" in form.group(0), "logout button has no icon (invisible in the icon rail)"


def test_collapse_toggle_is_present(render_sidebar):
    html = render_sidebar()
    assert "sidebarCollapsed = !sidebarCollapsed" in html


def test_rail_controls_use_sidebar_action_not_justify_start(render_sidebar):
    """Collapse toggle, theme toggle and logout share .sidebar-action (which
    centers the lone icon in the rail); none should carry justify-start."""
    html = render_sidebar()
    assert html.count("sidebar-action") == 3
    assert "justify-start" not in html
