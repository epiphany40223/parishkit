"""Write-only credential forms for one original, still-collecting setup attempt."""

from django import forms
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version

from .admin_editing import error_response
from .authentication import runtime
from .integration_forms import LABELS, CredentialForm
from .sessions import require_fresh
from .setup_credentials import TARGETS, credential_status, stage_credential
from .setup_drafts import view_draft
from .setup_policy import SetupState
from .setup_views import ERRORS, _checked, _closed


class SetupCredentialForm(CredentialForm):
    """Reuse the write-only widget; this attempt/version replaces a live intent."""

    intent = None

    def __init__(self, target, *args, **kwargs):
        """Only ParishSoft accepts an explicit organization; other scope is staged."""
        super().__init__(*args, **kwargs)
        if target == "parishsoft":
            self.fields["organization_id"] = forms.IntegerField(
                label=_("Expected ParishSoft organization ID"),
                min_value=1,
                max_value=2**31 - 1,
            )
        self.fields["candidate"].label = _("Setup credential")


@sensitive_post_parameters("candidate")
@require_http_methods(["GET", "HEAD", "POST"])
def setup_credential(request, target):
    """Do not write files, contact providers or enqueue a live replacement here."""
    try:
        if target not in TARGETS:
            raise LookupError("Unknown setup credential target.")
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != SetupState.COLLECTING:
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        require_fresh(request)
        _closed(request, {*SetupCredentialForm(target).fields, "version"})
        receipts = credential_status(request, service, draft.status.attempt_id)
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload this setup form.")
            form = SetupCredentialForm(target, request.POST)
            if form.is_valid():
                candidate = form.cleaned_data.pop("candidate").encode("utf-8")
                stage_credential(
                    request,
                    service,
                    draft.status.attempt_id,
                    target=target,
                    candidate=candidate,
                    expected_version=version,
                    organization_id=form.cleaned_data.get("organization_id"),
                )
                del candidate
                return _checked(request, service, HttpResponseRedirect("/admin/setup"))
            status = 400
        else:
            form, status = SetupCredentialForm(target), 200
        response = render(
            request,
            "stewardship/setup-credential.html",
            {
                "draft": draft,
                "form": form,
                "label": LABELS[target],
                "saved": any(receipt.target == target for receipt in receipts),
            },
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)
