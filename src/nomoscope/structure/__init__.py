"""Parse a printed code into divisions and subdivisions.

`pdf` and `lines` read a document as lines with layout features. A
`grammar` names the code's levels and label forms. `parse` builds the
tree, `checks` reports problems in it, and `sections` turns it into
carved rows.

Lines, bookmarks, nodes, and findings are dicts (`Row`).
"""

from typing import Any

Row = dict[str, Any]

# Bump when the same lines and grammar give a different parse or section text.
VERSION = "2"
