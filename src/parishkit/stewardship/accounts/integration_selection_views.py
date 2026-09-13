"""Fresh-Admin confirmation of an installed credential's non-secret YAML reference."""

from uuid import uuid4

from django.core import signing
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .integration_forms import LABELS
from .integration_selection import (
    TARGETS,
    StaleCredentialReceipt,
    current_receipt,
    integration_records,
)
from .integration_views import ERRORS, _checked
from .request_admission import intake_base
from .request_patch import build_candidate, credential_request_schema
from .secret_models import SecretReplacementRequest
from .sessions import require_fresh

SALT = "stewardship-integration-selection-v1-"


def _preview_schema(request, request_id):
    """Choose the frozen signed base's parser even after a later cadence edit.

    The shared confirmation owner independently checks signature, actor, current
    receipt and authorization. Reading this immutable base grants no authority.
    """
    token = request.POST.get("preview", "")
    if len(token) > 256_000:
        raise ValueError("Invalid configuration preview.")
    intent = signing.loads(token, salt=SALT + str(request_id), max_age=900)
    _, base = intake_base(intent["base"])
    return credential_request_schema(base.document())


def _selection(service, request_id):
    """The original request is correlation, not authority or proof of current use."""
    configuration = editable_configuration(service)
    receipt = SecretReplacementRequest.objects.filter(
        pk=request_id,
        state="applied",
        target__in=TARGETS,
    ).first()
    if receipt is None:
        raise LookupError("An acknowledged replacement is unavailable.")
    records = integration_records(configuration.active_configuration.canonical_document)
    if receipt.target not in records:
        raise StaleRecordError("This integration is no longer configured.")
    try:
        proof = current_receipt(receipt.target, receipt.resulting_fingerprint, records)
    except StaleCredentialReceipt:
        raise StaleRecordError("This replacement is no longer current.") from None
    if proof.pk != receipt.pk:
        raise StaleRecordError("This replacement is no longer current.")
    record = records[receipt.target]
    if record["values"]["credential_fingerprint"] not in {
        receipt.expected_fingerprint,
        receipt.resulting_fingerprint,
    }:
        raise StaleRecordError("The integration fingerprint changed.")
    return configuration, receipt, record


@require_http_methods(["GET", "HEAD", "POST"])
def select_credential(request, request_id):
    """Any fresh Admin may select a receipt using their own exact signed preview."""
    try:
        service = runtime()
        actor = principal(request, service)
        require_fresh(request)
        if request.FILES:
            raise ValueError("Credential selection accepts no files.")
        filters(request.GET, allowed=set())
        if request.method == "POST":
            if form_action(request.POST, preview_fields=set()) != "confirm":
                raise ValueError("Confirm the exact credential selection.")

            def scope(service):
                """Intake repeats freshness and current receipt proof under its lock."""
                require_fresh(request)
                configuration, _, _ = _selection(service, request_id)
                return configuration, None

            response = confirm(
                request,
                service,
                actor,
                salt=SALT + str(request_id),
                current_scope=scope,
                request_schema=_preview_schema(request, request_id),
            )
        else:
            with work_transaction():
                configuration, receipt, record = _selection(service, request_id)
                base = service.store.active()
                if (
                    base is None
                    or base.digest != configuration.active_configuration.digest
                ):
                    raise StaleRecordError("The credential preview base changed.")
            # Preview/render work uses the captured immutable inputs without
            # blocking task claims or source promotion. Confirmation rechecks
            # current receipt, freshness and base under the owning work lock.
            selected = (
                record["values"]["credential_fingerprint"]
                == receipt.resulting_fingerprint
            )
            patch = [
                {
                    "operation": "update",
                    "section": "integrations",
                    "id": record["id"],
                    "values": {"credential_fingerprint": receipt.resulting_fingerprint},
                }
            ]
            preview = None
            if not selected:
                build_candidate(
                    base,
                    patch,
                    candidate_id=uuid4(),
                    request_schema=credential_request_schema(base.document()),
                )
                preview = sign_preview(
                    actor=actor,
                    configuration=configuration,
                    patch=patch,
                    salt=SALT + str(request_id),
                )
            response = render(
                request,
                "stewardship/credential-selection.html",
                {
                    "receipt": receipt,
                    "label": LABELS[receipt.target],
                    "before": record["values"]["credential_fingerprint"],
                    "preview": preview,
                    "selected": selected,
                },
            )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)
