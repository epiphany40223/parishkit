"""The block contract and registry of the report emails (#1786).

A report email is a list of blocks (report-emails spec, "Block registry").
Each block module declares one ``Block``; ``REGISTRY`` (in the package's
``__init__``) maps the stable keys to them, and the compiler
(``compiler.py``) reads only the registry. Adding
a block needs its module, its spec section and its tests, never a schema
change.

It holds what the compiler needs to draw today's Daily and Weekly digests
and the report pipeline's snapshots: the declarations, the email renderer,
the option check, the snapshot capture and the saved copy renderer (#1946),
which draws the block on its send's saved copy page from the data the
snapshot kept. The editor form, Testing behaviour declaration and preview
flag arrive with the editor and Preview (report-emails spec, "Delivery
plan"); today each block's Testing behaviour lives in its renderer.
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
    Response dashboard's address (None in a report with no opening line);
    ``copy_url`` the send's saved copy (None when the report has none);
    ``requests_url`` the address each listed request's id (and a slash)
    completes: the saved copy's own request pages, or the live requests
    in a Preview, which has no saved copy.
    """

    public_origin: str
    dashboard_url: str | None = None
    copy_url: str | None = None
    requests_url: str | None = None


@dataclass(frozen=True)
class CopyFrame:
    """What the saved copy page hands every block's saved copy renderer.

    ``send_path`` is the send's saved copy address, which a listed
    request's own page sits under. ``participation`` is the participation
    document of the fact generation the snapshot pinned (None without
    one), the one source besides the block's kept data a block may draw
    from. ``requests`` maps each listed request's id (text) to its Family's
    campaign record id for the shared Family-name link (None draws the
    name unlinked).
    """

    send_path: str
    participation: Any = None
    requests: dict = field(default_factory=dict)


def no_options(options):
    """The option check of a block that declares no options: nothing to check.

    A report's stored options hold only keys the block declares
    (``campaigns.report_values``), so a block without options always gets
    an empty mapping here.
    """


@dataclass(frozen=True)
class Block:
    """One block's declaration (report-emails spec, "Block contract").

    ``draw(document, frame, options)`` returns the block's ``Section`` and
    runs inside the compiler's pinned date format. ``options`` are the
    block's defaults; ``empty`` is the one line it will draw when it has
    nothing to show ("" for a block the spec leaves out instead, or that
    always draws something). The compiler draws it for a report email whose
    source has nothing for the block (``ReportDocument.drawable``); today's
    digests never send an empty block.

    ``capture(document, options)`` returns the block's data as the report
    snapshot keeps it (JSON values only), read from the same source the
    email is drawn from, inside the same pinned date format.

    ``copy(data, frame)`` is the saved copy renderer: from the block's kept
    ``data`` (never None: the page draws ``empty`` for that) and a
    ``CopyFrame`` it returns the context of the block's template,
    ``components/report-copy/<key>.html`` (``copy_template``), or None when
    the kept data drew nothing in the email, so the page draws nothing too.

    ``check(options)`` raises ``ValueError`` for an option value the block
    cannot draw; a report's stored options are checked with it on save.
    ``version`` is the version of the block's captured data that a report
    stores and a snapshot keeps beside it, so a saved copy drawn later
    knows how its data was captured.
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
    check: Callable[[dict], None] = no_options
    version: int = 1
    capture: Callable[[Any, dict], Any] = None
    copy: Callable[[Any, CopyFrame], dict | None] = None

    @property
    def copy_template(self):
        """The template that draws the block on its send's saved copy page."""
        return f"stewardship/components/report-copy/{self.key}.html"

    def has_content(self, data):
        """Whether captured ``data`` counts as content (spec, decision 16).

        A list block has content when it lists an item; a status block
        always has, even drawing its empty statement, except a block left
        out entirely (Compared with past campaigns without imported years),
        whose data is None.
        """
        if self.kind == LIST:
            return bool(data and data["items"])
        return data is not None or self.empty != ""

    def __post_init__(self):
        """Reject a declaration outside the contract's closed values."""
        if (
            self.kind not in {LIST, STATUS}
            or self.placement not in {BODY, AFTER_BUTTON}
            or self.reader_role != "staff"
        ):
            raise ValueError(f"Block {self.key!r} breaks the block contract.")
