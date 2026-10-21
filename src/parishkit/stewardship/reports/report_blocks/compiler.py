"""The one compiler of every report email (report-emails spec, "Compiling a report").

It draws a report's blocks in the shared Administrator report layout (#720):
the opening line when a block reads the pinned daily facts, the blocks in
order, the closing button, then any block placed after the button. The
Testing heading, the Administrator's intro and the subject's completion are
the envelope's (``jobs.digest_content.render_digest_envelope``), which every
report email already shares.

The Daily and Weekly digests are drawn through it (``daily_digest`` and
``weekly_digest`` name their blocks), byte for byte as before the blocks
existed; ``test_report_blocks`` gates that against frozen copies of the old
compilers.
"""

from dataclasses import dataclass
from html import escape

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.report_markup import button

from ..daily_digest import DASHBOARD_LABEL, response_dashboard_url
from ..links import report_url
from . import REGISTRY
from .markup import TABLE, plain, style
from .registry import AFTER_BUTTON, BODY, Frame

# The closing button of a report without a fact block: its saved copy.
COPY_LABEL = "Open protected report"


@dataclass(frozen=True, repr=False)
class CompiledReport:
    """A report email's subject line, HTML and text, and its inline images.

    ``subject`` is the report's own title and date, which the envelope
    completes; ``images`` are the blocks' ``InlineImage`` values in block
    order. Like the digest contents, it has no recipients or file paths.
    """

    subject: str
    html: str
    text: str
    images: tuple = ()


def report_blocks(keys):
    """The registered blocks for ``keys``, each at most once, in report order."""
    if len(set(keys)) != len(keys):
        raise ValueError("A report lists each block at most once.")
    return tuple(REGISTRY[key] for key in keys)


def opening_html(opening, url):
    """The opening line with the dashboard link right-aligned beside it (#1532).

    Prominent (#1016): body size and colour, not the muted caption style.
    The two-cell presentation table stands in for floats and flexbox, which
    are unreliable in Outlook and Gmail. The link cell is 30% wide,
    top-aligned and wraps into short lines, so the sentence keeps most of a
    phone's width.
    """
    return (
        f"<table{TABLE}{style('track')}><tbody><tr>"
        f"<td{style('opening')}>{plain(opening)}</td>"
        f'<td width="30%" align="right"{style("opening-link")}>'
        f'<a href="{escape(url, quote=True)}"{style("link")} '
        f'rel="noopener noreferrer">{plain(DASHBOARD_LABEL)}</a></td>'
        "</tr></tbody></table>"
    )


def compile_report(document, keys, *, public_origin, copy_path=None, options=None):
    """Draw ``document`` as the report email made of the blocks ``keys``.

    ``document`` is the report's immutable source (today a Daily or Weekly
    digest document), which supplies its ``title``, ``date_format`` and,
    with a fact block, its ``opening`` line and ``mode``. ``copy_path`` is
    the send's saved copy, which the closing button of a report without a
    fact block links. ``options`` maps a block key to option values over its
    defaults. Everything runs in the date format the source pinned, not
    whichever the worker has active.
    """
    blocks = report_blocks(keys)
    facts = any(block.fact for block in blocks)
    with dates.using(document.date_format):
        frame = Frame(
            public_origin,
            dashboard_url=(
                response_dashboard_url(public_origin, document.mode) if facts else None
            ),
            copy_url=report_url(public_origin, copy_path) if copy_path else None,
        )
        html, text, images = [], [], []

        def add(html_part, text_part, image_parts=()):
            """Append one drawn part; an empty text part adds no blank line."""
            html.append(html_part)
            if text_part:
                text.append(text_part)
            images.extend(image_parts)

        def draw(placement):
            """Draw the report's blocks placed at ``placement``, in order."""
            for block in blocks:
                if block.placement == placement:
                    chosen = block.options | (options or {}).get(block.key, {})
                    section = block.draw(document, frame, chosen)
                    add(section.html, section.text, section.images)

        url = frame.dashboard_url
        # The spec also opens a report as of the end of the previous day
        # with this line; reports gain that as-of choice with the report
        # pipeline (delivery plan item 3), and today only the Daily has it.
        if facts:
            # Say each fact once (#720): the opening line carries the
            # report-day definition (#721) and is the only place that names
            # the time zone; whitespace collapses as the strict HTML
            # boundary requires.
            opening = " ".join(document.opening.split())
            link_line = f"{DASHBOARD_LABEL} (staff login required): {url}"
            add(opening_html(opening, url), f"{opening}\n{link_line}")
        draw(BODY)
        if facts:
            add(button(url, DASHBOARD_LABEL), link_line)
        else:
            if frame.copy_url is None:
                raise ValueError("A report without a fact block links its saved copy.")
            add(
                button(frame.copy_url, COPY_LABEL),
                f"{COPY_LABEL} (staff login required): {frame.copy_url}",
            )
        draw(AFTER_BUTTON)
        return CompiledReport(
            document.title, "".join(html), "\n\n".join(text), tuple(images)
        )
