"""The Go live page's Confirm Production step (#1585 slice 4): GET, dialog POSTs.

The step's state comes from ``go_live_confirm.read``; this module draws it
and answers the Confirm Production dialog. Its Review answers a fragment
of the shared review dialog; its Apply answers a redirect to Production
activation (the Activation step) or a refusal drawn inside the dialog,
never a page, so a browser reload never sends a form again. Stop go-live
and Refresh and prepare again post to the Prepare step, which owns them.

Every rule of the final confirmation stays with its owners
(``confirmation_commands``, the SQL confirmation guard): the readiness
re-check, the public web address check run again before anything reaches
Families, the typed ``Production`` word, the completed cleanup (a
sign-in of any age confirms, #1547), the campaign-closed refusal and the
token's five-minute lifetime. The token is minted at Apply, from the
preview re-checked then, so that lifetime never reaches the Administrator
however long the dialog stays open (admin-portal spec, "Server checks
instead of page age").
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import refused_step, unexpected_fields

from . import confirmation_commands, go_live_check, go_live_confirm
from .admin_editing import principal
from .authentication import runtime
from .go_live_views import _problems
from .installation_lock import ConfigurationBusy
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response

# The Confirm Production dialog's Review binding (``review``), signed
# without a time: Apply compares what it names with the preview built then.
SALT = "stewardship-go-live-confirm-review-v1"
# The typed word, as the SQL-facing owner checks it (spaces around ignored).
WORD = "Production"
# Each action, by its form's ``action``, with its other fields.
_FIELDS = {"review": set(), "confirm": {"review", "typed"}}
# The dialog's refusals (go-live-confirm-review.html) and their statuses;
# the refusal log records each by its key (``refused_step``).
_REFUSALS = {
    "typed": 400,
    "changed": 409,
    "target": 409,
    "closed": 409,
    "hold_ended": 409,
    "moved": 409,
    "problems": 409,
    "stopped": 409,
    "confirmed": 409,
    "origin_unverified": 409,
}
# How often the step re-reads itself while it waits for the Administrator:
# only another tab, another Administrator or ParishSoft data changes it.
_IDLE_INTERVAL_MS = 30000
# The longest the shared watcher follows a page (live-status-v1.js).
_WATCH_MS = 3 * 60 * 60 * 1000


@require_http_methods(["GET", "HEAD", "POST"])
def confirmation(request, campaign_id):
    """The Go live page's Confirm Production step (#1585): GET, dialog POSTs.

    GET draws the step from durable state, so a reload returns to it with
    its current status; it changes nothing, sends nothing and does not
    extend the sign-in's idle time. Each POST:

    - ``review`` answers the Confirm Production dialog's review: the target
      and headline counts, the caution and the typed field, holding a
      signed binding of the preview's inputs; or a refusal.
    - ``confirm`` (the dialog's Apply) refuses without the typed word, runs
      the public web address check again, builds the preview again, mints
      the confirmation token from it and confirms Production, then opens
      Production activation; a real change since the review is a refusal
      inside the dialog that names it.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        if campaign_id is None:
            # No current campaign: authorize first, then refuse plainly.
            principal(request, service, passive=True)
            raise ObjectDoesNotExist("No current campaign.")
        if request.method != "POST":
            _closed(request, set())
            shown = go_live_confirm.read(request, service, campaign_id)
            response = render(
                request, "stewardship/go-live-confirm.html", _context(shown)
            )
            return _checked(request, service, response)
        action = request.POST.get("action")
        if action not in _FIELDS:
            # No sources: an unknown action value, not an unexpected field.
            raise unexpected_fields()
        _closed(request, {"action", *_FIELDS[action]})
        if action == "review":
            return _review(request, service, campaign_id)
        return _apply(request, service, campaign_id)
    except ConfigurationBusy:
        return error_response(
            StaleRecordError(
                "Another configuration operation is finishing; retry confirmation."
            )
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Production confirmation is unavailable."))
    except ERRORS as error:
        return error_response(error)


def _context(shown):
    """The Confirm Production step's template context."""
    context = {
        "confirm": shown,
        "stage": shown.prepare,
        "campaign": shown.request.campaign if shown.request else None,
        "wizard": go_live_check.bar(
            "started" if shown.started else "idle",
            current="confirm",
            prepared=shown.state in {"ready", "problems"},
        ),
        "live_pending": shown.started and shown.state != "moved",
        "live_interval": _IDLE_INTERVAL_MS,
        "live_limit": _WATCH_MS,
    }
    if shown.preview is not None:
        context |= shown.counts()
        # The preview's problems include live mail it found uncertain,
        # which readiness alone does not (``collect_preview``).
        context["problems"] = _problems(
            SimpleNamespace(
                problems=shown.preview.problems,
                configuration=shown.preview.readiness.configuration,
            )
        )
        context["preview"] = shown.preview
    return context


def _review(request, service, campaign_id):
    """The dialog's Review: the preview to confirm, or a refusal.

    It asks for no recent sign-in (#1547). The binding names the
    request, the prepared links, the preview's inputs digest and target
    and a fresh request key, signed without a time: Apply compares it with
    the preview it builds again.
    """
    actor = principal(request, service)
    shown = go_live_confirm.read(request, service, campaign_id)
    if shown.state != "ready":
        return _not_ready(request, service, actor, shown)
    readiness = shown.preview.readiness
    review = signing.Signer(salt=SALT).sign_object(
        {
            "actor": str(actor.identity),
            "request": str(shown.request.pk),
            "preparation": str(shown.prepare.prepared.pk),
            "digest": readiness.digest,
            "target": readiness.target_state,
            "key": str(uuid4()),
        }
    )
    return _answer(request, service, {"shown": shown, "review": review})


def _binding(token):
    """The Review binding Apply posted; a forged one is ``BadSignature`` (a 400)."""
    if type(token) is not str or len(token) > 4096:
        raise signing.BadSignature("Invalid Production confirmation review.")
    value = signing.Signer(salt=SALT).unsign_object(token)
    if type(value) is not dict or set(value) != {
        "actor",
        "request",
        "preparation",
        "digest",
        "target",
        "key",
    }:
        raise signing.BadSignature("Invalid Production confirmation review.")
    return value


def _apply(request, service, campaign_id):
    """The dialog's Apply: confirm Production from a preview re-checked now.

    In order: the binding must be this Administrator's (else a 403); a
    resend of an Apply that already confirmed opens Production activation
    (its request key made it happen once); the typed word must be there
    (else a 400 inside the dialog); the step must still be ready and on
    the same links; the public web
    address check runs again; the preview is built again, and its inputs
    digest and target must match the Review's. Only then is the
    confirmation token minted, from that preview, and Production confirmed
    by its owner, which re-checks everything under the final locks. A
    confirmation or stop that won a race is said in the dialog.
    """
    actor = principal(request, service)
    binding = _binding(request.POST.get("review", ""))
    if binding["actor"] != str(actor.identity):
        raise PermissionError("This confirmation belongs to another sign-in.")
    key = UUID(binding["key"])
    if ProductionConfirmation.objects.filter(
        request_key=key, actor_id=actor.identity
    ).exists():
        return _activation(request, service)
    typed = request.POST.get("typed", "")
    if typed.strip() != WORD:
        return _answer(request, service, {"refused": "typed"})
    shown = go_live_confirm.read(request, service, campaign_id, preview=False)
    if shown.state != "ready":
        return _not_ready(request, service, actor, shown)
    row, prepared = shown.request, shown.prepare.prepared
    if (str(row.pk), str(prepared.pk)) != (binding["request"], binding["preparation"]):
        return _answer(request, service, {"refused": "changed"})
    try:
        preview, verified, token = confirmation_commands.verify_preview(
            request, service, campaign_id, row.pk, prepared.pk, key=key
        )
    except StaleRecordError:
        return _settled(request, service, campaign_id, actor)
    if not verified:
        return _answer(request, service, {"refused": "origin_unverified"})
    readiness = preview.readiness
    if "campaign_closed" in preview.problems:
        return _answer(request, service, {"refused": "closed"})
    if "go_live_hold_ended" in preview.problems:
        return _answer(request, service, {"refused": "hold_ended"})
    if preview.problems:
        return _answer(request, service, {"refused": "problems"})
    if readiness.target_state != binding["target"]:
        return _answer(request, service, {"refused": "target"})
    if readiness.digest != binding["digest"] or token is None:
        return _answer(request, service, {"refused": "changed"})
    try:
        confirmation_commands.confirm(
            request, service, campaign_id, row.pk, prepared.pk, token=token, typed=typed
        )
    except (StaleRecordError, PermissionError, ObjectDoesNotExist):
        # Another Administrator's confirmation or stop won the final locks
        # (the request is no longer the open Testing draft's), or an input
        # moved under them: say which. Anything else is raised again.
        return _settled(request, service, campaign_id, actor)
    return _activation(request, service)


def _settled(request, service, campaign_id, actor):
    """Answer an Apply whose step moved while it ran, by what it is now.

    A confirmation or a stop that won is said as such (or, for this
    Administrator's own confirmation from another tab, opens Production
    activation); a step still ready had an input move under the owner's
    locks: "changed", to review again.
    """
    shown = go_live_confirm.read(request, service, campaign_id, preview=False)
    if shown.state != "ready":
        return _not_ready(request, service, actor, shown)
    return _answer(request, service, {"refused": "changed"})


def _not_ready(request, service, actor, shown):
    """Refuse a Review or Apply made while the step is not ``ready``.

    Production this Administrator already confirmed (in another tab) is no
    refusal: the answer opens Production activation, which the dialog
    follows. Every other state is refused in its own words.
    """
    if shown.state == "confirmed" and not shown.confirmed_by_other:
        return _activation(request, service)
    refused = shown.state
    if refused == "not_started":
        refused = "stopped"
    return _answer(request, service, {"refused": refused, "shown": shown})


def _activation(request, service):
    """Open the Activation step: today's Production activation page."""
    return _checked(
        request,
        service,
        HttpResponseRedirect(reverse("admin:production_progress"), status=303),
    )


def _answer(request, service, values):
    """Draw the Confirm Production dialog's fragment; a refusal with its status."""
    status = _REFUSALS.get(values.get("refused"), 200)
    shown = values.get("shown")
    context = values | {
        "check_url": reverse("admin:go_live"),
        "prepare_url": reverse("admin:go_live_prepare"),
        "word": WORD,
    }
    if shown is not None and shown.preview is not None:
        context |= shown.counts()
    response = render(
        request, "stewardship/go-live-confirm-review.html", context, status=status
    )
    if status != 200:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
        refused_step(response, values["refused"])
    return _checked(request, service, response)
