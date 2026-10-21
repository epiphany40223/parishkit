"""The one offline conversion of the Daily and Weekly digests (#1786).

The report-emails spec's "Conversion" is the contract. Every Daily and
Weekly digest record of the applied configuration, in every campaign, keeps
its record ID and gets its report values (``reports_conversion``); nothing
else changes. The converted document is installed through the
configuration installer's ordinary checkpoint protocol as one tagged
``operator_upgrade`` request (operator "system", reason "#1786 conversion"),
so the YAML and the database stay in step, and its activation retypes each
digest definition in place (frozen file 0161). The activation is refused,
and the request recorded as failed, while any digest send is in progress.

It runs only offline, as ``pk-stewardship upgrade-reports`` under the
admin-recovery profile, after the migrations and grants of the release
that brings report emails (the frozen file's header gives the order). A
repeat resumes an unfinished conversion; a configuration with no digest
left is not changed.
"""

from uuid import UUID, uuid4

from django.db import transaction

from parishkit.config import ConfigError

from .configuration_installation import _install_request, coherent_configuration
from .configuration_requests import _own_transaction, _status
from .reports_conversion import upgrade_patch
from .reports_schema import UPGRADE_REQUEST_SCHEMA
from .request_admission import intake_base
from .request_models import ConfigurationChangeRequest
from .request_patch import build_candidate
from .runtime_models import SystemConfiguration
from .users_schema import uses_users

OPERATOR = "system"
REASON = "#1786 conversion"
UNFINISHED = frozenset({"staged", "validating", "prepared", "yaml_activated"})


def unfinished_conversion():
    """The conversion request still in progress, or None."""
    for request in ConfigurationChangeRequest.objects.filter(
        authority="operator_upgrade", request_schema=UPGRADE_REQUEST_SCHEMA
    ).order_by("-created_at"):
        try:
            state = _status(request).state
        except ConfigError:
            # Created but not yet checkpointed: the installer starts it.
            return request
        if state in UNFINISHED:
            return request
    return None


def digests_remain():
    """Whether any schedule definition still has a digest kind.

    The conversion's own check. The services' startup refusal
    (``require_reports_converted``) reads the applied document instead,
    which the conversion's activation brings in step with the definitions.
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition

    return ScheduleDefinition.objects.filter(
        kind__in=("daily_digest", "weekly_digest")
    ).exists()


def require_reports_converted(runtime):
    """Refuse to run before ``upgrade-reports`` converted the digests (#1786).

    The release's SQL and Python serve only report emails, so no service
    may start while the applied configuration still holds a Daily or Weekly
    digest (``runtime`` is the system configuration row, whose applied
    document is already loaded).
    """
    from .reports_schema import has_digests

    document = getattr(runtime.active_configuration, "canonical_document", None)
    if document is not None and has_digests(document):
        raise ConfigError(
            "The configuration still holds Daily or Weekly digests; "
            "run upgrade-reports first."
        )


def upgrade_reports(store, *, deployment_id, correlation_id, offline_interlock):
    """Convert the digests once, offline, or refuse; resume an unfinished run.

    Returns the request status, or None when nothing is left to convert.
    Only state "applied" is success; a refused or failed receipt is
    terminal, and a later run makes a new request.
    """
    if any(not isinstance(value, UUID) for value in (deployment_id, correlation_id)):
        raise TypeError("Explicit deployment and correlation UUIDs are required.")
    if not callable(offline_interlock):
        raise ConfigError("An offline interlock is required.")
    _own_transaction()
    with offline_interlock():
        try:
            runtime = SystemConfiguration.objects.get()
        except (
            SystemConfiguration.DoesNotExist,
            SystemConfiguration.MultipleObjectsReturned,
        ):
            raise ConfigError(
                "The conversion needs one initialized deployment."
            ) from None
        if runtime.pk != deployment_id:
            raise ConfigError("The confirmed deployment does not match.")
        request = unfinished_conversion()
        if request is None:
            runtime = coherent_configuration(store)
            base, version = intake_base(runtime.active_configuration.digest)
            document = version.document()
            patch = upgrade_patch(document)
            if not patch:
                return None
            if not uses_users(document):
                raise ConfigError("Run upgrade-users before converting the digests.")
            intent = build_candidate(
                version,
                patch,
                candidate_id=uuid4(),
                request_schema=UPGRADE_REQUEST_SCHEMA,
            )
            with transaction.atomic(durable=True):
                request = ConfigurationChangeRequest.objects.create(
                    base=base,
                    patch=intent.patch(),
                    actor_id=None,
                    request_key=uuid4(),
                    request_schema=UPGRADE_REQUEST_SCHEMA,
                    correlation_id=correlation_id,
                    payload_fingerprint=intent.payload_fingerprint,
                    candidate_version_id=intent.candidate.version_id,
                    candidate_digest=intent.candidate.digest,
                    authority="operator_upgrade",
                    operator_name=OPERATOR,
                    operator_reason=REASON,
                    confirmed_deployment_id=deployment_id,
                )
        result = _install_request(store, request=request, correlation_id=correlation_id)
        if result.state == "applied":
            coherent_configuration(store)
        return result
