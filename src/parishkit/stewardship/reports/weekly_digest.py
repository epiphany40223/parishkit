"""Detached weekly content over an already-authorized immutable observation.

Selection, interval advancement and delivery coverage belong to the durable
owner. This compiler cannot decide which requests were successfully delivered
or recapture mutable dispositions. Corrections have no text field by design.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.dates import format_date, format_local
from parishkit.stewardship.web.report_markup import STYLES, button
from parishkit.stewardship.web.weekly_digest_content import validate_weekly_body

from .links import report_url

EXCERPT_CHARACTERS = 240
# A full-width presentational table, as the report layout builds them (#720).
TABLE = ' role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'


def _style(name):
    """One compiler-owned inline style attribute from the validator's closed set."""
    return f' style="{STYLES[name]}"'


def _instant(value):
    """Require an aware observation; never infer the host or browser timezone."""
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError("Weekly digest timestamps must be timezone-aware.")


def _identity(item_id, family_duid, family_name, submitted_at):
    """Reject untyped or unbounded identity fields before formatting private data."""
    if (
        not isinstance(item_id, UUID)
        or type(family_duid) is not int
        or family_duid <= 0
        or not bounded_text(family_name).strip()
        or len(family_name.encode("utf-8")) > 512
    ):
        raise ValueError("Weekly digest requires a valid Family and item identity.")
    _instant(submitted_at)


@dataclass(frozen=True, repr=False)
class WeeklyInformation:
    """One item still actionable at capture, not a mutable follow-up work object."""

    item_id: UUID
    family_duid: int
    family_name: str
    submitted_at: datetime
    text: str

    def __post_init__(self):
        """The caller supplies original text; only its displayed excerpt is trimmed."""
        _identity(self.item_id, self.family_duid, self.family_name, self.submitted_at)
        if not bounded_text(self.text).strip():
            raise ValueError("Weekly information requires nonblank text.")


@dataclass(frozen=True, repr=False)
class WeeklyCorrection:
    """Previously mailed item now withdrawn/superseded, intentionally without text."""

    item_id: UUID
    family_duid: int
    family_name: str
    submitted_at: datetime
    disposition: str

    def __post_init__(self):
        """Only terminal actionability corrections belong in this section."""
        _identity(self.item_id, self.family_duid, self.family_name, self.submitted_at)
        if type(self.disposition) is not str or self.disposition not in {
            "superseded",
            "withdrawn",
        }:
            raise ValueError("Weekly correction requires a changed disposition.")


@dataclass(frozen=True, repr=False)
class WeeklyDigestDocument:
    """A coherent captured interval with canonical item order and campaign timezone.

    ``date_format`` is the parish date format of the configuration the
    snapshot pinned (None means the default style), so a compile retried
    after an Admin changes the format still renders as first captured.
    """

    snapshot_id: UUID
    campaign_id: UUID
    parish_name: str
    campaign_name: str
    campaign_timezone: str
    observed_at: datetime
    information: tuple[WeeklyInformation, ...]
    corrections: tuple[WeeklyCorrection, ...]
    manual: bool = False
    date_format: str | None = None

    def __post_init__(self):
        """Reject mixed/duplicate rows and future input before any email is compiled."""
        if (
            not isinstance(self.snapshot_id, UUID)
            or not isinstance(self.campaign_id, UUID)
            or type(self.manual) is not bool
            or not isinstance(self.date_format, str | None)
        ):
            raise ValueError("Weekly digest requires typed report identity.")
        _instant(self.observed_at)
        for label in (self.parish_name, self.campaign_name):
            if not bounded_text(label).strip():
                raise ValueError("Weekly digest requires nonblank report labels.")
        try:
            ZoneInfo(self.campaign_timezone)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise ValueError(
                "Weekly digest requires a valid campaign timezone."
            ) from None
        seen = set()
        for rows, kind in (
            (self.information, WeeklyInformation),
            (self.corrections, WeeklyCorrection),
        ):
            if type(rows) is not tuple or any(type(row) is not kind for row in rows):
                raise ValueError("Weekly digest requires typed immutable rows.")
            keys = tuple((row.submitted_at, row.item_id.int) for row in rows)
            if keys != tuple(sorted(keys)):
                raise ValueError("Weekly digest rows must be in canonical order.")
            for row in rows:
                if row.item_id in seen or row.submitted_at > self.observed_at:
                    raise ValueError(
                        "Weekly digest contains duplicate or future input."
                    )
                seen.add(row.item_id)

    @property
    def report_path(self):
        """This opaque selection still requires current portal authorization."""
        # Its Emailed reports address (NAV-12); emails sent before link the
        # old one, which redirects here. Imported here so the document
        # module itself stays free of Django.
        from django.urls import reverse

        return reverse("admin:weekly_digest_snapshot", args=[self.snapshot_id])

    @property
    def empty(self):
        """The durable owner records empty coverage instead of creating a message."""
        return not self.information and not self.corrections


