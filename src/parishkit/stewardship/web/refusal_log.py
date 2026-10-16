"""The one shared hook that logs refused Admin page requests (#1403).

Operations spec, Admin input refusals. ``AdminRefusalMiddleware`` sits just
inside ``web.error_pages.BrowserErrorMiddleware``, so it sees each Admin
response as the view (or a CSRF or access-gate refusal before it) made it,
before that response becomes a page for people, and after the view's own
transaction has ended. No view makes a logging call of its own: everything
the entry says comes from what the response already carries.

- Which requests: one that resolved to an Admin page (a 404 for an address
  no page answers is not input) and was answered 400, 403, 404, 409, 410 or
  422, or a 200 answer marked as a refusal drawn in place
  (``web.refusals.refused_in_place``), unless the code that refused it
  already wrote its own record and marked the request or response
  (``web.refusals.mark_recorded``), or the response refuses no input: a
  greyed-out menu page opened directly
  (``web.refusals.mark_not_input``).
- Why: the response's ``stewardship_reason`` (``web.refusals.annotate``,
  ``with_reason``, ``web.security.csrf_failure``), else its refusal's
  reason, else ``unclassified`` with the exception's class name; with a
  setting reason's expected and actual values, and an unexpected-fields
  refusal's count of names the form does not have.
- What: the closed ``ErrorCode`` values and the server-owned field
  identifiers of its typed errors, a body of its own's closed codes
  (``web.refusals.with_codes``), the form's own fields an
  ``unexpected_fields`` refusal found sent twice, then the field names and Django error
  codes of every Admin form this request validated with errors
  (``web.admin_forms``), and the first such form's name. ``fields`` never
  comes from the keys a request submitted: a ``FieldError``'s identifier is
  fixed by the view that refused, and a form's field names are its own
  fields (with a formset row's prefix and index), never copied from the
  request. A field of a refused form's own sent more than once is named
  with the code ``duplicate``.
- Who: the signed-in Administrator's ``PortalUser`` id, or none.

``audit.refusals.record_refusal`` then writes the process-log line and, for
a signed-in user, the capped durable entry, on this request's own database
connection; it never raises, and neither does this hook.
"""

import logging
from contextlib import suppress
from uuid import UUID

from django import forms

from parishkit.stewardship.audit.refusal_context import STATUSES
from parishkit.stewardship.audit.refusals import (
    RefusalRecord,
    exception_class_of,
    record_refusal,
)
from parishkit.stewardship.observability import Event, emit_failure

from .admin_forms import AdminFormSet, collecting
from .namespaces import is_admin
from .refusals import IN_PLACE, NOT_INPUT, RECORDED

# Where ``process_exception`` keeps the class of a view's uncaught
# exception (an ``Http404``, say), which Django answers without a typed
# response.
_EXCEPTION = "stewardship_refused_exception_class"


