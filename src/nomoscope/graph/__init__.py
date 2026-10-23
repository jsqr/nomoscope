"""The graph store: one DuckDB file that records each step's inputs and outputs."""

from nomoscope.errors import NomoscopeError


class GraphError(NomoscopeError):
    pass
