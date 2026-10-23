"""Shared authenticated, exact-preview admission for non-secret Admin editors.

Only server-built patches are signed. A signature binds intent, not permission:
confirmation rechecks current session, role, configuration and optional source
scope inside the request owner's transaction. The web process never installs
YAML or claims that an accepted request has already applied.
"""

import json
import logging
from datetime import datetime
from urllib.parse import urlencode
from uuid import UUID, uuid4, uuid5

from django.core import signing
from django.core.exceptions import NON_FIELD_ERRORS
from django.core.paginator import InvalidPage
from django.db import connection
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.live_end_date import bind_reviewed_end_edit
from parishkit.stewardship.campaigns.single_campaign import refuse_campaign_creation
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import (
    current_correlation,
    debug_logging_enabled,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    UserFacingGone,
    UserFacingUnavailable,
    annotate,
    expired_preview,
    gone_response,
    load_preview,
    send_time_passed,
    stale_page,
    unavailable_response,
    unexpected_fields,
)

from . import admin_navigation
from .configuration_installation import coherent_configuration
from .configuration_request_reads import receipt as configuration_receipt
from .configuration_requests import record_request
from .policy import Capability, allows
from .request_models import ConfigurationChangeRequest
from .role_refusal import role_denied
from .sessions import authenticated_admin, sign_in_instant


def principal(
    caller, service, *, passive=False, read_only=False, capability=Capability.CONFIGURE
):
    """Read current authorization; passive progress pages never renew idle time.

    A read-only recheck after rendering renews nothing either. Pages that need
    a different Administrator capability name it, so one admission function
    serves every Admin page. ``caller`` is an ``AdminCaller``, or (until the
    final ADM-11 PR) a Django request that ``authenticated_admin`` converts.
    """
    value = authenticated_admin(
        caller,
        store=service.store,
        activity=not passive and not read_only,
        read_only=read_only,
    )
    if value is None:
        # No current session (idle timeout, sign-out, revocation): say so,
        # rather than implying the signed-in account lacks a capability.
        raise signed_out()
    if not allows(value, capability):
        # A current session without the role: say the role is the reason,
        # not the session (#1924).
        raise role_denied(capability)
    return value


def signed_out():
    """The refusal for a request whose Admin session has ended."""
    return UserFacingDenied(
        _(
            "Your sign-in has ended, for example after an hour without "
            "activity or after signing out in another tab."
        ),
        fix=_("Sign in again to continue."),
        link="/admin/login",
        link_label=_("Sign in"),
    )


def editable_configuration(service):
    """No ordinary edit is possible before setup or during restore/mismatch."""
    value = coherent_configuration(service.store)
    if value.restore_review_required or not service.configured():
        raise ConfigError("Configuration is unavailable.")
    return value


def form_action(
    parameters,
    *,
    preview_fields,
    multiple_fields=frozenset(),
    confirm_fields=frozenset(),
    direct=False,
):
    """Reject hidden, repeated and cross-action fields before constructing intent.

    ``confirm_fields`` names the few explicit confirmation inputs, such as an
    acknowledgement checkbox, that accompany the signed preview. An editor
    that also saves directly inside the campaign wizard (``direct``, #1404
    PR 4) accepts a third action, ``save``: the preview's fields plus the
    page-drawn ``request_key`` (``direct_key``).
    """
    fields = {
        "preview": {"action", "csrfmiddlewaretoken", *preview_fields},
        "confirm": {"action", "csrfmiddlewaretoken", "preview", *confirm_fields},
    }
    if direct:
        fields["save"] = fields["preview"] | {"request_key"}
    action = parameters.get("action")
    if action not in fields:
        # No sources: an unknown action value, not an unexpected field.
        raise unexpected_fields()
    multiple = multiple_fields if action in {"preview", "save"} else ()
    if set(parameters) - fields[action] or any(
        len(values) != 1 and name not in multiple for name, values in parameters.lists()
    ):
        raise unexpected_fields(parameters, allowed=fields[action], multiple=multiple)
    return action


