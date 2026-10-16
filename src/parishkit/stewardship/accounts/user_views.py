"""The Users page (#952 slice 5): one table, New and Edit user, and Delete.

Who may sign in to the Admin portal is the active configuration's list of
users, each one email address with a Name and one role (admin-portal
specification, "Portal user management"). Every route here is
Administrator-only (``Capability.MANAGE_USERS``) and checks that again after
building its answer, so an Administrator demoted meanwhile is refused:

- ``users`` (``/admin/users/``) lists the users in one table sorted on the
  server, and is where a reviewed change is applied (``action=confirm``).
- ``user_new`` and ``user_edit`` are one page: Email, Name and Role. Its
  Review answers with a fragment the shared review dialog shows (UX
  conventions, "Review dialog", #750): each changed value before and after,
  signed for one Apply, which posts to ``users``; once applied the page
  returns to Users. A refusal of a field's value is this page again, with
  each error at its field, which the page script marks on the form.
- ``user_delete`` answers the table's confirmation dialog: it records one
  configuration change at once, as Dates and mail schedules' Delete does.
- ``user_name_suggestions`` answers the New page's Email field in place: is
  the address valid and new, and which ParishSoft Members list it.

Below the users table, ``users`` also draws the ParishSoft Ministry leaders
who have no user yet, and its Apply records their import
(``leader_import_views``, #952 slice 6).

Nothing here edits configuration directly. Every change is one
``ConfigurationChangeRequest`` built by ``user_changes.plan_changes`` and
recorded through ``admin_editing.confirm_intent``, which rechecks the
session, the capability and the applied version under the work lock; the
installer activates it, and SQL repeats the last-Administrator guard and
raises the security event when someone is made an Administrator. Each
recorded change also gets a ``user_change_requested`` audit event naming the
request, whose patch holds the address, Name and role; an import of
ParishSoft leaders gets ``user_leaders_import_requested`` instead, and one
``user_leader_imported`` per new user.

An Administrator never demotes or deletes their own user here (Administrator
decision, 2026-10-11): their row has no Delete, their Edit page offers only
Administrator, ``plan_changes`` refuses it at Review and Delete, and the
request's own transaction refuses it again (``_audit_change``). Without
this, the change would end the Administrator's access to its own Change
status page partway through.
"""

from uuid import UUID, uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError, transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingMissing,
    unexpected_fields,
    with_default_code,
)
from parishkit.stewardship.web.tables import paginate, table_parameters

from . import admin_navigation
from .admin_editing import (
    confirm,
    confirm_intent,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .leader_import import imported_ids
from .leader_import_views import PREFIX as LEADER_PREFIX
from .leader_import_views import leaders_context
from .limiting import Counter, LimiterUnavailable
from .policy import Capability
from .policy_models import PortalUser
from .request_patch import build_candidate
from .user_changes import (
    DEFAULT_ROLE,
    MESSAGES,
    LookupLimited,
    UserRefused,
    acting_email,
    clean_email,
    member_names,
    only_administrator,
    own_user_ids,
    plan_changes,
    refuse_own_demotion,
    saved_users,
)
from .user_reads import USER_SORTING, policy_identities, user_list
from .user_rows import ROLE_HELP, ROLE_LABELS
from .users_schema import NAME_LIMIT, uses_users

SALT = "stewardship-users-preview-v1"
# The users table's query-string prefix, so the ParishSoft Ministry leaders
# table that follows it (#952 slice 6, ``leader_import_views.PREFIX``) keeps
# its own place.
PREFIX = "users_"
ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    LookupError,
    StaleRecordError,
    signing.BadSignature,
)
# New user's Name lookups the page admits for one Administrator in a minute
# (#952 review), ahead of the lookup definer's 60 an hour (migration 0053).
LOOKUP_LIMIT = 20
LOOKUP_SECONDS = 60
# Only one of these refusals concerns the change as a whole; the rest are a
# field's (``UserRefused.field``).
LAST_ADMINISTRATOR = _(
    "This is the only Administrator, so it must stay an Administrator. Make "
    "another user an Administrator first."
)
# The acting Administrator's own user, on Edit user and under the table.
OWN_ROLE = _(
    "This is your own user, so it must stay an Administrator. Another "
    "Administrator can change your role."
)
OWN_DELETE = _(
    "Your own user has no Delete: you can't delete yourself. Another Administrator can."
)


