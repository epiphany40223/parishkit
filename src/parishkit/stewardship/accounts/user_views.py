"""Read-only Administrator review of who may sign in to the portal and why."""

from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import editable_configuration, error_response
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .policy_models import AssignmentOverlay, MinistryAssignment, PortalUser
from .sessions import authenticated_admin
from .user_rows import address_rows, domain_assignment_rows, domain_rows


def _principal(request, service, *, read_only=False):
    """Only an Administrator may see who else holds access."""
    actor = authenticated_admin(
        request, store=service.store, activity=not read_only, read_only=read_only
    )
    if not allows(actor, Capability.MANAGE_USERS):
        raise PermissionError("Portal user management requires an Administrator.")
    return actor


def _active_seeded(configuration):
    """Seeded assignments the promoted source currently confirms.

    A missing overlay is not proof of a current Chairperson, exactly as sign-in
    itself decides, so this page never shows a scope a sign-in would not get.
    """
    seeded = MinistryAssignment.objects.filter(
        configuration=configuration.active_configuration, source="chair-seed"
    ).values_list("record_id", flat=True)
    active = AssignmentOverlay.objects.filter(
        assignment_record_id__in=seeded, active=True
    ).values_list("assignment_record_id", flat=True)
    return frozenset(str(identifier) for identifier in active)


@require_safe
def users(request):
    """List the applied login rules, their effective roles and provenance.

    The work lock keeps the applied policy, the source overlays and the Google
    identities one coherent observation; separate READ COMMITTED statements could
    pair a newly activated rule with an older overlay. Editing arrives later and
    will go through configuration requests, never through this page's reads.
    """
    try:
        service = runtime()
        actor = _principal(request, service)
        # The page takes no parameters, so an address never reaches a URL or log.
        filters(request.GET, allowed=set())
        with work_transaction():
            configuration = editable_configuration(service)
            records = configuration.active_configuration.canonical_document[
                "sections"
            ].get("login_rules", [])
            identities = list(
                PortalUser.objects.values(
                    "email", "hosted_domain", "verified_at", "disabled"
                )
            )
            active = _active_seeded(configuration)
            context = {
                "domains": domain_rows(records, identities),
                "addresses": address_rows(records, identities, active),
                "domain_assignments": domain_assignment_rows(records, active),
            }
            # A demotion between admission and rendering must not disclose the
            # list, so current access is rechecked before the response exists.
            _principal(request, service, read_only=True)
            response = render(request, "stewardship/users.html", context)
            record_action(
                Action.USERS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor.identity,
                # Counts only: an audit row never carries an address or a role.
                context={
                    "outcome": Outcome.SUCCEEDED,
                    "count": len(context["domains"]) + len(context["addresses"]),
                },
            )
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
    ) as error:
        return error_response(error)
