from __future__ import annotations

from types import SimpleNamespace

from whisper_ui.web.flash import FLASH_SESSION_KEY, consume_flash, set_flash


def _request(session: dict | None = None) -> SimpleNamespace:
    """A stand-in exposing just the ``.session`` attribute the helpers touch."""
    return SimpleNamespace(session=session if session is not None else {})


def test_consume_flash_with_no_messages_returns_empty_list():
    assert consume_flash(_request()) == []


def test_set_then_consume_returns_message_and_clears_session():
    request = _request()
    set_flash(request, "已提交 1 個任務", "success")
    assert consume_flash(request) == [{"message": "已提交 1 個任務", "type": "success"}]
    assert consume_flash(request) == []
    assert FLASH_SESSION_KEY not in request.session


def test_set_flash_accumulates_messages_in_order():
    request = _request()
    set_flash(request, "first")
    set_flash(request, "second", "warning")
    assert consume_flash(request) == [
        {"message": "first", "type": "info"},
        {"message": "second", "type": "warning"},
    ]


def test_set_flash_defaults_category_to_info():
    request = _request()
    set_flash(request, "plain")
    assert consume_flash(request)[0]["type"] == "info"