class UserForm(AdminForm):
    """New and Edit user's three fields; Edit's Email is shown, not posted.

    Only shape is checked here: ``plan_changes`` decides everything else
    (a valid, new address, a usable Name, the last-Administrator guard), so
    the page and ``pk-admin users preview`` cannot disagree.
    """

    # Required in the browser too, so the shared page script marks an empty
    # field as the reader leaves it; ``plan_changes`` words the server's
    # refusal. Each field is described by its reserved error line (and the
    # Name by its help line), whose ids user-entry.html draws.
    email = forms.CharField(
        label=_("Email"),
        max_length=254,
        required=False,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "off",
                "spellcheck": "false",
                "required": True,
                "aria-describedby": "id_email_error",
                "data-user-email": True,
            }
        ),
    )
    name = forms.CharField(
        label=_("Name"),
        max_length=NAME_LIMIT,
        required=False,
        strip=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "list": "user-names",
                "required": True,
                "aria-describedby": "id_name_help id_name_error",
                "data-user-name": True,
            }
        ),
    )
    role = forms.ChoiceField(
        label=_("Role"),
        choices=[(role, ROLE_LABELS[role]) for role in reversed(ROLE_LABELS)],
        initial=DEFAULT_ROLE,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, new=True, **kwargs):
        """Edit leaves Email out of the posted fields: it never changes."""
        super().__init__(*args, **kwargs)
        if not new:
            del self.fields["email"]


def _users(service):
    """The applied users, and the configuration they come from.

    The page exists only for a configuration in the users form, which every
    deployment past setup is once the users release starts (#952 slice 4).
    """
    configuration = editable_configuration(service)
    document = configuration.active_configuration.canonical_document
    if not uses_users(document):
        raise ConfigError("Configuration is unavailable.")
    return configuration, document["sections"]["login_rules"]


def _scope(service):
    """User changes pin YAML only; they do not depend on a source refresh."""
    return editable_configuration(service), None


def _audit_change(actor):
    """``confirm_intent``'s ``attach``: refuse self-demotion, then audit it.

    First the request's own own-Administrator check (``refuse_own_demotion``
    against the request's base), which rolls the request back with a plain
    refusal. Then one event names the request, whose patch names the
    address, Name and role and whose base names what they were; the context
    counts the users the change touches, never an address (audit context
    holds no personal data). That event is ``user_change_requested``, or,
    for a reviewed import of ParishSoft leaders (signed with
    ``leader_import``), ``user_leaders_import_requested``, followed by one
    ``user_leader_imported`` per new user, whose subject is that user's
    record ID.
    """

    def attach(request, extra):
        """Check, then record the events, in the request's own transaction."""
        try:
            refuse_own_demotion(
                request.base.canonical_document["sections"]["login_rules"],
                request.patch,
                acting_email(actor.identity),
            )
        except UserRefused as refused:
            raise UserFacingError(
                refused.message, fix=_("Nothing was changed.")
            ) from None
        imported = bool((extra or {}).get("leader_import"))
        record_action(
            Action.USER_LEADERS_IMPORT_REQUESTED
            if imported
            else Action.USER_CHANGE_REQUESTED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=request.pk,
            context={"outcome": Outcome.SUCCEEDED, "count": len(request.patch)},
        )
        if imported:
            for identifier in imported_ids(request.patch):
                record_action(
                    Action.USER_LEADER_IMPORTED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=identifier,
                    context={"outcome": Outcome.SUCCEEDED},
                )

    return attach


def _recheck(request, service, actor):
    """Refuse an answer whose reader lost access while it was built."""
    current = principal(
        request, service, read_only=True, capability=Capability.MANAGE_USERS
    )
    if current.identity != actor.identity:
        raise PermissionError("Users reader changed.")
    return current


