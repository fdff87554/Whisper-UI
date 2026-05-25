"""Visual-contrast regression for the dark/light themes (issue #61).

Renders the compiled stylesheet in a headless browser and asserts the card
border is perceptibly distinct from the card surface in both themes. This is
the objective oracle for the `--color-line` values in input.css — OKLCH
lightness is not WCAG luminance, so the only reliable check is to read the
*rendered* sRGB colors and compute the real contrast ratio.

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

# Resolve a computed CSS color (which Chromium may report as oklch(...)) to
# sRGB bytes by painting it on a canvas — robust across color spaces.
_TO_RGB = """
(el, prop) => {
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.fillStyle = getComputedStyle(el)[prop];
  ctx.fillRect(0, 0, 1, 1);
  const d = ctx.getImageData(0, 0, 1, 1).data;
  return [d[0], d[1], d[2], d[3]];
}
"""


def _opaque_rgb(locator, prop: str) -> tuple[int, int, int]:
    """Read a computed color as sRGB, asserting it is opaque.

    The contrast math below assumes opaque colors. A translucent value would
    need compositing over its background before the ratio is meaningful, so we
    fail loudly rather than compute a wrong number silently. All current theme
    tokens are opaque OKLCH; this guard documents and enforces that.
    """
    r, g, b, a = locator.evaluate(_TO_RGB, prop)
    assert a == 255, f"{prop} is not opaque (alpha={a}); composite over the background before measuring"
    return (r, g, b)


# Minimum border-vs-surface contrast we require. Not the WCAG 1.4.11 3:1 bar:
# card borders are decorative (the card is identifiable by its fill/content),
# and a strict 3:1 line on a near-white light card reads as a heavy outline.
# This floor is well above the ~1.05:1 near-invisible status quo #61 reported,
# so it guards against the border disappearing again while staying subtle.
_MIN_CONTRAST = 1.5


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("card_id", ["card-light", "card-dark"])
def test_card_border_is_distinct_from_surface(page: Page, card_id: str) -> None:
    page.goto(_HARNESS.resolve().as_uri())
    card = page.locator(f"#{card_id}")
    border = _opaque_rgb(card, "borderTopColor")
    surface = _opaque_rgb(card, "backgroundColor")
    ratio = _contrast(border, surface)
    assert ratio >= _MIN_CONTRAST, (
        f"{card_id}: border {border} vs surface {surface} contrast {ratio:.2f} < required {_MIN_CONTRAST}"
    )


def test_auto_theme_border_follows_prefers_dark(page: Page) -> None:
    """With no data-theme set (pre-hydration / JS off) and the system preferring
    dark, the border must use the dark value — not the bright light default.
    Asserts the no-data-theme card matches the explicit dark card."""
    page.emulate_media(color_scheme="dark")
    page.goto(_HARNESS.resolve().as_uri())
    auto = _opaque_rgb(page.locator("#card-auto"), "borderTopColor")
    dark = _opaque_rgb(page.locator("#card-dark"), "borderTopColor")
    assert auto == dark, f"auto-theme border {auto} != dark border {dark} (bright-border regression)"


def test_explicit_light_border_ignores_system_dark_preference(page: Page) -> None:
    """A nested/explicit data-theme=whisper-light keeps the light border even
    when the OS prefers dark — it must not inherit the root's dark fallback."""
    page.emulate_media(color_scheme="light")
    page.goto(_HARNESS.resolve().as_uri())
    under_light = _opaque_rgb(page.locator("#card-light"), "borderTopColor")
    page.emulate_media(color_scheme="dark")
    page.goto(_HARNESS.resolve().as_uri())
    under_dark = _opaque_rgb(page.locator("#card-light"), "borderTopColor")
    dark_border = _opaque_rgb(page.locator("#card-dark"), "borderTopColor")
    assert under_dark == under_light, f"explicit-light border drifted under dark pref: {under_dark} != {under_light}"
    assert under_dark != dark_border, "explicit-light border matched the dark theme (regression)"


def test_speaker_colors_switch_between_themes(page: Page) -> None:
    """Guards the var-based speaker colors: the same .speaker-1 renders a
    different color under whisper-light vs whisper-dark."""
    page.goto(_HARNESS.resolve().as_uri())
    light = _opaque_rgb(page.locator("#spk-light"), "color")
    dark = _opaque_rgb(page.locator("#spk-dark"), "color")
    assert light != dark, f"speaker color did not switch per theme: {light} == {dark}"
