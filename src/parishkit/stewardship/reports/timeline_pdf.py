"""The Family timeline PDF: an infographic sheet on the shared page frame (#926).

The CSV and XLSX of a timeline export are the shared information files
(``information_rendering``); only the PDF is drawn here, because one
Family's story reads better as a picture than as a stack of record cards.
Every page carries the shared frame (``pdf_design.PdfFrame``) with one line
under the title naming the Family, so a loose page still says whose it is.
The first page then shows:

- summary cards: one per Summary line of the page, in its order and words,
  the Family code's card set apart in a warning tint with its notice;
- one count tile per kind of timeline line, each with its marker shape, so
  the legend and the counts are the same row;

and then the vertical timeline, oldest first (the file's order), grouped
under day headings: each line's time, a marker on the spine, What happened
in bold and its Details beside it. A page that starts in the middle of a day
repeats that day's heading marked "(continued)".

As every Admin PDF, nothing is cut: values wrap in full and characters the
fonts cannot draw become visible escapes (``pdf_design.safe``). Pages are
counted, then drawn one at a time, from the same detached document.
"""

import re
from itertools import chain
from typing import NamedTuple

from parishkit.stewardship.web import dates

from . import pdf_design as design
from .pdf_design import (
    ACCENT,
    ACCENT_SOFT,
    ACCENT_STRONG,
    BODY_SIZE,
    BODY_WIDTH,
    BORDER,
    GOLD,
    INK,
    LABEL_SIZE,
    LINE_PITCH,
    MARGIN,
    MUTED,
    PAGE_WIDTH,
    PAPER_SOFT,
    SMALL_PITCH,
    WARNING_INK,
    safe,
    wrap_text,
)

WARNING_BG = design.COLORS["warning-bg"]


class Kind(NamedTuple):
    """How one kind of timeline line is marked: words, marker shape and colors.

    The shape and the words tell the kinds apart (never color alone); the
    color only echoes the portal's palette. ``fill`` is the marker's face,
    or None for a solid marker in ``color``.
    """

    label: str
    shape: str
    color: str
    fill: str | None = None


# In the order the count tiles show them. "other" is only for a line whose
# words this module does not know (a future kind of line), so a new line
# never breaks the file; a test keeps every line the page builds known.
KINDS = {
    "visit": Kind("Sign-ins and form opens", "o", ACCENT, "white"),
    "email": Kind("Emails", "s", ACCENT_STRONG),
    "skipped": Kind("Emails not sent", "X", WARNING_INK),
    "progress": Kind("Form progress", "^", ACCENT),
    "submitted": Kind("Submissions", "D", ACCENT_STRONG, GOLD),
    "other": Kind("Other lines", "P", MUTED),
}
# The words of each kind's lines (family_timeline.events and email_name).
# The capture keeps only English words, so a translated page would need kinds.
VISITS = frozenset({"Signed in", "Staff opened the form", "Opened the form"})
PROGRESS = frozenset({"Got past the first step", "Reached the furthest step so far"})
SUBMITTED = frozenset({"Submitted a response"})
EMAILS = frozenset(
    {"Invitation", "Reminder", "Submission receipt", "Chosen-Family test email"}
)
NUMBERED_REMINDER = re.compile(r"Reminder \d+")


def kind_of(what):
    """The ``KINDS`` key for one line, from its What happened words."""
    what = str(what)
    if what in VISITS:
        return "visit"
    if what in PROGRESS:
        return "progress"
    if what in SUBMITTED:
        return "submitted"
    if what.endswith(" not sent"):
        return "skipped"
    if what in EMAILS or NUMBERED_REMINDER.fullmatch(what):
        return "email"
    return "other"


# Report details drawn somewhere other than a summary card: the frame (title,
# parish, privacy line, request time and its zone), the line under the title
# (the Family, its DUID and envelope number, the mode) and the timeline
# heading. The Family code is a card of its own look (``code_card``).
FRAME_FIELDS = frozenset({"Report", "Parish", "Privacy", "Requested at"})
IDENTITY_FIELDS = ("Family", "Family DUID", "Envelope number", "Mode")
CODE_FIELD = "Family code"
LINES_FIELD = "Timeline lines"
NOT_CARDS = FRAME_FIELDS | design.HIDDEN_FIELDS | set(IDENTITY_FIELDS) | {LINES_FIELD}
# Words a value of the code field may hold instead of a code.
NO_CODE = frozenset({"Not shown", "Unavailable for this campaign"})
CODE_NOTICE = (
    "Sensitive: this code signs in to this Family's form. Share it only "
    "with the Family or authorized recipients."
)