@dataclass(frozen=True, repr=False)
class WeeklyDigestContent:
    """No-image compiled email content, never arbitrary attachments or templates."""

    subject: str
    html: str
    text: str


def shorten(text):
    """Collapse display whitespace and shorten long text at a word boundary.

    Returns the display text and whether it was shortened. Shortened text
    ends with an ellipsis, so a reader can see it is not the whole request;
    complete text never gets one.
    """
    result = " ".join(text.split())
    if len(result) <= EXCERPT_CHARACTERS:
        return result, False
    cut = result[: EXCERPT_CHARACTERS - 1]
    # Break between words; one unbroken word longer than the limit is cut.
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:.-–—") + "…", True


def excerpt(text):
    """The shortened display text alone (see ``shorten``)."""
    return shorten(text)[0]


def render_weekly_digest(document, *, public_origin):
    """Escape every Family value and retain every selected row, or fail explicitly."""
    if not isinstance(document, WeeklyDigestDocument):
        raise TypeError("Weekly digest rendering requires an immutable document.")
    if document.empty:
        raise ValueError("An empty weekly interval must not create an email.")
    # Pin the snapshot configuration's style, not the worker's active one.
    with dates.using(document.date_format):
        return _render_weekly_digest(document, public_origin=public_origin)


def _render_weekly_digest(document, *, public_origin):
    """Compile the email body; the caller has pinned the parish date format."""
    zone = ZoneInfo(document.campaign_timezone)
    observed = document.observed_at.astimezone(zone)
    title = (
        "Manual weekly" if document.manual else "Weekly"
    ) + f" information digest — {format_date(observed.date())}"
    url = report_url(public_origin, document.report_path)
    # The Administrator asked (#720) for the email to open straight into the
    # numbered requests: the subject names the parish, campaign, report and
    # capture date (see render_digest_envelope), so the body repeats none of
    # them and has no explanatory small print. Each section's heading
    # carries its count; rows give submitted times without a zone.
    new = "current" if document.manual else "new"
    sections = (
        (
            f"{len(document.information):,} {new} actionable request"
            + ("" if len(document.information) == 1 else "s"),
            document.information,
        ),
        (
            f"{len(document.corrections):,} correction"
            + ("" if len(document.corrections) == 1 else "s")
            + " to previously reported requests",
            document.corrections,
        ),
    )
    text = ""

    def plain(value):
        """Escape a text node; canonical HTML leaves quotes literal there."""
        return escape(value, quote=False)

    html = ""
    for heading, rows in sections:
        if not rows:
            continue
        html += f"<h2{_style('heading')}>{heading}</h2>"
        html += f"<table{TABLE}{_style('table')}><tbody>"
        text += ("\n\n" if text else "") + heading
        numbered = rows is document.information
        for number, row in enumerate(rows, start=1):
            # Imported names can contain NBSP or CR; collapse display-only
            # whitespace so the strict HTML serializer agrees.
            family = " ".join(
                f"{row.family_name} — Family DUID {row.family_duid}".split()
            )
            submitted = "Submitted " + format_local(
                row.submitted_at.astimezone(zone), compact=True
            )
            if isinstance(row, WeeklyInformation):
                detail, shortened = shorten(row.text)
            else:
                detail = (
                    f"{row.disposition.title()}: the previously reported request "
                    "is no longer actionable."
                )
                shortened = False
            item_url = escape(url + f"items/{row.item_id}/", quote=True)
            link = f'<a href="{item_url}"{_style("link")} rel="noopener noreferrer">'
            # Numbers are table cells, not list markers, so every mail program
            # shows the same stable 1, 2, 3 a reader can refer back to.
            label = f"{number}." if numbered else ""
            html += (
                f'<tr><td width="4%"{_style("number")}>{label}</td>'
                f'<td width="32%"{_style("who")}>{link}{plain(family)}</a><br>'
                f"{plain(submitted)}</td>"
                f'<td width="64%"{_style("excerpt")}>{plain(detail)}'
                + (f" {link}Read the full request</a>" if shortened else "")
                + "</td></tr>"
            )
            prefix = f"{number}. " if numbered else ""
            text += f"\n\n{prefix}{family}. {submitted}\n{detail}"
            link_label = "Read the full request: " if shortened else ""
            text += "\n" + link_label + url + f"items/{row.item_id}/"
        html += "</tbody></table>"
    html += button(url, "Open protected report")
    text += "\n\nOpen protected report (staff login required): " + url
    validate_weekly_body(html, text)
    return WeeklyDigestContent(title, html, text)
