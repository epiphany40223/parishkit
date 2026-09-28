"""Safe installation progress on the existing original-login cancellation surface."""

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun

from .models import PortalSession
from .request_models import ConfigurationRequestCheckpoint
from .secret_models import CredentialConsumerAcknowledgement
from .sessions import database_now
from .setup_cancellation import _owned
from .setup_install_models import SetupCredentialInstallation, SetupPreparationReceipt
from .setup_models import SetupConfigurationIntent, SetupDraftSection
from .setup_staging import _status, _window


def finalization_status(request, service):
    """Return only exact-owning metadata; do not renew idle time or grant editing."""
    with work_transaction():
        _, attempt = _owned(request, service)
        intent = SetupConfigurationIntent.objects.get(attempt=attempt)
        latest = (
            ConfigurationRequestCheckpoint.objects.filter(request=intent.request)
            .order_by("-sequence")
            .values("state", "failure_code")
            .first()
        )
        checkpoint = latest["state"] if latest else None
        credentials = list(
            SetupCredentialInstallation.objects.filter(readiness__intent=intent)
            .order_by("target")
            .values(
                "target",
                "request_id",
                "request__state",
                "request__cleanup_reason",
                "request__required_consumers",
                "request__resulting_fingerprint",
            )
        )
        # Count each credential's consumers that loaded exactly the installed
        # value; fingerprints and consumer names stay on the server.
        for item in credentials:
            loaded = dict(
                CredentialConsumerAcknowledgement.objects.filter(
                    request_id=item["request_id"]
                ).values_list("consumer", "fingerprint")
            )
            required = item["request__required_consumers"]
            item["consumers"] = len(required)
            item["acknowledged"] = sum(
                loaded.get(name) == item["request__resulting_fingerprint"]
                and loaded.get(name) is not None
                for name in required
            )
        slack = (
            SetupDraftSection.objects.filter(
                attempt=attempt, step="slack", scrubbed_at=None
            )
            .values_list("values", flat=True)
            .first()
        )
        login = PortalSession.objects.values_list("authenticated_at", flat=True).get(
            pk=attempt.session_id
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
            "failure_code": latest["failure_code"] if latest else "",
            "confirmed_at": intent.created_at,
            "authenticated_at": login,
            "server_now": database_now(),
            # Slack is installed only when the frozen draft enabled it.
            "targets": ["parishsoft", "google_workspace"]
            + (["slack"] if (slack or {}).get("enabled") else []),
            "credentials": credentials,
            "source": source,
            "prepared": prepared is not None,
            "deadlines": _window(attempt, request.portal_session),
        }