# Layout, in points.
GAP = 8.0
PAD = 7.0
SECTION_GAP = 12.0
CARD_COLUMNS = 4
CARD_WIDTH = (BODY_WIDTH - (CARD_COLUMNS - 1) * GAP) / CARD_COLUMNS
CODE_SIZE = 14.0
CODE_PITCH = 18.0
NOTICE_SIZE = 7.0
NOTICE_PITCH = 9.0
TILE_HEIGHT = 40.0
TILE_SIZE = 15.0
HEADING_SIZE = 11.5
HEADING_HEIGHT = 22.0
DAY_HEIGHT = 20.0
# The timeline's columns: the time (right-aligned to TIME_RIGHT), the
# spine with its markers, What happened, then Details.
TIME_RIGHT = MARGIN + 92.0
SPINE_X = MARGIN + 108.0
MARKER_SIZE = 7.0
WHAT_X = MARGIN + 124.0
WHAT_WIDTH = 230.0
DETAIL_X = WHAT_X + WHAT_WIDTH + 12.0
DETAIL_WIDTH = PAGE_WIDTH - MARGIN - DETAIL_X
ENTRY_GAP = 5.0
COLUMNS_HEIGHT = 16.0
CONTINUED = " (continued)"
# Blocks never left alone at the bottom of a page.
KEEP_WITH_NEXT = frozenset({"heading", "columns", "day"})


class Block(NamedTuple):
    """One piece of the body, placed whole on a page.

    ``kind`` names its drawing (``cards``, ``note``, ``tiles``, ``columns``,
    ``heading``, ``day``, ``entry`` or ``empty``), ``height`` includes the
    space after it, and ``data`` is what the drawing needs. An entry's
    ``day`` is its day heading's words, repeated when a page starts mid-day.
    """

    kind: str
    height: float
    data: object
    day: str = ""


def identity_line(metadata):
    """The line under every page's title: Family, DUID, envelope and mode."""
    values = {label: value for label, value in metadata if label in IDENTITY_FIELDS}
    parts = [
        str(values.get("Family", "")),
        f"Family DUID {values['Family DUID']}" if values.get("Family DUID") else "",
        (
            f"Envelope number {values['Envelope number']}"
            if values.get("Envelope number")
            else ""
        ),
        str(values.get("Mode", "")),
    ]
    return " · ".join(part for part in parts if part)


class Card(NamedTuple):
    """One summary card: a label over its value, wrapped to the card width.

    ``face``, ``size`` and ``pitch`` set the value's type. ``code`` marks
    the Family code card, drawn in a warning tint with ``notice`` lines.
    """

    labels: list
    values: list
    face: str = "regular"
    size: float = BODY_SIZE
    pitch: float = LINE_PITCH
    notice: tuple = ()
    code: bool = False

    @property
    def height(self):
        """The card's height: top bar, padding, label, value and notice lines."""
        notice = len(self.notice) * NOTICE_PITCH + (3 if self.notice else 0)
        labels = len(self.labels) * SMALL_PITCH
        return 3 + PAD + labels + len(self.values) * self.pitch + notice + PAD


def card(label, value):
    """One Summary line's card, in the page's words."""
    width = CARD_WIDTH - 2 * PAD
    return Card(
        wrap_text(safe(label), width, LABEL_SIZE, "bold"),
        wrap_text(safe(value), width, BODY_SIZE),
    )


def code_card(label, value):
    """The Family code card: set apart, the code large, with its notice.

    A code is set in the monospace face (when that face can draw every
    character) so look-alike characters are easy to tell apart. The words
    for no code ("Not shown", "Unavailable for this campaign") are plain
    text, and need no notice.
    """
    width = CARD_WIDTH - 2 * PAD
    labels = wrap_text(safe(label), width, LABEL_SIZE, "bold")
    text = safe(value)
    if not value or value in NO_CODE:
        return Card(labels, wrap_text(text, width, BODY_SIZE), code=True)
    mono = design.charmap("mono")
    face = "mono" if all(ord(c) in mono for c in text) else "bold"
    return Card(
        labels,
        wrap_text(text, width, CODE_SIZE, face),
        face,
        CODE_SIZE,
        CODE_PITCH,
        tuple(wrap_text(CODE_NOTICE, width, NOTICE_SIZE, "bold")),
        True,
    )


