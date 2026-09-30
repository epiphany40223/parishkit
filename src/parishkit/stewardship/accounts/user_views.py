"""Read-only Administrator review of who may sign in to the portal and why."""

from dataclasses import replace

from django.db import DatabaseError, connection, transaction
from django.db.models import Max, Q
from django.shortcuts import render
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .chair_review_data import ministry_names, open_reviews
from .chair_review_rows import suspended_rows
from .chair_rows import suggestion_rows
from .limiting import LimiterUnavailable
from .ministry_activity import active_ministries
from .ministry_views import current_catalog
from .policy import Capability, confirmed_seeded
from .policy_models import PortalUser
from .user_rows import (
    ROLE_LABELS,
    AppliedPolicy,
    address_rows,
    domain_assignment_rows,
    domain_rows,
)
from .user_rules import ROLE_ORDER

# Query-string prefixes, one per table, so each pages and sorts on its own.
DOMAINS = "domains_"
ADDRESSES = "addresses_"
ASSIGNMENTS = "assignments_"
REVIEWS = "reviews_"
SUGGESTIONS = "suggestions_"
PREFIXES = (DOMAINS, ADDRESSES, ASSIGNMENTS, REVIEWS, SUGGESTIONS)


def _text(values):
    """A case-insensitive sort key for a list of display labels."""
    return ", ".join(str(value) for value in values).casefold()


def _ministries(assignments):
    """Sort key for a Ministry assignments cell: its Ministry names, then DUIDs."""
    return _text(
        sorted(
            str(item["ministry_name"] or item["ministry_duid"]) for item in assignments
        )
    )


# Every data column of every table sorts on the server, over the whole
# applied policy the page already holds in memory. A cell listing several
# values sorts by its labels joined in order, a warnings cell by how many
# warnings it has, and a sign-in by its time (never is last either way).
# The Change and Decide columns and the selection column hold controls, not
# data, so they are not sort keys. Each table's default is its former order.
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
        "assignments": lambda row: _ministries(row["assignments"]),
        **LAST_LOGIN,
        "warnings": lambda row: len(row["warnings"]),
    },
    default="email",
    descending_first={"last_login", "warnings"},
)
ASSIGNMENT_SORTING = Sorting.by_column(
    {
        "email": lambda row: row["email"].casefold(),
        "assignments": lambda row: _ministries(row["assignments"]),
        "leading": lambda row: not row["leading"],
        **LAST_LOGIN,
        "warnings": lambda row: len(row["warnings"]),
    },
    default="email",
    descending_first={"last_login", "warnings"},
)
REVIEW_SORTING = Sorting.by_column(
    {
        "email": lambda row: row["email"].casefold(),
        "ministry": lambda row: (row["ministry_name"].casefold(), row["ministry_duid"]),
        "member": lambda row: row["member_duid"],
        "reason": lambda row: str(row["reason"]).casefold(),
        "opened": lambda row: row["opened_at"],
        "current": lambda row: row["latest_at"],
        "granted": lambda row: _text(row["granted"]),
        **LAST_LOGIN,
    },
    default="email",
    descending_first={"opened", "current", "last_login"},
)
SUGGESTION_SORTING = Sorting.by_column(
    {
        "email": lambda row: row["email"].casefold(),
        "ministry": lambda row: (row["ministry_name"].casefold(), row["ministry_duid"]),
        "member": lambda row: _text(member["name"] for member in row["candidates"]),
        "publishable": lambda row: (
            not any(member["publishable"] for member in row["candidates"])
        ),
        "rule": lambda row: (
            str(row["rule"]["kind"] or ""),
            _text(row["rule"]["roles"]),
        ),
        "assignment": lambda row: _text(item["source"] for item in row["assignments"]),
        "ambiguity": lambda row: row["owners"],
    },
    default="ministry",
    descending_first={"ambiguity"},
)
TABLES = {
    "domain_table": (DOMAINS, DOMAIN_SORTING),
    "address_table": (ADDRESSES, ADDRESS_SORTING),
    "assignment_table": (ASSIGNMENTS, ASSIGNMENT_SORTING),
    "review_table": (REVIEWS, REVIEW_SORTING),
    "suggestion_table": (SUGGESTIONS, SUGGESTION_SORTING),
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


SUGGESTION_COLUMNS = (
    "member_duid",
    "member_name",
    "ministry_duid",
    "ministry_name",
    "email",
    "publish_email",
    "address_members",
)


def chair_relationships(document):
    """The current source's Chairperson relationships and the active Ministries.

    Read from the schema-owned projection under the observation's lock, for
    the promoted snapshot only, so a relationship is never paired with another
    generation's names. Which of those Ministries the applied activity keeps
    active is decided by the same rule the reconciliation owner applies. With
    no promoted source there is nothing to suggest.
    """
    current = SourceCurrent.objects.filter(singleton=True).first()
    if current is None or current.snapshot_id is None:
        return [], frozenset()
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {','.join(SUGGESTION_COLUMNS)} FROM stewardship_chair_suggestion"
            " WHERE snapshot_id=%s AND organization_id=%s"
            " ORDER BY ministry_duid,email,member_duid,roster_key",
            [current.snapshot_id, current.organization_id],
        )
        relationships = [
            dict(zip(SUGGESTION_COLUMNS, row, strict=True)) for row in cursor
        ]
    ministries = active_ministries(
        document,
        organization_id=current.organization_id,
        catalog_duids=frozenset(item["ministry_duid"] for item in relationships),
    )
    return relationships, ministries


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

    One read-only snapshot keeps the applied policy, the source overlays and the
    Google identities one coherent observation; separate READ COMMITTED
    statements could pair a newly activated rule with an older overlay. It
    takes no work-order lock, so the page never waits behind a source promotion
    or installer, and only the observation runs inside it.
    Shaping and rendering happen after release, and only then does a short
    transaction recheck current access and record the view: a response that
    failed to render, or whose reader was revoked meanwhile, never leaves a
    successful disclosure on record. Editing goes through previewed
    configuration requests on its own route, never these reads.

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
            # The same definition of a confirmed Chairperson that sign-in uses.
            active = confirmed_seeded(configuration.active_configuration)
            relationships, ministries = chair_relationships(
                configuration.active_configuration.canonical_document
            )
            current = SourceCurrent.objects.filter(singleton=True).first()
            reviews = open_reviews(configuration, current)
            names = ministry_names(current)
            # The chrome presents this verified observation, never a newer one.
            request._stewardship_display_configuration = configuration
        policy = AppliedPolicy(records, identities, active, names=names)
        # The assignment editor offers the promoted catalog's active Ministries,
        # judged by the same rule the suggestion table applies, and only when
        # the editor itself would accept the catalog, so the page never offers
        # an addition the route refuses. Removals need no catalog.
        document = configuration.active_configuration.canonical_document
        assignable = sorted(
            (
                (duid, names[duid])
                for duid in active_ministries(
                    document,
                    organization_id=current.organization_id,
                    catalog_duids=frozenset(names),
                )
            )
            if current_catalog(document, current) is not None
            else (),
            key=lambda item: (item[1].casefold(), item[0]),
        )
        tables = user_tables(
            paging,
            {
                "domain_table": domain_rows(policy),
                "address_table": address_rows(policy),
                "assignment_table": domain_assignment_rows(policy),
                "review_table": suspended_rows(policy, reviews),
                "suggestion_table": suggestion_rows(
                    policy, relationships, active=ministries
                ),
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
                "assignable": assignable,
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
