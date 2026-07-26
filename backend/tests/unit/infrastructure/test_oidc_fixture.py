"""Security contract for the local browser-only OIDC fixture."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import cast


def _fixture_namespace() -> dict[str, object]:
    path = Path(__file__).parents[2] / "fixtures" / "oidc_server.py"
    return runpy.run_path(str(path))


def test_fixture_accepts_only_exact_redirects_and_header_safe_state() -> None:
    namespace = _fixture_namespace()
    validate = cast(
        object,
        namespace["_validated_authorization_request"],
    )
    assert callable(validate)
    valid = {
        "client_id": ["openenterprise-twin-browser"],
        "redirect_uri": ["http://127.0.0.1:4173/auth/callback"],
        "response_type": ["code"],
        "code_challenge_method": ["S256"],
        "code_challenge": ["a" * 43],
        "state": ["state.0123_safe"],
        "nonce": ["nonce-0123"],
        "scope": ["openid profile"],
    }

    assert validate(valid) is not None  # type: ignore[operator]
    assert validate(  # type: ignore[operator]
        {**valid, "state": ["safe\r\nX-Injected: yes"]}
    ) is None
    assert validate(  # type: ignore[operator]
        {
            **valid,
            "redirect_uri": [
                "http://127.0.0.1:4173/auth/callback\r\nX-Injected: yes"
            ],
        }
    ) is None
    assert validate(  # type: ignore[operator]
        {**valid, "redirect_uri": ["https://attacker.example/callback"]}
    ) is None


def test_fixture_logout_uses_one_constant_safe_destination() -> None:
    namespace = _fixture_namespace()
    destination = namespace["_validated_logout_destination"]
    assert callable(destination)

    assert destination(  # type: ignore[operator]
        {"post_logout_redirect_uri": ["http://127.0.0.1:4173/"]}
    ) == "http://127.0.0.1:4173/"
    assert destination(  # type: ignore[operator]
        {"post_logout_redirect_uri": ["safe\r\nX-Injected: yes"]}
    ) is None
