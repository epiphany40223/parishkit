"""The Family timeline export's file contents, shared by CSV, XLSX and PDF.

Built by the worker from the retained capture (``timeline_exports.capture``)
inside the export's campaign read guard, and rendered by the shared report
renderer (``information_rendering.render_information``): metadata lines, then
one row per timeline line, oldest first as the page's tie order keeps them.
The Family code is added here, decrypted under the key-set lock, only for a
requester who may see Family codes; the capture never holds it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

HEADINGS = ("When", "What happened", "Details")


@dataclass(frozen=True, repr=False)
class TimelineDocument:
    """All captured values, with no live queries or mutable rows."""

    metadata: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    headings: ClassVar[tuple[str, ...]] = HEADINGS
    title: ClassVar[str] = "Family timeline"
    sheet_name: ClassVar[str] = "Timeline"


def timeline_document(
    payload, *, code, parish_name, requested_at, timezone, code_hidden=False
):
    """The file's metadata and rows from one retained capture.

    ``code`` is the decrypted Family code, or None: ``code_hidden`` says the
    requester may not see codes (the file says it is not shown); otherwise
    the Family has none for this campaign, as the page says. Rows are
    oldest first, the page's tie order; the page lists them newest first.
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

    submitted = (
        f"Yes, {maybe(summary['first_submitted_at'], '')}"
        + (
            f"; {summary['submissions']:,} times, most recently "
            f"{maybe(summary['last_submitted_at'], '')}"
            if summary["submissions"] > 1
            else ""
        )
        if summary["submissions"]
        else "Not yet"
    )
    metadata = (
        ("Report", "Family timeline"),
        ("Parish", parish_name),
        ("Family", family["name"] or "Not in the latest ParishSoft data"),
        ("ParishSoft DUID", str(family["duid"])),
        (
            "Envelope number",
            str(family["envelope"]) if family["envelope"] is not None else "",
        ),
        # As the page: a Family with no code has none for this campaign; a
        # requester who may not see codes is told it is left out.
        (
            "Family code",
            code or ("Not shown" if code_hidden else "Unavailable for this campaign"),
        ),
        ("Submitted", submitted),
        (
            "Last email",
            f"{email['name']}, {maybe(email['at'], '')}: {email['outcome']}"
            if email
            else "None sent yet",
        ),
        ("Campaign email can reach this Family", family["reach"]),
        (
            "Furthest step reached",
            f"{summary['furthest_step']}, {maybe(summary['furthest_at'], '')}"
            if summary["furthest_step"]
            else "None recorded",
        ),
        ("Last seen", maybe(summary["last_seen_at"], "Never")),
        ("Mode", "Testing" if payload["mode"] == "testing" else "Production"),
        ("Captured at", instant(payload["as_of"])),
        ("Requested at", instant(requested_at)),
        ("Display timezone", timezone),
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
    from parishkit.stewardship.storage import StorageInvariantError

    snapshot = request.timeline_snapshot
    family = snapshot.family
    code = None
    # Every requester is an Administrator (the export's SQL and service
    # admit no one else), and Administrators hold FAMILY_CODES, so this is
    # true today; it stays so the file follows the page's own rule if a role
    # without codes may ever export.
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
        requested_at=request.created_at,
        timezone=request.browser_timezone,
    )
