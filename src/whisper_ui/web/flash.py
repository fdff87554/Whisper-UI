"""Server-side flash messages backed by the Starlette session.

A flash is a one-shot user-facing message that survives a POST-redirect-GET:
``set_flash`` stashes it in ``request.session``; the next full-page render
calls ``consume_flash`` to pop and display it. This replaces the earlier
client-side localStorage stopgap so the redirect URL stays clean and the
message's lifecycle is owned by the server.

Each entry is ``{"message": str, "type": str}`` where ``type`` is a toast
category (``success`` / ``warning`` / ``error`` / ``info``) consumed by the
Alpine toast store in ``base.html``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

FLASH_SESSION_KEY = "_flash"


def set_flash(request: Request, message: str, category: str = "info") -> None:
    """Queue a flash message for the next full-page render."""
    messages = request.session.get(FLASH_SESSION_KEY, [])
    messages.append({"message": message, "type": category})
    request.session[FLASH_SESSION_KEY] = messages


def consume_flash(request: Request) -> list[dict[str, str]]:
    """Return and clear all queued flash messages (empty list if none)."""
    return request.session.pop(FLASH_SESSION_KEY, [])
