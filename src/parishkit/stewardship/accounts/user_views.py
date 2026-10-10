"""Read-only Administrator review of who may sign in to the portal and why."""

from dataclasses import replace

from django.db import DatabaseError, transaction
from django.db.models import Max, Q
from django.shortcuts import render
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability
from .policy_models import PortalUser
from .user_rows import ROLE_LABELS, AppliedPolicy, address_rows, domain_rows
from .user_rules import ROLE_ORDER

# Query-string prefixes, one per table, so each pages and sorts on its own.
DOMAINS = "domains_"
ADDRESSES = "addresses_"
PREFIXES = (DOMAINS, ADDRESSES)


def _text(values):
    """A case-insensitive sort key for a list of display labels."""
    return ", ".join(str(value) for value in values).casefold()


# Every data column of every table sorts on the server, over the whole
# applied policy the page already holds in memory. A cell listing several
# values sorts by its labels joined in order, a warnings cell by how many
# warnings it has, and a sign-in by its time (never is last either way).
# The Change column holds controls, not data, so it is not a sort key.
# Each table's default is its former order.
LAST_LOGIN = {"last_login": lambda row: row["last_login"]}
DOMAIN_SORTING = Sorting.by_column(
    {
        "domain": lambda row: row["domain"].casefold(),
        "roles": lambda row: _text(row["roles"]),
        "authorized": lambda row: row["authorized"],
        **LAST_LOGIN,
        "warnings": lambda row: len(row["warnings"]),
    },
    default="domain",
    descending_first={"authorized", "last_login", "warnings"},
)
ADDRESS_SORTING = Sorting.by_column(
    {
        "email": lambda row: row["email"].casefold(),
        "origin": lambda row: str(row["origin"]).casefold(),
        # An explicit deny configures no role, so it sorts first.
        "configured": lambda row: (
            _text(grant["role"] for grant in row["grants"]) if not row["deny"] else ""
        ),
        "granted": lambda row: _text(row["granted"]),
        **LAST_LOGIN,
        "warnings": lambda row: len(row["warnings"]),
    },
    default="email",
    descending_first={"last_login", "warnings"},
)
TABLES = {
    "domain_table": (DOMAINS, DOMAIN_SORTING),
    "address_table": (ADDRESSES, ADDRESS_SORTING),
}


def policy_identities(records):
    """Only the Google identities this policy names, with successful sign-ins.

    Every verified Google attempt records an identity and refreshes its
    verification time, including a stranger's attempt that policy then denies.
    So the read is bounded by policy, not by attempts, and a sign-in means the
    durable login event written only when a session was actually issued.
    """
    emails = {
        record["values"]["email"]
        for record in records
        if record["values"]["kind"] != "domain"
    }
    domains = {
        record["values"]["domain"]
        for record in records
        if record["values"]["kind"] == "domain"
    }
    # Addresses are stored normalized, so the stored column is compared as is.
    # An identity at a configured domain is history for that domain's row even
    # when no rule names its address and it presented no hosted claim, such as
    # a consumer account whose exact rule was since removed; the evaluator's
    # stricter claim test still decides whom the rule authorizes.
    named = Q(email__in=emails)
    for domain in domains:
        named |= Q(email__endswith=f"@{domain}")
    rows = list(
        PortalUser.objects.filter(named).values(
            "id", "email", "hosted_domain", "disabled"
        )
    )
    logins = dict(
        AuditEvent.objects.filter(
            event_type="admin_login", actor_id__in=[row["id"] for row in rows]
        )
        .values_list("actor_id")
        .annotate(latest=Max("created_at"))
    )
    return [row | {"last_login": logins.get(row["id"])} for row in rows]


def user_tables(paging, rows):
    """Sort and page every table on the page, each keeping the others' place.

    ``rows`` maps each ``TABLES`` name to its full row list; ``paging`` is
    the validated query string. Every navigator link and sort heading of
    one table carries the other tables' page, size and sort, so changing
    one table never resets another.
    """
    tables = {
        name: paginate(rows[name], paging, prefix=prefix, sorting=sorting)
        for name, (prefix, sorting) in TABLES.items()
    }

    def state(table):
        """One table's page, size and sort as (name, value) pairs."""
        return (
            (table.size_name, table.size_value),
            (table.page_name, str(table.number)),
            *table.sort_fields,
        )

    return {
        name: replace(
            table,
            carried=tuple(
                pair
                for other, kept in tables.items()
                if other != name
                for pair in state(kept)
            ),
        )
        for name, table in tables.items()
    }


@require_safe
def users(request):
    """Observe one snapshot, render outside it, then recheck and audit.

    One read-only snapshot keeps the applied policy and the Google identities
    one coherent observation. It takes no work-order lock, so the page never
    waits behind a source promotion or installer, and only the observation
    runs inside it. Shaping and rendering happen after release, and only then
    does a short transaction recheck current access and record the view: a
    response that failed to render, or whose reader was revoked meanwhile,
    never leaves a successful disclosure on record. Editing goes through
    previewed configuration requests on its own route, never these reads.

    Like the Admin editors it sits beside, the page is unavailable before setup
    completes and during a restore review, when the applied configuration is not
    yet trusted as the parish's own.
    """
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        # Only the tables' page numbers, sizes and closed sort tokens are
        # parameters, so an address never reaches a URL or log.
        paging = filters(
            request.GET,
            allowed={name for prefix in PREFIXES for name in table_parameters(prefix)},
        )
        with read_transaction():
            configuration = editable_configuration(service)
            records = configuration.active_configuration.canonical_document[
                "sections"
            ].get("login_rules", [])
            identities = policy_identities(records)
            # The chrome presents this verified observation, never a newer one.
            request._stewardship_display_configuration = configuration
        policy = AppliedPolicy(records, identities)
        tables = user_tables(
            paging,
            {
                "domain_table": domain_rows(policy),
                "address_table": address_rows(policy),
            },
        )
        response = render(
            request,
            "stewardship/users.html",
            tables
            | {
                # Every edit form carries the digest it was drawn from, so a
                # change proposed against an older policy is refused as stale.
                "base_digest": configuration.active_configuration.digest,
                "roles": [(role, ROLE_LABELS[role]) for role in ROLE_ORDER],
            },
        )
        with transaction.atomic():
            current = principal(
                request, service, read_only=True, capability=Capability.MANAGE_USERS
            )
            if current.identity != actor.identity:
                raise PermissionError("Portal users reader changed.")
            record_action(
                Action.USERS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                # The rows actually rendered, counted; never an address or role.
                context={
                    "outcome": Outcome.SUCCEEDED,
                    "count": sum(len(table.rows) for table in tables.values()),
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
