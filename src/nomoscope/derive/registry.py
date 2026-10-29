"""The derived datasets by name, and the kinds of block that segments are cut from.

Each dataset is built per jurisdiction. publications, sessions,
law_codes, and law_attachments are built for every jurisdiction. The law
datasets are built only for jurisdictions whose publications carry law
tables. The reference datasets are not partitioned.
"""

from dataclasses import dataclass

PUBLICATIONS = "publications"
SESSIONS = "sessions"
LAW_OBSERVATIONS = "law_observations"
LAW_VERSIONS = "law_versions"
LAW_HISTORY = "law_history"
LAW_NODES = "law_nodes"
LAW_ANCESTORS = "law_ancestors"
LAW_SUBDIVISIONS = "law_subdivisions"
SEGMENTS = "segments"
LAW_NOTES = "law_notes"
# The codes each publication holds, and its material that is not law.
LAW_CODES = "law_codes"
LAW_ATTACHMENTS = "law_attachments"
COMMON_DATASETS = (PUBLICATIONS, SESSIONS, LAW_CODES, LAW_ATTACHMENTS)
# Built only for jurisdictions whose publications carry law tables.
LAW_DATASETS = (
    LAW_OBSERVATIONS,
    LAW_VERSIONS,
    LAW_HISTORY,
    LAW_NODES,
    LAW_ANCESTORS,
    LAW_SUBDIVISIONS,
    SEGMENTS,
    LAW_NOTES,
)
DATASETS = (*COMMON_DATASETS, *LAW_DATASETS)
JURISDICTIONS = "jurisdictions"
REFERENCE_DATASETS = (JURISDICTIONS,)


@dataclass(frozen=True)
class BlockKind:
    """A kind of text block: the dataset that holds its text, and its id column."""

    kind: str
    dataset: str
    key: str


LAW_SECTION = BlockKind("law_section", LAW_VERSIONS, "id")
