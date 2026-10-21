"""Pause, Resume, Delete and Skip one send of a report email (#1786).

The Report emails page's row actions and ``pk-admin report-email
preview``/``confirm`` build the same change here (report-emails spec,
"Pause and resume", "Delete a report email", "Skip one send"): each is an
ordinary campaign configuration change to the report's ``report`` schedule
record, built, validated against the whole candidate, signed with the
campaign's work fingerprint and recorded under the work lock exactly as
every schedule change is (``admin_editing``). Nothing here takes a request
or checks authority; the callers admit first and run ``build_review``
inside their own work transaction.

- **Pause** adds an open pause interval and **Resume** closes it, each at
  the database's clock when the change is reviewed (a signed change cannot
  name the moment of its own Apply); Apply is refused once any work of the
  report changed since, so no send falls between the two unnoticed.
- **Delete** removes the record; the definition keeps its history.
- **Skip** adds one send's slot key to ``skip_dates``, with the digest
  skip's rules (``accounts.digest_skips``): only a send of the report that
  is still ahead, not skipped already and without Production work. Its
  time is signed into the review, so Apply refuses once it has passed.

While a send of the report is in progress or its result is uncertain the
work summary counts it as blocking, and the review then carries no signed
preview (the caller refuses it, naming Sent mail).
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.admin_editing import database_now, sign_preview
from parishkit.stewardship.accounts.campaign_views import _state
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.accounts.schedule_preview import fingerprint, work_summary
from parishkit.stewardship.campaigns.models import ScheduleOccurrence
from parishkit.stewardship.campaigns.report_values import PAUSE_LIMIT
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    UserFacingError,
    UserFacingStale,
    stale_page,
    unexpected_fields,
)

from .report_email_list import plan, report_records

OPERATIONS = ("pause", "resume", "delete", "skip")
# A change document names at most one entry per report, and a campaign has
# at most twenty reports (report_values.REPORT_LIMIT).
ENTRY_LIMIT = 20

ARCHIVED = _("The campaign is archived, so its report emails can no longer change.")
ALREADY_PAUSED = _("This report email is already paused.")
NOT_PAUSED = _("This report email is not paused.")
TOO_MANY_PAUSES = _(
    "This report email has been paused %(limit)s times, the most it can be. "
    "Delete it and add it again to pause it once more."
)
SKIP_PASSED = _(
    "That send of the report email is already past, so it can't be skipped."
)
SKIP_WORKED = _(
    "That send of the report email has already been prepared or sent, so it "
    "can't be skipped."
)
DOES_NOT_FIT = _("This change can't be made to the report email as it is saved now.")
HELD = _("This campaign's report emails can't be changed right now.")
BLOCKED = _(
    "A send of this report email is in progress or has an uncertain result, "
    "so it can't be changed yet."
)
BLOCKED_FIX = _("Wait for it to finish, or resolve it on the Sent mail page.")


def preview_salt(campaign_id):
    """The signing salt of one campaign's report email changes.

    Apart from the mail schedules' salt, so a report email review never
    confirms as a schedule change or the other way round.
    """
    return f"stewardship-report-emails-preview-v1:{campaign_id}"


def refuse_archived(campaign):
    """Refuse any change to an archived campaign's report emails."""
    if campaign.state == "archived":
        raise UserFacingDenied(ARCHIVED, fix=_("Nothing was changed."))


def change_state(service, campaign_id):
    """Pin the applied configuration and find the campaign a change names.

    Returns ``(state, campaign)``, ``state`` being ``campaign_views._state``.
    An unknown campaign is a ``LookupError``; one that is not current, or
    that background work holds (a mail send or Testing cleanup), is
    refused as the Dates and mail schedules page refuses it.
    """
    state = _state(service)
    configuration, held = state[0], state[3]
    campaign = next((row for row in state[1] if row.pk == campaign_id), None)
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    refuse_archived(campaign)
    if held or configuration.current_campaign_id != campaign.pk:
        raise UserFacingStale(
            HELD,
            fix=_(
                "Only the current campaign's report emails can change, and not "
                "while mail delivery or other background work is running. Try "
                "again later."
            ),
        )
    return state, campaign


def confirm_scope(service, campaign_id):
    """What a confirmation rechecks under the work lock: the version and work.

    The campaign is refused once archived, as at review.
    """
    state = _state(service)
    campaign = next((row for row in state[1] if row.pk == campaign_id), None)
    if campaign is not None:
        refuse_archived(campaign)
    return state[0], work_fingerprint(state, campaign_id, work_summary(campaign_id))


