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
from parishkit.stewardship.web.refusals import stale_page

from .admin_editing import sign_preview
from .campaign_views import _state
from .content_forms import EMAIL_LABELS
from .request_patch import build_candidate
from .schedule_forms import WEEKDAYS
from .schedule_preview import fingerprint, work_summary

# A preview that changes nothing is refused with this, not with the catch-all
# rule text, which would wrongly suggest a schedule breaks a rule (#878).
NO_CHANGES = _("Nothing has changed.")
# The candidate breaks a schedule rule: most often mail that a date change
# leaves outside the campaign. A live end-date review (#912) recognizes it to
# offer the combined date-change review that resolves every such mailing.
DOES_NOT_FIT = (
    "Every Family mailing must fit the campaign; reminders must follow "
    "exactly one initial mailing and use distinct times. Resolve all "
    "affected schedules, or change at most 100 records per request."
)


def preview_salt(campaign_id):
    """The signing salt of one campaign's schedule previews.

    The campaign is in the salt, so a preview signed for one campaign never
    confirms on another's page or command.
    """
    return f"stewardship-schedules-preview-v1:{campaign_id}"


def confirm_scope(service, campaign_id):
    """Confirmation rechecks the same schedule generation inside intake's work lock."""
    state = _state(service)
    return state[0], fingerprint(state[-1], work_summary(campaign_id))


def describe(values, campaign):
    """Pair civil intent with bounded UTC previews resolved in its own campaign zone."""
    if values is None:
        return None
    page = preview_slots(values, campaign)
    return values | {
        "weekday_label": _(WEEKDAYS[values["weekday"]])
        if values["weekday"] is not None
        else None,
        "timezone": campaign["timezone"],
        "resolved_slots": [
            {"key": slot.key, "due_at": slot.due_at.isoformat()} for slot in page.slots
        ],
        "more_slots": not page.exhausted,
    }


def build_preview(
    service, actor, state, campaign, window, schedules, *, base_digest, salt
):
    """Validate the complete candidate, including each stranded or reordered mailing.

    ``window`` and ``schedules`` are the page's bound forms; ``base_digest``
    is the applied configuration's digest the change was made against (the
    page's hidden field). A base that is no longer applied raises the page's
    stale refusal. Returns None when the forms are invalid, with the errors
    on the forms; otherwise the review: each change (with its schedule
    ``id``, which the page's template does not show), the window before and
    after, the count of work that blocks it and, when nothing blocks it, the
    signed ``preview`` the confirmation takes.
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
    if changed:
        patch.append(
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": changed,
            }
        )
    base = service.store.active()
    if base is None or base.digest != digest:
        raise StaleRecordError("The applied configuration changed.")
    if not patch:
        window.add_error(None, NO_CHANGES)
        return None
    try:
        build_candidate(base, patch, candidate_id=uuid4())
    except (ConfigError, ValueError):
        window.add_error(None, DOES_NOT_FIT)
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
