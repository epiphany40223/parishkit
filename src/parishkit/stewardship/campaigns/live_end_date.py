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
- ``admit_end_edit`` is the installer's owning admission for ``edit_end``.

An end edit binds no new token generation, so every Family code and link
already emailed keeps working until the new end (Family sign-in admits while
the current time is before the active end).
"""

from parishkit.config import ConfigError

# A live campaign: the states in which the end date may still move.
LIVE_STATES = frozenset({"scheduled", "active"})
# The installer's actions for an exceptional end-edit request: preparation
# and activation ("edit_end"), a settled request's receipt, and the abort.
END_EDIT_ACTIONS = frozenset(
    {"edit_end", "configuration_receipt", "abort_configuration"}
)


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