def report_work(campaign_id):
    """The campaign's report sends still being prepared, by id and phase.

    The shared work summary counts a send's occurrences and messages, which
    a send has only once its preparation has planned them; a preparation
    the scheduler has just allocated is work too, so the review's
    fingerprint names these as well.
    """
    from .report_email_models import ReportEmailPreparation

    return sorted(
        [str(identifier), phase]
        for identifier, phase in ReportEmailPreparation.objects.filter(
            campaign_id=campaign_id
        )
        .exclude(phase__in=("complete", "cancelled"))
        .values_list("id", "phase")
    )


def work_fingerprint(state, campaign_id, summary):
    """The review's binding to the campaign's scope and its report work."""
    return fingerprint(state[-1], [summary, report_work(campaign_id)])


def parse_entries(entries):
    """Check a change's entries: ``[{"operation", "id"[, "send"]}]``.

    One entry per report, each a known operation on a report id; a skip
    names one send by its slot key (an ISO date), and only a skip does.
    Returns the entries with canonical ids. Anything else is an unknown
    request (400), which only a crafted or stale request makes.
    """
    if type(entries) is not list or not 1 <= len(entries) <= ENTRY_LIMIT:
        raise unexpected_fields()
    result, seen = [], set()
    for entry in entries:
        if type(entry) is not dict or entry.get("operation") not in OPERATIONS:
            raise unexpected_fields()
        keys = {"operation", "id"} | (
            {"send"} if entry["operation"] == "skip" else set()
        )
        if set(entry) != keys:
            raise unexpected_fields()
        try:
            identifier = str(UUID(str(entry["id"])))
            send = (
                date.fromisoformat(entry["send"]).isoformat()
                if "send" in entry and type(entry["send"]) is str
                else None
            )
        except ValueError:
            raise unexpected_fields() from None
        if identifier in seen or ("send" in keys and send != entry["send"]):
            raise unexpected_fields()
        seen.add(identifier)
        result.append(
            {"operation": entry["operation"], "id": identifier}
            | ({"send": send} if send else {})
        )
    return result


def _pause(values, now):
    """The report's pause intervals with one opened at ``now``."""
    ranges = list(values["paused_ranges"])
    if ranges and ranges[-1]["end"] is None:
        raise UserFacingError(ALREADY_PAUSED, fix=_("Reload the page to see it now."))
    if len(ranges) >= PAUSE_LIMIT:
        raise UserFacingError(TOO_MANY_PAUSES % {"limit": PAUSE_LIMIT})
    return ranges + [{"start": now.isoformat(), "end": None}]


def _resume(values, now):
    """The report's pause intervals with the open one closed at ``now``."""
    ranges = list(values["paused_ranges"])
    if not ranges or ranges[-1]["end"] is not None:
        raise UserFacingError(NOT_PAUSED, fix=_("Reload the page to see it now."))
    if now <= datetime.fromisoformat(ranges[-1]["start"]):
        # Only a clock that stepped back can close a pause before it began.
        raise stale_page()
    return ranges[:-1] + [ranges[-1] | {"end": now.isoformat()}]


def skip_send(identifier, values, campaign, slot, now):
    """The report send at ``slot`` if it may be skipped; else the refusal.

    A one-time report has no single send to skip (Delete removes it), and
    a slot that is no send of the report (a paused or missing day, or
    another report's) is an unknown request (400); a slot skipped already
    is a stale page (409); a send that is past, or that has Production
    work, is refused (403). Returns the ``ScheduleSlot``.
    """
    if values["schedule"]["type"] != "repeat":
        raise unexpected_fields()
    if slot in values["skip_dates"]:
        raise stale_page()
    unskipped = values | {"skip_dates": []}
    page = plan(unskipped, campaign).page(
        through=datetime.max.replace(tzinfo=UTC),
        after=date.fromisoformat(slot) - timedelta(days=1),
        limit=8,
    )
    send = next((item for item in page.slots if item.key == slot), None)
    if send is None:
        raise unexpected_fields()
    if send.due_at <= now:
        raise UserFacingDenied(SKIP_PASSED)
    if _worked(identifier, slot):
        raise UserFacingDenied(SKIP_WORKED)
    return send


def _worked(identifier, slot):
    """Whether the send has Production work: prepared, sending, sent or failed.

    A Testing rehearsal's occurrence is practice mail, so skipping the real
    send is still allowed, as for the digests (``digest_skips._worked``).
    """
    return ScheduleOccurrence.objects.filter(
        definition_id=identifier, slot=slot, mode="production"
    ).exists()


