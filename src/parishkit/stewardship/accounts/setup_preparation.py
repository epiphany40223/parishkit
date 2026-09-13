"""Select initial YAML without activating source, Families or product state.

Only the configuration installer writes these public files and projections. It
stops at yaml_activated until the separate atomic setup owner commits the whole
configured state. All initial credentials retain their rollback material.
"""

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

from .configuration_errors import ConfigurationReadinessUnavailable
from .configuration_requests import _status
from .configuration_service import admit_configuration_database
from .integration_selection import authentication_scope, integration_records
from .provider_models import ProviderValidationContext
from .request_admission import check_historical_additions
from .request_patch import build_candidate
from .secret_models import CredentialConsumerAcknowledgement
from .setup_credential_installation import ready_live
from .setup_install_models import SetupCredentialInstallation, SetupPreparationReceipt
from .setup_readiness_models import SetupReadinessBinding


def _readiness(request, document):
    """Require original ownership and the exact initial target/consumer set."""
    readiness = SetupReadinessBinding.objects.filter(intent__request=request).first()
    if readiness is None or not ready_live(readiness.pk):
        raise ConfigurationReadinessUnavailable("Original setup is unavailable.")
    records = integration_records(document)
    targets = {"parishsoft", "google_workspace"}
    if readiness.slack_delivery_id is not None:
        targets.add("slack")
    if targets != records.keys() & {"parishsoft", "google_workspace", "slack"}:
        raise ConfigError("Initial integrations differ from reviewed readiness.")
    bindings = {
        row.target: row
        for row in SetupCredentialInstallation.objects.select_related("request").filter(
            readiness=readiness
        )
    }
    if bindings.keys() != targets:
        raise ConfigurationReadinessUnavailable("Initial credentials are installing.")
    for target, binding in bindings.items():
        receipt = binding.request
        required = sorted(
            role.value for role, names in ALLOWED_SECRETS.items() if target in names
        )
        fingerprint = records[target]["values"]["credential_fingerprint"]
        acknowledgements = dict(
            CredentialConsumerAcknowledgement.objects.filter(
                request=receipt
            ).values_list("consumer", "fingerprint")
        )
        if (
            receipt.state != "awaiting_ack"
            or receipt.resulting_fingerprint != binding.fingerprint
            or fingerprint != binding.fingerprint
            or sorted(receipt.required_consumers) != required
            or acknowledgements != {consumer: fingerprint for consumer in required}
        ):
            raise ConfigurationReadinessUnavailable("Initial consumers are not ready.")
        context = ProviderValidationContext.objects.get(request=receipt, target=target)
        if context.settings != authentication_scope(
            target, records, recipient=context.settings.get("recipient")
        ):
            raise ConfigError("Initial credentials have different provider settings.")
    return readiness


def prepare_setup_configuration(materializer):
    """Resume exact public preparation under the pinned installation lock.

    Every installer pass processes the existing cancellation/abort journal
    before this entry point. Cancellation during file IO cannot activate product
    state; its next pass restores the predecessor through that same journal.
    """
    admit_configuration_database()
    materializer._check()
    request = materializer.request
    if request is None or request.request_schema != "initial-setup-patch-v7":
        raise ConfigError("Only initial setup may prepare without activation.")
    candidate = build_candidate(
        materializer.store.read_version(request.base_id),
        request.patch,
        candidate_id=request.candidate_version_id,
        request_schema=request.request_schema,
    )
    if (
        candidate.candidate.digest != request.candidate_digest
        or candidate.payload_fingerprint != request.payload_fingerprint
    ):
        raise ConfigError("Initial candidate differs from its frozen request.")
    _readiness(request, candidate.candidate.document())
    selected = materializer.store.active()
    if selected is None or selected.version_id not in {
        request.base_id,
        request.candidate_version_id,
    }:
        raise ConfigError("Initial setup has unrelated YAML authority.")
    if materializer.active_digest() != request.base.digest:
        raise ConfigError("Initial setup no longer has its bootstrap database base.")
    if selected.version_id == request.candidate_version_id:
        if selected != candidate.candidate or not materializer.is_prepared(
            selected.digest
        ):
            raise ConfigError("Selected setup has no matching prepared projection.")
        materializer.checkpoint("yaml_activated")
        _record_prepared(materializer, candidate.candidate.document())
        return _status(request)
    check_historical_additions(request.base_id, request.patch)
    if _status(request).state == "staged":
        materializer.checkpoint("validating")
    materializer.store.write_version(candidate.candidate)
    materializer.prepare(candidate.candidate)
    if not materializer.is_prepared(candidate.candidate.digest):
        raise ConfigError("Initial setup projection is incomplete.")
    _readiness(request, candidate.candidate.document())
    materializer._check()
    materializer.store.select(candidate.candidate)
    materializer._check()
    materializer.checkpoint("yaml_activated")
    _record_prepared(materializer, candidate.candidate.document())
    return _status(request)


def _record_prepared(materializer, document):
    """Publish only safe exact installation proof, serialized against cancellation."""
    with work_transaction():
        materializer._check()
        readiness = _readiness(materializer.request, document)
        existing = SetupPreparationReceipt.objects.filter(readiness=readiness).first()
        if existing is not None:
            if existing.configuration_id != materializer.request.candidate_version_id:
                raise ConfigError("Prepared setup receipt differs from its candidate.")
            return existing
        return SetupPreparationReceipt.objects.create(
            readiness=readiness,
            configuration_id=materializer.request.candidate_version_id,
            actor_id=materializer.actor_id,
            correlation_id=materializer.correlation_id,
        )
