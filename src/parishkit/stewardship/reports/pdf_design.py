"""The shared look of every Admin portal PDF: page frame, type, tables, cards.

Every report PDF is drawn with matplotlib (already the export renderer) on a
landscape US Letter page, in points measured from the page's top-left corner.
Each page carries the same frame: the parish name and report title with the
capture time in the header, and the privacy line, any Testing-mode note and
"Page N of M" in the footer. Inside the frame a report draws one of three
bodies: a table (``table_pages``/``draw_table``), field/value record cards
(``record_pages``/``draw_record_page``, via ``write_records``) or a grid of
address cards (``card_pages``/``draw_cards``).

Text is set in the DejaVu fonts matplotlib bundles, so no font is installed
or downloaded. Wrapping measures each glyph's advance in those fonts and is
lossless: every character of every value is drawn, nothing is elided, and
characters the fonts cannot show become visible escapes (``visible_text``).
The palette is the Admin portal's (``ui-v1.css``): dark ink on white or
very light tints, so pages print cleanly in grayscale.
"""

import re
from dataclasses import dataclass, replace
from functools import cache, cached_property
from itertools import chain, zip_longest
from pathlib import Path
from typing import NamedTuple

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.design_tokens import COLORS

# Admin portal colors (design_tokens, ui-v1.css). Text colors meet WCAG AA
# on white and on the light fills; the fills print light in grayscale.
INK = COLORS["ink"]
MUTED = COLORS["muted"]
ACCENT = COLORS["accent"]
ACCENT_STRONG = COLORS["accent-strong"]
ACCENT_SOFT = COLORS["accent-soft"]
PAPER_SOFT = COLORS["paper-soft"]
BORDER = COLORS["border"]
GOLD = COLORS["gold"]
WARNING_INK = COLORS["warning-ink"]

# Landscape US Letter, in points.
PAGE_WIDTH, PAGE_HEIGHT = 792.0, 612.0
MARGIN = 36.0
BODY_WIDTH = PAGE_WIDTH - 2 * MARGIN

# Type scale (points).
TITLE_SIZE = 17.0
EYEBROW_SIZE = 7.5
EYEBROW_PITCH = 9.0
DETAIL_SIZE = 8.0
BODY_SIZE = 8.75
LABEL_SIZE = 8.0
FOOTER_SIZE = 7.5
# Baseline-to-baseline distance for body text and for the smaller lines.
LINE_PITCH = 11.5
SMALL_PITCH = 10.0

FONT_FILES = {
    "regular": "DejaVuSans.ttf",
    "bold": "DejaVuSans-Bold.ttf",
    "serif": "DejaVuSerif-Bold.ttf",
}


@cache
def font_path(face):
    """The bundled matplotlib font file for a face name in ``FONT_FILES``."""
    from matplotlib import get_data_path

    return str(Path(get_data_path()) / "fonts/ttf" / FONT_FILES[face])


@cache
def _font(face):
    """A FreeType face at 1 pt per em, for glyph advances and coverage."""
    from matplotlib.ft2font import FT2Font

    font = FT2Font(font_path(face))
    # At 72 dpi one pixel is one point, so advances come out in points per
    # point of type size.
    font.set_size(100, 72)
    return font


@cache
def _charmap(face):
    """The code points one face can draw."""
    return frozenset(_font(face).get_charmap())


@cache
def pdf_glyphs():
    """Characters both body faces can draw; anything else is escaped.

    Values are drawn in the regular and the bold face (a bold label, a bold
    addressee), so a character must be in both to be safe everywhere.
    """
    return _charmap("regular") & _charmap("bold")


_ADVANCES = {face: {} for face in FONT_FILES}


def text_width(text, size, face="regular"):
    """The drawn width of ``text`` in points, from the font's glyph advances.

    Kerning is ignored; it only ever narrows DejaVu text, so a measured line
    never overflows its column.
    """
    advances = _ADVANCES[face]
    total = 0.0
    for character in text:
        advance = advances.get(character)
        if advance is None:
            font = _font(face)
            advance = font.load_char(ord(character)).linearHoriAdvance / 65536 / 100
            advances[character] = advance
        total += advance
    return total * size


_TOKENS = re.compile(r"\s+|\S+")


def wrap_text(text, width, size, face="regular"):
    """Wrap text to ``width`` points without losing a single character.

    Newlines start new lines (blank lines stay blank); tabs expand. Words
    move whole to the next line; a word wider than the column breaks
    between characters. Whitespace is kept (a run at a line end simply
    hangs invisibly), so each paragraph's lines rejoin to exactly its text.
    """
    lines = []
    for paragraph in text.expandtabs().split("\n"):
        line, used = "", 0.0
        for token in _TOKENS.findall(paragraph):
            token_width = text_width(token, size, face)
            if used + token_width <= width or token.isspace():
                line, used = line + token, used + token_width
                continue
            if line and token_width <= width:
                lines.append(line)
                line, used = token, token_width
                continue
            # Too wide for any line: break it between characters.
            for character in token:
                advance = text_width(character, size, face)
                if line and used + advance > width:
                    lines.append(line)
                    line, used = "", 0.0
                line, used = line + character, used + advance
        lines.append(line)
    return lines


def safe(value):
    """Report text for a value: parish date format, unsupported glyphs escaped."""
    from .information_rendering import visible_text

    return visible_text(dates.display_text(value), supported=pdf_glyphs())


