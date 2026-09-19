"""Passive cleanup status and current-Admin cancellation/retry, not activation."""

from uuid import UUID, uuid4

from django.core import signing

from parishkit.stewardship.campaigns.cleanup_requests import (
    request_cancellation,
    retry_cleanup,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCancellation,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.production_storage import _status
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.observability import current_correlation

from .admin_editing import editable_configuration, principal

CONTROL_SALT = "stewardship-go-live-cleanup-control-v1"


def _current(request, service, campaign_id, request_id, *, passive):
    """Bind current Admin identity and the sole Testing draft before journal reads."""
    actor = principal(request, service, passive=passive)
    configuration = editable_configuration(service)
    if (
        configuration.current_campaign_id != campaign_id
        or configuration.mode != "testing"
    ):
        raise PermissionError("Cleanup belongs to the current Testing campaign.")
    row = ProductionTransitionRequest.objects.select_related("campaign").get(
        pk=request_id, campaign_id=campaign_id
    )
    if row.campaign.state != "draft":
        raise PermissionError("Cleanup control requires the current Testing draft.")
    return actor, row


def progress(request, service, campaign_id, request_id):
    """Read bounded durable progress; signed controls do not renew idle authority."""
    with work_transaction():
        actor, row = _current(request, service, campaign_id, request_id, passive=True)
        cancelling = ProductionCleanupCancellation.objects.filter(request=row).exists()
        task = (
            TaskRun.objects.filter(root_id=row.task_id)
            .order_by("-retry_sequence")
            .first()
        )
        actions = []
        if not cancelling and row.state not in {"cancelled", "activated"}:
            actions.append("cancel")
            if row.state == "cleanup_failed":
                actions.append("retry")
        controls = {
            action: signing.dumps(
                {
                    "actor": str(actor.identity),
                    "campaign": str(campaign_id),
                    "request": str(request_id),
                    "version": row.version,
                    "action": action,
                    "key": str(uuid4()),
                },
                salt=CONTROL_SALT,
            )
            for action in actions
        }
        return {
            "campaign": row.campaign,
            "status": _status(row),
            "cancelling": cancelling,
            "task": task,
            "controls": controls,
        }


def control(request, service, campaign_id, request_id, *, token):
    """Apply one exact current intent; the worker owns in-flight cancellation."""
    if type(token) is not str or len(token) > 4096:
        raise ValueError("Invalid cleanup control.")
    with work_transaction():
        binding = signing.loads(token, salt=CONTROL_SALT, max_age=300)
        if (
            type(binding) is not dict
            or set(binding)
            != {"actor", "campaign", "request", "version", "action", "key"}
            or binding["action"] not in {"cancel", "retry"}
            or type(binding["version"]) is not int
            or binding["version"] < 1
            or any(
                type(binding[key]) is not str
                for key in ("actor", "campaign", "request", "key")
            )
        ):
            raise ValueError("Invalid cleanup control.")
        actor, _ = _current(request, service, campaign_id, request_id, passive=False)
        if (binding["actor"], binding["campaign"], binding["request"]) != (
            str(actor.identity),
            str(campaign_id),
            str(request_id),
        ):
            raise PermissionError("Cleanup control belongs to another scope.")
        allowed = (
            {
                "request_cancel",
                "cancel",
                "cancel_task",
                "release_gate",
                "transition_replay",
            }
            if binding["action"] == "cancel"
            else {"retry_cleanup", "retry_task", "retry_failed", "transition_replay"}
        )

        def admit(action, campaign, status, proposal):
            """Repeat authority for mutations/replays without changing worker claims."""
            current, _ = _current(
                request, service, campaign_id, request_id, passive=True
            )
            return (
                current.identity == actor.identity
                and campaign.pk == campaign_id
                and action in allowed
            )

        owner = request_cancellation if binding["action"] == "cancel" else retry_cleanup
        return owner(
            request_id=request_id,
            command_id=UUID(binding["key"]),
            expected_version=binding["version"],
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            admit=admit,
        )
