"""Changing a live campaign's end date through a reviewed configuration request.

A live campaign's structural settings are locked, but its end date may still
move (#912; the top-level spec's structural-lock rules). The storage for this
was built first (``configuration_intents``): an exceptional ``edit_end``
intent binds one reviewed request to the campaign and runtime versions it was
reviewed at, and only such a request may cross the live lock. This module is
the product's side of it:

- ``live_end_editable`` decides when the pages and commands offer the change;
- ``bind_reviewed_end_edit`` binds the intent inside the request's own durable
  transaction, so the configuration installer never sees the request
  without it;
- ``admit_end_edit`` is the installer's owning admission for ``edit_end``;
- ``end_edit_refusal`` says why a bound edit can no longer apply, so the
  installer refuses it cleanly (through the abort journal once its
  candidate is prepared) instead of retrying it forever (#944);
- ``refusal_text`` is what Change status shows for a refused edit.

An end edit binds no new token generation, so every Family code and link
already emailed keeps working until the new end (Family sign-in admits while
the current time is before the active end).
"""

from typing import NamedTuple

from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError

# A live campaign: the states in which the end date may still move.
LIVE_STATES = frozenset({"scheduled", "active"})
# The installer's actions for an exceptional end-edit request: preparation
# and activation ("edit_end"), a settled request's receipt, and the abort.
END_EDIT_ACTIONS = frozenset(
    {"edit_end", "configuration_receipt", "abort_configuration"}
)

# Why the installer refused a bound end edit (#944). The sentence is stored
# as the abort journal's reason once the candidate is prepared, and Change
# status shows it. "changed" is the stale-base refusal; the rest are
# invalid candidates, the only other failure a checkpoint may record.
REFUSALS = {
    "changed": "The campaign changed after this end date was reviewed.",
    "restore": "A restore review began before the end date could change.",
    "ended": "The campaign closed, or its previous end date passed, before "
    "the end date could change.",
    "past": "The new end date had already passed when the change would have applied.",
    "closing": "The campaign's closing work had already started when the "
    "change would have applied.",
    "busy": "Other campaign work was running when the change would have applied.",
    "refused": "The database refused the end date when the change would have applied.",
}


class EndRefusal(NamedTuple):
    """Why a bound end edit can no longer apply: a ``REFUSALS`` key and its text."""

    code: str
    reason: str

    @property
    def failure_code(self):
        """The checkpoint failure code a refusal before preparation records."""
        return "stale_base" if self.code == "changed" else "invalid_candidate"


def refusal(code):
    """The ``EndRefusal`` named ``code``."""
    return EndRefusal(code, REFUSALS[code])


def live_end_editable(configuration, campaign, *, held, now):
    """Whether ``campaign``'s end date may change now, outside the draft editor.

    True for the current Production campaign while it is scheduled or open,
    before its closing instant, and while no background work holds campaign
    changes (``held``). A draft changes its dates on the ordinary editors; a
    closed campaign would need the guarded reopen (#527). The installer and
    SQL recheck all of this when the change applies.
    """
    return (
        not held
        and campaign.pk == configuration.current_campaign_id
        and configuration.mode == "production"
        and not configuration.restore_review_required
        and campaign.structural_locked
        and campaign.state in LIVE_STATES
        and now < campaign.active_configuration.ends_at
    )


def end_edit_campaign(patch):
    """The campaign id (a string) whose end date ``patch`` changes, or None."""
    for item in patch:
        if (
            type(item) is dict
            and item.get("section") == "campaigns"
            and item.get("operation") == "update"
            and "end_date" in (item.get("values") or {})
        ):
            return item.get("id")
    return None


def bind_reviewed_end_edit(request):
    """Bind ``request``'s exceptional end edit in its own durable transaction.

    Called from the confirmation's request attachment
    (``admin_editing.confirm_intent``), after the confirmation rechecked,
    under the work-order lock, that nothing changed since the review. That
    makes the current campaign and runtime versions the reviewed ones. A
    request that does not change a locked campaign's end date (any draft
    edit) binds nothing. Returns the intent, or None.

    The intent's SQL trigger repeats every check here (current campaign,
    versions, live state, the request's base and actor), so a binding this
    function should not make fails the whole confirmation instead.
    """
    from uuid import UUID

    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.audit.schemas import Action, ActorKind
    from parishkit.stewardship.audit.services import record_action

    from .models import Campaign, CampaignConfigurationIntent
    from .work_locks import lock_campaign_exports, lock_work_order

    identifier = end_edit_campaign(request.patch)
    if identifier is None:
        return None
    campaign_id = UUID(identifier)
    # The trigger's lock order: the work order, this campaign's exports
    # (#147), then the runtime row and the campaign row.
    lock_work_order()
    lock_campaign_exports(campaign_id)
    runtime = SystemConfiguration.objects.select_for_update().get()
    campaign = Campaign.objects.select_for_update(of=("self",)).get(pk=campaign_id)
    if not campaign.structural_locked:
        return None
    if campaign.state not in LIVE_STATES:
        raise ConfigError("Only a live campaign's end date can change here.")
    intent = CampaignConfigurationIntent.objects.create(
        campaign=campaign,
        request=request,
        action="edit_end",
        prior_projection_id=campaign.active_configuration_id,
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        actor_id=request.actor_id,
        correlation_id=request.correlation_id,
    )
    record_action(
        Action.CAMPAIGN_END_DATE_REQUESTED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=request.actor_id,
        subject_id=request.pk,
        campaign_id=campaign.pk,
        current_parish=True,
    )
    return intent


