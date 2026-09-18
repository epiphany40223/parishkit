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

from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.weekly_digest_content import validate_weekly_body

from .links import report_url

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
    """A coherent captured interval with canonical item order and campaign timezone."""

    snapshot_id: UUID
    campaign_id: UUID
    parish_name: str
    campaign_name: str
    campaign_timezone: str
    observed_at: datetime
    information: tuple[WeeklyInformation, ...]
    corrections: tuple[WeeklyCorrection, ...]
    manual: bool = False

    def __post_init__(self):
        """Reject mixed/duplicate rows and future input before any email is compiled."""
        if (
            not isinstance(self.snapshot_id, UUID)
            or not isinstance(self.campaign_id, UUID)
            or type(self.manual) is not bool
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
        return f"/admin/reports/weekly-digests/{self.snapshot_id}/"

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


def excerpt(text):
    """Collapse display whitespace and visibly shorten only the email quotation."""
    result = " ".join(text.split())
    return (
        result
        if len(result) <= EXCERPT_CHARACTERS
        else result[: EXCERPT_CHARACTERS - 1] + "…"
    )


def render_weekly_digest(document, *, public_origin):
    """Escape every Family value and retain every selected row, or fail explicitly."""
    if not isinstance(document, WeeklyDigestDocument):
        raise TypeError("Weekly digest rendering requires an immutable document.")
    if document.empty:
        raise ValueError("An empty weekly interval must not create an email.")
    zone = ZoneInfo(document.campaign_timezone)
    observed = document.observed_at.astimezone(zone)
    title = (
        "Manual weekly" if document.manual else "Weekly"
    ) + f" information digest — {observed.date().isoformat()}"
    url = report_url(public_origin, document.report_path)
    information_label = (
        "Current actionable requests" if document.manual else "New actionable requests"
    )
    labels = (
        document.parish_name,
        document.campaign_name,
        title,
        f"Captured {observed.isoformat()} ({observed.tzname()}); "
        + f"campaign timezone {document.campaign_timezone}.",
        f"{information_label}: {len(document.information):,}. "
        + f"Corrections: {len(document.corrections):,}.",
        "Text below is an excerpt. Open the protected report for full details "
        + "and current request status; emailed content reflects capture time.",
    )
    # Imported/authored names can contain NBSP or CR. Collapse display-only
    # whitespace before escaping so the strict HTML serializer agrees; retain
    # the original identities unchanged in the immutable document/snapshot.
    labels = tuple(" ".join(label.split()) for label in labels)
    # Quotes in text nodes are inert and the canonical serializer leaves them
    # literal. Attribute values below still escape quotes separately.
    html = "".join("<p>" + escape(label, quote=False) + "</p>" for label in labels)
    text = "\n".join(labels)
    for heading, rows in (
        (information_label, document.information),
        ("Corrections to previously reported requests", document.corrections),
    ):
        if not rows:
            continue
        html += "<h2>" + heading + "</h2><ul>"
        text += "\n\n" + heading
        for row in rows:
            family_name = " ".join(row.family_name.split())
            identity = (
                f"{family_name} — Family DUID {row.family_duid:,}; submitted "
                + row.submitted_at.astimezone(zone).isoformat()
            )
            detail = (
                excerpt(row.text)
                if isinstance(row, WeeklyInformation)
                else f"{row.disposition.title()}: the previously reported request "
                + "is no longer actionable."
            )
            item_url = url + f"items/{row.item_id}/"
            html += (
                '<li><p><a href="'
                + escape(item_url, quote=True)
                + '" rel="noopener noreferrer">'
                + escape(identity, quote=False)
                + "</a><br>"
                + escape(detail, quote=False)
                + "</p></li>"
            )
            text += "\n\n" + identity + "\n" + detail + "\n" + item_url
        html += "</ul>"
    html += (
        '<p><a href="'
        + escape(url, quote=True)
        + '" rel="noopener noreferrer">Open protected report '
        + "(staff login required)</a></p>"
    )
    text += "\n\nOpen protected report (staff login required): " + url
    validate_weekly_body(html, text)
    return WeeklyDigestContent(title, html, text)