def _missing():
    """An unknown user ID, such as one deleted meanwhile or an old link."""
    return UserFacingMissing(
        _("That user no longer exists."),
        fix=_("Return to Users to see the current list."),
        link=reverse("admin:users"),
        link_label=_("Users"),
    )


def table_rows(rows, users, own_email=None):
    """Add each row's Edit address and whether it may be deleted.

    The reader's own row and the only Administrator's offer no Delete
    (#563); the reason is said once under the table (the only Administrator
    is always the reader, who must be one), and the server refuses either
    deletion anyway. ``confirm_name`` is how the Delete dialog names the
    user.
    """
    own = own_user_ids(users, own_email)
    return [
        row
        | {
            "role_label": ROLE_LABELS[row["role"]],
            "edit_url": reverse("admin:user_edit", args=[row["id"]]),
            "deletable": row["id"] not in own
            and not only_administrator(users, row["id"]),
            # The delete dialog names the user's address, Name and role.
            "confirm_name": _("%(email)s (%(name)s, %(role)s)")
            % {
                "email": row["email"],
                "name": row["name"],
                "role": ROLE_LABELS[row["role"]],
            },
        }
        for row in rows
    ]


@require_http_methods(["GET", "HEAD", "POST"])
def users(request):
    """List the users, or apply a reviewed change (``action=confirm``).

    The list is observed in one read-only snapshot and rendered after it, then
    the reader's access is rechecked and the view audited with the rows
    shown, never an address or role. The ParishSoft Ministry leaders table
    below it is read through its definer, which audits itself
    (``leader_import_views.leaders_context``). Apply records the signed
    review (a user change, or an import of leaders) as one configuration
    change and answers with its Change status page, which the review
    dialog follows (never shown as a page) before it returns here or redraws
    this page in place.
    """
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        if request.method == "POST":
            if request.GET or request.FILES:
                raise unexpected_fields()
            form_action(request.POST, preview_fields=set())
            if request.POST.get("action") != "confirm":
                raise unexpected_fields()
            response = confirm(
                request,
                service,
                actor,
                salt=SALT,
                current_scope=_scope,
                capability=Capability.MANAGE_USERS,
                attach=_audit_change(actor),
            )
            _recheck(request, service, actor)
            response["Cache-Control"] = "no-store"
            return response
        paging = filters(
            request.GET,
            allowed=set(table_parameters(PREFIX))
            | set(table_parameters(LEADER_PREFIX)),
        )
        with read_transaction():
            configuration, records = _users(service)
            rows = user_list(records, policy_identities(records))
            saved = saved_users(records)
            own = acting_email(actor.identity)
            # The chrome presents this verified observation, never a newer one.
            request._stewardship_display_configuration = configuration
        table = paginate(
            table_rows(rows, saved, own),
            paging,
            prefix=PREFIX,
            carry=_carried(paging, LEADER_PREFIX),
            sorting=USER_SORTING,
        )
        leaders = leaders_context(request, paging, _carried(paging, PREFIX))
        response = render(
            request,
            "stewardship/users.html",
            leaders
            | {
                "table": table,
                "base_digest": configuration.active_configuration.digest,
                "refresh_url": request.get_full_path(),
                "new_url": reverse("admin:user_new"),
                "delete_url": reverse("admin:user_delete"),
                # The leader import's review dialog applies here.
                "apply_url": reverse("admin:users"),
                # Why the reader's own row has no Delete, said whichever page
                # it is on. The reader is always an Administrator, so the
                # only Administrator, when there is one, is the reader too,
                # and the own-Administrator rule is the one that governs.
                "delete_reason": OWN_DELETE,
            },
        )
        with transaction.atomic():
            current = _recheck(request, service, actor)
            record_action(
                Action.USERS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                context={"outcome": Outcome.SUCCEEDED, "count": len(table.rows)},
            )
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)


