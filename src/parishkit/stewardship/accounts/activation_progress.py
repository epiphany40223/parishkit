"""Current-Admin preparation controls; signatures bind intent, never authority."""

from dataclasses import asdict
from uuid import UUID, uuid4

from django.core import signing

from parishkit.stewardship.campaigns.activation_cleanup import (
    disposed,
    request_cancellation,
)
from parishkit.stewardship.campaigns.activation_inputs import TokenPreparationInputs
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.activation_tokens import (
    current_inputs,
    preparation_available,
    prepared_generation,
    request_preparation,
    require_current,
)
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.storage import retry_failed
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError

from .go_live_progress import _current

SALT = "stewardship-go-live-links-control-v1"


def _inputs(value):
    """Use a closed, JSON-safe input projection for exact signed preview binding."""
    return {
        key: str(item) if isinstance(item, UUID) else item
        for key, item in asdict(value).items()
    }


def _latest(root_id):
    """A failed historical execution must never hide its running retry child."""
    return TaskRun.objects.filter(root_id=root_id).order_by("-retry_sequence").first()


def _sign(actor, row, action, *, preparation=None, task=None, inputs=None):
    """Bind the complete owner scope and one opaque idempotency key."""
    return signing.dumps(
        {
            "actor": str(actor.identity),
            "campaign": str(row.campaign_id),
            "request": str(row.pk),
            "action": action,
            "preparation": str(preparation.pk) if preparation else "",
            "task": str(task.pk) if task else "",
            "version": task.version if task else 0,
            "inputs": _inputs(inputs) if inputs else None,
            "key": str(uuid4()),
        },
        salt=SALT,
    )


def progress(request, service, campaign_id, request_id, *, window):
    """Read a bounded history without renewing idle time or enumerating Families."""
    with work_transaction():
        actor, row = _current(request, service, campaign_id, request_id, passive=True)
        try:
            inputs = current_inputs(row)
        except StaleRecordError:
            inputs = None
        preparations = ProductionTokenPreparation.objects.filter(transition=row)
        available = inputs is not None and preparation_available(row)
        records, has_next = window.rows(preparations.order_by("-created_at", "-id"))
        results = []
        for preparation in records:
            task = _latest(preparation.task_id)
            cancellation = ProductionTokenCancellation.objects.filter(
                preparation=preparation
            ).first()
            cleanup = _latest(cancellation.task_id) if cancellation else None
            generation = prepared_generation(preparation)
            current = (
                inputs is not None
                and not cancellation
                and TokenPreparationInputs.retained(preparation) == inputs
            )
            controls = {}
            if not cancellation and (
                not disposed(preparation) or task.state in NONTERMINAL_STATES
            ):
                controls["cancel"] = _sign(
                    actor, row, "cancel", preparation=preparation
                )
            if current and task.state == "failed":
                controls["retry"] = _sign(
                    actor, row, "retry", preparation=preparation, task=task
                )
            if cleanup and cleanup.state == "failed":
                controls["retry_cleanup"] = _sign(
                    actor, row, "retry_cleanup", preparation=preparation, task=cleanup
                )
            results.append(
                {
                    "preparation": preparation,
                    "task": task,
                    "cleanup": cleanup,
                    "generation": generation,
                    "current": current,
                    "completion": Percentage(
                        task.progress_current, preparation.eligible_count
                    ),
                    "controls": controls,
                }
            )
        return {
            "campaign": row.campaign,
            "transition": row,
            "records": results,
            "prepare": _sign(actor, row, "prepare", inputs=inputs)
            if available
            else None,
            "inputs_unavailable": inputs is None,
            "page": window.page,
            "previous_page": window.page - 1,
            "next_page": window.page + 1,
            "has_next": has_next,
        }


def control(request, service, campaign_id, request_id, *, token):
    """Recheck current authority and durable scope after waiting for common locks."""
    if type(token) is not str or len(token) > 4096:
        raise ValueError("Invalid link preparation control.")
    with work_transaction():
        binding = signing.loads(token, salt=SALT, max_age=300)
        if (
            type(binding) is not dict
            or set(binding)
            != {
                "actor",
                "campaign",
                "request",
                "action",
                "preparation",
                "task",
                "version",
                "inputs",
                "key",
            }
            or binding["action"] not in {"prepare", "cancel", "retry", "retry_cleanup"}
            or any(
                type(binding[key]) is not str
                for key in (
                    "actor",
                    "campaign",
                    "request",
                    "action",
                    "preparation",
                    "task",
                    "key",
                )
            )
            or type(binding["version"]) is not int
        ):
            raise ValueError("Invalid link preparation control.")
        actor, row = _current(request, service, campaign_id, request_id, passive=False)
        if (binding["actor"], binding["campaign"], binding["request"]) != (
            str(actor.identity),
            str(campaign_id),
            str(request_id),
        ):
            raise PermissionError("Link preparation belongs to another scope.")

        def admit(*unused):
            """A replay also checks identity; a signature is not bearer authority."""
            current, _ = _current(
                request, service, campaign_id, request_id, passive=True
            )
            return current.identity == actor.identity

        common = dict(
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            admit=admit,
        )
        if binding["action"] == "prepare":
            if binding["inputs"] != _inputs(current_inputs(row)):
                raise StaleRecordError(
                    "Link preparation inputs changed; refresh this page."
                )
            return request_preparation(
                transition_id=request_id, request_key=UUID(binding["key"]), **common
            )
        preparation = ProductionTokenPreparation.objects.get(
            pk=UUID(binding["preparation"]), transition=row
        )
        if binding["action"] == "cancel":
            return request_cancellation(
                preparation_id=preparation.pk,
                request_key=UUID(binding["key"]),
                **common,
            )
        if binding["action"] == "retry":
            require_current(preparation)
            root_id = preparation.task_id
        else:
            cancellation = ProductionTokenCancellation.objects.get(
                preparation=preparation
            )
            root_id = cancellation.task_id
        task = TaskRun.objects.get(pk=UUID(binding["task"]), root_id=root_id)
        if task.version != binding["version"]:
            raise StaleRecordError("The task changed; refresh before retrying.")
        return retry_failed(run_id=task.pk, command_id=UUID(binding["key"]), **common)
