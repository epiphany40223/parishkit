"""CSRF-protected Family flow endpoints; no intermediate-answer endpoint exists."""

import json
from importlib import import_module
from uuid import UUID

from django.conf import settings
from django.http import JsonResponse, RawPostDataException
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.family_authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.campaigns.work_locks import work_transaction

from .answers import InvalidAnswers
from .baselines import (
    FamilyAdmissionDenied,
    RehearsalAcknowledgmentRequired,
    issue_baseline,
)
from .inputs import FormInputsUnavailable
from .presentation import form_presentation
from .submission import submit_family
from .validation import BaselineUnavailable

MAX_SUBMIT_BYTES = 256 * 1024
UNAVAILABLE = (
    ConfigError,
    CryptographicError,
    LimiterUnavailable,
    FormInputsUnavailable,
)


def _json(value, *, status=200):
    """Mark caller-owned static errors so the shared boundary retains the JSON."""
    response = JsonResponse(value, status=status)
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _body(request, *, maximum):
    """Reject alternate/unbounded representations without reflecting private input."""
    try:
        if request.content_type != "application/json":
            raise ValueError
        length = int(request.META.get("CONTENT_LENGTH", "0"))
        if not 0 < length <= maximum or len(request.body) > maximum:
            raise ValueError
        result = json.loads(request.body)
        if type(result) is not dict:
            raise ValueError
        return result
    except (ValueError, UnicodeError, RawPostDataException, RecursionError):
        raise InvalidAnswers({"form": "Reload the form and try again."}) from None


def _failure(error):
    """Use closed status shapes, not exception text or submitted field values."""
    if isinstance(error, InvalidAnswers):
        return _json({"error": "validation", "fields": error.fields}, status=422)
    if isinstance(error, RehearsalAcknowledgmentRequired):
        return _json({"error": "testing_acknowledgment"}, status=409)
    if isinstance(error, BaselineUnavailable):
        return _json({"error": "reload_required"}, status=409)
    if isinstance(error, FamilyAdmissionDenied):
        return _json({"error": "session_ended"}, status=403)
    return _json({"error": "temporarily_unavailable"}, status=503)


@require_POST
def start(request):
    """Accept only explicit entry consent; the response is an answer-free baseline.

    No request data is passed to persistence except the consent boolean. Values
    returned to the tab are transient materialization of trusted retained data.
    """
    try:
        body = _body(request, maximum=128)
        if (
            set(body) != {"testing_acknowledged"}
            or type(body["testing_acknowledged"]) is not bool
        ):
            raise InvalidAnswers({"form": "Confirm the displayed response mode."})
        with work_transaction():
            form = issue_baseline(
                request, runtime(), testing_acknowledged=body["testing_acknowledged"]
            )
            return _json({"form": form_presentation(form)})
    except (
        *UNAVAILABLE,
        InvalidAnswers,
        FamilyAdmissionDenied,
        RehearsalAcknowledgmentRequired,
    ) as error:
        return _failure(error)


@require_POST
def submit(request):
    """Only this endpoint accepts answers; final service owns their atomic commit."""
    try:
        body = _body(request, maximum=MAX_SUBMIT_BYTES)
        if set(body) != {"baseline", "answers"} or type(body["baseline"]) is not str:
            raise InvalidAnswers({"form": "Review the authorized form again."})
        try:
            identifier = UUID(body["baseline"])
        except ValueError:
            raise BaselineUnavailable from None
        with work_transaction():
            result = submit_family(
                request, runtime(), baseline_id=identifier, payload=body["answers"]
            )
            if result.refreshed is not None:
                return _json(
                    {
                        "error": "review_required",
                        "form": form_presentation(result.refreshed),
                    },
                    status=409,
                )
        # Separate cookie namespace middleware clears this empty Family store;
        # the independently authenticated Admin cookie remains untouched.
        request.session = import_module(settings.SESSION_ENGINE).SessionStore()
        return _json({"accepted": True})
    except (
        *UNAVAILABLE,
        InvalidAnswers,
        FamilyAdmissionDenied,
        BaselineUnavailable,
        RehearsalAcknowledgmentRequired,
    ) as error:
        return _failure(error)
