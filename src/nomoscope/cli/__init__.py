"""Build a project's command.

app(registry, name) returns a typer app. Its settings are read with
settings.load(name), under the registry's project file.
"""

import logging
import sys
from typing import Annotated

import typer

from nomoscope.cli import (
    answer,
    coverage,
    crunch,
    derive,
    embed,
    graph,
    jurisdictions,
    ocr,
    query,
    snarf,
)
from nomoscope.crunch.registry import Registry


def app(registry: Registry, name: str) -> typer.Typer:
    built = typer.Typer(name=name, no_args_is_help=True, add_completion=False)

    @built.callback()
    def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(message)s",
            stream=sys.stderr,
        )
        logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.WARNING)

    for module in (
        snarf,
        ocr,
        crunch,
        derive,
        embed,
        jurisdictions,
        coverage,
        query,
        answer,
        graph,
    ):
        module.add(built, registry, name)
    return built
