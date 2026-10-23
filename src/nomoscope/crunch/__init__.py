"""Crunch: turn archived records into the carved tables."""

from typing import Protocol

from nomoscope.errors import NomoscopeError


class CrunchError(NomoscopeError):
    pass


class Summarized(Protocol):
    @property
    def summary(self) -> str: ...