def admit_end_edit(action, campaign, runtime, intent):
    """The configuration installer's owning admission for exceptional requests.

    Admits an ``edit_end`` intent's preparation, activation, receipt and
    abort. The campaign validator and SQL still decide whether the date is
    valid now (live state, closing instant, quiet close work, restore
    review); this only names which exceptional requests have an owner. The
    guarded reopen is not built yet (#527), so its intents have none and
    stay staged, a retryable refusal, rather than failing.
    """
    kind = intent.action if intent is not None else action
    if kind != "edit_end" or action not in END_EDIT_ACTIONS:
        raise ConfigError("This campaign change has no available owner.")


def end_intent(request_id):
    """The ``edit_end`` intent bound to request ``request_id``, or None."""
    from .models import CampaignConfigurationIntent

    return CampaignConfigurationIntent.objects.filter(
        request_id=request_id, action="edit_end"
    ).first()


def proposed_end(document, campaign_id):
    """The closing instant ``document`` gives campaign ``campaign_id``, or None."""
    from .configuration import campaign_values

    for row in document["sections"].get("campaigns", []):
        if row["id"] == str(campaign_id):
            try:
                return campaign_values(row["values"]).end
            except ConfigError:
                return None
    return None


def end_edit_refusal(request_id, document=None):
    """Why request ``request_id``'s bound end edit can no longer apply, or None.

    The same conditions the activation trigger
    (``stewardship_campaign_end_admission_v1``) and the installer's own
    checks enforce, judged first so the installer can refuse the request
    cleanly rather than leave it to fail, and be retried, forever (#944):
    the campaign or runtime changed since the review, a restore review
    began, the campaign closed or its previous end passed, the new end
    (from the candidate ``document``, when given) has passed, its closing
    work is claimed, or campaign work holds changes. A request with no
    ``edit_end`` intent is never refused here.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .admission import close_work_running
    from .models import Campaign, CampaignWorkGate
    from .runtime import _now

    intent = end_intent(request_id)
    if intent is None:
        return None
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=intent.campaign_id
    )
    runtime = SystemConfiguration.objects.get()
    now = _now()
    new_end = None if document is None else proposed_end(document, campaign.pk)
    if (campaign.version, runtime.version, campaign.active_configuration_id) != (
        intent.expected_version,
        intent.expected_runtime_version,
        intent.prior_projection_id,
    ):
        return refusal("changed")
    if runtime.restore_review_required:
        return refusal("restore")
    if (
        campaign.state not in LIVE_STATES
        or now >= campaign.active_configuration.ends_at
    ):
        return refusal("ended")
    if new_end is not None and new_end <= now:
        return refusal("past")
    if close_work_running(campaign.pk):
        return refusal("closing")
    if CampaignWorkGate.objects.filter(state__in=["preparing", "running"]).exists():
        return refusal("busy")
    return None


# What Change status says about a refused end edit (#944), by its reason or,
# for one refused before its candidate was prepared (no journal), by the
# checkpoint's failure code.
REFUSAL_TEXT = {
    REFUSALS["changed"]: _(
        "The campaign changed after this end date was reviewed, so it was not applied."
    ),
    REFUSALS["restore"]: _(
        "A restore review began before the end date could change, so it was "
        "not applied."
    ),
    REFUSALS["ended"]: _(
        "The campaign closed, or its previous end date passed, before the end "
        "date could change, so it was not applied."
    ),
    REFUSALS["past"]: _(
        "The new end date had already passed when the change would have "
        "applied, so it was not applied."
    ),
    REFUSALS["closing"]: _(
        "The campaign had already started closing when the change would have "
        "applied, so it was not applied."
    ),
    REFUSALS["busy"]: _(
        "Other campaign work was running when the change would have applied, "
        "so it was not applied. Try again once it finishes."
    ),
    REFUSALS["refused"]: _(
        "The end date could no longer change when the change would have "
        "applied, so it was not applied."
    ),
}
UNPREPARED_TEXT = {
    "stale_base": REFUSAL_TEXT[REFUSALS["changed"]],
    "invalid_candidate": _(
        "The end date could no longer change when the change would have "
        "applied: the campaign had closed or started closing, a date had "
        "passed, or other campaign work was running. It was not applied."
    ),
}


def refusal_text(status):
    """What Change status says about ``status``'s refused end edit, or "".

    ``status`` is a ``RequestStatus``. Only a failed request with a bound
    end edit has one: the journal's reason (the installer's own, shown in
    the page's words, or an Administrator's cancellation reason as written),
    else the end-date wording of its failure code.
    """
    from .models import CampaignConfigurationAbort

    if status.state != "failed":
        return ""
    intent = end_intent(status.request_id)
    if intent is None:
        return ""
    abort = CampaignConfigurationAbort.objects.filter(intent=intent).first()
    if abort is None:
        return UNPREPARED_TEXT.get(status.failure_code, "")
    text = REFUSAL_TEXT.get(abort.reason)
    if text is None:
        text = _("An Administrator cancelled this change: %(reason)s") % {
            "reason": abort.reason
        }
    return text
