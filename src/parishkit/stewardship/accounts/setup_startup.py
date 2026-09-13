"""Recognize an exact initial YAML-selection gap without granting its authority.

A restarting scheduler must be able to expire an abandoned original login so
the installer can restore its predecessor. Requiring a live login here would
deadlock recovery. Ordinary routes and tasks still require current coherence;
the finalization owner separately requires the live original prepared attempt.
"""

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction

from .configuration_models import AppliedConfigurationVersion
from .request_admission import intake_base
from .request_models import ConfigurationRequestCheckpoint
from .runtime_models import ConfigurationActivation, SystemConfiguration
from .setup_models import SetupConfigurationIntent


def initial_setup_hold(store, *, projections=True):
    """Return only the still-applied bootstrap runtime for recovery-only startup.

    No session is authorized or renewed, no configuration is selected, and no
    caller may use this returned predecessor in ordinary policy admission. Mail
    needs only the immutable canonical-document proof, not unused projections.
    """
    with work_transaction():
        selected = store.active()
        runtime = SystemConfiguration.objects.select_related(
            "active_configuration"
        ).first()
        if (
            selected is None
            or runtime is None
            or runtime.active_configuration_id is None
            or runtime.mode != "testing"
            or runtime.restore_review_required
            or runtime.current_campaign_id is not None
            or runtime.active_configuration.validation_schema != "bootstrap-policy-v1"
        ):
            raise ConfigError("Initial setup recovery authority is unavailable.")
        intent = (
            SetupConfigurationIntent.objects.select_related("request", "attempt")
            .filter(
                request__candidate_version_id=selected.version_id,
                request__candidate_digest=selected.digest,
                request__base_id=runtime.active_configuration_id,
                request__request_schema="initial-setup-patch-v7",
                attempt__base_id=runtime.active_configuration_id,
                attempt__state__in=("frozen", "expired"),
            )
            .first()
        )
        if (
            intent is None
            or ConfigurationActivation.objects.filter(request=intent.request).exists()
        ):
            raise ConfigError("Initial setup recovery has no exact original intent.")
        if (
            intent.attempt.owner_id != intent.request.actor_id
            or ConfigurationRequestCheckpoint.objects.filter(request=intent.request)
            .order_by("-sequence")
            .values_list("state", flat=True)
            .first()
            not in {"validating", "prepared", "yaml_activated"}
        ):
            raise ConfigError("Initial setup recovery checkpoint differs.")
        candidate = AppliedConfigurationVersion.objects.filter(
            pk=selected.version_id,
            digest=selected.digest,
            predecessor_id=runtime.active_configuration_id,
        ).first()
        if candidate is None or candidate.canonical_document != selected.document():
            raise ConfigError("Initial setup recovery document differs.")
        if projections and intake_base(selected.digest)[1] != selected:
            raise ConfigError("Initial setup recovery projections differ.")
        if store.manifest_reference() != (selected.version_id, selected.digest):
            raise ConfigError("Initial setup recovery selection changed.")
        return runtime
