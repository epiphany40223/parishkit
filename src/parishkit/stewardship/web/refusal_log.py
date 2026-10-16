"""The one shared hook that logs refused Admin page requests (#1403).

Operations spec, Admin input refusals. ``AdminRefusalMiddleware`` sits just
inside ``web.error_pages.BrowserErrorMiddleware``, so it sees each Admin
response as the view (or a CSRF or access-gate refusal before it) made it,
before that response becomes a page for people, and after the view's own
transaction has ended. No view makes a logging call of its own: everything
the entry says comes from what the response already carries.

- Which requests: one that resolved to an Admin page (a 404 for an address
  no page answers is not input) and was answered 400, 403, 404, 409, 410 or
  422, unless the code that refused it already wrote its own record and
  marked the request or response (``web.refusals.mark_recorded``), or the
  response refuses no input: a greyed-out menu page opened directly
  (``web.refusals.mark_not_input``).
- Why: the response's ``stewardship_reason`` (``web.refusals.annotate``,
  ``web.security.csrf_failure``), else its refusal's reason, else
  ``unclassified`` with the exception's class name.
- What: the closed ``ErrorCode`` values and the server-owned field
  identifiers of its typed errors. ``fields`` never comes from the keys a
  request submitted: a ``FieldError``'s identifier is fixed by the view that
  refused, never copied from the request.
- Who: the signed-in Administrator's ``PortalUser`` id, or none.

``audit.refusals.record_refusal`` then writes the process-log line and, for
a signed-in user, the capped durable entry, on this request's own database
connection; it never raises, and neither does this hook.
"""

import logging
from contextlib import suppress
from uuid import UUID

from parishkit.stewardship.audit.refusal_context import STATUSES
from parishkit.stewardship.audit.refusals import RefusalRecord, record_refusal
from parishkit.stewardship.observability import Event, emit_failure

from .namespaces import is_admin
from .refusals import NOT_INPUT, RECORDED

# Where ``process_exception`` keeps the class of a view's uncaught
# exception (an ``Http404``, say), which Django answers without a typed
# response.
_EXCEPTION = "stewardship_refused_exception_class"


class AdminRefusalMiddleware:
    """Log every refused Admin page request with its reason; never raise."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """Answer as the view did, recording the answer when it is a refusal."""
        response = self.get_response(request)
        log_refusal(request, response)
        return response

    def process_exception(self, request, exception):
        """Keep the class of the view's uncaught exception, never its text.

        Returning None leaves Django's own handling of it unchanged.
        """
        setattr(request, _EXCEPTION, type(exception))


def log_refusal(request, response):
    """Record ``response`` if it refuses an Admin page request; never raise."""
    try:
        record = refusal_record(request, response)
    except Exception as error:
        with suppress(Exception):
            emit_failure(error, event=Event.ADMIN_INPUT_REFUSED, level=logging.WARNING)
        return
    if record is not None:
        # record_refusal never raises; this keeps the answer safe even so.
        with suppress(Exception):
            record_refusal(record)


def refusal_record(request, response):
    """The ``RefusalRecord`` for a refused Admin page request, else None."""
    match = getattr(request, "resolver_match", None)
    if (
        match is None
        or not is_admin(request)
        or response.status_code not in STATUSES
        or getattr(request, RECORDED, False)
        or getattr(response, RECORDED, False)
        or getattr(response, NOT_INPUT, False)
    ):
        return None
    errors = getattr(response, "stewardship_errors", None) or ()
    refusal = getattr(response, "stewardship_refusal", None)
    return RefusalRecord(
        reason=(
            getattr(response, "stewardship_reason", None)
            or getattr(refusal, "reason", None)
            or "unclassified"
        ),
        actor_id=_actor(request),
        route=match.view_name,
        method=request.method,
        status=response.status_code,
        # Server-owned names only (see the module docstring).
        fields=tuple(item.field_id for item in errors if item.field_id),
        codes=tuple(item.code.value for item in errors),
        mode=_mode(),
        error=(
            getattr(response, "stewardship_exception_class", None)
            or getattr(request, _EXCEPTION, None)
        ),
    )


def _actor(request):
    """The signed-in Admin's ``PortalUser`` id, or None for an anonymous request.

    A view's admission leaves its principal on the request. A refusal made
    before any view admits the caller (a CSRF failure, say) reads the
    request's own session instead, counting it only while it is a live
    interactive Admin session, as Signed-in sessions does. A failed lookup
    counts as anonymous, so the refusal still gets its process-log line.
    """
    principal = getattr(request, "principal", None)
    identity = getattr(principal, "identity", None)
    if isinstance(identity, UUID) and getattr(principal, "family_id", None) is None:
        return identity
    # Deferred: the accounts package imports this web package.
    from parishkit.stewardship.accounts.signed_in_sessions import live_principal

    try:
        return live_principal(getattr(request, "session", None))
    except Exception:
        return None


def _mode():
    """The global mode, ``testing`` or ``production``; None when unreadable."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    try:
        return SystemConfiguration.objects.values_list("mode", flat=True).first()
    except Exception:
        return None