def detail(metadata, key, default=""):
    """One report detail from a document's ``metadata`` pairs, or ``default``."""
    return next((value for name, value in metadata if name == key), default)


def _stamp_key(metadata):
    """The detail the header's right-hand time line states: capture, else request."""
    return next(
        (name for name in ("Captured at", "Requested at") if detail(metadata, name)),
        None,
    )


@dataclass(frozen=True)
class PdfFrame:
    """The header and footer every page of one report shares.

    ``eyebrow`` is the small line above the title (the parish and campaign,
    wrapped when long), ``stamp`` the right-aligned capture line with the
    display time zone, ``details`` the short summary lines under
    the title (counts, filters), ``notice`` the privacy line and ``note`` an
    optional Testing-mode line. All are plain values; ``safe`` escapes them.
    """

    title: str
    eyebrow: str = ""
    stamp: str = ""
    details: tuple[str, ...] = ()
    notice: str = ""
    note: str = ""

    @classmethod
    def for_document(cls, document, *, details=(), stamp_key=None):
        """The frame for a report document with the usual metadata pairs."""
        metadata = document.metadata
        key = stamp_key or _stamp_key(metadata)
        zone = detail(metadata, "Display timezone")
        stamp = (
            f"{key.removesuffix(' at')} {dates.display_text(detail(metadata, key))}"
            + (f" ({zone})" if zone else "")
            if key
            else ""
        )
        campaign = detail(metadata, "Campaign")
        return cls(
            title=document.title,
            eyebrow=" · ".join(
                filter(None, (detail(metadata, "Parish"), campaign and str(campaign)))
            ),
            stamp=stamp,
            details=tuple(details),
            notice=detail(metadata, "Privacy"),
            note=detail(metadata, "Testing mode"),
        )

    def _title_face(self):
        """The serif title face when it can draw every character, else sans bold."""
        serif = _charmap("serif")
        return "serif" if all(ord(c) in serif for c in safe(self.title)) else "bold"

    @cached_property
    def eyebrow_lines(self):
        """The wrapped parish and campaign line (uppercased before escaping)."""
        if not self.eyebrow:
            return []
        return wrap_text(safe(self.eyebrow.upper()), BODY_WIDTH, EYEBROW_SIZE, "bold")

    @property
    def _shift(self):
        """How far extra eyebrow lines push the title and details down."""
        return EYEBROW_PITCH * max(len(self.eyebrow_lines) - 1, 0)

    @cached_property
    def header_lines(self):
        """The wrapped detail lines under the title (all of them, never cut)."""
        return [
            line
            for text in self.details
            for line in wrap_text(safe(text), BODY_WIDTH, DETAIL_SIZE)
        ]

    @cached_property
    def footer_lines(self):
        """The wrapped privacy and Testing-mode lines (privacy first)."""
        width = BODY_WIDTH - 90  # leave room for "Page N of M"
        return [
            *(
                ("notice", line)
                for line in (
                    wrap_text(safe(self.notice), width - 10, FOOTER_SIZE, "bold")
                    if self.notice
                    else ()
                )
            ),
            *(
                ("note", line)
                for line in (
                    wrap_text(safe(self.note), width, FOOTER_SIZE) if self.note else ()
                )
            ),
        ]

    @property
    def body_top(self):
        """Where the body starts: below the title, details and header rule."""
        return 74.0 + self._shift + SMALL_PITCH * len(self.header_lines) + 12.0

    @property
    def body_bottom(self):
        """Where the body must end: above the footer rule and its lines."""
        return PAGE_HEIGHT - 26.0 - SMALL_PITCH * max(len(self.footer_lines), 1) - 8.0

    @property
    def body_height(self):
        """The usable body height of every page, in points."""
        return self.body_bottom - self.body_top

    def draw(self, canvas, number, count):
        """Draw the header band and the footer of one page."""
        for index, line in enumerate(self.eyebrow_lines):
            y = 34 + EYEBROW_PITCH * index
            canvas.text(MARGIN, y, line, EYEBROW_SIZE, "bold", MUTED)
        title_y = 58 + self._shift
        canvas.text(
            MARGIN,
            title_y,
            safe(self.title),
            TITLE_SIZE,
            self._title_face(),
            ACCENT_STRONG,
        )
        if self.stamp:
            canvas.text(
                PAGE_WIDTH - MARGIN,
                title_y,
                safe(self.stamp),
                DETAIL_SIZE,
                "regular",
                MUTED,
                ha="right",
            )
        y = 74.0 + self._shift
        for line in self.header_lines:
            canvas.text(MARGIN, y, line, DETAIL_SIZE, "regular", INK)
            y += SMALL_PITCH
        canvas.hline(MARGIN, PAGE_WIDTH - MARGIN, y - 4, ACCENT, 1.5)
        # Footer: a hairline, then the privacy and Testing-mode lines with the
        # page position on the right of the first line.
        top = self.body_bottom + 8.0
        canvas.hline(MARGIN, PAGE_WIDTH - MARGIN, top, BORDER, 0.75)
        y = top + 13.0
        canvas.text(
            PAGE_WIDTH - MARGIN,
            y,
            f"Page {number:,} of {count:,}",
            DETAIL_SIZE,
            "bold",
            MUTED,
            ha="right",
        )
        for kind, line in self.footer_lines:
            if kind == "notice":
                # A small gold marker plus the words: never color alone.
                canvas.rect(MARGIN, y - 6.5, 5, 5, GOLD)
                canvas.text(MARGIN + 10, y, line, FOOTER_SIZE, "bold", WARNING_INK)
            else:
                canvas.text(MARGIN, y, line, FOOTER_SIZE, "regular", MUTED)
            y += SMALL_PITCH

    def write(self, output, pages, page_count, draw_body, *, requested_at=None):
        """Draw each already paginated page inside this frame; return the count.

        ``pages`` may be a generator: only one page is held at a time, and
        each figure is released after it is saved.
        """
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib.figure import Figure

        from .charts import rendering_style

        with (
            rendering_style(),
            PdfPages(
                output,
                metadata={
                    "Title": safe(self.title),
                    "CreationDate": requested_at,
                    "ModDate": requested_at,
                },
            ) as pdf,
        ):
            for number, page in enumerate(pages, 1):
                figure = Figure(
                    figsize=(PAGE_WIDTH / 72, PAGE_HEIGHT / 72), facecolor="white"
                )
                try:
                    canvas = Canvas(figure)
                    self.draw(canvas, number, page_count)
                    draw_body(canvas, page)
                    pdf.savefig(figure)
                finally:
                    figure.clear()
        return page_count


