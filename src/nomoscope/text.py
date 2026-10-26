"""Section text in its normalized form, which is hashed and indexed.

`normalize` converts line endings, composes Unicode (NFC), replaces curly
quotes and non-ASCII spaces, removes soft hyphens, collapses runs of
spaces and tabs, and trims each line. Other characters, such as the
section sign, stay. The number of lines does not change.
"""

import hashlib
import re
import unicodedata

QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2033": '"',
    }
)
SPACES_RE = re.compile(r"[^\S\n]+")
# An invisible hint where a word may break; never part of the word.
SOFT_HYPHEN = "\u00ad"


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(SOFT_HYPHEN, "")
    text = unicodedata.normalize("NFC", text)
    lines = (SPACES_RE.sub(" ", line).strip() for line in text.translate(QUOTES).split("\n"))
    return "\n".join(lines)


def text_sha256(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()
