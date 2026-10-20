"""The schedule preview and its confirmation scope, moved out of the view.

Dates and mail schedules (``schedule_views``) builds its review of a schedule and
campaign-date change through ``build_preview``, and so does the Admin
automation command line (``schedule preview``, ADM-11), so both validate the
same candidate and sign the same intent. ``confirm_scope`` is what the
confirmation rechecks inside intake's work lock, for the page and for
``schedule confirm`` alike. Nothing here takes a request or checks authority;
the caller admits first and runs ``build_preview`` inside its own work
transaction.
"""

from uuid import uuid4

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import schedule_window_changed
from parishkit.stewardship.campaigns.schedule_evaluation import preview_slots
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.wall_clock import WEEKDAYS
from parishkit.stewardship.web.refusals import stale_page

from . import admin_editing, digest_skips, schedule_local
from .admin_editing import sign_preview
from .campaign_views import _state
from .content_forms import EMAIL_LABELS
from .report_rows import planned, translate
from .request_patch import build_candidate
from .schedule_preview import fingerprint, work_summary
from .schedule_reads import refuse_archived
from .series_schema import SECTION as SERIES
from .series_schema import upkeep
from .users_schema import uses_users

# A preview that changes nothing is refused with this, not with the catch-all
# rule text, which would wrongly suggest a schedule breaks a rule (#878).
NO_CHANGES = _("Nothing has changed.")
# The candidate breaks a schedule rule the forms did not catch: mail that
# must fit the campaign, reminder order, distinct times or the record limit.
DOES_NOT_FIT = (
    "Every Family mailing must fit the campaign; reminders must follow "
    "exactly one initial mailing and use distinct times. Resolve all "
    "affected schedules, or change at most 100 records per request."
)
# A remembered repeat's positions must name exactly the reminders it added;
# when the added forms and adds disagree, refuse rather than guess (#469).
SERIES_UNMATCHED = (
    "The repeat to remember could not be matched to the reminders it adds. "
    "Leave out added schedules that have no values."
)
# Only a users-form configuration can hold a remembered series (#469), so
# remembering one elsewhere is refused in plain words, not as DOES_NOT_FIT.
SERIES_NEEDS_USERS = (
    "A repeat can be remembered as a series only once the configuration "
    "holds Admin users; run upgrade-users first."
)

# The same, for a change that adds a schedule's own edited email
# (``schedule_email``): the Edit page shows it on the email's fields.
EMAIL_DOES_NOT_FIT = _(
    "Check this email's content and placeholders. If they are right, check "
    "the schedule's date and time against the campaign's rules."
)


def preview_salt(campaign_id):
    """The signing salt of one campaign's schedule previews.

    The campaign is in the salt, so a preview signed for one campaign never
    confirms on another's page or command.
    """
    return f"stewardship-schedules-preview-v1:{campaign_id}"


def confirm_scope(service, campaign_id, *, page=False):
    """Confirmation rechecks the same schedule generation inside intake's work lock.

    A ``page``'s confirmation (an Admin page's, including Campaign
    settings' live end date, which signs with the same salt; not the
    command line's) also refuses an archived campaign there (#1258).
    """
    state = _state(service)
    if page:
        refuse_archived(state, campaign_id)
    return state[0], fingerprint(state[-1], work_summary(campaign_id))


def reviewed_sends(changes, prior, before_zone, after_zone):
    """The send instants a schedule change sets that are still ahead (#1464).

    ``changes`` are the change's schedule rows, ``prior`` the saved values by
    id, and the zones the campaign's before and after the change. Each added
    or changed dated schedule (a digest has no one send) whose resolved
    moment differs from its saved one, and is after the database's clock
    now, is listed as an ISO 8601 instant; the confirmation refuses once any
    has passed (``admin_editing.refuse_passed_sends``). A moment already
    past at review is not listed: the ordinary Edit may move its reminder
    into the past, and a series edit may add a missed reminder again, both
    meant to send at once. Returns the sorted, distinct instants.
    """
    now = admin_editing.database_now()
    due = set()
    for row in changes:
        values = row.get("values")
        if not values or not values.get("date"):
            continue  # a delete, or a digest
        moment = schedule_local.instant(values, after_zone, now)
        saved = prior.get(row["id"])
        if (
            saved is not None
            and saved.get("date")
            and schedule_local.instant(saved, before_zone, now) == moment
        ):
            continue  # a send time the change leaves as it was
        if moment > now:
            due.add(moment.isoformat())
    return sorted(due)


