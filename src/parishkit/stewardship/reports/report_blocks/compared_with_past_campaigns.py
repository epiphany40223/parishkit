"""The Compared with past campaigns block (report-emails spec).

The Daily digest's Compared with past campaigns section (#1018 slice 7,
#1698), drawn after the closing button with its own link. The section
document, its markup and its chart images stay in ``digest_comparison``,
which the saved digest page shares; this module places them in the email.
With no section (no imported year, or the schedule has it off) the block
is left out entirely rather than drawing its empty statement. It counts
the historical cohort, like the archives it compares with, while the rest of
a report email counts today's campaign Families (#2331), so a fixed line
under it says which Families it counts.
"""

from parishkit.email.base import InlineImage

from ..daily_digest import comparison_url
from ..digest_comparison import HEADING, render_images, section_html, section_text
from ..report_document import ReportDocument
from .markup import plain, style
from .registry import AFTER_BUTTON, STATUS, Block, Section

# The fixed line naming the Families the section counts (#2331), in the
# report pages' Families counted words (#2324).
FAMILIES_COUNTED = (
    "Families counted: Including Families that left, as the past campaigns' "
    "archives count them"
)


def draw(document, frame, options):
    """The section, its Families counted line and its server-drawn charts.

    Nothing without a section.
    """
    section = document.comparison
    if section is None:
        return Section()
    link = comparison_url(section, frame.public_origin)
    images = tuple(InlineImage(data, name) for name, data in render_images(section))
    html, text = section_html(section, link), section_text(section, link)
    # A retired digest's document drew no such line (and counted the
    # historical cohort throughout).
    if isinstance(document, ReportDocument):
        html += f"<p{style('caption')}>{plain(FAMILIES_COUNTED)}</p>"
        text += "\n" + FAMILIES_COUNTED
    return Section(html, text, images)


def capture(document, options):
    """The section's retained figures, or None when it is left out.

    A report email's section keeps the Families counted line its email
    drew (#2331), so its saved copy shows the line only for a send that
    had it; a send captured before keeps no line and its copy shows none.
    """
    section = document.comparison
    if section is None or not isinstance(document, ReportDocument):
        return section
    return {**section, "families_counted": FAMILIES_COUNTED}


def copy(data, frame):
    """The retained section as the saved daily report page draws it (#1018).

    Its Families counted line is the one the send kept (none before #2331).
    """
    from ..digest_comparison import data_line
    from ..digest_presentation import comparison_path, saved_tiles

    return {
        "comparison": data,
        "comparison_line": data_line(data),
        "comparison_tiles": saved_tiles(data),
        "comparison_url": comparison_path(data),
        "comparison_population": data.get("families_counted", ""),
    }


BLOCK = Block(
    key="compared_with_past_campaigns",
    label=HEADING,
    help=(
        "This campaign beside last year and the average of the imported past "
        "campaigns, on the same campaign day."
    ),
    kind=STATUS,
    fact=True,
    draw=draw,
    capture=capture,
    copy=copy,
    placement=AFTER_BUTTON,
)
