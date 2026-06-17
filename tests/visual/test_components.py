"""Visual regression for the design-alignment components (the UI realignment).

Renders the compiled stylesheet in a headless browser and asserts the two
visual cues that must not silently regress:

* the soft-alert left accent bar stays distinct from the alert surface — the
  non-color cue that pairs with the per-variant icon (see ``_alert.html``);
* the dashboard hero surface stays distinct from a plain ``base-200`` card, so
  the in-flight job remains the focal point.

Like ``test_contrast.py`` this reads *rendered* sRGB colors (OKLCH lightness is
not a reliable proxy), so it is the objective oracle for these CSS tokens.

Run with ``pytest -m visual`` (needs ``playwright install chromium``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.visual

_HARNESS = Path(__file__).parent / "harness.html"

_TO_RGB = """
(el, prop) => {
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.fillStyle = getComputedStyle(el)[prop];
  ctx.fillRect(0, 0, 1, 1);
  const d = ctx.getImageData(0, 0, 1, 1).data;
  return [d[0], d[1], d[2], d[3]];
}
"""


def _rgb(locator, prop: str) -> tuple[int, int, int]:
    r, g, b, _a = locator.evaluate(_TO_RGB, prop)
    return (r, g, b)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_alert_accent_bar_is_distinct_from_surface(page: Page, theme: str) -> None:
    """The 4px left accent bar must not blend into the soft alert surface."""
    page.goto(_HARNESS.resolve().as_uri())
    alert = page.locator(f"#alert-{theme}")
    assert alert.evaluate("el => getComputedStyle(el).borderLeftWidth") == "4px"
    bar = _rgb(alert, "borderLeftColor")
    surface = _rgb(alert, "backgroundColor")
    assert bar != surface, f"{theme}: accent bar {bar} == surface {surface} (bar invisible)"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_dashboard_hero_surface_is_distinct_from_plain_card(page: Page, theme: str) -> None:
    """The hero tint must read as different from a neutral base-200 card."""
    page.goto(_HARNESS.resolve().as_uri())
    hero = _rgb(page.locator(f"#hero-{theme}"), "backgroundColor")
    plain = _rgb(page.locator(f"#card-{theme}"), "backgroundColor")
    assert hero != plain, f"{theme}: hero {hero} == plain card {plain} (no tint)"
