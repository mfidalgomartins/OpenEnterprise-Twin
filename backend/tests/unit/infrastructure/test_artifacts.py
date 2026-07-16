"""Contracts for content-addressed artifact persistence."""

from pathlib import Path

import pytest

from openenterprise_twin.infrastructure.artifacts import (
    ArtifactNotFoundError,
    FileArtifactStore,
)


def test_artifact_store_is_deterministic_and_round_trips_json(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    payload = {"metric": "ebitda", "values": [3, 1, 2]}

    first = store.put_json(payload)
    second = store.put_json({"values": [3, 1, 2], "metric": "ebitda"})

    assert first == second
    assert store.get_json(first) == payload
    assert tuple(tmp_path.iterdir()) == (tmp_path / f"{first}.json.gz",)


def test_artifact_store_rejects_invalid_or_missing_digests(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="SHA-256"):
        store.get_json("invalid")
    with pytest.raises(ArtifactNotFoundError):
        store.get_json("0" * 64)

