"""Durable session attribution; authentication is still disabled until ARC-04."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField

from .auth_models import (  # noqa: F401
    AuthenticationIncident,
    LimiterStoreHealth,
    OAuthStateConsumption,
)
from .branding_models import BrandingAsset, BrandingBundle  # noqa: F401
from .campaign_mail_models import CampaignMailTest  # noqa: F401
from .chair_models import (  # noqa: F401
    ChairAssignmentReview,
    ChairReconciliation,
    ChairSeedEvidence,
)

# Django discovers these models through this module; keep session and immutable
# configuration contracts in separate source files for maintainability.
from .configuration_models import (  # noqa: F401
    AppliedConfigurationVersion,
    AppliedIntegration,
    MinistryActivity,
    Parish,
)
from .content_models import ContentVersion  # noqa: F401
from .handoff_models import PublicCredentialHandoff  # noqa: F401
from .policy_models import (  # noqa: F401
    AddressRoleGrant,
    AddressRule,
    AdminRevocation,
    AssignmentOverlay,
    DomainRule,
    MinistryAssignment,
    PolicyEpoch,
    PolicySecurityEvent,
    PortalUser,
)
from .provider_models import ProviderValidationContext  # noqa: F401
from .request_models import (  # noqa: F401
    ConfigurationChangeRequest,
    ConfigurationRequestCheckpoint,
)
from .runtime_models import ConfigurationActivation, SystemConfiguration  # noqa: F401
from .secret_models import (  # noqa: F401
    CredentialConsumerAcknowledgement,
    SealedCredentialStaging,
    SecretReplacementRequest,
    SecretRequestCheckpoint,
)
from .setup_delivery_models import SetupMailDelivery  # noqa: F401
from .setup_exchange_models import SetupSourceExchange, SetupSourceResult  # noqa: F401
from .setup_install_models import (
    SetupCompletion,  # noqa: F401
    SetupCredentialInstallation,  # noqa: F401
    SetupPreparationReceipt,  # noqa: F401
)
from .setup_mail_exchange_models import SetupMailExchange  # noqa: F401
from .setup_models import (  # noqa: F401
    SetupAttempt,
    SetupConfigurationAbort,
    SetupConfigurationIntent,
    SetupDraftSection,
)
from .setup_notification_models import SetupSlackDelivery  # noqa: F401
from .setup_readiness_models import SetupReadinessBinding  # noqa: F401
from .setup_secret_models import SetupSealedCredential  # noqa: F401


class PortalSession(MutableRecord):
    """Server-side session metadata without storing credential values in audit.

    The protected Django session relation prevents accidental session cleanup
    from erasing attribution. The identity service must revoke/delete this
    expiring metadata explicitly before deleting the session. Historical audits
    keep only this record's opaque UUID, never the Django session key.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "principal_id",
        "session_id",
        "authenticated_at",
    )
    write_once_fields = ("revoked_at",)

    session = models.OneToOneField(
        "sessions.Session", on_delete=models.PROTECT, related_name="stewardship_portal"
    )
    principal_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    last_activity_at = UTCDateTimeField()
    expires_at = UTCDateTimeField()
    revoked_at = UTCDateTimeField(null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_portal_session"
        indexes = [
            models.Index(
                fields=["principal_id", "revoked_at"], name="portal_session_principal"
            ),
            models.Index(fields=["expires_at"], name="portal_session_expiry"),
        ]
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("authenticated_at")),
                name="portal_session_positive_lifetime",
            ),
            models.CheckConstraint(
                condition=models.Q(last_activity_at__gte=models.F("authenticated_at")),
                name="portal_session_activity_after_auth",
            ),
            models.CheckConstraint(
                condition=models.Q(last_activity_at__lt=models.F("expires_at")),
                name="portal_session_activity_before_expiry",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_at__isnull=True)
                | models.Q(revoked_at__gte=models.F("authenticated_at")),
                name="portal_session_revoked_after_auth",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_at__isnull=True)
                | models.Q(revoked_at__gte=models.F("last_activity_at")),
                name="portal_session_no_activity_after_revoke",
            ),
        ]