def describe(values, campaign):
    """Pair civil intent with bounded UTC previews resolved in its own campaign zone.

    A digest's ``skipped_sends`` (#1658) are the sends its skip dates still
    skip, with their UTC times, so the review shows each as a browser-local
    send time rather than its slot key.
    """
    if values is None:
        return None
    # A converted report's digest view previews the report's own sends.
    page = preview_slots(planned(values), campaign)
    return values | {
        "skipped_sends": digest_skips.skipped_sends(values, campaign),
        "weekday_label": WEEKDAYS[values["weekday"]]
        if values["weekday"] is not None
        else None,
        "timezone": campaign["timezone"],
        "resolved_slots": [
            {"key": slot.key, "due_at": slot.due_at.isoformat()} for slot in page.slots
        ],
        "more_slots": not page.exhausted,
    }


def build_preview(
    service,
    actor,
    state,
    campaign,
    window,
    schedules,
    *,
    base_digest,
    salt,
    content=(),
    campaign_values=None,
    series=(),
    series_changes=(),
    request_key=None,
    skips=None,
):
    """Validate the complete candidate, including each stranded or reordered mailing.

    ``window`` and ``schedules`` are the page's bound forms; ``base_digest``
    is the applied configuration's digest the change was made against (the
    page's hidden field). A base that is no longer applied raises the page's
    stale refusal. Returns None when the forms are invalid, with the errors
    on the forms; otherwise the review: each change (with its schedule
    ``id``, which the page's template does not show), the window before and
    after, the count of work that blocks it and, when nothing blocks it, the
    signed ``preview`` the confirmation takes. ``content`` lists content
    records the change adds first (a schedule's own edited email,
    ``schedule_email``), which the schedules' forms already offer; they are
    validated, signed and applied with the schedules in the one request.
    ``campaign_values`` names other campaign values changed in the same
    campaign update (Campaign settings' one Review of a live campaign's end
    date with its Ministry leader roles, #1119); they are listed in neither
    ``window_changes`` nor the schedule changes, and the caller describes
    them. ``series`` lists repeats to remember as reminder series (#469),
    each ``{"rule", "added"}``: its canonical rule and the positions of its
    reminders among the schedules this change adds, in form order. Each
    becomes one ``schedule_series`` record, holding exactly those reminders,
    in the same signed intent; the review lists them as ``series``. An item
    may also ``keep`` saved reminder ids the series holds besides them, and
    name the existing series ``id`` it replaces in place rather than adding
    one (a series edit, ``series_edits.plan``). ``series_changes`` are other
    operations on existing series records (a series cut short or deleted).
    The review lists updated and removed series as ``series_changes``.
    Delivery never reads a series, so no series operation changes a send.
    ``request_key`` signs the preview with a direct save's request key
    (#1404 PR 4b, ``admin_editing.direct_key``); a review shown in a dialog
    takes a fresh one. ``skips`` maps a saved digest's id to the one send
    (slot date) it skips (#1658, ``digest_skips.apply``), which adds the date
    to its ``skip_dates`` and signs the send's time with the others.
    """
    configuration = state[0]
    digest = configuration.active_configuration.digest
    if base_digest != digest:
        raise stale_page()
    window_valid = window.is_valid()
    if window_valid:
        schedules.campaign = window.values()
    schedules_valid = schedules.is_valid()
    if not window_valid or not schedules_valid:
        return None
    changed = {
        key: value
        for key, value in window.values().items()
        if value != campaign.active_configuration.values[key]
    }
    patch = schedules.patch()
    prior = {row["id"]: row["values"] for row in schedules.previous}
    # A digest send skipped by this change (#1658): refused here unless it
    # may be skipped, and its send time is signed below with the others.
    skipped_due = (
        digest_skips.apply(
            patch,
            skips,
            prior,
            campaign.active_configuration.values,
            admin_editing.database_now(),
        )
        if skips
        else []
    )
    explicit = {row["id"] for row in patch}
    # A repeat's positions index the added forms, in form order. They name
    # the ids the form set just minted only if every added form produced
    # exactly one "add": the form set skips an added form with no values
    # (the page's blank extra row too), which would shift every later
    # position. The command line refuses such entries (parse_changes), and
    # this check fails closed for any other caller.
    added = [row["id"] for row in patch if row["operation"] == "add"]
    added_forms = [
        form
        for form in schedules.forms
        if not form.cleaned_data.get("id") and form not in schedules.deleted_forms
    ]
    if series and (
        len(added) != len(added_forms)
        or any(index >= len(added) for item in series for index in item["added"])
    ):
        window.add_error(
            None, ValidationError(SERIES_UNMATCHED, code="series_unmatched")
        )
        return None
    remembered = [
        {
            "operation": "update" if item.get("id") else "add",
            "section": SERIES,
            "id": item.get("id") or str(uuid4()),
            "values": {
                "campaign_id": str(campaign.pk),
                "rule": item["rule"],
                "schedules": sorted(
                    [*(added[index] for index in item["added"]), *item.get("keep", ())]
                ),
            },
        }
        for item in series
    ]
    remembered.extend(series_changes)
    # A new timezone or digest date window also replaces unchanged civil mail
    # fields. Include their existing work in the reviewed cancellation inventory.
    # Only the record's id and values: a converted report's row also
    # carries its report values (``report_rows.view_rows``), which are no
    # part of a change.
    patch.extend(
        {
            "operation": "update",
            "section": "schedules",
            "id": row["id"],
            "values": row["values"],
        }
        for row in schedules.previous
        if row["id"] not in explicit
        and schedule_window_changed(
            campaign.active_configuration.values,
            window.values(),
            kind=row["values"]["kind"],
        )
    )
    # A skip of a send still ahead that the change moves off the digest's
    # sends (a new weekday or campaign dates, #1658) is dropped, so it never
    # applies again if the digest later moves back; the review lists it.
    dropped = digest_skips.drop_stale(
        patch,
        prior,
        campaign.active_configuration.values,
        window.values(),
        admin_editing.database_now,
    )
    schedule_changes = list(patch)
    if changed or campaign_values:
        patch.append(
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": changed | (campaign_values or {}),
            }
        )
    base = service.store.active()
    if base is None or base.digest != digest:
        raise StaleRecordError("The applied configuration changed.")
    if not patch and not remembered:
        window.add_error(None, ValidationError(NO_CHANGES, code="unchanged"))
        return None
    if any(row["operation"] == "add" for row in remembered) and not uses_users(
        base.document()
    ):
        window.add_error(
            None, ValidationError(SERIES_NEEDS_USERS, code="series_needs_users")
        )
        return None
    # An added email comes before the schedule that sends it, and a new
    # series follows the reminders it holds.
    patch = [*content, *patch, *remembered]
    # A remembered reminder series (#469) follows its reminders: a removed
    # one, or one moved off its rule's dates, leaves it, in this same signed
    # intent. The review lists only the schedule changes.
    patch.extend(upkeep(base.document(), patch))
    try:
        # The candidate is checked as it will be recorded: a converted
        # report's change in its own values (#1786, ``admin_editing.sign_preview``
        # translates the signed patch the same way).
        build_candidate(base, translate(base.document(), patch), candidate_id=uuid4())
    except (ConfigError, ValueError):
        # With an added email, the email is the likelier cause (#446).
        window.add_error(
            None,
            ValidationError(
                EMAIL_DOES_NOT_FIT if content else DOES_NOT_FIT,
                code="candidate_refused",
            ),
        )
        return None
    summary = work_summary(campaign.pk)
    changes = [
        {
            "id": row["id"],
            "operation": row["operation"],
            "before": describe(
                prior.get(row["id"]), campaign.active_configuration.values
            ),
            "after": describe(row.get("values"), window.values()),
            "impact": summary.get(row["id"], {}),
            "label": EMAIL_LABELS[(row.get("values") or prior[row["id"]])["kind"]],
            # Skips this change drops (#1658, ``digest_skips.drop_stale``).
            "dropped_skips": dropped.get(row["id"], []),
        }
        for row in schedule_changes
    ]
    blocking = sum(change["impact"].get("blocking", 0) for change in changes)
    # Email the changed schedules already sent (each schedule's fulfillment,
    # across its revisions): only then does the review say a sent email
    # cannot be recalled. A future send's edit or move has sent nothing.
    delivered = sum(change["impact"].get("delivered", 0) for change in changes)
    return {
        "campaign": campaign,
        "changes": changes,
        "window_changes": changed,
        "before_window": campaign.active_configuration.values,
        "after_window": window.values(),
        "blocking": blocking,
        "delivered": delivered,
        "series": [
            {
                "id": row["id"],
                **{name: row["values"][name] for name in ("rule", "schedules")},
            }
            for row in remembered
            if row["operation"] == "add"
        ],
        # Existing series this change updates or removes, with an update's
        # resulting rule and reminders (#469).
        "series_changes": [
            {
                "id": row["id"],
                "operation": row["operation"],
                **{
                    name: row["values"][name]
                    for name in ("rule", "schedules")
                    if name in row.get("values", {})
                },
            }
            for row in remembered
            if row["operation"] != "add"
        ],
        "preview": None
        if blocking
        else sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint(state[-1], summary),
            # Each send time still ahead now, which the confirmation checks
            # again: a review lives 15 minutes (#1464).
            due=sorted(
                {
                    *reviewed_sends(
                        schedule_changes,
                        prior,
                        campaign.active_configuration.values["timezone"],
                        window.values()["timezone"],
                    ),
                    *(moment.isoformat() for moment in skipped_due),
                }
            ),
            key=request_key,
        ),
    }