def _carried(paging, prefix):
    """The other table's place (its prefixed page, size and sort) to keep."""
    return [(name, value) for name, value in paging.items() if name.startswith(prefix)]


def recorded_identities(email):
    """The recorded Google identities of one address, in ID order."""
    return [
        {"id": str(row["id"]), "disabled": row["disabled"]}
        for row in PortalUser.objects.filter(email=email)
        .order_by("id")
        .values("id", "disabled")
    ]


def _entry_context(
    request,
    form,
    *,
    user=None,
    user_id=None,
    users,
    digest,
    identities=(),
    own_email=None,
):
    """Everything New and Edit user draw besides the form itself.

    On Edit, the lower roles are unavailable for the reader's own user and
    for the only Administrator, with the reason in a line that is always
    there (#563, no layout shift); the recorded Google identities of the
    address (``recorded_identities``) give its disabled-identity line and one
    Sign-in activity button each.
    """
    own = user_id is not None and user_id in own_user_ids(users, own_email)
    locked = own or (user_id is not None and only_administrator(users, user_id))
    identities = list(identities)
    return {
        "form": form,
        "new": user is None,
        "user": user,
        "roles": [
            {
                "value": role,
                "label": ROLE_LABELS[role],
                "help": ROLE_HELP[role],
                "locked": locked and role != "administrator",
            }
            for role in reversed(ROLE_LABELS)
        ],
        "locked": locked,
        "locked_reason": (OWN_ROLE if own else LAST_ADMINISTRATOR) if locked else "",
        "identities": identities,
        "disabled_count": sum(row["disabled"] for row in identities),
        "base_digest": digest,
        "post_url": request.path,
        # The review dialog's Apply posts to Users, then returns there.
        "apply_url": reverse("admin:users"),
        "list_url": reverse("admin:users"),
        "names_url": reverse("admin:user_name_suggestions"),
    }


def _entry_page(request, context, *, status=200):
    """Render New or Edit user; a refused one gets the error summary."""
    form = context["form"]
    if status == 400:
        context = context | {
            "errors": [
                {
                    "message": message,
                    # A radio group's first choice stands for the group.
                    "field_id": None
                    if field == "__all__"
                    else f"{form[field].auto_id}_0"
                    if field == "role"
                    else form[field].auto_id,
                }
                for field, messages in form.errors.items()
                for message in messages
            ]
        }
    response = render(request, "stewardship/user-entry.html", context, status=status)
    if status == 400:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def _entries(form, user_id):
    """The change document's one entry for a valid page."""
    data = form.cleaned_data
    if user_id is None:
        return [{"email": data["email"], "name": data["name"], "role": data["role"]}]
    return [{"id": user_id, "name": data["name"], "role": data["role"]}]


def _refuse(form, refused):
    """Show a builder refusal at its field, or refuse the change as a whole.

    A refusal of a field's value stays on the form (the page again, marked
    at the field). Any other (an unchanged review, a guard on the change as
    a whole) is a plain refusal, which the review dialog shows (UX
    conventions, "Review dialog", #750).
    """
    if refused.field not in form.fields:
        raise UserFacingError(
            _refusal_message(refused), fix=_("Nothing was changed.")
        ) from None
    form.add_error(
        refused.field,
        forms.ValidationError(_refusal_message(refused), code=refused.code),
    )


def _refusal_message(refused):
    """A builder refusal in the page's words: the guards name the user."""
    return {
        "last_administrator": LAST_ADMINISTRATOR,
        "own_administrator": OWN_ROLE,
    }.get(refused.code, refused.message)


def review_lines(change):
    """Edit user's compact review (#1356): one line per changed field.

    Each is the field's label with its old and new value in words, which
    components/compact-review.html draws as "Field: old → new". New user's
    confirmation draws none (``change.before`` is None).
    """
    if change.before is None:
        return []
    fields = {
        "name": (_("Name"), lambda values: values["name"]),
        "role": (_("Role"), lambda values: ROLE_LABELS.get(values["role"])),
    }
    return [
        {"label": label, "before": shown(change.before), "after": shown(change.after)}
        for key, (label, shown) in fields.items()
        if key in change.changed
    ]


