"""The blob store, shared by every source.

A blob is a file's decompressed bytes, stored at sha256/<aa>/<hash>. The
manifests say what each blob is.
"""

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from nomoscope.errors import NomoscopeError

ALGORITHM = "sha256"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 20


class BlobError(NomoscopeError):
    pass


def check_hash(sha256: str) -> str:
    if not HEX_RE.match(sha256):
        raise BlobError(f"not a {ALGORITHM} hex digest: {sha256!r}")
    return sha256


@dataclass(frozen=True)
class BlobStore:
    root: Path

    def path(self, sha256: str) -> Path:
        check_hash(sha256)
        return self.root / ALGORITHM / sha256[:2] / sha256

    def has(self, sha256: str) -> bool:
        return self.path(sha256).is_file()

    def put_stream(self, reader: BinaryIO) -> tuple[str, int, bool]:
        """Store the reader's bytes and return (sha256, size, written).

        The bytes are written to a temporary file and renamed into place.
        written is False when the blob was already stored.
        """
        tmp_dir = self.root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False) as tmp:
            for chunk in iter(lambda: reader.read(CHUNK), b""):
                h.update(chunk)
                size += len(chunk)
                tmp.write(chunk)
        sha256 = h.hexdigest()
        target = self.path(sha256)
        if target.exists():
            os.unlink(tmp.name)
            return sha256, size, False
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp.name, target)
        return sha256, size, True

    def put(self, data: bytes) -> str:
        sha256, _, _ = self.put_stream(BytesIO(data))
        return sha256

    def open(self, sha256: str) -> BinaryIO:
        path = self.path(sha256)
        try:
            return path.open("rb")
        except FileNotFoundError:
            raise BlobError(f"no blob {sha256}") from None

    def read(self, sha256: str) -> bytes:
        with self.open(sha256) as f:
            return f.read()
