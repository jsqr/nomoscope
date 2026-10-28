"""Row: one record of a table as a plain dict, the unit that readers and parsers pass around."""

from typing import Any

Row = dict[str, Any]