def _review(request, service, actor, configuration, changes, patch):
    """The signed review of one user change, for the review dialog.

    A fragment, not a page: the New or Edit page's script shows it in the
    shared review dialog, whose Apply posts the signed preview to Users.
    """
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    # The whole resulting configuration is validated now, so a change the
    # installer would refuse is refused here, before anything is signed.
    build_candidate(base, patch, candidate_id=uuid4())
    (change,) = changes
    return render(
        request,
        "stewardship/user-review.html",
        {
            "change": change,
            "lines": review_lines(change),
            "preview": sign_preview(
                actor=actor, configuration=configuration, patch=patch, salt=SALT
            ),
        },
    )


def _entry(request, service, actor, user_id=None):
    """New user (``user_id`` None) or Edit user, GET or POST (Review).

    A POST reviews the change and answers with the review dialog's fragment
    (``_review``), whose Apply posts to Users; one refused for a field's
    value shows this page again with each error at its field (status 400),
    which the page script turns into marks on the form it already shows.
    Any other refusal (``_refuse``, an invalid resulting configuration) is
    a plain refusal for the dialog.
    """
    filters(request.GET, allowed=set())
    if request.FILES:
        raise unexpected_fields()
    new = user_id is None
    posted = request.POST if request.method == "POST" else None
    if posted is not None and (
        form_action(
            posted,
            preview_fields={"base_digest", "name", "role", *(["email"] if new else [])},
        )
        != "preview"
    ):
        raise unexpected_fields()
    with read_transaction():
        configuration, records = _users(service)
        users = saved_users(records)
        user = None
        if not new:
            user = users.get(str(user_id))
            if user is None:
                raise _missing()
        digest = configuration.active_configuration.digest
        initial = (
            {"role": DEFAULT_ROLE}
            if new
            else {"name": user["name"], "role": user["role"]}
        )
        form = UserForm(posted, initial=initial, new=new)
        own = acting_email(actor.identity)
        context = _entry_context(
            request,
            form,
            user=user,
            user_id=None if new else str(user_id),
            users=users,
            digest=digest,
            identities=() if new else recorded_identities(user["email"]),
            own_email=own,
        )
        request._stewardship_display_configuration = configuration
        admin_navigation.place(request, flow="change", step="edit")
        if posted is None:
            return _entry_page(request, context)
        if posted.get("base_digest") != digest:
            raise StaleRecordError("Reload Users before changing a user.")
        if not form.is_valid():
            return _entry_page(request, context, status=400)
        try:
            changes, patch = plan_changes(
                records,
                _entries(form, None if new else str(user_id)),
                own_email=own,
            )
        except UserRefused as refused:
            if refused.code == "missing":
                raise _missing() from None
            _refuse(form, refused)
            return _entry_page(request, context, status=400)
        try:
            return _review(request, service, actor, configuration, changes, patch)
        except ConfigError:
            raise UserFacingError(
                MESSAGES["invalid"], fix=_("Nothing was changed.")
            ) from None


