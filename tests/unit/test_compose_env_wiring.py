"""Guard that deployer-facing settings actually reach the frontend container.

compose.yml has no ``env_file`` (see its header): a value reaches a container
only when it is listed in an ``environment:`` map, directly or via a merged
anchor. So a Settings field can exist and parse correctly yet be silently
unreachable in a Compose deploy — the bug class caught in PR #156 review.
This test fails if any such field stops being wired into the frontend service.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Deployer-facing env vars the web app reads and must receive via Compose.
_REQUIRED_FRONTEND_ENV = {
    "DIARIZATION_DEFAULT_ENABLED",
    "TRUSTED_PROXY_COUNT",
    "MAX_REGISTER_ATTEMPTS_PER_IP",
    "REDIS_SOCKET_TIMEOUT",
    "REDIS_SOCKET_CONNECT_TIMEOUT",
    "REDIS_HEALTH_CHECK_INTERVAL",
}


def _frontend_env_keys() -> set[str]:
    compose = yaml.safe_load((_REPO_ROOT / "compose.yml").read_text())
    keys: set[str] = set()
    front = compose["services"]["frontend"].get("environment")
    # PyYAML may or may not fold the `<<` merge into a plain dict; handle both,
    # and fold the shared anchors explicitly as a belt-and-suspenders check.
    if isinstance(front, dict):
        keys |= set(front)
    elif isinstance(front, list):
        for item in front:
            if isinstance(item, dict):
                keys |= set(item)
    for anchor in ("x-core-env", "x-timeout-env"):
        block = compose.get(anchor) or {}
        keys |= set(block)
    return keys


def test_frontend_wires_all_required_deployer_settings():
    missing = _REQUIRED_FRONTEND_ENV - _frontend_env_keys()
    assert not missing, f"frontend compose environment is missing deployer settings: {sorted(missing)}"
