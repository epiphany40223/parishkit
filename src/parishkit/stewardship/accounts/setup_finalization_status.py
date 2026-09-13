"""Safe installation progress on the existing original-login cancellation surface."""

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun

from .request_models import ConfigurationRequestCheckpoint
from .setup_cancellation import _owned
from .setup_install_models import SetupCredentialInstallation, SetupPreparationReceipt
from .setup_models import SetupConfigurationIntent
from .setup_staging import _status, _window


def finalization_status(request, service):
    """Return only exact-owning metadata; do not renew idle time or grant editing."""
    with work_transaction():
        _, attempt = _owned(request, service)
        intent = SetupConfigurationIntent.objects.get(attempt=attempt)
        checkpoint = (
            ConfigurationRequestCheckpoint.objects.filter(request=intent.request)
            .order_by("-sequence")
            .values_list("state", flat=True)
            .first()
        )
        credentials = list(
            SetupCredentialInstallation.objects.filter(readiness__intent=intent)
            .order_by("target")
            .values("target", "request__state")
        )
        prepared = SetupPreparationReceipt.objects.filter(
            readiness__intent=intent
        ).first()
        source = None
        if prepared is not None:
            source = (
                TaskRun.objects.filter(
                    task_type="setup_finalize",
                    domain_request_id=prepared.pk,
                    initiated_by_id=attempt.owner_id,
                )
                .order_by("-created_at", "-id")
                .values("id", "state", "phase", "progress_current", "progress_total")
                .first()
            )
            if source is not None:
                source["progress"] = Percentage(
                    source["progress_current"], source["progress_total"]
                )
        return {
            "attempt": _status(attempt),
            "checkpoint": checkpoint,
            "credentials": credentials,
            "source": source,
            "prepared": prepared is not None,
            "deadlines": _window(attempt, request.portal_session),
        }
