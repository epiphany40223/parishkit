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

from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import schedule_window_changed
from parishkit.stewardship.campaigns.schedule_evaluation import preview_slots
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.wall_clock import WEEKDAYS
from parishkit.stewardship.web.refusals import stale_page

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


def describe(values, campaign):
    """Pair civil intent with bounded UTC previews resolved in its own campaign zone."""
    if values is None:
        return None
    page = preview_slots(values, campaign)
    return values | {
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
    in the same signed intent; the review lists them as ``series``. Delivery
    never reads a series, so remembering one changes no send.
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
    explicit = {row["id"] for row in patch}
    # The form set adds new schedules in form order, so a repeat's positions
    # name the ids it just minted for that repeat's reminders.
    added = [row["id"] for row in patch if row["operation"] == "add"]
    remembered = [
        {
            "operation": "add",
            "section": SERIES,
            "id": str(uuid4()),
            "values": {
                "campaign_id": str(campaign.pk),
                "rule": item["rule"],
                "schedules": sorted(added[index] for index in item["added"]),
            },
        }
        for item in series
    ]
    # A new timezone or digest date window also replaces unchanged civil mail
    # fields. Include their existing work in the reviewed cancellation inventory.
    patch.extend(
        {"operation": "update", "section": "schedules", **row}
        for row in schedules.previous
        if row["id"] not in explicit
        and schedule_window_changed(
            campaign.active_configuration.values,
            window.values(),
            kind=row["values"]["kind"],
        )
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
    if not patch:
        window.add_error(None, NO_CHANGES)
        return None
    if remembered and not uses_users(base.document()):
        window.add_error(None, SERIES_NEEDS_USERS)
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
        window.add_error(None, EMAIL_DOES_NOT_FIT if content else DOES_NOT_FIT)
        return None
    summary = work_summary(campaign.pk)
    prior = {row["id"]: row["values"] for row in schedules.previous}
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
        }
        for row in schedule_changes
    ]
    blocking = sum(change["impact"].get("blocking", 0) for change in changes)
    return {
        "campaign": campaign,
        "changes": changes,
        "window_changes": changed,
        "before_window": campaign.active_configuration.values,
        "after_window": window.values(),
        "blocking": blocking,
        "series": [
            {
                "id": row["id"],
                **{name: row["values"][name] for name in ("rule", "schedules")},
            }
            for row in remembered
        ],
        "preview": None
        if blocking
        else sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint(state[-1], summary),
        ),
    }
