"""Worker process entrypoint that initialises logging before delegating to RQ.

The container entrypoint runs ``python -m whisper_ui.worker worker ...``
instead of ``python -m rq.cli worker ...`` so that the dictConfig from
:func:`whisper_ui.core.logging_setup.setup_logging` is applied inside the
same process that RQ will run in. Calling ``setup_logging`` from the shell
entrypoint (via ``python -c``) would not work — that subprocess exits
before ``exec`` hands control to RQ, and the new RQ process would inherit
none of the logging config.

Delegates to :func:`rq.cli.main` so every CLI flag (``--url``, ``--name``,
queue list, etc.) behaves identically to ``python -m rq.cli``.
"""

from __future__ import annotations

from whisper_ui.core.logging_setup import setup_logging


def main() -> None:
    # Env-based first so any log line during Settings load is formatted, then
    # re-apply from Settings so LOG_LEVEL / LOG_JSON in .env is honoured too.
    setup_logging()
    from whisper_ui.core.config import get_settings

    settings = get_settings()
    setup_logging(log_level=settings.log_level, log_json=settings.log_json)

    from rq.cli import main as rq_main

    rq_main()


if __name__ == "__main__":
    main()