def _answer(request, user_id=None):
    """New or Edit user, rechecked after it was built and never cached."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        response = _entry(request, service, actor, user_id)
        _recheck(request, service, actor)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def user_new(request):
    """New user: Email, Name (from ParishSoft when it can) and Role."""
    return _answer(request)


@require_http_methods(["GET", "HEAD", "POST"])
def user_edit(request, user_id):
    """Edit user: the New page filled in, its Email fixed."""
    return _answer(request, user_id)


def _chosen(parameters):
    """The one user a Delete names: users are deleted one at a time."""
    if set(parameters) != {"csrfmiddlewaretoken", "base_digest", "user_id"} or any(
        len(values) != 1 for _name, values in parameters.lists()
    ):
        raise unexpected_fields()
    try:
        return str(UUID(parameters["user_id"]))
    except ValueError:
        raise unexpected_fields() from None


@require_POST
def user_delete(request):
    """Delete one user in one configuration change, as the dialog confirmed.

    The change is planned and signed as a review would be, then recorded at
    once (``confirm_intent`` rechecks the session, the capability and the
    applied version under the work lock). A refusal changes nothing and is
    shown in the dialog. The answer is the change's status page, which the
    dialog follows until the change is applied.
    """
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields()
        user_id = _chosen(request.POST)
        listing = reverse("admin:users")
        with read_transaction():
            configuration, records = _users(service)
            if request.POST["base_digest"] != configuration.active_configuration.digest:
                raise StaleRecordError("Reload Users before deleting a user.")
            try:
                _changes, patch = plan_changes(
                    records,
                    [{"id": user_id, "delete": True}],
                    own_email=acting_email(actor.identity),
                )
            except UserRefused as refused:
                if refused.code == "missing":
                    raise _missing() from None
                raise UserFacingError(
                    OWN_DELETE
                    if refused.code == "own_administrator"
                    else _refusal_message(refused),
                    fix=_("Nothing was deleted."),
                ) from None
            token = sign_preview(
                actor=actor, configuration=configuration, patch=patch, salt=SALT
            )
        receipt = confirm_intent(
            request,
            service,
            actor,
            token=token,
            salt=SALT,
            link=listing,
            current_scope=_scope,
            capability=Capability.MANAGE_USERS,
            attach=_audit_change(actor),
        )
        admin_navigation.remember_origin(request, receipt.request_id, path=listing)
        _recheck(request, service, actor)
        return HttpResponseRedirect(
            reverse("admin:configuration_request", args=[receipt.request_id])
        )
    except ERRORS as error:
        return error_response(error)


def _lookup_delay(service, actor):
    """Count one Name lookup against the Administrator's per-minute window.

    The page's own limit, ahead of the definer's hourly one: a lookup that
    would be the ``LOOKUP_LIMIT``th within ``LOOKUP_SECONDS`` for this
    Administrator answers 429 with the seconds to wait. An unavailable
    limiter raises ``LimiterUnavailable`` (503): the lookup fails closed,
    never unlimited. Call it outside any transaction, as the limiter needs.
    """
    limiter = service.limiter
    counter = Counter(
        "admin_user_names",
        limiter.fingerprint("user_names", str(actor.identity)),
        LOOKUP_LIMIT,
        LOOKUP_SECONDS,
    )
    return limiter.counters([counter], failure=True)


def _lookup_limited(retry):
    """New user's lookup refused for now; the page asks the reader to type."""
    response = JsonResponse({"error": None, "names": []}, status=429)
    response["Retry-After"] = str(min(3600, max(1, int(retry))))
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def user_name_suggestions(request):
    """Check New user's Email in place, and suggest Names from ParishSoft.

    A CSRF-protected POST, so the address never appears in a URL. The answer
    is JSON: ``error``, the address's problem in the page's words (not an
    address, or already a user) or null, and ``names``, the distinct Names
    of the current ParishSoft Members that list it, best first. It saves
    nothing but the lookup's audit event, which the definer records itself
    with the count of names found, never the address. Two limits apply: the
    page's per-minute window (``_lookup_delay``) and the definer's 60 an
    hour for each Administrator; either answers 429.
    """
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        if request.FILES or set(request.POST) != {"csrfmiddlewaretoken", "email"}:
            raise unexpected_fields()
        error, names = None, []
        try:
            email = clean_email(request.POST["email"])
        except UserRefused as refused:
            error = refused.message
        if error is None:
            delay = _lookup_delay(service, actor)
            if delay:
                return _lookup_limited(delay)
        try:
            with transaction.atomic():
                if error is None:
                    _configuration, records = _users(service)
                    if email in {
                        values["email"] for values in saved_users(records).values()
                    }:
                        error = MESSAGES["email_taken"]
                    else:
                        names = member_names(request.session.session_key, email)
                _recheck(request, service, actor)
        except LookupLimited:
            return _lookup_limited(LOOKUP_SECONDS)
        response = JsonResponse(
            {"error": None if error is None else str(error), "names": names}
        )
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