def summary_blocks(document):
    """Page one's blocks: summary cards, any escape note, tiles and heading.

    The cards follow the page's Summary order, the Family code among them.
    """
    metadata = document.metadata
    cards = [
        (code_card if label == CODE_FIELD else card)(label, value)
        for label, value in metadata
        if label not in NOT_CARDS
    ]
    for start in range(0, len(cards), CARD_COLUMNS):
        row = cards[start : start + CARD_COLUMNS]
        yield Block("cards", max(item.height for item in row) + GAP, row)
    if design.needs_escape(
        chain((value for _, value in metadata), chain.from_iterable(document.rows))
    ):
        from .information_rendering import FORMAT_NOTE

        lines = wrap_text(f"{design.FORMAT_LABEL}: {FORMAT_NOTE}", BODY_WIDTH, 7.5)
        yield Block("note", len(lines) * SMALL_PITCH + GAP, lines)
    counts = dict.fromkeys(KINDS, 0)
    for _, what, _ in document.rows:
        counts[kind_of(what)] += 1
    tiles = [
        (kind, count) for kind, count in counts.items() if kind != "other" or count
    ]
    yield Block("tiles", TILE_HEIGHT + SECTION_GAP, tiles)
    lines = design.detail(metadata, LINES_FIELD)
    heading = f"Timeline · {lines}" if lines else "Timeline"
    yield Block("heading", HEADING_HEIGHT, safe(heading))
    if document.rows:
        yield column_block(document.headings)


def column_block(headings):
    """The timeline's column headings, the page's own words (When, ...)."""
    return Block("columns", COLUMNS_HEIGHT, tuple(map(safe, headings)))


def _day(at):
    """A line's day heading in the parish date format."""
    return safe(dates.format_date(at.date()))


def _clock(at):
    """A line's time of day with its zone abbreviation (the day is above it)."""
    return safe(f"{dates.format_time(at)} {at.tzname()}")


def timeline_blocks(document):
    """Every body block in order: the summary, then day headings and lines."""
    yield from summary_blocks(document)
    if not document.rows:
        yield Block("empty", LINE_PITCH + GAP, "No timeline lines.")
        return
    day = None
    for at, what, detail in document.rows:
        if _day(at) != day:
            day = _day(at)
            yield Block("day", DAY_HEIGHT, day)
        whats = wrap_text(safe(what), WHAT_WIDTH, BODY_SIZE, "bold")
        details = wrap_text(safe(detail), DETAIL_WIDTH, BODY_SIZE) if detail else []
        height = max(len(whats), len(details)) * LINE_PITCH + ENTRY_GAP
        yield Block("entry", height, (_clock(at), kind_of(what), whats, details), day)


def timeline_pages(blocks, frame):
    """Place blocks on pages; each page is a list of blocks.

    A block moves whole to the next page. Headings (the section heading,
    the column headings and a day heading) are kept with the line after
    them. Every later page of the timeline repeats the column headings, and
    one that starts in the middle of a day opens with that day's heading
    marked "(continued)". The blocks are bounded (system words and one
    Family's summary), so none is taller than a page.
    """
    room = frame.body_height
    page, used, held, columns = [], 0.0, [], None
    for block in blocks:
        if block.kind == "columns":
            columns = block
        if block.kind in KEEP_WITH_NEXT:
            held.append(block)
            continue
        group, held = [*held, block], []
        if page and used + sum(item.height for item in group) > room:
            yield page
            page, used = [], 0.0
            if block.kind == "entry":
                if group[0].kind == "entry":
                    group.insert(0, Block("day", DAY_HEIGHT, block.day + CONTINUED))
                if group[0].kind == "day":
                    group.insert(0, columns)
        page.extend(group)
        used += sum(item.height for item in group)
    # Only a malformed sequence ends on a heading; draw it rather than lose it.
    yield page + held


def draw_page(canvas, page, frame):
    """Draw one page of blocks, top to bottom."""
    y = frame.body_top
    for block in page:
        DRAW[block.kind](canvas, block, y)
        y += block.height


def _draw_cards(canvas, block, top):
    """A row of summary cards: label over value under a colored top bar.

    The Family code card is tinted and barred in the warning colors, so it
    stands apart in print and grayscale as well as by its notice's words.
    """
    height = block.height - GAP
    for column, item in enumerate(block.data):
        x = MARGIN + column * (CARD_WIDTH + GAP)
        fill, bar = (WARNING_BG, GOLD) if item.code else (PAPER_SOFT, ACCENT)
        canvas.rect(x, top, CARD_WIDTH, height, fill, BORDER)
        canvas.rect(x, top, CARD_WIDTH, 3, bar)
        y = top + 3 + PAD
        for line in item.labels:
            canvas.text(x + PAD, y + SMALL_PITCH - 3, line, LABEL_SIZE, "bold", MUTED)
            y += SMALL_PITCH
        for line in item.values:
            canvas.text(x + PAD, y + item.pitch - 3, line, item.size, item.face, INK)
            y += item.pitch
        y += 3 if item.notice else 0
        for line in item.notice:
            canvas.text(
                x + PAD, y + NOTICE_PITCH - 2, line, NOTICE_SIZE, "bold", WARNING_INK
            )
            y += NOTICE_PITCH


