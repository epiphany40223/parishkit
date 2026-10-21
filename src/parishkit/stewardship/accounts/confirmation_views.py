"""Production activation, the Go live page's Activation step (#1585 slice 5).

The final confirmation itself is the Go live page's Confirm Production step
(``go_live_confirm_views``, #1585 slice 4). This page follows what came
after it: the durable initial mail preparation of a direct activation and
its Retry, or a scheduled campaign and its Cancel go-live dialog. Its GET
is a passive read that draws the page from durable state, so a reload
returns to it with its current status; every POST answers a redirect or a
fragment of the Cancel go-live dialog, never a page, so a reload never
sends a form again.

Every rule of the pre-start withdrawal stays with its owner
(``withdrawal_commands``, the SQL withdrawal guard): the reason, the
pre-start and not-paused rule, the exact live work inventory and the
5-minute intent, which Apply mints from the cancellation it builds again
then, so that limit never reaches the Administrator however long the
dialog stays open (admin-portal spec, "Go live page", Activation).
"""

from uuid import UUID, uuid4

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.withdrawal_models import ProductionWithdrawal
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import refused_step, unexpected_fields

from . import confirmation_progress, go_live_check, stages, withdrawal_commands
from .admin_caller import AdminCaller
from .admin_editing import principal
from .authentication import runtime
from .installation_lock import ConfigurationBusy
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response

# The Cancel go-live dialog's Review binding (``_review``), signed without a
# time: Apply compares what it names with the cancellation built then.
SALT = "stewardship-production-cancel-review-v1"
# Each action, by its form's ``action``, with its other fields.
_FIELDS = {
    "retry": {"control"},
    "review_cancel": set(),
    "cancel": {"review", "reason"},
}
# The Cancel go-live dialog's refusals (production-cancel-review.html) and
# their statuses; the refusal log records each by its key (``refused_step``).
_REFUSALS = {
    "no_reason": 400,
    "blocking": 409,
    "changed": 409,
    "started": 409,
    "paused": 409,
    "cancelled": 409,
}
# The longest reason the owner accepts (``withdrawal_commands.preview``).
REASON_MAX = 2000
# A direct activation's three stage boxes, in order (admin-portal spec, "Go
# live page", Activation).
STAGES = (
    ("live", _("Go live")),
    ("prepare", _("Prepare initial emails")),
    ("sending", _("Sending")),
)
# How often the page re-reads itself while it waits: a Retry the
# Administrator may click, or a scheduled campaign whose start (or a
# cancellation in another tab) changes what it offers.
_IDLE_INTERVAL_MS = 30000
# The longest the shared watcher follows a page closely (live-status-v1.js);
# past it, it slows down rather than stopping.
_WATCH_MS = 3 * 60 * 60 * 1000


@require_http_methods(["GET", "HEAD", "POST"])
def progress(request, campaign_id):
    """Production activation (#1585 slice 5): passive GET, every POST in place.

    GET draws the page from durable state; it changes nothing and does not
    extend the sign-in's idle time. Each POST:

    - ``retry`` retries the failed initial mail preparation its control
      names, once, and redirects here (``confirmation_progress.retry_shown``);
    - ``review_cancel`` answers the Cancel go-live dialog's review: the
      cancellation counts, the caution and the reason field, holding a
      signed binding of the counts shown; or a refusal;
    - ``cancel`` (the dialog's Apply) checks the reason, builds the
      cancellation again, mints its intent from it and cancels go-live,
      then opens Check; a real change since the review is a refusal inside
      the dialog that names it.
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
            shown = confirmation_progress.progress(
                AdminCaller.from_request(request), service, campaign_id
            )
            response = render(
                request, "stewardship/production-progress.html", _context(shown)
            )
            return _checked(request, service, response)
        action = request.POST.get("action")
        if action not in _FIELDS:
            # No sources: an unknown action value, not an unexpected field.
            raise unexpected_fields()
        _closed(request, {"action", *_FIELDS[action]})
        if action == "review_cancel":
            return _review(request, service, campaign_id)
        if action == "cancel":
            return _apply(request, service, campaign_id)
        confirmation_progress.retry_shown(
            request, service, campaign_id, control=request.POST.get("control", "")
        )
        return _checked(
            request,
            service,
            HttpResponseRedirect(reverse("admin:production_progress"), status=303),
        )
    except ConfigurationBusy:
        return error_response(
            StaleRecordError("Another configuration operation is finishing; try again.")
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Production progress is unavailable."))
    except ERRORS as error:
        return error_response(error)


def _state(shown):
    """The page's state, from ``confirmation_progress.progress``'s read.

    ``preparing``, ``retrying`` (a failure the system is still recovering
    by itself) or ``failed`` while a direct activation's initial mail
    preparation is unfinished; ``prepared`` once it finished;
    ``scheduled``, ``started`` (confirmed before its start, now active) or
    ``closed`` for a campaign with no initial mail to prepare.
    """
    campaign, demand, task = shown["campaign"], shown["demand"], shown["task"]
    if demand is None:
        if campaign.state in {"scheduled", "closed"}:
            return campaign.state
        return "started"
    if shown["complete"]:
        return "prepared"
    if task is not None and task.state == "failed":
        return "failed"
    return "retrying" if demand.failure_code else "preparing"


def _boxes(state):
    """The three stage boxes for a direct activation in ``state``."""
    if state == "prepared":
        # Sending is In progress, never Done: delivery is Sent mail's to say.
        return stages.rows(STAGES, 2)
    return stages.rows(STAGES, 1, "failed" if state == "failed" else "active")


def _context(shown):
    """Production activation's template context."""
    state = _state(shown)
    campaign = shown["campaign"]
    direct = shown["demand"] is not None
    context = shown | {
        "state": state,
        "boxes": _boxes(state) if direct else None,
        # Only a direct activation still preparing is a step of going live
        # (wizard rule 12): afterwards this is the ordinary menu page.
        "wizard": go_live_check.bar("confirmed", current="activate")
        if state in {"preparing", "retrying", "failed"}
        else None,
        "cancel_unavailable": None,
        "live_pending": state in {"preparing", "retrying", "failed", "scheduled"},
        "live_interval": _IDLE_INTERVAL_MS
        if state in {"failed", "scheduled"}
        else None,
        "live_limit": _WATCH_MS,
    }
    if state == "scheduled" and not shown["withdrawal_available"]:
        context["cancel_unavailable"] = (
            "paused" if campaign.delivery_paused else "started"
        )
    return context


