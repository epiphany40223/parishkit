"""The Compared with past campaigns block (report-emails spec).

The Daily digest's Compared with past campaigns section (#1018 slice 7,
#1698), drawn after the closing button with its own link. The section
document, its markup and its chart images stay in ``digest_comparison``,
which the saved digest page shares; this module places them in the email.
With no section (no imported year, or the schedule has it off) the block
is left out entirely rather than drawing its empty statement.
"""

from parishkit.email.base import InlineImage

from ..daily_digest import comparison_url
from ..digest_comparison import HEADING, render_images, section_html, section_text
from .registry import AFTER_BUTTON, STATUS, Block, Section


def draw(document, frame, options):
    """The section and its server-drawn charts, or nothing without a section."""
    section = document.comparison
    if section is None:
        return Section()
    link = comparison_url(section, frame.public_origin)
    images = tuple(InlineImage(data, name) for name, data in render_images(section))
    return Section(section_html(section, link), section_text(section, link), images)


def capture(document, options):
    """The section's retained figures, or None when it is left out."""
    return document.comparison


def copy(data, frame):
    """The retained section as the saved daily report page draws it (#1018)."""
    from ..digest_comparison import data_line
    from ..digest_presentation import comparison_path, saved_tiles

    return {
        "comparison": data,
        "comparison_line": data_line(data),
        "comparison_tiles": saved_tiles(data),
        "comparison_url": comparison_path(data),
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
