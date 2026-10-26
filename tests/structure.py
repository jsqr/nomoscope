"""Grammars and line rows for the structure tests."""

from nomoscope.structure import grammar as gr


def level(**kw) -> gr.Level:
    return gr.Level(**{"kind": "subdivision", "style": "arabic", **kw})


GRAMMAR = gr.Grammar(
    skip=gr.Skip(top=70, bottom=752),
    levels=[
        level(name="chapter", kind="division", label="Chapter {n}", heading="next_line",
              sequence="increasing", signals=gr.Signals(centered=True)),
        level(name="article", kind="division", label="ARTICLE {n}", style="upper_roman",
              heading="next_line", signals=gr.Signals(centered=True)),
        level(name="section", kind="division", label="§ {chapter}-{n}.", decimal=True,
              heading="same_line", signals=gr.Signals(bold=True)),
        level(name="subsection", label="{n}.", style="upper_alpha", signals=gr.Signals(x=72)),
        level(name="paragraph", label="({n})", signals=gr.Signals(x=96)),
        level(name="subparagraph", label="({n})", style="lower_alpha", signals=gr.Signals(x=120)),
        level(name="clause", label="({n})", style="lower_roman", signals=gr.Signals(x=144)),
    ],
)  # fmt: skip

RUN_IN = gr.Grammar(
    skip=gr.Skip(top=70, bottom=752),
    levels=[
        level(name="section", kind="division", label="§ {n}.", heading="same_line"),
        level(name="subsection", label="{n}.", style="upper_alpha"),
        level(name="paragraph", label="({n})"),
    ],
    run_in=gr.RunIn(
        forms=[gr.Form(form="{lead}.", bold=True), gr.Form(form="{lead} —", caps=True)]
    ),
)

C = {"centered": True, "bold": 1.0}
B = {"bold": 1.0}


def lines(*rows) -> list[dict]:
    """Rows of (text, x0, extra features); y advances by 22 points per row, 20 rows a page."""
    out = []
    for i, (text, x0, *rest) in enumerate(rows):
        extra = rest[0] if rest else {}
        page, slot = divmod(i, 20)
        out.append(
            {"page": page, "ordinal": slot, "x0": x0, "y": 80 + 22 * slot, "size": 11.0,
             "bold": 0.0, "centered": False, "gap_before": 22.0, "page_height": 792.0,
             "block_type": None, "text": text, **extra}
        )  # fmt: skip
    return out


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in rows}
