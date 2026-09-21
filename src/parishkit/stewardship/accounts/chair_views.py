"""Administrator confirmation of Chairperson suggestions as reviewed requests.

Selected suggestions are proposed from the Portal users page as a native form,
shown back as exactly the rules and assignments that would be created and for
which Member, and applied only when the Administrator confirms the signed
preview. The confirmation records one `ConfigurationChangeRequest` under the
confirmation schema, bound to the applied digest and the promoted snapshot it
was drawn from, with the selected Members attached in the same transaction;
the installer, not this process, activates it, and every guard the rule editor
relies on applies here.
"""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .chair_confirmation import REQUEST_SCHEMA, ConfirmationRefused, seed_patch
from .chair_rows import suggestion_rows
from .chair_seeding import attach_intents
from .configuration_requests import policy_operation_id
from .limiting import LimiterUnavailable
from .policy import Capability, confirmed_seeded
from .request_patch import build_candidate
from .user_rows import AppliedPolicy, role_labels
from .user_views import chair_relationships, policy_identities

SALT = "stewardship-chair-confirmation-preview-v1"
PREVIEW_FIELDS = {"selection", "member", "base_digest"}


class ConfirmationForm(forms.Form):
    """Selected rows as "ministry:address" and, per ambiguous row, a Member DUID.

    Each ambiguous row posts its own choice under the one `member` name, so
    several ambiguous rows can be confirmed in one submission; an empty choice
    is the row's unanswered question, not a value.
    """

    # An untouched form posts no selection at all; that reaches the builder,
    # which refuses it by its own closed reason rather than as a bad form.
    selection = forms.MultipleChoiceField(choices=(), required=False)
    member = forms.MultipleChoiceField(choices=(), required=False)
    base_digest = forms.RegexField(regex=r"^[0-9a-f]{64}$")

    def __init__(self, data, rows):
        """Choices are the page's own rows, so a foreign value is refused."""
        super().__init__(data)
        self.fields["selection"].choices = [
            (f"{row['ministry_duid']}:{row['email']}",) * 2 for row in rows
        ]
        self.fields["member"].choices = [("", "")] + [
            (f"{row['ministry_duid']}:{row['email']}:{member['duid']}",) * 2
            for row in rows
            for member in row["candidates"]
        ]


def _error(request, code, *, status=400):
    """A closed explanation by reason code, never naming a person."""
    response = render(
        request,
        "stewardship/chair-confirmation-error.html",
        {"code": code},
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _scope(service):
    """A confirmation pins the applied policy and the promoted snapshot."""
    current = SourceCurrent.objects.filter(singleton=True).first()
    snapshot = current.snapshot_id if current is not None else None
    return editable_configuration(service), snapshot


def _members(rows, data):
    """One Member per selected row: the sole candidate, or the one chosen.

    An ambiguous row, one whose address several active Members use, is
    confirmed only with an explicit Member, never by taking the first.
    """
    chosen = {}
    for value in data["member"]:
        if not value:
            continue
        ministry, email, duid = value.rsplit(":", 2)
        chosen[(email, int(ministry))] = int(duid)
    selected = {}
    for value in data["selection"]:
        ministry, email = value.split(":", 1)
        key = (email, int(ministry))
        row = next(
            item
            for item in rows
            if item["ministry_duid"] == key[1] and item["email"] == email
        )
        candidates = {member["duid"] for member in row["candidates"]}
        if row["ambiguous"]:
            if key not in chosen:
                raise ConfirmationRefused("ambiguous")
            if chosen[key] not in candidates:
                raise ConfirmationRefused("invalid")
            selected[key] = chosen[key]
        else:
            (selected[key],) = candidates
    return selected


def _rows(configuration):
    """The suggestion rows exactly as the page drew them."""
    document = configuration.active_configuration.canonical_document
    records = document["sections"].get("login_rules", [])
    relationships, ministries = chair_relationships(document)
    policy = AppliedPolicy(
        records,
        policy_identities(records),
        confirmed_seeded(configuration.active_configuration),
    )
    return records, suggestion_rows(policy, relationships, active=ministries)


def _preview(request, service, actor):
    """Show exactly what each confirmation creates, signed for one confirmation."""
    # One observation under the work lock, as the page itself observes: the
    # applied policy, the suggestion rows and the snapshot they came from
    # cannot straddle a source promotion, so the snapshot signed into the
    # preview is the one the rows were drawn from.
    with work_transaction():
        configuration = editable_configuration(service)
        current = SourceCurrent.objects.get(singleton=True)
        records, rows = _rows(configuration)
    form = ConfirmationForm(request.POST, rows)
    if not form.is_valid():
        return _error(request, "invalid")
    data = form.cleaned_data
    if data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the suggestions before confirming them.")
    # The request key is chosen now so every seeded origin in the patch names
    # the request that will carry it; intake and the installer verify that.
    key = uuid4()
    operation = str(policy_operation_id(actor.identity, key))
    try:
        selected = _members(rows, data)
        patch, changes = seed_patch(records, selected, operation_id=operation)
    except ConfirmationRefused as refused:
        return _error(request, refused.code)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        build_candidate(
            base, patch, candidate_id=uuid4(), request_schema=REQUEST_SCHEMA
        )
    except ConfigError:
        return _error(request, "policy")
    # The selected Members ride in the signed preview, keyed by the seeded
    # assignment records they belong to, and become intents at confirmation.
    members = {
        identifier: {
            "organization_id": current.organization_id,
            "member_duid": selected[(change.email, ministry)],
        }
        for change in changes
        for ministry, identifier in zip(
            change.ministries, change.assignment_ids, strict=True
        )
    }
    request._stewardship_display_configuration = configuration
    return render(
        request,
        "stewardship/chair-confirmation-preview.html",
        {
            "changes": [
                {
                    "email": change.email,
                    "created": change.created,
                    "before": role_labels(change.before),
                    "after": role_labels(change.after),
                    "ministries": [
                        {
                            "duid": ministry,
                            "member": selected[(change.email, ministry)],
                        }
                        for ministry in change.ministries
                    ],
                }
                for change in changes
            ],
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT,
                snapshot=current.snapshot_id,
                key=key,
                extra={"members": members},
            ),
        },
    )


def _attach(request, extra):
    """Record the reviewed Member selections beside the new request."""
    attach_intents(request, (extra or {}).get("members", {}))


@require_POST
def chair_confirmations(request):
    """Preview selected suggestions, or confirm a signed preview as a request."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        action = form_action(
            request.POST,
            preview_fields=PREVIEW_FIELDS,
            multiple_fields={"selection", "member"},
        )
        try:
            if action == "preview":
                response = _preview(request, service, actor)
            else:
                response = confirm(
                    request,
                    service,
                    actor,
                    salt=SALT,
                    current_scope=_scope,
                    request_schema=REQUEST_SCHEMA,
                    capability=Capability.MANAGE_USERS,
                    attach=_attach,
                )
        except StaleRecordError:
            # Drawn from, or signed against, an older policy or source: an
            # ordinary case for a page left open, explained by the page.
            response = _error(request, "stale", status=409)
        fresh = principal(
            request, service, read_only=True, capability=Capability.MANAGE_USERS
        )
        if fresh.identity != actor.identity:
            raise PermissionError("Chairperson confirmation editor changed.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
