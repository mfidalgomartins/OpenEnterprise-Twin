"""Content-addressed storage for large immutable JSON artifacts."""

import gzip
import json
import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile


class ArtifactNotFoundError(FileNotFoundError):
    """Raised when a content digest is not present in the artifact store."""


class FileArtifactStore:
    """Persist canonical gzip JSON outside transactional database tables."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put_json(self, payload: object) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        digest = sha256(canonical).hexdigest()
        destination = self._path_for(digest)
        if destination.exists():
            return digest

        compressed = gzip.compress(canonical, mtime=0)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=self._root,
                prefix=f".{digest}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(compressed)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return digest

    def get_json(self, digest: str) -> object:
        path = self._path_for(digest)
        if not path.exists():
            raise ArtifactNotFoundError(
                f"artifact '{digest}' is not present"
            )
        return json.loads(gzip.decompress(path.read_bytes()))

    def _path_for(self, digest: str) -> Path:
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("artifact digest must be a lowercase SHA-256 value")
        return self._root / f"{digest}.json.gz"
