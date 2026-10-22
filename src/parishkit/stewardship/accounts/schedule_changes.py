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

from datetime import time
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import schedule_window_changed
from parishkit.stewardship.campaigns.schedule_evaluation import upcoming_slots
from parishkit.stewardship.occurrence_preview import Occurrence, build, wall_shift
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.dates import UnknownZone, browser_zone, format_time
from parishkit.stewardship.web.refusals import stale_page

from . import admin_editing, schedule_local
from .admin_editing import sign_preview
from .campaign_views import _state
from .content_forms import EMAIL_LABELS
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
    or changed schedule whose resolved
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
            continue  # a delete
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


# The schedule preview's words for one side of a schedule change (#1129;
# ui-conventions "Schedule previews"; admin-portal "Dates and mail
# schedules").
NO_MORE_SENDS = _("No more sends: this email's time has passed.")
NO_BROWSER_ZONE = _(
    "Your browser didn't report a time zone, so the send times can't be "
    "shown. Check your computer's time zone setting, then review again."
)
PAUSED_SENDS = _(
    "Live campaign email is paused, so these sends are held. Pause and resume "
    "mail shows what sends when mail resumes."
)
# How many sends a side lists, from now.
SHOWN_SENDS = 5


def report_window_rows(configuration, campaign, after, explicit):
    """The report email rows a change of the campaign's dates or zone replaces.

    Each is an update with the report's own saved values, for every report
    of the campaign that ``schedule_window_changed`` says the change
    reinterprets; ``explicit`` are the ids the change already names.
    """
    return [
        {
            "operation": "update",
            "section": "schedules",
            "id": row["id"],
            "values": row["values"],
        }
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("schedules", [])
        if row["values"].get("kind") == "report"
        and row["values"]["campaign_id"] == str(campaign.pk)
        and row["id"] not in explicit
        and schedule_window_changed(
            campaign.active_configuration.values, after, kind="report"
        )
    ]


def _what(values, *, shift, same_zone):
    """What one send is, in words, for its line in the schedule preview.

    A send is its mail type; when the shown zone is the campaign's and a
    clock change moves its wall time forward, the line also names the time
    it was set for, since the daylight-saving words then name the time it
    really sends.
    """
    label = str(EMAIL_LABELS[values["kind"]])
    if same_zone and shift == "forward":
        return _("%(label)s, set for %(time)s") % {
            "label": label,
            "time": format_time(time.fromisoformat(values["time"])),
        }
    return label


def sends_preview(values, campaign, page, *, now, zone=None, paused=False):
    """One side's send times as the schedule preview's context (#1129).

    ``page`` is ``upcoming_slots``'s next sends from ``now``. ``zone`` is the
    browser's posted IANA zone: the sends are grouped by its days and drawn
    in the compact layout; an unusable one gives the unavailable state, and
    None (the command line, Campaign settings' end-date review) a zone-less
    list of full browser-local instants. ``paused`` adds the paused notice.
    """
    schedule_zone = campaign["timezone"]
    unavailable, shown = None, None
    if zone is not None:
        try:
            shown = browser_zone(zone)
        except UnknownZone:
            unavailable = NO_BROWSER_ZONE
    same_zone = shown is not None and zone == schedule_zone
    occurrences = []
    for slot in page.slots:
        shift = wall_shift(slot.local_date, values["time"], slot.due_at, schedule_zone)
        occurrences.append(
            Occurrence(
                slot.due_at.astimezone(shown).date() if shown is not None else None,
                slot.due_at,
                _what(values, shift=shift, same_zone=same_zone),
                kind=values["kind"],
                shift=shift,
            )
        )
    return build(
        occurrences,
        shown_zone=zone if shown is not None else None,
        schedule_zone=schedule_zone,
        now=now,
        limit=SHOWN_SENDS,
        more=not page.exhausted,
        paused={
            "text": PAUSED_SENDS,
            "url": reverse("admin:delivery_control"),
            "label": _("Pause and resume mail"),
        }
        if paused
        else None,
        layout="compact",
        empty=NO_MORE_SENDS,
        unavailable=unavailable,
    )


def describe(values, campaign, *, now, zone=None, paused=False):
    """Pair civil intent with its next sends from now, resolved in its campaign zone.

    ``resolved_slots`` (at most five UTC instants, with ``more_slots`` when
    others follow) are the next sends after ``now`` (the database's clock),
    as ``schedule show`` and ``schedule preview`` list them;
    ``occurrences`` is the same sends as the shared schedule preview's
    context (``sends_preview``), shown in the browser's ``zone`` when one is
    given, and with the paused notice while live delivery is ``paused``.
    """
    if values is None:
        return None
    page = upcoming_slots(values, campaign, now=now, limit=SHOWN_SENDS)
    return values | {
        "timezone": campaign["timezone"],
        "resolved_slots": [
            {"key": slot.key, "due_at": slot.due_at.isoformat()} for slot in page.slots
        ],
        "more_slots": not page.exhausted,
        "occurrences": sends_preview(
            values, campaign, page, now=now, zone=zone, paused=paused
        ),
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
    zone=None,
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
    takes a fresh one. ``zone`` is the browser's posted time zone, which
    each side's send times are grouped and shown in (``describe``); a caller
    with no browser (the command line, Campaign settings' end-date review)
    passes none.
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
    # A new timezone also replaces unchanged civil mail fields. Include their
    # existing work in the reviewed cancellation inventory.
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
    schedule_changes = list(patch)
    # Report emails (#1786) are edited on Report emails, not here, but a
    # report's rule follows the campaign's dates and zone: a change of them
    # replaces each report's revision too, so its planned sends are made
    # again (``schedule_window_changed``). The review
    # names them.
    reports = report_window_rows(configuration, campaign, window.values(), explicit)
    patch.extend(reports)
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
        build_candidate(base, patch, candidate_id=uuid4())
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
    # Each side lists its next sends after the database's one now, held
    # while live delivery is paused (#1129).
    shown = {
        "now": admin_editing.database_now(),
        "zone": zone,
        "paused": configuration.mode == "production" and campaign.delivery_paused,
    }
    changes = [
        {
            "id": row["id"],
            "operation": row["operation"],
            "before": describe(
                prior.get(row["id"]), campaign.active_configuration.values, **shown
            ),
            "after": describe(row.get("values"), window.values(), **shown),
            "impact": summary.get(row["id"], {}),
            "label": EMAIL_LABELS[(row.get("values") or prior[row["id"]])["kind"]],
        }
        for row in schedule_changes
    ]
    blocking = sum(change["impact"].get("blocking", 0) for change in changes) + sum(
        summary.get(row["id"], {}).get("blocking", 0) for row in reports
    )
    # Email the changed schedules already sent (each schedule's fulfillment,
    # across its revisions): only then does the review say a sent email
    # cannot be recalled. A future send's edit or move has sent nothing.
    delivered = sum(change["impact"].get("delivered", 0) for change in changes)
    return {
        "campaign": campaign,
        "changes": changes,
        "reports": [row["values"]["name"] for row in reports],
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
            due=reviewed_sends(
                schedule_changes,
                prior,
                campaign.active_configuration.values["timezone"],
                window.values()["timezone"],
            ),
            key=request_key,
        ),
    }
