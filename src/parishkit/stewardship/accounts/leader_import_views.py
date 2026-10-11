"""The Users page's ParishSoft Ministry leaders table and its import (#952 slice 6).

The table sits below the users table on ``users`` (``user_views.users``
draws it with ``leaders_context``); Import selected and Import all post the
ticked addresses here, to ``user_leader_import``
(``/admin/users/leaders/import/``), which answers with the **Import
Ministry leaders** review. Its Apply posts the signed review to Users, which
records it as one configuration change like any other user change
(``user_views._audit_change`` adds the import's audit rows).

Every route is Administrator-only (``Capability.MANAGE_USERS``) and checks
that again after building its answer. The leaders are read through the
``stewardship_leaders_without_user_v1`` definer (``leader_import``), so a
posted address only chooses among the rows the server reads now; the Name,
the Member DUID and the role are never taken from the form.
"""

from uuid import uuid4

from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import UserFacingError, unexpected_fields
from parishkit.stewardship.web.tables import paginate

from . import admin_navigation
from .admin_editing import error_response, principal, sign_preview
from .authentication import runtime
from .leader_import import (
    LEADER_SORTING,
    SKIP_REASONS,
    plan_import,
    read_leaders,
    table_rows,
)
from .policy import Capability
from .request_patch import build_candidate
from .user_changes import ENTRY_LIMIT, UserRefused, clean_email

# The leaders table's query-string prefix, beside the users table's
# ``users_`` (the shared Admin tables keep each table's own place).
PREFIX = "leaders_"
# At most this many addresses in one Import selected post: more than any
# page of the table can show (250 rows, or All of a parish's leaders).
CHOSEN_LIMIT = 2000
SCOPES = {"selected", "all"}


def leaders_context(request, paging, carry):
    """The leaders table's part of the Users page context.

    Reads the leaders through the definer (which records its own audit
    event), then pages and sorts them. Call it outside any read-only
    snapshot: the definer writes. A refused session raises
    ``LeadersRefused`` (a ``PermissionError``), and a database failure
    propagates, so the page fails closed rather than showing a partial
    list. ``carry`` keeps the users table's own place in this table's links.
    An unknown sort is refused before anything is read.
    """
    LEADER_SORTING.parse(paging, PREFIX)
    leaders = read_leaders(request.session.session_key)
    table = paginate(
        table_rows(leaders),
        paging,
        prefix=PREFIX,
        carry=carry,
        sorting=LEADER_SORTING,
    )
    return {
        "leaders": table,
        "leaders_unreachable": leaders.unreachable,
        "import_url": reverse("admin:user_leader_import"),
    }


def _choice(parameters):
    """The import's scope and chosen addresses, or refuse the form.

    ``scope`` is ``all`` or ``selected``; ticked rows post ``email`` (their
    addresses), which ``all`` ignores, since the table's form posts them
    with either button. ``selected`` needs at least one.
    """
    if set(parameters) - {"csrfmiddlewaretoken", "scope", "email"} or any(
        len(values) != 1 for name, values in parameters.lists() if name != "email"
    ):
        raise unexpected_fields()
    scope = parameters.get("scope")
    emails = parameters.getlist("email")
    if scope not in SCOPES or len(emails) > CHOSEN_LIMIT:
        raise unexpected_fields()
    if scope == "all":
        return None
    try:
        chosen = [clean_email(email) for email in emails]
    except UserRefused:
        raise unexpected_fields() from None
    if not chosen:
        raise UserFacingError(
            _("No leader was chosen."),
            fix=_("Tick the leaders to import, or choose Import all."),
        )
    return chosen


def _recheck(request, service, actor):
    """Refuse an answer whose reader lost access while it was built."""
    current = principal(
        request, service, read_only=True, capability=Capability.MANAGE_USERS
    )
    if current.identity != actor.identity:
        raise PermissionError("Users reader changed.")


@require_POST
def user_leader_import(request):
    """Review importing the chosen ParishSoft Ministry leaders as users.

    Reads the leaders now, plans the import against the applied users in
    one read-only snapshot (``plan_import``), validates the whole resulting
    configuration and signs the review for one Apply on Users. Anyone who
    became a user meanwhile, or is no longer listed, is skipped and named
    on the review. With nobody left to import the review says so and offers
    no Apply. Nothing is recorded here but the definer's own audit event.
    """
    from .user_views import ERRORS, SALT, _users

    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields()
        chosen = _choice(request.POST)
        leaders = read_leaders(request.session.session_key)
        with read_transaction():
            configuration, records = _users(service)
            try:
                plan = plan_import(records, leaders, chosen)
            except UserRefused as refused:
                raise UserFacingError(
                    refused.message, fix=_("Return to Users and start again.")
                ) from None
            request._stewardship_display_configuration = configuration
            token = None
            if plan.patch:
                base = service.store.active()
                if (
                    base is None
                    or base.digest != configuration.active_configuration.digest
                ):
                    raise StaleRecordError("The applied configuration changed.")
                # The whole resulting configuration is validated now, so an
                # import the installer would refuse is refused here.
                build_candidate(base, plan.patch, candidate_id=uuid4())
                token = sign_preview(
                    actor=actor,
                    configuration=configuration,
                    patch=plan.patch,
                    salt=SALT,
                    extra={"leader_import": True},
                )
        admin_navigation.place(request, flow="change", step="review")
        response = render(
            request,
            "stewardship/user-leader-import.html",
            {
                "added": plan.added,
                "skipped": [
                    item | {"reason_label": SKIP_REASONS[item["reason"]]}
                    for item in plan.skipped
                ],
                "remaining": plan.remaining,
                "limit": ENTRY_LIMIT,
                "preview": token,
                "post_url": reverse("admin:users"),
                "list_url": reverse("admin:users"),
            },
        )
        _recheck(request, service, actor)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
