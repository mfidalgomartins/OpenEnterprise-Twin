"""Canonical content-addressing shared by the analytics artifacts.

Every analytics artifact is identified by a SHA-256 over its canonical JSON, so
the exact serialization is a shared contract. Centralising it here keeps that
contract in one place and byte-identical across the calibration, backtesting,
credibility, optimization, adaptive and monitoring modules.
"""

from __future__ import annotations

import json
from hashlib import sha256


def canonical_digest(body: object) -> str:
    """Return the SHA-256 hex digest of ``body`` as canonical, sorted JSON."""

    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
