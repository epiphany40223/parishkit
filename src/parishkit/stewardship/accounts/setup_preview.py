"""Original-login exact public preview, without freezing or activating setup."""

from dataclasses import dataclass, field
from uuid import UUID, uuid5

from django.core import signing

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction

from .branding_staging import staged_bundle
from .configuration_requests import policy_operation_id
from .request_patch import PatchedConfiguration
from .setup_campaign import admit_campaign_values
from .setup_candidate import CandidateCredential, compile_candidate
from .setup_drafts import DraftView, view_draft
from .setup_secret_models import SetupSealedCredential

PREVIEW_SALT = "stewardship-initial-setup-preview-v1"


@dataclass(frozen=True)
class SetupPreview:
    """Detached checked preview; never log its public configuration or access list."""

    draft: DraftView = field(repr=False)
    compiled: PatchedConfiguration = field(repr=False)
    request_key: UUID
    operation_id: UUID
    branding: dict = field(repr=False)

    def binding(self):
        """Sign fixed-size identity/digest evidence, not megabytes of copied content."""
        return {
            "attempt": str(self.draft.status.attempt_id),
            "version": self.draft.status.version,
            "base": self.compiled.candidate.predecessor_digest,
            "candidate": self.compiled.candidate.digest,
            "request_key": str(self.request_key),
        }


def prepare_preview(request, service):
    """Compile only the original owner's current ready source, logo and settings."""
    with work_transaction():
        draft = view_draft(request, service)
        if draft is None or draft.status.state != "collecting":
            raise LookupError("An original collecting setup draft is required.")
        required = {
            "parish",
            "access",
            "branding",
            "mail",
            "slack",
            "testing",
            "campaign",
            "schedules",
        }
        if not required <= draft.sections.keys():
            raise ConfigError("Complete the setup sections before final preview.")
        identifier = draft.status.attempt_id
        admit_campaign_values(request, service, identifier, draft.sections["campaign"])
        _, assets = staged_bundle(
            request,
            service,
            UUID(draft.sections["branding"]["bundle_id"]),
            setup_attempt_id=identifier,
        )
        branding = {asset.label: str(asset.pk) for asset in assets}
        credentials = [
            CandidateCredential(**values)
            for values in SetupSealedCredential.objects.filter(
                attempt_id=identifier, scrubbed_at=None
            ).values("target", "fingerprint", "settings")
        ]
        request_key = uuid5(identifier, f"finalize:{draft.status.version}")
        operation_id = policy_operation_id(
            request.portal_session.principal_id, request_key
        )
        result = compile_candidate(
            service.store.active(),
            attempt_id=identifier,
            candidate_id=uuid5(operation_id, "initial-configuration"),
            operation_id=operation_id,
            sections=draft.sections,
            branding=branding,
            credentials=credentials,
        )
        return SetupPreview(draft, result, request_key, operation_id, branding)


def verify_preview(request, service, token):
    """A signature never replaces fresh session, source, credential and draft checks."""
    if type(token) is not str or len(token) > 4096:
        raise ValueError("Invalid setup preview.")
    binding = signing.loads(token, salt=PREVIEW_SALT, max_age=900)
    current = prepare_preview(request, service)
    if current.binding() != binding:
        from parishkit.stewardship.storage import StaleRecordError

        raise StaleRecordError("Setup changed; review a new exact preview.")
    return current
