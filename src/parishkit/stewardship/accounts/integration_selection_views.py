"""Finish switching to an installed key, in the settings page's review dialog.

When a new key is installed but its automatic switch did not finish, the
integration's settings page offers **Finish switching to the new key**,
which reviews and applies the switch in the page's review dialog (#1568):
``review_finish`` is its Review and ``confirm_finish`` its Apply, both
reached through ``integration_views.integration_settings``. ``pk-admin
integration key finish-switching-preview`` and ``finish-switching`` share
``_selection``, ``_changes`` and the salt.
"""

from uuid import UUID, uuid4

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import (
    UserFacingStale,
    load_preview,
    unexpected_fields,
)

from .admin_editing import (
    confirm,
    editable_configuration,
    form_action,
    sign_preview,
)
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
from .request_admission import intake_base
from .request_patch import build_candidate, credential_request_schema
from .secret_models import SecretReplacementRequest

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
    """Settings the switch would change, as field/label/before/after rows.

    Shown in the switch's confirmation, so an Administrator sees exactly what else
    the switch saves besides the key (normally nothing, or the Slack channel
    or mailbox the key was checked against). ``field`` is the setting's
    stored name, which ``pk-admin integration key finish-switching-preview``
    prints.
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
            "field": name,
            "label": fields[name].label if name in fields else name,
            "before": before.get(name, ""),
            "after": after.get(name, ""),
        }
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name)
    ]


# The refusal of a switch that would change the loaded ParishSoft
# organization (#1492), shown in the review dialog (#1568).
ORGANIZATION_LOCKED = _(
    "This key was saved with a different ParishSoft organization ID. The "
    "organization ID can't change after ParishSoft data has been loaded, so "
    "this switch was refused and nothing was changed."
)


def _locked():
    """The dialog's refusal of a switch to another ParishSoft organization."""
    return UserFacingStale(
        ORGANIZATION_LOCKED,
        fix=_(
            "Paste the ParishSoft API key for the loaded organization on this "
            "page instead."
        ),
    )


def review_finish(request, service, actor, target):
    """The review dialog's confirmation of **Finish switching to the new key**.

    Opened from the settings page's status line (#1568), whose line for
    ``target`` must say the switch did not finish (``unselected``); the
    switch it reviews is that line's key change, so the form posts no
    request of its own. In one read snapshot it runs ``_selection`` (a key
    no longer current is refused as out of date; one that would change the
    loaded ParishSoft organization is refused in the dialog with that
    reason) and validates the candidate, then signs the preview, bound to
    the key change by its salt. The answer is ``integration-review.html``'s
    finish confirmation, listing the settings saved with the key that the
    switch also applies; its hidden fields are the preview and the key
    change's id, which Apply posts back (``confirm_finish``). Changes
    nothing.
    """
    from .integration_views import key_summary

    allowed = {"action", "csrfmiddlewaretoken"}
    if set(request.POST) - allowed or any(
        len(values) != 1 for _, values in request.POST.lists()
    ):
        raise unexpected_fields(request.POST, allowed=allowed)
    if target not in TARGETS:
        raise LookupError("Integration is unavailable.")
    with read_transaction():
        configuration = editable_configuration(service)
        line = key_summary(configuration, target)
        if line is None or line.kind != "unselected":
            raise UserFacingStale(
                _("No new key is waiting to be switched to."),
                fix=_("Reload the page to see the key's current status."),
            )
        try:
            configuration, receipt, _before, patch = _selection(
                service, line.request_id
            )
        except OrganizationLocked:
            raise _locked() from None
        if patch is None:
            raise UserFacingStale(
                _("This key is already in use."),
                fix=_("Reload the page to see the key's current status."),
            )
        base = service.store.active()
        if base is None or base.digest != configuration.active_configuration.digest:
            raise StaleRecordError("The credential preview base changed.")
    # Preview work uses the captured immutable inputs without blocking task
    # claims or source promotion. Apply rechecks the current receipt and
    # base under the owning work lock.
    build_candidate(
        base,
        patch,
        candidate_id=uuid4(),
        request_schema=credential_request_schema(base.document()),
    )
    return render(
        request,
        "stewardship/integration-review.html",
        {
            "kind": "finish",
            "target": target,
            "label": LABELS[target],
            "changes": [
                {
                    "label": row["label"],
                    "before": row["before"] or _("Not set"),
                    "after": row["after"] or _("Not set"),
                }
                for row in _changes(configuration, patch)
            ],
            "request_id": receipt.pk,
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT + str(receipt.pk),
            ),
        },
    )


def confirm_finish(request, service, actor, target):
    """The review dialog's Apply of Finish switching: the reviewed switch.

    Any Admin may confirm with their own exact signed preview, which is
    bound to the key change (``request_id``) by its salt, so an altered id
    is refused as a bad signature. The current scope repeats ``_selection``
    under the work lock, for a key change of this page's integration only;
    a switch that would now change the loaded ParishSoft organization is
    refused in the dialog. The answer is the change's status, which the
    dialog follows until it is applied. It is a privileged confirm, like
    the page's other one and ``pk-admin integration key finish-switching``.
    """
    if request.FILES:
        raise ValueError("Credential selection accepts no files.")
    form_action(request.POST, preview_fields=set(), confirm_fields={"request_id"})
    request_id = UUID(request.POST["request_id"])

    def scope(service):
        """Intake repeats the current receipt proof under its lock."""
        configuration, receipt, _before, _patch = _selection(service, request_id)
        if receipt.target != target:
            raise LookupError("An acknowledged replacement is unavailable.")
        return configuration, None

    try:
        return confirm(
            request,
            service,
            actor,
            salt=SALT + str(request_id),
            current_scope=scope,
            request_schema=_preview_schema(request, request_id),
            privileged=True,
        )
    except OrganizationLocked:
        raise _locked() from None