def _draw_note(canvas, block, top):
    """The escape note: why some characters print as \\uXXXX."""
    for number, line in enumerate(block.data):
        canvas.text(
            MARGIN, top + SMALL_PITCH * (number + 1) - 3, line, 7.5, "regular", MUTED
        )


def _draw_tiles(canvas, block, top):
    """One tile per kind: its marker, its count and its words."""
    count = len(block.data)
    width = (BODY_WIDTH - (count - 1) * GAP) / count
    for column, (key, number) in enumerate(block.data):
        kind = KINDS[key]
        x = MARGIN + column * (width + GAP)
        canvas.rect(x, top, width, TILE_HEIGHT, "white", BORDER)
        canvas.marker(x + 14, top + 15, kind.shape, kind.color, 9, fill=kind.fill)
        figure = f"{number:,}"
        canvas.text(x + 26, top + 20, figure, TILE_SIZE, "bold", ACCENT_STRONG)
        label = safe(kind.label)
        canvas.text(
            x + 8,
            top + 33,
            label,
            design.fit_size(label, LABEL_SIZE, width - 16, "regular"),
            "regular",
            MUTED,
        )


def _draw_heading(canvas, block, top):
    """The timeline's section heading over an accent rule."""
    canvas.text(MARGIN, top + 13, block.data, HEADING_SIZE, "bold", ACCENT_STRONG)
    canvas.hline(MARGIN, PAGE_WIDTH - MARGIN, top + 18, ACCENT, 1.0)


def _draw_columns(canvas, block, top):
    """The column headings over the timeline's columns, above a hairline."""
    when, what, details = block.data
    baseline = top + COLUMNS_HEIGHT - 5
    canvas.text(TIME_RIGHT, baseline, when, LABEL_SIZE, "bold", MUTED, ha="right")
    canvas.text(WHAT_X, baseline, what, LABEL_SIZE, "bold", MUTED)
    canvas.text(DETAIL_X, baseline, details, LABEL_SIZE, "bold", MUTED)
    canvas.hline(MARGIN, PAGE_WIDTH - MARGIN, top + COLUMNS_HEIGHT - 1, BORDER, 0.75)


def _draw_day(canvas, block, top):
    """A day heading: a tinted band across the timeline."""
    canvas.rect(MARGIN, top + 3, BODY_WIDTH, DAY_HEIGHT - 6, ACCENT_SOFT)
    canvas.text(
        MARGIN + 6, top + DAY_HEIGHT - 7, block.data, LABEL_SIZE, "bold", ACCENT_STRONG
    )


def _draw_entry(canvas, block, top):
    """One timeline line: time, spine and marker, What happened and Details."""
    clock, key, whats, details = block.data
    kind = KINDS[key]
    baseline = top + LINE_PITCH - 3
    canvas.rect(SPINE_X - 0.75, top, 1.5, block.height, BORDER)
    canvas.text(TIME_RIGHT, baseline, clock, BODY_SIZE, "regular", MUTED, ha="right")
    canvas.marker(
        SPINE_X, baseline - 3, kind.shape, kind.color, MARKER_SIZE, fill=kind.fill
    )
    for number, line in enumerate(whats):
        canvas.text(
            WHAT_X, baseline + number * LINE_PITCH, line, BODY_SIZE, "bold", INK
        )
    for number, line in enumerate(details):
        canvas.text(
            DETAIL_X, baseline + number * LINE_PITCH, line, BODY_SIZE, "regular", INK
        )


def _draw_empty(canvas, block, top):
    """The words for a timeline with no lines."""
    canvas.text(MARGIN, top + LINE_PITCH, block.data, BODY_SIZE, "regular", MUTED)


DRAW = {
    "cards": _draw_cards,
    "note": _draw_note,
    "tiles": _draw_tiles,
    "heading": _draw_heading,
    "columns": _draw_columns,
    "day": _draw_day,
    "entry": _draw_entry,
    "empty": _draw_empty,
}


def timeline_frame(document):
    """The shared frame, with the Family's identity line under the title."""
    line = identity_line(document.metadata)
    return design.PdfFrame.for_document(document, details=(line,) if line else ())


def timeline_pdf(document, output):
    """Count the pages, then draw them one at a time; return the page count."""
    frame = timeline_frame(document)
    count = sum(1 for _ in timeline_pages(timeline_blocks(document), frame))
    return frame.write(
        output,
        timeline_pages(timeline_blocks(document), frame),
        count,
        lambda canvas, page: draw_page(canvas, page, frame),
        requested_at=document.requested_at,
    )


def render_timeline(document, output, *, format):
    """The timeline export's file: the shared CSV and XLSX, or this PDF."""
    from .information_rendering import render_information

    if format == "pdf":
        return timeline_pdf(document, output)
    return render_information(document, output, format=format)
