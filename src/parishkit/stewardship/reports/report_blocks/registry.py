"""The block contract and registry of the report emails (#1786).

A report email is a list of blocks (report-emails spec, "Block registry").
Each block module declares one ``Block``; ``REGISTRY`` (in the package's
``__init__``) maps the stable keys to them, and the compiler
(``compiler.py``) reads only the registry. Adding
a block needs its module, its spec section and its tests, never a schema
change.

This first slice holds what the compiler needs to draw today's Daily and
Weekly digests: the declarations and the email renderer. The snapshot
capture, saved copy renderer, editor form, Testing behaviour declaration
and preview flag arrive with the report pipeline, the Report emails page
and Preview (report-emails spec, "Delivery plan"); today each block's
Testing behaviour lives in its renderer.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# A list block reports records that can run out; a status block reports
# where the campaign stands and always draws something (spec, "Block
# contract"). Every block counts for "send only when there is content"
# (decision 16), so a report is skipped as empty only when no block has
# content. A status block always has content, even when it draws its empty
# statement; the one exception is Compared with past campaigns when it is
# left out for want of imported years.
LIST, STATUS = "list", "status"
# Where the compiler places a block: in the body, or after the closing
# button (Compared with past campaigns, which carries its own link).
BODY, AFTER_BUTTON = "body", "after_button"


@dataclass(frozen=True)
class Section:
    """One block's drawing: its HTML, its text part and its inline images.

    An empty ``html`` and ``text`` leave the block out of the email; the
    compiler joins the text parts of the drawn blocks with a blank line.
    ``images`` are ``InlineImage`` values in the order the HTML cites them.
    """

    html: str = ""
    text: str = ""
    images: tuple = ()


@dataclass(frozen=True)
class Frame:
    """What the compiler hands every block besides the report's document.

    ``public_origin`` is the portal's public origin; ``dashboard_url`` the
    Response dashboard's address (None in a report with no fact block);
    ``copy_url`` the send's saved copy (None when the report has none).
    """

    public_origin: str
    dashboard_url: str | None = None
    copy_url: str | None = None


@dataclass(frozen=True)
class Block:
    """One block's declaration (report-emails spec, "Block contract").

    ``draw(document, frame, options)`` returns the block's ``Section`` and
    runs inside the compiler's pinned date format. ``options`` are the
    block's defaults; ``empty`` is the one line it will draw when it has
    nothing to show ("" for a block the spec leaves out instead, or that
    always draws something). Nothing draws it yet: today's digests never
    send an empty block.
    """

    key: str
    label: str
    help: str
    kind: str
    fact: bool
    draw: Callable[[Any, Frame, dict], Section]
    empty: str = ""
    placement: str = BODY
    reader_role: str = "staff"
    options: dict = field(default_factory=dict)

    def __post_init__(self):
        """Reject a declaration outside the contract's closed values."""
        if (
            self.kind not in {LIST, STATUS}
            or self.placement not in {BODY, AFTER_BUTTON}
            or self.reader_role != "staff"
        ):
            raise ValueError(f"Block {self.key!r} breaks the block contract.")
