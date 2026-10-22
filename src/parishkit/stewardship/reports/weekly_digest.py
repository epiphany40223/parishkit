"""Detached weekly content over an already-authorized immutable observation.

Selection, interval advancement and delivery coverage belong to the durable
owner. This compiler cannot decide which requests were successfully delivered
or recapture mutable dispositions. Corrections have no text field by design.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.weekly_digest_content import validate_weekly_body

from .response_metrics import ResponseMetrics

EXCERPT_CHARACTERS = 240


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

    ``funnel`` is the response funnel at the capture instant
    (``digest_funnel``, #477), counted from durable timestamps by the
    compiling worker and again by the saved report page, so the two agree;
    ``mode`` is the report's system mode. A Testing report has no funnel and
    says where it is instead; a document without a mode shows neither.
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
    funnel: ResponseMetrics | None = None
    mode: str | None = None

    def __post_init__(self):
        """Reject mixed/duplicate rows and future input before any email is compiled."""
        if (
            not isinstance(self.snapshot_id, UUID)
            or not isinstance(self.campaign_id, UUID)
            or type(self.manual) is not bool
            or not isinstance(self.date_format, str | None)
            or not isinstance(self.funnel, ResponseMetrics | None)
            or self.mode not in {None, "production", "testing"}
            # Only a Production report counts a funnel (see digest_funnel).
            or (self.funnel is not None and self.mode != "production")
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
        # Its saved report address under Reports (NAV-12), which Report
        # emails lists (#1786); the old digest address
        # is gone, with no redirect (#864). Imported here so the document
        # module itself stays free of Django.
        from django.urls import reverse

        return reverse("admin:weekly_digest_snapshot", args=[self.snapshot_id])

    @property
    def title(self):
        """The report's name and capture date in the campaign's zone.

        For example "Weekly information digest — November 2, 2026", or
        "Manual weekly …" for a report an Administrator sent; the envelope
        completes it into the subject. The caller pins the date format.
        """
        observed = self.observed_at.astimezone(ZoneInfo(self.campaign_timezone))
        name = "Manual weekly" if self.manual else "Weekly"
        return f"{name} information digest — {format_date(observed.date())}"

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


# The Weekly digest's blocks, in its order (report-emails spec, "Block
# registry"): the requests, which the weekly report exists for, then the
# response funnel (#477), drawn with the daily report's rows and bars.
WEEKLY_BLOCKS = ("additional_information", "response_funnel")


def render_weekly_digest(document, *, public_origin):
    """Escape every Family value and retain every selected row, or fail explicitly.

    The Administrator asked (#720) for the email to open straight into the
    numbered requests: the subject names the parish, campaign, report and
    capture date (see render_digest_envelope), so the body repeats none of
    them and has no explanatory small print. It closes with the button to
    the protected saved report.
    """
    # The blocks read this module's rows, so the compiler is imported here
    # rather than at the top, where it would be circular.
    from .report_blocks.compiler import compile_report

    if not isinstance(document, WeeklyDigestDocument):
        raise TypeError("Weekly digest rendering requires an immutable document.")
    if document.empty:
        raise ValueError("An empty weekly interval must not create an email.")
    report = compile_report(
        document,
        WEEKLY_BLOCKS,
        public_origin=public_origin,
        copy_path=document.report_path,
    )
    validate_weekly_body(report.html, report.text)
    return WeeklyDigestContent(report.subject, report.html, report.text)