def direct_key(parameters):
    """The request key of a direct save (``form_action``'s ``save``).

    The page draws a fresh ``request_key`` each time it is drawn; the
    change's own key is derived from it and every posted value (UUID5, the
    names sorted, each name's values in posted order; the CSRF token, which
    may be rotated, left out). So a repeated submit of the same values from
    the same page (a double click, a retry after a lost answer) returns the
    first submit's receipt instead of recording the change twice, while a
    different change from the same page is a request of its own and is
    never answered with the first one's status (#1486 review). Raises
    ``ValueError`` (a 400 refusal) when the page's key is not a UUID.
    """
    page_key = UUID(parameters.get("request_key", ""))
    posted = sorted(
        (name, values)
        for name, values in parameters.lists()
        if name not in {"csrfmiddlewaretoken", "request_key"}
    )
    return uuid5(page_key, json.dumps(posted, separators=(",", ":")))


def sign_preview(
    *, actor, configuration, patch, salt, snapshot=None, key=None, extra=None, due=()
):
    """A preview has one request key, one base and a fifteen-minute validity window.

    An editor whose patch must name its own request, as manual policy
    provenance does, chooses the key first and passes it; the rest take a
    fresh one. An editor whose request needs companion rows signs their
    content as `extra`, so confirmation records exactly what was reviewed.
    ``due`` lists the send instants (ISO 8601) the change sets that were
    still ahead at review; confirmation refuses once any has passed
    (``refuse_passed_sends``, #1464). A preview without any carries no key.
    """
    from .report_rows import translate

    # A schedule editor's change to a converted report (#1786) is recorded
    # in the report's own values; no other change needs the document.
    if any(item.get("section") == "schedules" for item in patch):
        patch = translate(configuration.active_configuration.canonical_document, patch)
    intent = {
        "actor": str(actor.identity),
        "key": str(key or uuid4()),
        "base": configuration.active_configuration.digest,
        "snapshot": str(snapshot) if snapshot is not None else None,
        "patch": patch,
        "extra": extra,
    }
    if due:
        intent["due"] = list(due)
    return signing.dumps(intent, salt=salt)


