"""Fresh-Admin confirmation of an installed credential's non-secret YAML reference."""

from uuid import uuid4

from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import load_preview

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .integration_credentials import switch_patch
from .integration_forms import LABELS, IntegrationForm
from .integration_selection import (
    TARGETS,
    OrganizationLocked,
    StaleCredentialReceipt,
    current_receipt,
    integration_records,
    refuse_organization_change,
)
from .integration_views import ERRORS, _checked, place_key_page
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
    intent = load_preview(token, salt=SALT + str(request_id), link=request.path)
    _, base = intake_base(intent["base"])
    return credential_request_schema(base.document())


def _proposed(records, patch):
    """The integration records as they would be once ``patch`` applies."""
    item = patch[0]
    target = item["values"].get("kind") or next(
        kind for kind, row in records.items() if row["id"] == item["id"]
    )
    current = records.get(target, {"id": item["id"], "values": {}})
    return records | {
        target: {**current, "values": {**current["values"], **item["values"]}}
    }


def _selection(service, request_id):
    """Check the key can still be switched to, and return the patch that does it.

    The original request is correlation, not authority or proof of current
    use. The patch repeats the key's original selection on the current
    settings (``switch_patch``), so **Finish switching** also recovers a
    selection that failed. Returns the configuration, the receipt, the
    currently selected fingerprint (None when the integration is not set up)
    and the patch.
    """
    configuration = editable_configuration(service)
    receipt = SecretReplacementRequest.objects.filter(
        pk=request_id,
        state="applied",
        target__in=TARGETS,
    ).first()
    if receipt is None:
        raise LookupError("An acknowledged replacement is unavailable.")
    records = integration_records(configuration.active_configuration.canonical_document)
    record = records.get(receipt.target)
    before = None if record is None else record["values"]["credential_fingerprint"]
    if before == receipt.resulting_fingerprint:
        return configuration, receipt, before, None
    patch = switch_patch(receipt, records)
    try:
        proof = current_receipt(
            receipt.target, receipt.resulting_fingerprint, _proposed(records, patch)
        )
    except StaleCredentialReceipt:
        raise StaleRecordError("This replacement is no longer current.") from None
    if proof.pk != receipt.pk:
        raise StaleRecordError("This replacement is no longer current.")
    # A key added with a new integration has no predecessor in the settings.
    if record is not None and before != receipt.expected_fingerprint:
        raise StaleRecordError("The integration fingerprint changed.")
    refuse_organization_change(records, _proposed(records, patch))
    return configuration, receipt, before, patch


def _changes(configuration, patch):
    """Settings the switch would change, as label/before/after rows.

    Shown on the confirm page, so an Administrator sees exactly what else
    the switch saves besides the key (normally nothing, or the Slack channel
    or mailbox the key was checked against).
    """
    item = patch[0]
    records = integration_records(configuration.active_configuration.canonical_document)
    target = item["values"].get("kind") or next(
        kind for kind, row in records.items() if row["id"] == item["id"]
    )
    before = records[target]["values"]["settings"] if target in records else {}
    after = item["values"].get("settings", before)
    fields = IntegrationForm(target).fields
    return [
        {
            "label": fields[name].label if name in fields else name,
            "before": before.get(name, ""),
            "after": after.get(name, ""),
        }
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name)
    ]


def _locked_page(request, request_id):
    """Explain that this switch would change the loaded ParishSoft organization."""
    target = (
        SecretReplacementRequest.objects.filter(pk=request_id)
        .values_list("target", flat=True)
        .first()
    )
    place_key_page(request, target)
    response = render(
        request,
        "stewardship/credential-selection.html",
        {"blocked": True, "label": LABELS.get(target, "")},
        status=409,
    )
    response.stewardship_safe_error = True
    return response


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
                configuration, _, _, _ = _selection(service, request_id)
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
            try:
                with read_transaction():
                    configuration, receipt, before, patch = _selection(
                        service, request_id
                    )
                    base = service.store.active()
                    if (
                        base is None
                        or base.digest != configuration.active_configuration.digest
                    ):
                        raise StaleRecordError("The credential preview base changed.")
            except OrganizationLocked:
                # Refuse with a page that says why, not a generic refusal.
                return _checked(request, service, _locked_page(request, request_id))
            # Preview/render work uses the captured immutable inputs without
            # blocking task claims or source promotion. Confirmation rechecks
            # current receipt, freshness and base under the owning work lock.
            selected = patch is None
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
            # Finish switching reviews one more settings change; a key
            # already in use has nothing left to review.
            place_key_page(
                request,
                receipt.target,
                **({} if selected else {"flow": "change", "step": "review"}),
            )
            response = render(
                request,
                "stewardship/credential-selection.html",
                {
                    "receipt": receipt,
                    "label": LABELS[receipt.target],
                    "before": before,
                    "preview": preview,
                    "selected": selected,
                    "changes": [] if selected else _changes(configuration, patch),
                },
            )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)
