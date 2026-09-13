"""Explicit original-Admin confirmation; all installation runs in background owners."""

from django import forms
from django.core import signing
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .authentication import runtime
from .setup_confirmation import freeze_setup, ready_inputs
from .setup_preview import PREVIEW_SALT, prepare_preview
from .setup_views import ERRORS, _checked, _closed, _context, error_response


class SetupConfirmationForm(forms.Form):
    """An explicit acknowledgement accompanies the exact signed review binding."""

    preview_token = forms.CharField(max_length=4096, widget=forms.HiddenInput)
    confirmed = forms.BooleanField(
        label=_(
            "I reviewed the parish and first-campaign settings "
            "and want to finish setup."
        )
    )


@require_http_methods(["GET", "HEAD", "POST"])
def setup_confirmation(request):
    """A POST freezes intent only; GET/HEAD never send, install, load or activate."""
    try:
        service = runtime()
        _closed(request, set(SetupConfirmationForm.base_fields))
        form = SetupConfirmationForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            freeze_setup(
                request, service, preview_token=form.cleaned_data["preview_token"]
            )
            response = HttpResponseRedirect("/admin/setup/cancel")
            response["Cache-Control"] = "no-store"
            return response
        preview = prepare_preview(request, service)
        fresh_token = signing.dumps(preview.binding(), salt=PREVIEW_SALT)
        if request.method == "POST":
            # The displayed preview is current even if the submitted checkbox
            # was missing. Keep its errors, but bind the next explicit Submit
            # to what this response actually shows, not the old hidden token.
            data = request.POST.copy()
            data["preview_token"] = fresh_token
            form = SetupConfirmationForm(data)
            form.is_valid()
        else:
            form = SetupConfirmationForm(initial={"preview_token": fresh_token})
        problem = None
        try:
            ready_inputs(preview)
        except ValueError as error:
            # This owner emits only closed readiness messages, not provider text.
            problem = str(error)
        response = render(
            request,
            "stewardship/setup-confirmation.html",
            _context(preview.draft)
            | {
                "form": form,
                "readiness_problem": problem,
                "candidate_digest": preview.compiled.candidate.digest,
            },
            status=400 if request.method == "POST" else 200,
        )
        return _checked(request, service, response, preview.draft)
    except signing.BadSignature:
        return error_response(ValueError("The setup preview has expired or changed."))
    except ERRORS as error:
        return error_response(error)
