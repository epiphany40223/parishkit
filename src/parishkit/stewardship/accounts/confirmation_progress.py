"""Passive Production status and exact, current-Admin catch-up retry controls."""

from uuid import UUID, uuid4

from django.core import signing
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.catchup_counts import prepared_counts
from parishkit.stewardship.campaigns.catchup_tasks import (
    admit_catchup,
    completed,
    eligible,
)
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import TaskRetryConflict, retry_failed
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .confirmation_digest_outcomes import digest_outcomes

SALT = "stewardship-production-progress-v1"

COUNT_LABELS = (
    ("family_messages", _("Family message candidates")),
    ("daily_messages", _("Daily Admin messages created")),
    ("weekly_messages", _("Weekly Admin messages created")),
    ("coalesced_slots", _("Coalesced semantic slots")),
    ("active_families", _("Active Families when their groups were prepared")),
    ("eligible_families", _("Email-eligible Families when their groups were prepared")),
    (
        "no_email_families",
        _("Families without eligible email when their groups were prepared"),
    ),
)


def _current(request, service, campaign_id, *, passive=True):
    """Authorize before reading any receipt; historical campaigns cannot retry."""
    actor = principal(request, service, passive=passive)
    configuration = editable_configuration(service)
    if (
        configuration.current_campaign_id != campaign_id
        or configuration.mode != "production"
    ):
        raise PermissionError("Production progress belongs to the current campaign.")
    receipt = (
        ProductionConfirmation.objects.filter(request__campaign_id=campaign_id)
        .select_related("request__campaign__active_configuration")
        .latest("created_at")
    )
    return actor, receipt


def progress(request, service, campaign_id):
    """Task success alone never clears the durable scheduled-mail preparation hold."""
    with work_transaction():
        actor, receipt = _current(request, service, campaign_id)
        demand = ActivationCatchUpDemand.objects.filter(
            activation_id=receipt.activation_id
        ).first()
        task, ready, control = None, False, None
        outcomes = []
        if demand:
            ready = completed(demand)
            task = (
                TaskRun.objects.filter(root_id=demand.task_root_id)
                .order_by("-retry_sequence")
                .first()
            )
            actual = prepared_counts(
                demand, receipt.request.campaign.active_configuration_id
            )
            digests = digest_outcomes(demand)
            actual.update({key: value["actual"] for key, value in digests.items()})
            outcomes = [
                {
                    "label": label,
                    "preview": receipt.preview_counts[key],
                    "actual": actual[key],
                    "difference": actual[key] - receipt.preview_counts[key],
                    "complete": digests[key]["complete"] if key in digests else ready,
                }
                for key, label in COUNT_LABELS
            ]
            if task and task.state == "failed" and not ready and eligible(demand):
                control = signing.dumps(
                    {
                        "actor": str(actor.identity),
                        "campaign": str(campaign_id),
                        "receipt": str(receipt.pk),
                        "demand": str(demand.pk),
                        "task": str(task.pk),
                        "version": task.version,
                        "key": str(uuid4()),
                    },
                    salt=SALT,
                )
        return {
            "campaign": receipt.request.campaign,
            "receipt": receipt,
            "demand": demand,
            "task": task,
            "complete": ready,
            "control": control,
            "outcomes": outcomes,
        }


def retry(request, service, campaign_id, *, token):
    """Serialize retries with current lifecycle and exact persisted task ownership."""
    if type(token) is not str or len(token) > 4096:
        raise ValueError("Invalid Production progress control.")
    with work_transaction():
        binding = signing.loads(token, salt=SALT, max_age=300)
        if (
            type(binding) is not dict
            or set(binding)
            != {"actor", "campaign", "receipt", "demand", "task", "version", "key"}
            or type(binding["version"]) is not int
            or binding["version"] < 1
            or any(
                type(value) is not str
                for key, value in binding.items()
                if key != "version"
            )
        ):
            raise ValueError("Invalid Production progress control.")
        actor, receipt = _current(request, service, campaign_id, passive=False)
        if (binding["actor"], binding["campaign"], binding["receipt"]) != (
            str(actor.identity),
            str(campaign_id),
            str(receipt.pk),
        ):
            raise PermissionError("Production retry belongs to another scope.")
        demand = ActivationCatchUpDemand.objects.get(
            pk=UUID(binding["demand"]), activation_id=receipt.activation_id
        )
        task = TaskRun.objects.get(
            pk=UUID(binding["task"]), root_id=demand.task_root_id
        )
        if task.version != binding["version"]:
            raise StaleRecordError("Preparation changed; refresh before retrying.")

        def admit(action, status):
            """An identical retry replay also requires current authority and scope."""
            current, selected = _current(request, service, campaign_id)
            return (
                current.identity == actor.identity
                and selected.pk == receipt.pk
                and status.domain_request_id == demand.pk
                and status.root_id == demand.task_root_id
                and admit_catchup(action, status)
            )

        try:
            return retry_failed(
                run_id=task.pk,
                command_id=UUID(binding["key"]),
                actor_id=actor.identity,
                correlation_id=current_correlation(),
                admit=admit,
            )
        except TaskRetryConflict:
            raise StaleRecordError(
                "Preparation changed; refresh before retrying."
            ) from None
