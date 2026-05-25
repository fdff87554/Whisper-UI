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
  return [d[0], d[1], d[2]];
}
"""

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
    border = tuple(card.evaluate(_TO_RGB, "borderTopColor"))
    surface = tuple(card.evaluate(_TO_RGB, "backgroundColor"))
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
    auto = tuple(page.locator("#card-auto").evaluate(_TO_RGB, "borderTopColor"))
    dark = tuple(page.locator("#card-dark").evaluate(_TO_RGB, "borderTopColor"))
    assert auto == dark, f"auto-theme border {auto} != dark border {dark} (bright-border regression)"