class AdminRefusalMiddleware:
    """Log every refused Admin page request with its reason; never raise."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """Answer as the view did, recording the answer when it is a refusal.

        The Admin forms the request validates are collected meanwhile, so a
        refused form's errors can be read as its answer leaves the view.
        """
        with collecting() as validated:
            response = self.get_response(request)
            log_refusal(request, response, validated)
        return response

    def process_exception(self, request, exception):
        """Keep the class of the view's uncaught exception, never its text.

        Returning None leaves Django's own handling of it unchanged.
        """
        setattr(request, _EXCEPTION, type(exception))


def log_refusal(request, response, validated=()):
    """Record ``response`` if it refuses an Admin page request; never raise.

    ``validated`` are the Admin forms and formsets the request validated.
    """
    try:
        record = refusal_record(request, response, validated)
    except Exception as error:
        with suppress(Exception):
            emit_failure(error, event=Event.ADMIN_INPUT_REFUSED, level=logging.WARNING)
        return
    if record is not None:
        # record_refusal never raises; this keeps the answer safe even so.
        with suppress(Exception):
            record_refusal(record)


def refusal_record(request, response, validated=()):
    """The ``RefusalRecord`` for a refused Admin page request, else None.

    The typed errors' fields and codes come first, then the forms' own, so
    the entry's limit of 10 keeps the refusal's own codes.
    """
    match = getattr(request, "resolver_match", None)
    in_place = getattr(response, IN_PLACE, False)
    if (
        match is None
        or not is_admin(request)
        or (response.status_code not in STATUSES and not in_place)
        or getattr(request, RECORDED, False)
        or getattr(response, RECORDED, False)
        or getattr(response, NOT_INPUT, False)
    ):
        return None
    errors = getattr(response, "stewardship_errors", None) or ()
    refusal = getattr(response, "stewardship_refusal", None)
    form, form_fields, form_codes = form_errors(validated)
    # A shared refusal's own fields sent twice (``unexpected_fields``).
    duplicates = tuple(getattr(response, "stewardship_duplicates", ()) or ())
    return RefusalRecord(
        reason=(
            getattr(response, "stewardship_reason", None)
            or getattr(refusal, "reason", None)
            or "unclassified"
        ),
        actor_id=_actor(request),
        route=match.view_name,
        method=request.method,
        # A refusal drawn in place answers 200, which is not recorded.
        status=response.status_code if response.status_code in STATUSES else None,
        form=form,
        # Server-owned names only (see the module docstring).
        fields=(
            tuple(item.field_id for item in errors if item.field_id)
            + duplicates
            + form_fields
        ),
        codes=(
            tuple(item.code.value for item in errors)
            + tuple(getattr(response, "stewardship_codes", ()) or ())
            + ("duplicate",) * bool(duplicates)
            + form_codes
        ),
        expected=getattr(response, "stewardship_expected", None),
        actual=getattr(response, "stewardship_actual", None),
        unexpected_count=getattr(response, "stewardship_unexpected_count", None),
        mode=current_mode(),
        error=(
            getattr(response, "stewardship_exception_class", None)
            or getattr(request, _EXCEPTION, None)
        ),
    )


def form_errors(validated):
    """``(form, fields, codes)`` of the validated forms' errors.

    ``form`` is the first form with errors, by its class name in lowercase
    snake form (``CampaignForm`` is ``campaign_form``), else None. ``fields``
    and ``codes`` are in order, possibly repeated: ``refusal_context.clean``
    keeps each once, at most 10. A code-less error (a view's
    ``add_error(field, message)``) names its field with no code; its message
    is never read. A formset row marked for deletion is skipped, as Django's
    formset leaves its errors out too.
    """
    form, fields, codes = None, [], []
    deleted = _deleted_rows(validated)
    for item in validated:
        if id(item) in deleted:
            continue
        found = list(_errors_of(item))
        if found and form is None:
            # The snake form of any class name, an exception's or a form's;
            # a private class (``_Row``) loses its leading underscore.
            form = exception_class_of(item).lstrip("_")
        for field, code in found:
            if field is not None:
                fields.append(field)
            if isinstance(code, str):
                codes.append(code)
    return form, tuple(fields), tuple(codes)


def _deleted_rows(validated):
    """The ids of the collected formsets' rows marked for deletion."""
    return {
        id(row)
        for item in validated
        if isinstance(item, AdminFormSet) and item.can_delete
        for row in item.forms
        if item._should_delete_form(row)
    }


def _errors_of(item):
    """``(field, code)`` for each error of a validated form or formset.

    A formset gives its own (non-form) errors with no field; its rows are
    forms of their own. A form gives each field error under the field's
    prefixed name, only when that name is one of the form's own fields, and
    each form-wide error with no field, and then, only for a form that has
    errors, each of its own fields sent more than once (``duplicate``): a
    form that validated cleanly is not the one that was refused. Errors are
    read as the forms hold them; reading them validates nothing again.
    """
    if isinstance(item, AdminFormSet):
        for error in item.non_form_errors().as_data():
            yield None, error.code
        return
    # A validated form's errors are an ErrorDict; ``item.errors`` would
    # validate an unvalidated one, so the stored dict is read instead.
    if not item._errors:
        return
    for name, found in item._errors.as_data().items():
        field = item.add_prefix(name) if name in item.fields else None
        for error in found:
            yield field, error.code
    for field in _duplicated(item):
        yield field, "duplicate"


def _duplicated(form):
    """The prefixed names of the form's own one-value fields sent twice or more.

    Only names of the form's own fields are looked up, so a submitted name
    the form does not have is never read. A field drawn with a widget that
    sends several values (a multiple choice, ``MultipleHiddenInput``) is
    skipped.
    """
    for name, field in form.fields.items():
        widget = field.widget
        if getattr(widget, "allow_multiple_selected", False) or isinstance(
            widget, forms.MultipleHiddenInput
        ):
            continue
        key = form.add_prefix(name)
        for source in (form.data, form.files):
            getlist = getattr(source, "getlist", None)
            if getlist is not None and len(getlist(key)) > 1:
                yield key
                break


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


def current_mode():
    """The global mode, ``testing`` or ``production``; None when unreadable."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    try:
        return SystemConfiguration.objects.values_list("mode", flat=True).first()
    except Exception:
        return None