def database_now():
    """The database's ``clock_timestamp()``, the clock dispatch sends by.

    Not the transaction's start time: a confirmation may wait for the work
    lock, and the check must see the clock after that wait (#1464).
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def refuse_passed_sends(due, link):
    """Refuse a confirmation whose reviewed send time has since passed (#1464).

    ``due`` is the signed intent's send instants (ISO 8601), each still
    ahead when the change was reviewed. One at or before the database's
    clock would send at once once applied, minutes after the time the
    Administrator chose, so the confirmation is refused and nothing
    changes. This only ever refuses: it never moves a time or sends.
    """
    if due and min(map(datetime.fromisoformat, due)) <= database_now():
        raise send_time_passed(link)


def confirm(
    request,
    service,
    actor,
    *,
    salt,
    current_scope,
    request_schema=None,
    capability=Capability.CONFIGURE,
    attach=None,
    privileged=False,
    region=None,
    first_campaign=False,
):
    """Admit one exact intent from the page; redirect to its request status.

    The page's form carries the signed preview; ``confirm_intent`` does the
    rest. The status page leads back to this editor (#196). ``privileged``
    marks an integration change, which an automation session must make with
    a live full-scope session, reported once (``sign_in_instant``). An editor
    that names its ``region`` (Create the campaign's, which its review dialog
    follows) is answered with itself instead, naming the request at that
    region.
    """
    receipt = confirm_intent(
        request,
        service,
        actor,
        token=request.POST.get("preview", ""),
        salt=salt,
        link=request.path,
        current_scope=current_scope,
        request_schema=request_schema,
        capability=capability,
        attach=attach,
        privileged=privileged,
        first_campaign=first_campaign,
    )
    admin_navigation.remember_origin(request, receipt.request_id)
    if region:
        query = urlencode({"request": receipt.request_id})
        return HttpResponseRedirect(f"{request.path}?{query}#{region}")
    return HttpResponseRedirect(
        reverse("admin:configuration_request", args=[receipt.request_id])
    )


def requested_status(caller, service, actor, parameters):
    """The status of the change a page that follows it names, or None.

    ``parameters`` is the page's query: nothing, or ``request`` naming one
    of this Administrator's own changes (the confirmation's answer). Another
    Administrator's or an unknown request raises ``LookupError``; a value
    that is not a request id raises ``ValueError``. Runs inside the caller's
    transaction, as Change status does.
    """
    selected = filters(parameters, allowed={"request"})
    if "request" not in selected:
        return None
    return configuration_receipt(caller, service, UUID(selected["request"]), actor)


def summary_errors(form):
    """A refused form's (or formset's) errors for an error summary.

    The summary is drawn, for a review dialog's editor, on the page a
    Review refused for its values answers with (status 400), which the page
    script places above the heading. The form itself is never replaced (its
    scripts and the reader's typing stay), so the summary names each field
    and links to it. A hidden
    field's error (a stale version) has nothing to link to. A formset's
    errors are its own, then each row's.
    """
    if hasattr(form, "forms"):
        # A formset: its own errors, then each row's.
        errors = [
            {"field_id": None, "message": message} for message in form.non_form_errors()
        ]
        for row in form.forms:
            errors.extend(summary_errors(row))
        return errors
    errors = []
    for name, messages in form.errors.items():
        field = None if name == NON_FIELD_ERRORS else form[name]
        for message in messages:
            if field is None or field.is_hidden:
                errors.append({"field_id": None, "message": message})
            else:
                errors.append(
                    {
                        "field_id": field.id_for_label,
                        "message": _("%(label)s: %(message)s")
                        % {"label": field.label, "message": message},
                    }
                )
    return errors


def receipt_status(receipt):
    """Template context for the live status of a change a page names.

    ``receipt`` is the change's status (Create the campaign's, once
    confirmed), or None; its live region polls Change status's passive read.
    """
    status_url = None
    if receipt is not None:
        status_url = reverse("admin:configuration_request", args=[receipt.request_id])
    return {"receipt": receipt, "status_url": status_url}


def confirm_intent(
    caller,
    service,
    actor,
    *,
    token,
    salt,
    link,
    current_scope,
    request_schema=None,
    capability=Capability.CONFIGURE,
    attach=None,
    privileged=False,
    first_campaign=False,
    direct=False,
):
    """Admit one exact intent; identical retries return the original receipt.

    ``caller`` is the page's request or an ``AdminCaller`` (the command
    line's ``schedule confirm``); ``link`` is the page an expired preview
    sends the Administrator back to (None from the command line). The
    recheck under the work transaction tests the same capability the page
    admitted with, so the two cannot diverge if the capability matrix
    changes. `attach(request, extra)` records an editor's companion rows from
    the signed preview inside the request's own durable transaction, and is
    never called on a retry. With ``privileged``, an automation caller must
    still hold a live full-scope session under the work lock
    (``sign_in_instant``); a repeat (the request already exists) still
    passes that check, but records no second automation event or notice.
    A web sign-in of any age confirms (#1547).
    ``first_campaign`` is Create the campaign's confirmation (#142): its
    intent must add exactly one campaign, and its ``current_scope`` refuses
    unless the deployment still has none. A preview that carries reviewed
    send times (``sign_preview``'s ``due``) is refused under the work lock
    once any has passed (``refuse_passed_sends``, #1464). ``direct`` is a
    campaign wizard step's direct save (#1404 PR 4,
    ``campaign_wizard.save_step``): its preview was signed by the same
    request, never shown, so a version that moved since the page was drawn
    is the stale page's refusal rather than "review again". Returns the
    request's ``RequestStatus``.
    """
    if len(token) > 256_000:
        raise ValueError("Invalid configuration preview.")
    intent = load_preview(token, salt=salt, link=link)
    if intent["actor"] != str(actor.identity):
        raise PermissionError("Preview belongs to another Administrator.")
    # Until #145 no editor may add a campaign, even with a preview signed
    # before New campaign and Copy campaign were retired (rule 10). Create the
    # campaign adds the first one; each salt keeps its previews apart.
    refuse_campaign_creation(intent["patch"], first=first_campaign)
    key = UUID(intent["key"])

    def admit():
        """The owning work lock persists until intake's durable transaction commits."""
        with work_transaction():
            current = authenticated_admin(caller, store=service.store, read_only=True)
            if not allows(current, capability) or current.identity != actor.identity:
                return False
            existing = ConfigurationChangeRequest.objects.filter(
                actor_id=actor.identity, request_key=key
            ).exists()
            if privileged:
                # A repeat still needs the check, but an automation session's
                # repeat records no second event or notice, as the export
                # commands' repeats record none.
                sign_in_instant(caller, record=not existing)
            if existing:
                # An identical retry returns the original receipt (record_request
                # finds it by key) even when the page's own admission no longer
                # holds, such as Create the campaign once its campaign exists.
                return True
            configuration, snapshot = current_scope(service)
            if (
                configuration.active_configuration.digest != intent["base"]
                or (str(snapshot) if snapshot is not None else None)
                != intent["snapshot"]
            ):
                # Settings changed since the review; start a fresh one.
                raise stale_page() if direct else expired_preview(link)
            # Under the work lock, so the clock is read after any wait for it.
            refuse_passed_sends(intent.get("due"), link)
            return True

    def attach_all(created):
        """Bind a live end-date edit, then record the editor's companion rows.

        A change to a live campaign's end date (#912) crosses the structural
        lock only with its exceptional intent, bound here in the request's
        own transaction so the installer never sees the request without it.
        """
        bind_reviewed_end_edit(created)
        if attach is not None:
            attach(created, intent.get("extra"))

    return record_request(
        base_digest=intent["base"],
        patch=intent["patch"],
        actor_id=actor.identity,
        request_key=key,
        correlation_id=current_correlation(),
        admit=admit,
        request_schema=request_schema,
        attach=attach_all,
    )


def error_response(error):
    """Expose only closed, static error messages, never exception or submitted text.

    Scripts receive JSON. A browser page request is shown the same messages as
    an HTML page by the browser-error middleware. A user-facing refusal
    (``web.refusals``) keeps its base type's status but adds its reviewed
    explanation and fix link. Every response carries the error's reason for
    the refusal log (``web.refusals.annotate``).

    A ``KeyError`` or ``IndexError`` is re-raised instead (#2044). Views
    catch ``LookupError`` as their "not found", and those two are its
    subclasses, but every intentional not-found raises a plain
    ``LookupError`` or a dedicated subclass (``UserFacingMissing``,
    ``DayOutOfRange``); only a defect raises these two. Re-raised, it
    reaches Django's closed 500 boundary with its traceback logged rather
    than answering an unknown page (#2029's ``KeyError`` looked like one).
    """
    if isinstance(error, (KeyError, IndexError)):
        raise error
    return annotate(_error_response(error), error)


def _error_response(error):
    """The closed response for ``error``, as ``error_response`` describes."""
    if debug_logging_enabled():
        # The response names only a closed category; say what actually failed.
        logging.getLogger("parishkit.stewardship.debug").debug(
            "admin request refused", exc_info=error
        )
    if isinstance(error, UserFacingGone):
        return gone_response(error)
    if isinstance(error, UserFacingUnavailable):
        return unavailable_response(error)
    if isinstance(error, PermissionError):
        code, status = ErrorCode.DENIED, 403
    elif isinstance(error, StaleRecordError):
        code, status = ErrorCode.STALE, 409
    elif isinstance(error, ConfigError):
        code, status = ErrorCode.UNAVAILABLE, 503
    elif isinstance(error, (ValueError, InvalidPage, signing.BadSignature)):
        code, status = ErrorCode.INVALID, 400
    elif isinstance(error, LookupError):
        code, status = ErrorCode.UNAVAILABLE, 404
    else:
        code, status = ErrorCode.UNAVAILABLE, 503
    return validation_response(
        [FieldError(code)], status=status, refusal=getattr(error, "refusal", None)
    )