def change_rows(entries, records, campaign, now):
    """The configuration rows and reviewed send times of a change.

    ``records`` maps each report's id to its saved values; ``campaign`` is
    the applied campaign values; ``now`` the database's clock. Returns
    ``(patch, due, changes)``: ``due`` the skipped sends' instants, which
    the review signs, and ``changes`` one plain description per entry for
    the review and the command line.
    """
    patch, due, changes = [], [], []
    stamp = now.astimezone(UTC)
    for entry in entries:
        identifier, operation = entry["id"], entry["operation"]
        values = records.get(identifier)
        if values is None:
            # No sources: the fields were allowed; the id is no report's.
            raise unexpected_fields()
        change = {"id": identifier, "operation": operation, "name": values["name"]}
        if operation == "delete":
            patch.append(
                {"operation": "remove", "section": "schedules", "id": identifier}
            )
        else:
            if operation == "pause":
                update = {"paused_ranges": _pause(values, stamp)}
            elif operation == "resume":
                update = {"paused_ranges": _resume(values, stamp)}
                # The first send it makes is signed, so Apply is refused once
                # that send's time has passed: applied later, it would send
                # late (#1464).
                first = plan(values | update, campaign).next_slot(after=now)
                if first is not None:
                    due.append(first.due_at)
                    change |= {"next_send": first.due_at}
            else:
                send = skip_send(identifier, values, campaign, entry["send"], now)
                update = {"skip_dates": sorted({*values["skip_dates"], send.key})}
                due.append(send.due_at)
                change |= {"send": send.key, "due_at": send.due_at}
            patch.append(
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": identifier,
                    "values": update,
                }
            )
        changes.append(change)
    return patch, due, changes


def build_review(service, actor, state, campaign, entries, *, base_digest):
    """Build and sign one report email change, or refuse it.

    ``state`` is ``campaign_views._state`` read in the caller's work
    transaction, ``campaign`` the campaign it names and ``entries`` the
    parsed entries (``parse_entries``). A ``base_digest`` other than the
    applied configuration's is the stale page's refusal. Returns ``{"changes",
    "blocking", "preview"}``: ``blocking`` counts the affected reports' work
    in progress or uncertain, and ``preview`` is the signed change, None
    while anything blocks it.
    """
    refuse_archived(campaign)
    configuration = state[0]
    digest = configuration.active_configuration.digest
    if base_digest != digest:
        raise stale_page()
    records = {
        row["id"]: row["values"] for row in report_records(configuration, campaign.pk)
    }
    # A send of a changed report still being prepared, a Send now queued
    # included, refuses the change, as the page's row does: the work
    # summary counts queued work as cancellable, and a change would leave
    # that send on a revision no longer current.
    for entry in entries:
        if entry.get("id") and open_send(entry["id"], configuration.mode):
            raise blocked()
    now = database_now()
    patch, due, changes = change_rows(
        entries, records, campaign.active_configuration.values, now
    )
    base = service.store.active()
    if base is None or base.digest != digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        # The candidate is checked whole, as it will be recorded.
        build_candidate(base, patch, candidate_id=uuid4())
    except (ConfigError, ValueError):
        raise UserFacingError(
            DOES_NOT_FIT, fix=_("Reload the page and try again.")
        ) from None
    summary = work_summary(campaign.pk)
    blocking = sum(
        summary.get(change["id"], {}).get("blocking", 0) for change in changes
    )
    return {
        "changes": changes,
        "blocking": blocking,
        "preview": None
        if blocking
        else sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=preview_salt(campaign.pk),
            snapshot=work_fingerprint(state, campaign.pk, summary),
            due=sorted(moment.isoformat() for moment in due),
        ),
    }


def open_send(identifier, mode):
    """Whether a send of the report is being prepared, or its email delivered."""
    from .report_email_models import ReportEmailPreparation, ReportEmailRecipient

    preparing = (
        ReportEmailPreparation.objects.filter(definition_id=identifier, mode=mode)
        .exclude(phase__in=("complete", "cancelled"))
        .exists()
    )
    return preparing or (
        ReportEmailRecipient.objects.filter(
            snapshot__preparation__definition_id=identifier,
            snapshot__preparation__mode=mode,
            outbox_id__isnull=False,
        )
        .exclude(outbox__state__in=("delivered", "permanent_failure", "cancelled"))
        .exists()
    )


def blocked():
    """The refusal of a change that work in progress or uncertain blocks."""
    return UserFacingDenied(BLOCKED, fix=BLOCKED_FIX)
