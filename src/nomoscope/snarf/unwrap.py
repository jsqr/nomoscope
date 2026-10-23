"""Open a downloaded file's container and yield the files inside.

An unwrapper takes the staged file and its name, and yields (name, reader)
for each file inside. It handles containers and compression only.
"""

import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Protocol, cast

from nomoscope.errors import NomoscopeError


class UnwrapError(NomoscopeError):
    pass


class Unwrapper(Protocol):
    def __call__(self, path: Path, name: str) -> Iterator[tuple[str, BinaryIO]]: ...


def unwrap_zip(path: Path, name: str) -> Iterator[tuple[str, BinaryIO]]:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise UnwrapError(f"{name}: {e}") from e
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            with zf.open(info) as reader:
                yield info.filename, cast(BinaryIO, reader)


def unwrap_none(path: Path, name: str) -> Iterator[tuple[str, BinaryIO]]:
    with path.open("rb") as reader:
        yield name, reader


UNWRAPPERS: dict[str, Unwrapper] = {
    "zip": unwrap_zip,
    "none": unwrap_none,
}


def unwrapper(kind: str) -> Unwrapper:
    try:
        return UNWRAPPERS[kind]
    except KeyError:
        raise UnwrapError(f"unknown unwrapper: {kind!r}") from None