class Canvas:
    """Draw on a matplotlib figure in points from the page's top-left corner."""

    def __init__(self, figure):
        """Wrap one page figure."""
        self.figure = figure

    def text(self, x, y, text, size, face="regular", color=INK, *, ha="left"):
        """Draw one line of text with its baseline at ``y``."""
        if text:
            self.figure.text(
                x / PAGE_WIDTH,
                1 - y / PAGE_HEIGHT,
                text,
                fontproperties=_properties(face, size),
                color=color,
                ha=ha,
                va="baseline",
            )

    def rect(self, x, y, width, height, color, edge=None):
        """Fill a rectangle with its top-left corner at (x, y); ``edge`` outlines it."""
        from matplotlib.patches import Rectangle

        self.figure.add_artist(
            Rectangle(
                (x / PAGE_WIDTH, 1 - (y + height) / PAGE_HEIGHT),
                width / PAGE_WIDTH,
                height / PAGE_HEIGHT,
                transform=self.figure.transFigure,
                facecolor=color,
                edgecolor=edge or "none",
                linewidth=0.75 if edge else 0,
            )
        )

    def hline(self, x0, x1, y, color, width):
        """Draw a horizontal rule ``width`` points thick."""
        from matplotlib.lines import Line2D

        self.figure.add_artist(
            Line2D(
                (x0 / PAGE_WIDTH, x1 / PAGE_WIDTH),
                (1 - y / PAGE_HEIGHT,) * 2,
                transform=self.figure.transFigure,
                color=color,
                linewidth=width,
                solid_capstyle="butt",
            )
        )


@cache
def _properties(face, size):
    """One shared FontProperties per face and size (matplotlib copies it)."""
    from matplotlib.font_manager import FontProperties

    return FontProperties(fname=font_path(face), size=size)


# --- Tables ---------------------------------------------------------------

CELL_PAD = 5.0
ROW_PAD = 3.5


@dataclass(frozen=True)
class Table:
    """Column headings and widths (points) for one table PDF."""

    headings: tuple[str, ...]
    widths: tuple[float, ...]

    @classmethod
    def weighted(cls, headings, weights):
        """Share the body width among the columns in proportion to ``weights``."""
        total = sum(weights)
        return cls(tuple(headings), tuple(BODY_WIDTH * w / total for w in weights))

    def cells(self, values, size=BODY_SIZE, face="regular"):
        """Each value wrapped to its column's text width."""
        return tuple(
            wrap_text(safe(value), width - 2 * CELL_PAD, size, face)
            for value, width in zip(values, self.widths, strict=True)
        )

    @property
    def heading_cells(self):
        """The wrapped, bold column headings."""
        return self.cells(self.headings, LABEL_SIZE, "bold")

    @property
    def heading_height(self):
        """The repeated heading row's height."""
        return _row_height(self.heading_cells, SMALL_PITCH)


def _row_height(cells, pitch=LINE_PITCH):
    """A row tall enough for its tallest cell, with padding above and below."""
    return max(len(cell) for cell in cells) * pitch + 2 * ROW_PAD


