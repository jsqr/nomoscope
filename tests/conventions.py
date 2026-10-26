"""Drafting conventions for the tests, as config files.

ca, tx, and il are ashokan's California, Texas, and Illinois conventions.
fake is for the fake source. write() puts the files in a config root.
CONVENTIONS is the same files, parsed.
"""

import tomllib

from nomoscope.derive import conventions

FILES = {
    "fake": r"""
[history]
format = "numeric_mdy"
which = "last"
enacted = '\bpassed {NUMERIC_DATE}'
effective = '\beff\. {NUMERIC_DATE}'
repeal = '\bRepealed by Ord\. [\w-]+, eff\. {NUMERIC_DATE}'

[levels]
words = ["subsection", "paragraph", "subparagraph"]
""",
    "ca": r"""
[history]
format = "month_day_year"
which = "first"
note = 'Note:'
effective = '\bEffective {MONTH_DATE}'
operative = '\b[Oo]perative {MONTH_DATE}'
inoperative = '\b[Ii]noperative (?:on )?{MONTH_DATE}'
repeal = '\bRepealed as of {MONTH_DATE}'

[levels]
words = ["subdivision", "paragraph", "subparagraph", "clause", "subclause"]
""",
    "tx": r"""
[history]
format = "month_day_year"
which = "last"
effective = '\beff\. {MONTH_DATE}'
inoperative = '\bexpires {MONTH_DATE}'
repeal = '\bRepealed by .{0,200}?eff\. {MONTH_DATE}'

[levels]
words = [
  "subsection", "subdivision", "paragraph", "subparagraph",
  "sub-subparagraph", "sub-sub-subparagraph", "sub-sub-sub-subparagraph",
]
indent = [7, 6]
""",
    "il": r"""
[history]
format = "numeric_mdy"
which = "last"
effective = '\beff\. {NUMERIC_DATE}'
repeal = '\brepealed by P\.A\. [\d-]+, eff\. {NUMERIC_DATE}'

[levels]
words = ["subsection", "paragraph", "subparagraph", "item"]
""",
}

CONVENTIONS = conventions.parse({name: tomllib.loads(text) for name, text in FILES.items()})


def write(directory) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in FILES.items():
        (directory / f"{name}.toml").write_text(text)
