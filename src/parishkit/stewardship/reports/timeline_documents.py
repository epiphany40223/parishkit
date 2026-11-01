"""The Timeline export's file contents, shared by CSV, XLSX and PDF.

Built by the worker from the retained capture (``timeline_exports.capture``)
inside the export's campaign read guard, and rendered by
``timeline_pdf.render_timeline``: the shared report renderer's CSV and XLSX
(``information_rendering``), and an infographic PDF of its own. The document
is metadata lines, then one row per timeline line, oldest first as the page's
tie order keeps them.

The file follows its page (UX conventions, "Downloaded files"): the table is
the page's Timeline table, with its three columns in the page's order and
words, and the Family and Summary lines are the report information (the XLSX
"Report information" sheet and the PDF's summary and code; a CSV is the table
alone), named and ordered as the page shows them. Lines that only a file
needs (report, parish, campaign, request time, time zone, line count,
privacy) come first or last. The XLSX's Timeline sheet also opens with
``title_rows`` (#1841), so the sheet itself names its Family.
The Family code is added here, decrypted under the key-set lock, only for a
requester who may see Family codes; the capture never holds it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

from parishkit.stewardship.source.source_status import (
    STATUS_HEADING,
    family_status,
    source_status_label,
)
from parishkit.stewardship.web import dates

HEADINGS = ("When", "What happened", "Details")


@dataclass(frozen=True, repr=False)
class TimelineDocument:
    """All captured values, with no live queries or mutable rows."""

    metadata: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    headings: ClassVar[tuple[str, ...]] = HEADINGS
    title: ClassVar[str] = "Timeline"
    sheet_name: ClassVar[str] = "Timeline"
    # The details the XLSX's Timeline sheet repeats above its table (#1841).
    TITLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "Family",
        "Family code",
        "Campaign",
        "Counted at",
    )

    @property
    def title_rows(self):
        """The (label, value) rows above the table: whose timeline, and when.

        The report information's own words: a Family the data no longer has
        shows its ParishSoft status in place of its blank name, as the PDF's
        heading does; a detail the document lacks is left out.
        """
        values = dict(self.metadata)
        if not values.get("Family"):
            values["Family"] = values.get(STATUS_HEADING, "")
        return tuple(
            (label, values[label]) for label in self.TITLE_FIELDS if values.get(label)
        )


def timeline_document(
    payload,
    *,
    code,
    parish_name,
    requested_at,
    timezone,
    code_hidden=False,
    campaign_name="",
):
    """The file's metadata and rows from one retained capture.

    ``code`` is the decrypted Family code, or None: ``code_hidden`` says the
    requester may not see codes (the file says it is not shown); otherwise
    the Family has none for this campaign, as the page says. Rows are
    oldest first, the page's tie order; the page lists them newest first.
    ``campaign_name`` is the request's campaign name (left out when empty).
    """
    zone = ZoneInfo(timezone)

    def instant(value):
        """A stored instant in the display time zone; unzoned is refused."""
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Timeline instants must be aware.")
        return parsed.astimezone(zone)

    family = payload["family"]
    events = payload["events"]
    summary = payload["summary"]
    email = summary["last_email"]

    def maybe(value, empty):
        """An optional instant in the display zone, or ``empty`` words."""
        return instant(value) if value else empty

    def words(value):
        """An instant inside a line of words, in the parish's date format.

        A line that combines words and a time (as the page's Summary does) is
        text, so its time is formatted here, in the style the export pinned
        (``dates.using`` around the worker's load), not left as Python's
        ISO text. A lone instant stays a value the renderer formats.
        """
        return dates.display_text(instant(value)) if value else ""

    # The page's own words and punctuation (timeline-panel.html):
    # "Yes, <first>", and for more than one, " · N times; most recently <last>".
    submitted = (
        f"Yes, {words(summary['first_submitted_at'])}"
        + (
            f" · {summary['submissions']:,} times; most recently "
            f"{words(summary['last_submitted_at'])}"
            if summary["submissions"] > 1
            else ""
        )
        if summary["submissions"]
        else "Not yet"
    )
    # The page's own lines, in its order and words: the identity line
    # (Family, any ParishSoft status, Family DUID, Envelope number), the mode
    # line (Testing only), Counted at, then the Summary panel (Submitted,
    # Last email, Family code, Reminders when the Family is in the Reminder
    # WorkGroup, Campaign email can reach this Family, Furthest step reached,
    # Last seen).
    workgroup = family.get("reminder_workgroup")
    status = source_status_label(family_status(family["name"]))
    metadata = (
        ("Report", "Timeline"),
        ("Parish", parish_name),
        *((("Campaign", campaign_name),) if campaign_name else ()),
        # A Family the data no longer has: no name, and the page's badge
        # words as a detail of their own, only when there is a mark (#1216).
        ("Family", family["name"] or ""),
        *(((STATUS_HEADING, status),) if status else ()),
        ("Family DUID", str(family["duid"])),
        (
            "Envelope number",
            str(family["envelope"]) if family["envelope"] is not None else "",
        ),
        # Only a Testing capture names its mode, as the directory exports;
        # every export since #1570 is Production and says nothing (#1841).
        *((("Mode", "Testing rehearsal"),) if payload["mode"] == "testing" else ()),
        ("Counted at", instant(payload["as_of"])),
        ("Submitted", submitted),
        (
            "Last email",
            f"{email['name']}, {words(email['at'])}: {email['outcome']}"
            if email
            else "None sent yet",
        ),
        # As the page: a Family with no code has none for this campaign; a
        # requester who may not see codes is told it is left out.
        (
            "Family code",
            code or ("Not shown" if code_hidden else "Unavailable for this campaign"),
        ),
        *(
            (
                (
                    "Reminders",
                    f"Reminders skipped: in ParishSoft WorkGroup “{workgroup}”",
                ),
            )
            if workgroup
            else ()
        ),
        ("Campaign email can reach this Family", family["reach"]),
        (
            "Furthest step reached",
            f"{summary['furthest_step']}, {words(summary['furthest_at'])}"
            if summary["furthest_step"]
            else "None recorded",
        ),
        ("Last seen", maybe(summary["last_seen_at"], "Never")),
        ("Requested at", instant(requested_at)),
        ("Display time zone", timezone),
        ("Timeline lines", f"{len(events):,}, oldest first"),
        (
            "Privacy",
            "Sensitive parish information. Share only with authorized recipients.",
        ),
    )
    rows = tuple(
        (instant(event["at"]), event["what"], event["detail"]) for event in events
    )
    return TimelineDocument(metadata, rows, len(events), requested_at)


def load_timeline(request, *, general, store):
    """The worker's document for a timeline export, the code added if allowed.

    The code is decrypted under the key-set lock, inside the caller's read
    guard, only when the requester holds ``FAMILY_CODES`` now, as the page
    shows it only to such readers.
    """
    from parishkit.stewardship.accounts.policy import (
        Capability,
        allows,
        current_principal,
    )
    from parishkit.stewardship.campaigns.credential_keys import key_set_lock
    from parishkit.stewardship.campaigns.family_identity import code_context
    from parishkit.stewardship.campaigns.models import CampaignConfiguration
    from parishkit.stewardship.storage import StorageInvariantError

    snapshot = request.timeline_snapshot
    family = snapshot.family
    code = None
    # Every requester is an Administrator or Staff (the export's SQL and
    # service admit no one else, #1799), and both hold FAMILY_CODES, so this
    # is true today; it stays so the file follows the page's own rule if a
    # role without codes may ever export.
    hidden = not allows(
        current_principal(store, request.requester_id), Capability.FAMILY_CODES
    )
    if family.code_ciphertext and not hidden:
        if general is None:
            raise StorageInvariantError("Timeline render keys are unavailable.")
        with key_set_lock(general):
            code = general.decrypt(
                family.code_ciphertext, context=code_context(family.pk)
            ).decode("ascii")
    return timeline_document(
        snapshot.document,
        code=code,
        code_hidden=hidden,
        parish_name=request.configuration.parish.name,
        # The campaign's name in the configuration the request was made under.
        campaign_name=CampaignConfiguration.objects.values_list("name", flat=True).get(
            configuration_id=request.configuration_id, record_id=request.campaign_id
        ),
        requested_at=request.created_at,
        timezone=request.browser_timezone,
    )