def _review(request, service, campaign_id):
    """The Cancel go-live dialog's Review: the counts to cancel, or a refusal.

    The binding names the confirmation, the counts shown and a fresh
    request key, signed without a time: Apply compares it with the
    cancellation it builds again. Mail that blocks the cancellation is a
    refusal that links the pages that resolve it.
    """
    refused, values = review_cancel(request, service, campaign_id)
    if refused is None:
        return _answer(
            request,
            service,
            {
                "inventory": values["inventory"],
                "review": values["review"],
                "reason_max": REASON_MAX,
            },
        )
    if refused == "opened":
        return _check(request, service)
    answer = {"refused": refused}
    if "inventory" in values:
        answer["inventory"] = values["inventory"]
    return _answer(request, service, answer)


def review_cancel(caller, service, campaign_id):
    """The Cancel go-live dialog's Review, request-free: ``(refused, values)``.

    Shared with ``pk-admin go-live cancel-preview`` (#1809). ``refused`` is
    None for a full review, whose ``values`` hold the ``confirmation``, the
    ``inventory`` shown and the signed ``review`` binding; "blocking" for
    mail that blocks it (``values`` hold the ``confirmation`` and the
    ``inventory``); "opened" for go-live this Administrator already
    cancelled (Check opens); else ``_unavailable_refusal``'s key, with no
    values.
    """
    try:
        actor, confirmation, inventory = withdrawal_commands.review(
            caller, service, campaign_id
        )
    except StaleRecordError:
        refused = _unavailable_refusal(
            caller, service, campaign_id, principal(caller, service)
        )
        return refused or "opened", {}
    if inventory["blocking"]:
        return "blocking", {"confirmation": confirmation, "inventory": inventory}
    review = signing.Signer(salt=SALT).sign_object(
        {
            "actor": str(actor.identity),
            "confirmation": str(confirmation.pk),
            "inventory": inventory,
            "key": str(uuid4()),
        }
    )
    return None, {
        "confirmation": confirmation,
        "inventory": inventory,
        "review": review,
    }


def _binding(token):
    """The Review binding Apply posted; a forged one is ``BadSignature`` (a 400)."""
    if type(token) is not str or len(token) > 16384:
        raise signing.BadSignature("Invalid Cancel go-live review.")
    value = signing.Signer(salt=SALT).unsign_object(token)
    if (
        type(value) is not dict
        or set(value) != {"actor", "confirmation", "inventory", "key"}
        or type(value["inventory"]) is not dict
    ):
        raise signing.BadSignature("Invalid Cancel go-live review.")
    return value


def _apply(request, service, campaign_id):
    """The dialog's Apply, through ``apply_cancel``: open Check, which says
    when go-live was cancelled, or draw the refusal inside the dialog (a
    missing reason is a 400, the rest 409s)."""
    refusal = apply_cancel(
        request,
        service,
        campaign_id,
        review=request.POST.get("review", ""),
        reason=request.POST.get("reason", ""),
    )
    if refusal is None:
        return _check(request, service)
    refused, inventory = refusal
    values = {"refused": refused}
    if inventory is not None:
        values["inventory"] = inventory
    return _answer(request, service, values)