def table_pages(table, rows, frame):
    """Paginate rows (lists of values) under a repeated heading row.

    A row moves whole to the next page; a row taller than a whole page
    (a Family with very many phone numbers) splits between lines rather
    than drawing past the footer. Each page is a list of (index, cells).
    """
    room = frame.body_height - table.heading_height
    most = max(int((room - 2 * ROW_PAD) // LINE_PITCH), 1)
    page, used = [], 0.0
    for index, values in enumerate(rows):
        cells = table.cells(values)
        tall = max(len(cell) for cell in cells)
        for start in range(0, max(tall, 1), most):
            piece = tuple(cell[start : start + most] or [""] for cell in cells)
            height = _row_height(piece)
            if page and used + height > room:
                yield page
                page, used = [], 0.0
            page.append((index, piece))
            used += height
    yield page


def draw_table(table, frame):
    """A ``draw_body`` callable that draws one page of ``table_pages``."""

    def draw(canvas, page):
        """Heading row, then zebra-striped rows of wrapped cells."""
        y = frame.body_top
        _draw_row(canvas, table, y, table.heading_cells, ACCENT_SOFT, heading=True)
        y += table.heading_height
        canvas.hline(MARGIN, PAGE_WIDTH - MARGIN, y, ACCENT, 1.0)
        for index, cells in page:
            fill = PAPER_SOFT if index % 2 else None
            y += _draw_row(canvas, table, y, cells, fill)
        if page:
            canvas.hline(MARGIN, PAGE_WIDTH - MARGIN, y, BORDER, 0.75)
        else:
            canvas.text(
                MARGIN + CELL_PAD, y + 16, "No rows match.", BODY_SIZE, "regular", MUTED
            )

    return draw


def _draw_row(canvas, table, y, cells, fill, *, heading=False):
    """Draw one row at ``y`` and return its height."""
    pitch = SMALL_PITCH if heading else LINE_PITCH
    height = _row_height(cells, pitch)
    if fill:
        canvas.rect(MARGIN, y, BODY_WIDTH, height, fill)
    x = MARGIN
    for cell, width in zip(cells, table.widths, strict=True):
        for number, line in enumerate(cell):
            canvas.text(
                x + CELL_PAD,
                y + ROW_PAD + pitch * (number + 1) - 3,
                line,
                LABEL_SIZE if heading else BODY_SIZE,
                "bold" if heading else "regular",
                ACCENT_STRONG if heading else INK,
            )
        x += width
    return height


# --- Record cards ---------------------------------------------------------
#
# Field/value reports (additional information, financial, Ministry, Family
# test names, packets) print one card per record. The PDF is the readable
# view: CSV and XLSX stay the complete audit files, so a card leaves out the
# internal references and blank fields a reader cannot use, titles itself
# with the record's name, DUID and status, and nests workflow history under
# its item (``row_records``). The report-information card leads with count
# tiles and states only what the page header and footer do not
# (``report_record``). Every value a card does show is drawn in full.

# Fields a PDF card never shows: internal references, row versions and
# notes for software, not readers. CSV and XLSX keep every one of them.
HIDDEN_FIELDS = frozenset(
    {
        "Row type",
        "Campaign reference",
        "Source reference",
        "Source generation",
        "Version",
        "Response reference",
        "Proposed Member reference",
        "Email revision",
        "Family version",
        "Digest resolution",
        "Display timezone",
        "Campaign date-filter timezone",
    }
)
# Report details the page frame already shows on every page (the parish and
# campaign line, the title, the privacy and Testing-mode footer lines).
FRAME_FIELDS = frozenset({"Report", "Parish", "Campaign", "Privacy", "Testing mode"})
# A card's title is its name field, then that record's DUID, then its status.
NAME_FIELDS = ("Family", "Member", "Ministry", "Family name")
TAG_FIELDS = ("Disposition", "State", "Status", "Family status", "Activity")
# A nested workflow revision is titled by when and by whom it changed; the
# item fields its spreadsheet row repeats (to identify the item without an
# internal reference) are already on the card above it.
CHANGE_FIELDS = ("Changed at", "Changed by")
REPEATED_FIELDS = frozenset(
    {"Submitted", "Replaced by request submitted", "Submitted text"}
)
FORMAT_LABEL = "Text representation"

LABEL_WIDTH = 125.0
CARD_INSET = 12.0
INDENT = 16.0
VALUE_X = MARGIN + CARD_INSET + LABEL_WIDTH
VALUE_WIDTH = PAGE_WIDTH - MARGIN - CARD_INSET - VALUE_X
# The right half of a line holding two short fields.
RIGHT_LABEL_X = PAGE_WIDTH / 2 + CARD_INSET / 2
RIGHT_VALUE_X = RIGHT_LABEL_X + LABEL_WIDTH
HALF_VALUE_WIDTH = min(
    RIGHT_LABEL_X - CARD_INSET - VALUE_X,
    PAGE_WIDTH - MARGIN - CARD_INSET - RIGHT_VALUE_X,
)
CARD_TITLE_SIZE = 10.0
TITLE_PITCH = 14.0
TITLE_PAD = 5.0
BODY_PAD = 5.0
FIELD_GAP = 3.0
CARD_SPACING = 10.0
SUB_LEAD = 7.0
WRITE_LEAD = 5.0
STAT_COLUMNS = 4
STAT_GAP = 8.0
STATS_HEIGHT = 46.0
STAT_SIZE = 15.0
CONTINUED_TITLE = " (continued)"


@dataclass(frozen=True)
class Record:
    """One printed card.

    ``title`` and ``tag`` (a status chip) head the card; ``stats`` are count
    tiles; ``fields`` are (label, value) pairs; ``children`` are nested
    (title, fields) blocks such as workflow revisions. ``style`` is
    ``"info"`` for a report-information or Ministry-details card.
    ``new_page`` starts the card on a fresh page; ``keep_blank`` prints a
    blank field as a write-in line (packets are completed by hand).
    """

    title: str = ""
    tag: str = ""
    fields: tuple = ()
    children: tuple = ()
    stats: tuple = ()
    style: str = "card"
    new_page: bool = False
    keep_blank: bool = False


class RecordLine(NamedTuple):
    """One drawn line of a card body.

    ``kind`` is ``"field"`` (label and value text), ``"write"`` (a label
    over a write-in rule), ``"sub"`` (a nested block's heading) or
    ``"stats"`` (a row of count tiles in ``text``). ``label`` is blank on a
    field's continuation lines. ``lead`` is space above the text and
    ``height`` the whole line, so pagination and drawing agree exactly.
    ``right`` is a second (kind, label, text) field set beside this one in
    the card's right half.
    """

    kind: str
    label: str
    text: object
    depth: int = 0
    lead: float = 0.0
    height: float = LINE_PITCH
    right: tuple | None = None


class Piece(NamedTuple):
    """The part of one card drawn on one page."""

    record: Record
    titles: tuple[str, ...]
    lines: tuple[RecordLine, ...]
    chip: str


def _blank(value):
    """A field with nothing in it (absent money is blank; zero is not)."""
    return value is None or value == ""


@cache
def _plain_characters():
    """Characters a PDF draws as themselves: no escape, no doubled backslash.

    The mirror of ``visible_text`` with the PDF's glyph set: format and
    control characters are escaped even when the font maps them.
    """
    from unicodedata import category

    return frozenset(
        character
        for character in map(chr, pdf_glyphs())
        if character != "\\" and category(character) not in {"Cc", "Cf"}
    ) | {"\n"}


def needs_escape(values):
    """Whether any value would print with an escape or a doubled backslash."""
    from .information_rendering import plain

    plain_characters = _plain_characters()
    return any(
        not plain_characters.issuperset(text)
        for text in (dates.display_text(plain(value)) for value in values)
        if isinstance(text, str)
    )


def humanize_filters(value):
    """A captured filters/sort JSON object in plain words.

    Search and sort are always stated; any other filter only when it narrows
    the report (not blank and not "any"). Nested objects are flattened, and
    text that is not a JSON object is returned unchanged.
    """
    import json

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    if not isinstance(parsed, dict):
        return value

    def items(mapping):
        """Each (key, value) pair, nested objects' pairs in place."""
        for key, item in mapping.items():
            if isinstance(item, dict):
                yield from items(item)
            else:
                yield key, item

    def words(item):
        """One filter value as words."""
        if isinstance(item, list):
            return ", ".join(map(str, item)) or "none"
        if isinstance(item, bool):
            return "yes" if item else "no"
        return str(item).replace("_", " ") if item not in (None, "") else "none"

    parts = [
        f"{key.replace('_', ' ').capitalize()}: {words(item)}"
        for key, item in items(parsed)
        if key in {"search", "sort"} or item not in (None, "", "any", [])
    ]
    return " · ".join(parts) or "None"


def _count(value):
    """Whether a report detail is a count or an amount, shown as a tile.

    Counts are written with thousands separators, so a bare year such as
    "2027" is not one.
    """
    from .money import MoneyAmount

    return isinstance(value, MoneyAmount) or (
        isinstance(value, str) and re.fullmatch(r"\d{1,3}(,\d{3})*", value) is not None
    )


def report_record(metadata, *, values=(), stamp_key=None):
    """The report-information card: count tiles, then the remaining details.

    Leaves out what the page frame already shows (parish, campaign, title,
    capture time, privacy and Testing-mode lines) and the internal fields.
    The text-representation note appears only when ``values`` (every cell
    of the report) or the details need an escape.
    """
    stamp = stamp_key or _stamp_key(metadata)
    skip = HIDDEN_FIELDS | FRAME_FIELDS | {stamp}
    shown = [(label, value) for label, value in metadata if label not in skip]
    fields = [
        (label, humanize_filters(value) if label == "Filters and sort" else value)
        for label, value in shown
        if not _count(value)
    ]
    if needs_escape(chain((value for _, value in metadata), values)):
        from .information_rendering import FORMAT_NOTE

        fields.append((FORMAT_LABEL, FORMAT_NOTE))
    return Record(
        title="About this report",
        stats=tuple((label, value) for label, value in shown if _count(value)),
        fields=tuple(fields),
        style="info",
    )


def _identity(values):
    """The fields that title a record: its name, its DUID and its status.

    Each is a field name, or None when the record has no such field.
    """
    name_field = next((name for name in NAME_FIELDS if name in values), None)
    duid_field = next(
        (
            field
            for field in (f"{name_field} DUID", "Family DUID", "Member DUID")
            if field in values
        ),
        None,
    )
    tag_field = next((field for field in TAG_FIELDS if field in values), None)
    return name_field, duid_field, tag_field


def detail_record(pairs, *, style="card", new_page=False, keep_blank=False):
    """One card from (label, value) pairs: titled, internal fields left out."""
    values = dict(pairs)
    name_field, duid_field, tag_field = _identity(values)
    name = str(values.get(name_field) or "") if name_field else ""
    duid = str(values.get(duid_field) or "") if duid_field else ""
    if name and duid:
        title = f"{name} (DUID {duid})"
    else:
        title = name or (f"DUID {duid}" if duid else "")
    used = {name_field, duid_field, tag_field}
    return Record(
        title=title,
        tag=str(values.get(tag_field) or "") if tag_field else "",
        fields=tuple(
            (label, value)
            for label, value in pairs
            if label not in used and label not in HIDDEN_FIELDS
        ),
        style=style,
        new_page=new_page,
        keep_blank=keep_blank,
    )


def _revision(pairs, used):
    """A workflow revision nested under its item: (heading, fields)."""
    values = dict(pairs)
    changed = " by ".join(
        part
        for part in (
            dates.display_text(values.get("Changed at", "")),
            values.get("Changed by", ""),
        )
        if part
    )
    heading = f"Earlier workflow{f' · changed {changed}' if changed else ''}"
    skip = used | HIDDEN_FIELDS | REPEATED_FIELDS | set(CHANGE_FIELDS)
    return heading, tuple(pair for pair in pairs if pair[0] not in skip)


def row_records(headings, rows, *, keep_blank=False):
    """One card per record row, built lazily (one record held at a time).

    A row whose ``Row type`` is not ``Item`` (an earlier workflow) nests
    under the card before it, its item. A row continuing the previous record
    (the same name and DUID with a "(continued)" value, as financial share
    wording does) adds its fields to that card.
    """
    from .financial_documents import CONTINUED

    current = None
    for row in rows:
        pairs = tuple(zip(headings, row, strict=True))
        values = dict(pairs)
        record = detail_record(pairs, keep_blank=keep_blank)
        if current is not None and values.get("Row type", "Item") != "Item":
            used = set(_identity(values))
            current = replace(
                current, children=(*current.children, _revision(pairs, used))
            )
            continue
        if (
            current is not None
            and record.title == current.title
            and not record.tag
            and any(
                isinstance(value, str) and value.startswith(CONTINUED) for value in row
            )
        ):
            current = replace(current, fields=(*current.fields, *record.fields))
            continue
        if current is not None:
            yield current
        current = record
    if current is not None:
        yield current


def _field_lines(fields, depth, keep_blank):
    """Wrap each field's label and value into card lines, losslessly.

    Two neighboring fields that each fit on one line share a line, side by
    side, so short values (dates, Yes/No, amounts) do not leave most of the
    card empty; a longer field takes the full width.
    """
    from .information_rendering import plain

    label_width = LABEL_WIDTH - 8 - depth * INDENT
    cells = []
    for label, value in fields:
        blank = _blank(value)
        # Packets print a blank field as a rule to write on; other reports
        # leave it out.
        if blank and not keep_blank:
            continue
        labels = wrap_text(safe(label), label_width, LABEL_SIZE, "bold")
        text = "" if blank else safe(plain(value))
        # One line in half the width: a single label line and a value with
        # no line break (checked first: a newline has no glyph to measure).
        half = len(labels) == 1 and (
            blank
            or "\n" not in text
            and text_width(text, BODY_SIZE) <= HALF_VALUE_WIDTH
        )
        cells.append((labels, text, blank, half))
    index = 0
    while index < len(cells):
        labels, text, blank, half = cells[index]
        if half and index + 1 < len(cells) and cells[index + 1][3]:
            right_labels, right_text, right_blank, _ = cells[index + 1]
            lead = WRITE_LEAD if blank or right_blank else 0.0
            yield RecordLine(
                "write" if blank else "field",
                labels[0],
                text,
                depth,
                lead,
                lead + LINE_PITCH + FIELD_GAP,
                ("write" if right_blank else "field", right_labels[0], right_text),
            )
            index += 2
            continue
        index += 1
        if blank:
            for number, name in enumerate(labels):
                lead = WRITE_LEAD if number == 0 else 0.0
                last = number == len(labels) - 1
                yield RecordLine(
                    "write" if last else "field",
                    name,
                    "",
                    depth,
                    lead,
                    lead + LINE_PITCH + (FIELD_GAP if last else 0.0),
                )
            continue
        values = wrap_text(text, VALUE_WIDTH, BODY_SIZE)
        total = max(len(labels), len(values))
        for number, (name, line) in enumerate(
            zip_longest(labels, values, fillvalue="")
        ):
            gap = FIELD_GAP if number == total - 1 else 0.0
            yield RecordLine("field", name, line, depth, 0.0, LINE_PITCH + gap)


def record_body(record):
    """Every body line of one card, in drawing order."""
    lines = [
        RecordLine(
            "stats",
            "",
            record.stats[start : start + STAT_COLUMNS],
            0,
            0.0,
            STATS_HEIGHT,
        )
        for start in range(0, len(record.stats), STAT_COLUMNS)
    ]
    lines.extend(_field_lines(record.fields, 0, record.keep_blank))
    width = BODY_WIDTH - 2 * CARD_INSET - INDENT
    for heading, fields in record.children:
        for number, text in enumerate(
            wrap_text(safe(heading), width, LABEL_SIZE, "bold")
        ):
            lead = SUB_LEAD if not number else 0.0
            lines.append(RecordLine("sub", "", text, 1, lead, lead + LINE_PITCH))
        lines.extend(_field_lines(fields, 1, record.keep_blank))
    return tuple(lines)


def _chip(record):
    """The status chip text, or "" when the record has none or it is too wide.

    A tag wider than a third of the card joins the title instead.
    """
    tag = safe(record.tag)
    if tag and text_width(tag, LABEL_SIZE, "bold") + 12 <= BODY_WIDTH / 3:
        return tag
    return ""


def _titles(record, chip, continued, most):
    """The wrapped title lines of one card piece, at most ``most`` of them.

    A title too long for ``most`` lines (a Family name thousands of
    characters long) is cut with an ellipsis so the title band, plus at
    least one body line, always fits a page. A "(continued)" piece carries
    only the title's first line, marked as continued.
    """
    title = record.title
    if record.tag and not chip:
        title = f"{title} · {record.tag}"
    width = BODY_WIDTH - 2 * CARD_INSET
    if chip:
        width -= text_width(chip, LABEL_SIZE, "bold") + 20
    lines = wrap_text(safe(title), width, CARD_TITLE_SIZE, "bold")
    if continued:
        most = 1
    cut = len(lines) > most
    tail = ("…" if cut else "") + (CONTINUED_TITLE if continued else "")
    if not tail:
        return tuple(lines)
    lines = lines[:most]
    # Drop characters from the last kept line until the marks fit after it.
    last = lines[-1].rstrip()
    while last and text_width(last + tail, CARD_TITLE_SIZE, "bold") > width:
        last = last[:-1].rstrip()
    lines[-1] = last + tail
    return tuple(lines)


def _title_room(lines, room):
    """How many title lines fit a page body of ``room`` points with a body line.

    The band's padding, the body's padding and the record's tallest body
    line are reserved first; at least one title line is always allowed.
    """
    reserve = 2 * TITLE_PAD
    if lines:
        reserve += 2 * BODY_PAD + max(line.height for line in lines)
    return max(int((room - reserve) // TITLE_PITCH), 1)


def _title_height(titles):
    """The title band's height."""
    return len(titles) * TITLE_PITCH + 2 * TITLE_PAD if titles else 0.0


def piece_height(piece):
    """One card piece's whole height: title band, padding and lines."""
    body = sum(line.height for line in piece.lines)
    # A card with no fields (a test-names Family) is just its title band.
    return _title_height(piece.titles) + (body + 2 * BODY_PAD if piece.lines else 0)


def record_pages(records, frame):
    """Place cards on pages; each page is a list of ``Piece``.

    A card that fits the space left moves whole; one that does not starts a
    new page. Only a card taller than a whole page splits, between lines,
    and each later piece repeats its title's first line marked
    "(continued)". A title too tall for a page is cut (see ``_titles``). A
    nested heading is never left alone at the bottom of a piece.
    """
    room = frame.body_height
    page, used, yielded = [], 0.0, False
    for record in records:
        if record.new_page and page:
            yield page
            page, used, yielded = [], 0.0, True
        lines = record_body(record)
        chip = _chip(record)
        most = _title_room(lines, room)
        continued = False
        while True:
            titles = _titles(record, chip, continued, most)
            overhead = _title_height(titles) + (2 * BODY_PAD if lines else 0.0)
            spacing = CARD_SPACING if page else 0.0
            space = room - used - spacing - overhead
            whole = sum(line.height for line in lines)
            if (
                whole > space
                and page
                and (whole + overhead <= room or space < 3 * LINE_PITCH)
            ):
                yield page
                page, used, yielded = [], 0.0, True
                continue
            count, total = 0, 0.0
            for line in lines:
                if total + line.height > space and count:
                    break
                count, total = count + 1, total + line.height
            if count < len(lines) and count > 1 and lines[count - 1].kind == "sub":
                count -= 1
            piece = Piece(record, titles, lines[:count], chip)
            page.append(piece)
            used += spacing + piece_height(piece)
            lines, continued = lines[count:], True
            if not lines:
                break
            yield page
            page, used, yielded = [], 0.0, True
    if page or not yielded:
        yield page


def draw_record_page(canvas, page, frame):
    """Draw one page of card pieces, top to bottom."""
    y = frame.body_top
    for index, piece in enumerate(page):
        if index:
            y += CARD_SPACING
        y += _draw_piece(canvas, piece, y)


def _draw_piece(canvas, piece, top):
    """Draw one card piece at ``top`` and return its height."""
    info = piece.record.style == "info"
    height = piece_height(piece)
    band = _title_height(piece.titles)
    left, right = MARGIN, PAGE_WIDTH - MARGIN
    canvas.rect(left, top, BODY_WIDTH, height, ACCENT_SOFT if info else "white", BORDER)
    if band:
        canvas.rect(left, top, BODY_WIDTH, band, ACCENT if info else ACCENT_SOFT)
    canvas.rect(left, top, 3, height, ACCENT_STRONG if info else ACCENT)
    for number, title in enumerate(piece.titles):
        canvas.text(
            left + CARD_INSET,
            top + TITLE_PAD + TITLE_PITCH * (number + 1) - 3.5,
            title,
            CARD_TITLE_SIZE,
            "bold",
            "white" if info else ACCENT_STRONG,
        )
    if piece.chip:
        width = text_width(piece.chip, LABEL_SIZE, "bold") + 12
        x = right - CARD_INSET - width
        canvas.rect(x, top + TITLE_PAD + 1, width, TITLE_PITCH - 2, "white", ACCENT)
        canvas.text(
            x + 6,
            top + TITLE_PAD + TITLE_PITCH - 4.5,
            piece.chip,
            LABEL_SIZE,
            "bold",
            ACCENT_STRONG,
        )
    y = top + band + BODY_PAD
    for line in piece.lines:
        _draw_line(canvas, line, y)
        y += line.height
    return height


def _draw_line(canvas, line, top):
    """Draw one card body line whose box starts at ``top``."""
    baseline = top + line.lead + LINE_PITCH - 3
    x = MARGIN + CARD_INSET + line.depth * INDENT
    if line.kind == "stats":
        _draw_stats(canvas, line.text, top)
    elif line.kind == "sub":
        canvas.rect(MARGIN + CARD_INSET, baseline - 7, 4, 4, ACCENT)
        canvas.text(x, baseline, line.text, LABEL_SIZE, "bold", ACCENT_STRONG)
    else:
        end = PAGE_WIDTH - MARGIN - CARD_INSET
        if line.right:
            kind, label, text = line.right
            _draw_field(
                canvas, kind, label, text, RIGHT_LABEL_X, RIGHT_VALUE_X, end, baseline
            )
            end = RIGHT_LABEL_X - CARD_INSET
        _draw_field(canvas, line.kind, line.label, line.text, x, VALUE_X, end, baseline)


def _draw_field(canvas, kind, label, text, label_x, value_x, end, baseline):
    """One label and its value, or its write-in rule ending at ``end``."""
    canvas.text(label_x, baseline, label, LABEL_SIZE, "bold", MUTED)
    if kind == "write":
        canvas.hline(value_x, end, baseline + 2, MUTED, 0.6)
    else:
        canvas.text(value_x, baseline, text, BODY_SIZE, "regular", INK)


def _fit(text, size, width, face):
    """The type size (at most ``size``) at which one line of text fits ``width``."""
    measured = text_width(text, size, face)
    return size if measured <= width else size * width / measured


def _draw_stats(canvas, stats, top):
    """A row of count tiles: a large figure over its small label."""
    width = (BODY_WIDTH - 2 * CARD_INSET - (STAT_COLUMNS - 1) * STAT_GAP) / STAT_COLUMNS
    from .information_rendering import plain

    for column, (label, value) in enumerate(stats):
        x = MARGIN + CARD_INSET + column * (width + STAT_GAP)
        canvas.rect(x, top + 4, width, STATS_HEIGHT - 8, "white", BORDER)
        figure, name = safe(plain(value)), safe(label)
        canvas.text(
            x + 8,
            top + 24,
            figure,
            _fit(figure, STAT_SIZE, width - 16, "bold"),
            "bold",
            ACCENT_STRONG,
        )
        canvas.text(
            x + 8,
            top + 35,
            name,
            _fit(name, LABEL_SIZE, width - 16, "regular"),
            "regular",
            MUTED,
        )


def write_records(output, frame, records, *, requested_at=None):
    """Count, then draw, the pages of ``records()`` inside ``frame``.

    ``records`` is called once per pass and may return a generator, so only
    one card and one page are held at a time.
    """
    count = sum(1 for _ in record_pages(records(), frame))
    return frame.write(
        output,
        record_pages(records(), frame),
        count,
        lambda canvas, page: draw_record_page(canvas, page, frame),
        requested_at=requested_at,
    )


# --- Address cards --------------------------------------------------------

CARD_COLUMNS = 3
CARD_PAD = 10.0
CARD_GAP = 12.0
CARD_WIDTH = (BODY_WIDTH - CARD_GAP * (CARD_COLUMNS - 1)) / CARD_COLUMNS


class CardLine(NamedTuple):
    """One line of an address card and how it is set."""

    text: str
    face: str = "regular"
    color: str = INK
    size: float = BODY_SIZE


def card_lines(lines):
    """Wrap (text, emphasis) pairs to the card width; emphasis sets the type.

    ``"title"`` is the bold first line, ``"muted"`` a small trailing line,
    anything else ordinary text.
    """
    looks = {
        "title": ("bold", INK, BODY_SIZE),
        "muted": ("regular", MUTED, LABEL_SIZE),
        "warning": ("bold", WARNING_INK, BODY_SIZE),
    }
    result = []
    for text, emphasis in lines:
        face, color, size = looks.get(emphasis, ("regular", INK, BODY_SIZE))
        result.extend(
            CardLine(line, face, color, size)
            for line in wrap_text(safe(text), CARD_WIDTH - 2 * CARD_PAD, size, face)
        )
    return tuple(result)


def card_pages(cards, frame):
    """Place cards three across; a row is as tall as its tallest card.

    A card taller than a page splits so nothing draws past the footer.
    Each page is a list of rows, each a tuple of up to three cards.
    """
    most = max(int((frame.body_height - 2 * CARD_PAD) // LINE_PITCH), 1)
    pieces = [
        card[start : start + most]
        for card in cards
        for start in range(0, max(len(card), 1), most)
    ]
    page, used = [], 0.0
    for start in range(0, len(pieces), CARD_COLUMNS):
        row = tuple(pieces[start : start + CARD_COLUMNS])
        height = _card_height(row)
        if page and used + height > frame.body_height:
            yield page
            page, used = [], 0.0
        page.append(row)
        used += height + CARD_GAP
    yield page


def _card_height(row):
    """A card row's height: its tallest card plus padding."""
    return max(len(card) for card in row) * LINE_PITCH + 2 * CARD_PAD


def draw_cards(canvas, page, frame):
    """Draw one page of address-card rows."""
    y = frame.body_top
    if not page:
        canvas.text(MARGIN, y + 16, "No Families match.", BODY_SIZE, "regular", MUTED)
    for row in page:
        height = _card_height(row)
        for column, card in enumerate(row):
            x = MARGIN + column * (CARD_WIDTH + CARD_GAP)
            canvas.rect(x, y, CARD_WIDTH, height, PAPER_SOFT)
            canvas.rect(x, y, 3, height, ACCENT)
            for number, line in enumerate(card):
                canvas.text(
                    x + CARD_PAD,
                    y + CARD_PAD + LINE_PITCH * (number + 1) - 3,
                    line.text,
                    line.size,
                    line.face,
                    line.color,
                )
        y += height + CARD_GAP
