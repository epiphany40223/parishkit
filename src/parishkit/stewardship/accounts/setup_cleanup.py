"""Release only never-applied setup assets after journaled restoration completes."""

from django.db.models import Exists, OuterRef, Subquery

from .request_models import ConfigurationChangeRequest, ConfigurationRequestCheckpoint
from .runtime_models import ConfigurationActivation
from .setup_models import SetupConfigurationAbort


def aborted_setup_candidates():
    """A retained journal alone is insufficient: failed checkpoint proves restoration.

    The candidate's public immutable configuration history remains verifiable.
    Wizard-only media may be scrubbed only after that candidate can never apply
    and the installer has completed its manifest restoration. Any other retained
    configuration referencing the same assets continues to pin them normally.
    """
    latest = ConfigurationRequestCheckpoint.objects.filter(
        request_id=OuterRef("pk")
    ).order_by("-sequence")
    return (
        ConfigurationChangeRequest.objects.annotate(
            aborted=Exists(
                SetupConfigurationAbort.objects.filter(
                    intent__request_id=OuterRef("pk")
                )
            ),
            activated=Exists(
                ConfigurationActivation.objects.filter(request_id=OuterRef("pk"))
            ),
            final_state=Subquery(latest.values("state")[:1]),
            final_failure=Subquery(latest.values("failure_code")[:1]),
        )
        .filter(
            request_schema="initial-setup-patch-v7",
            aborted=True,
            activated=False,
            final_state="failed",
            final_failure="invalid_candidate",
        )
        .values("candidate_version_id")
    )