def apply_cancel(caller, service, campaign_id, *, review, reason, record=None):
    """Cancel go-live from a cancellation built now: the dialog's Apply.

    Request-free, so the page and ``pk-admin go-live cancel`` share it
    (#1809). In order: the binding ``review`` must be this Administrator's
    (else ``PermissionError``, a 403); a resend of an Apply that already
    cancelled cancels nothing more (its request key made it happen once);
    the reason must be more than spaces and at most 2,000 characters; the
    cancellation is built again under the work lock, with the Review's
    request key, and must still be available, on the same confirmation and
    with the same counts and nothing blocking. Only then is its signed
    intent minted, from that build, and go-live cancelled by its owner,
    which re-checks everything under the campaign lock and in SQL;
    ``record`` is the owner's hook for the command line's own event, in its
    transaction.

    Returns None once go-live is cancelled (now, or already by this
    Administrator), else ``(refused, inventory)``: the dialog's refusal key
    and, for blocking mail, the counts that show it (else None).
    """
    actor = principal(caller, service)
    binding = _binding(review)
    if binding["actor"] != str(actor.identity):
        raise PermissionError("This cancellation belongs to another sign-in.")
    key = UUID(binding["key"])
    if ProductionWithdrawal.objects.filter(
        request_key=key, actor_id=actor.identity
    ).exists():
        return None
    if not reason.strip() or len(reason) > REASON_MAX:
        return "no_reason", None
    try:
        built, token = withdrawal_commands.preview(
            caller,
            service,
            campaign_id,
            reason=reason,
            acknowledged=True,
            key=key,
        )
    except StaleRecordError:
        return _unavailable(caller, service, campaign_id, actor)
    if token is None:
        return "blocking", built["inventory"]
    if (
        built["confirmation"] != binding["confirmation"]
        or built["inventory"] != binding["inventory"]
    ):
        return "changed", None
    try:
        withdrawal_commands.withdraw(
            caller, service, campaign_id, token=token, record=record
        )
    except StaleRecordError:
        # The campaign started, delivery was paused, or the work changed
        # under the campaign lock: say which (nothing was cancelled).
        return _unavailable(caller, service, campaign_id, actor)
    except PermissionError:
        # Two Applies of one Review raced past the resend check above: the
        # first cancelled, and the owner refuses the second's newer intent
        # under the same request key. That is this Administrator's own
        # cancellation, so it stands; anything else is refused as raised.
        if ProductionWithdrawal.objects.filter(
            request_key=key, actor_id=actor.identity
        ).exists():
            return None
        raise
    return None


def _unavailable(caller, service, campaign_id, actor):
    """``_unavailable_refusal`` as ``apply_cancel``'s answer: None or a refusal."""
    refused = _unavailable_refusal(caller, service, campaign_id, actor)
    return None if refused is None else (refused, None)


def _unavailable_refusal(caller, service, campaign_id, actor):
    """Why Cancel go-live is not available for a Review or Apply, or None.

    Go-live this Administrator already cancelled (in another tab) is no
    refusal (None): Check opens, which the dialog follows. Otherwise the
    key says why: another Administrator cancelled it, live delivery is
    paused, or the campaign has started; a cancellation that is available
    again (its owner found the work moved under its lock), or a campaign
    that is a draft again some other way, is "changed". Read through
    ``withdrawal_commands.page``, which still finds the confirmation once
    the campaign is a draft again.
    """
    now = withdrawal_commands.page(caller, service, campaign_id)
    withdrawal = now["withdrawal"]
    if withdrawal is not None:
        if withdrawal.actor_id == actor.identity:
            return None
        return "cancelled"
    if now["available"]:
        return "changed"
    if now["campaign"].delivery_paused:
        return "paused"
    if now["campaign"].state != "draft":
        # Scheduled past its start, active or closed.
        return "started"
    # A draft again without a withdrawal of this confirmation: the campaign
    # moved on some other way since the page was drawn.
    return "changed"


def _check(request, service):
    """Open Check, which says when go-live was cancelled."""
    return _checked(
        request,
        service,
        HttpResponseRedirect(reverse("admin:go_live"), status=303),
    )


def _answer(request, service, values):
    """Draw the Cancel go-live dialog's fragment; a refusal with its status."""
    status = _REFUSALS.get(values.get("refused"), 200)
    response = render(
        request,
        "stewardship/production-cancel-review.html",
        values | {"check_url": reverse("admin:go_live")},
        status=status,
    )
    if status != 200:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
        refused_step(response, values["refused"])
    return _checked(request, service, response)
