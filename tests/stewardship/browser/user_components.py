"""The Users and access pages reuse the shared browser server and real row builders.

Sign-in rules, Ministry assignments and Chairpersons (NAV-15) each render
their own template from the same sample policy, so the pages and their rows
agree.
"""

from datetime import UTC, datetime

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.user_rows import (
    ROLE_LABELS,
    AppliedPolicy,
    address_rows,
    domain_assignment_rows,
    domain_rows,
)
from parishkit.stewardship.accounts.user_rules import ROLE_ORDER
from parishkit.stewardship.accounts.user_views import PAGES, user_tables

from ..policy_factory import address, assignment, domain


def components(context, admin):
    """Production shaping over sample policy, so the page and its rows agree."""
    moment = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
    seeded = assignment("chair@example.org", ministry=9, seeded=True)
    records = [
        domain("workspace.example", roles=("ministry_leader", "staff")),
        domain("unused.example", roles=("staff",)),
        address("admin@example.org"),
        address("blocked@example.org", roles=()),
        address("chair@example.org", roles=("ministry_leader",), seeded=True),
        seeded,
        address("leader@workspace.example", roles=("ministry_leader", "staff")),
        assignment("leader@workspace.example", ministry=4),
        assignment("helper@workspace.example", ministry=7),
        assignment("consumer@workspace.example", ministry=6),
        assignment("stray@elsewhere.example", ministry=8),
    ]

    def identity(email, *, hosted=None, login=moment, disabled=False):
        """One recorded Google identity; `login` is its last successful sign-in."""
        return dict(
            email=email, hosted_domain=hosted, last_login=login, disabled=disabled
        )

    identities = [
        identity("admin@example.org"),
        # Verified by Google, refused by the explicit deny: never a sign-in.
        identity("blocked@example.org", login=None),
        identity("leader@workspace.example", hosted="workspace.example", disabled=True),
        identity("helper@workspace.example", hosted="workspace.example"),
        # The address ends with the domain, but Google presented no such claim.
        identity("consumer@workspace.example", login=None),
    ]

    # One suspended Chairperson assignment awaiting review, shaped as
    # chair_review_rows.suspended_rows builds it (#563: its reason gate).
    review = dict(
        email="chair@example.org",
        ministry_duid=9,
        ministry_name="Choir",
        member_duid=41,
        reason="The Ministry is inactive in the applied activity.",
        opened_at=moment,
        generation=3,
        latest_at=moment,
        elsewhere=False,
        manual=False,
        granted=["Ministry leader"],
        leading=False,
        last_login=moment,
    )

    # One Chairperson suggestion, shaped as the view builds it (#563: the
    # selection hint of its bulk Review).
    suggestion = dict(
        email="newchair@example.org",
        ministry_duid=9,
        ministry_name="Choir",
        candidates=[dict(name="Sample Member", duid=41, publishable=True)],
        ambiguous=False,
        owners=1,
        rule=dict(kind=None, roles=[], deny=False, suspended=False, domain=None),
        assignments=[],
    )

    def page(
        rules, known, active=frozenset(), reviews=(), suggestions=(), *, section="rules"
    ):
        """Render exactly the context the view builds for one of the pages."""
        policy = AppliedPolicy(rules, known, active)
        template, names = PAGES[section]
        rows = {
            "domain_table": lambda: domain_rows(policy),
            "address_table": lambda: address_rows(policy),
            "assigned_table": lambda: [
                row for row in address_rows(policy) if row["assignments"]
            ],
            "assignment_table": lambda: domain_assignment_rows(policy),
            "review_table": lambda: list(reviews),
            "kept_table": lambda: [
                row for row in address_rows(policy) if row["seed_only"]
            ],
            "suggestion_table": lambda: list(suggestions),
        }
        return render_to_string(
            template,
            context
            | {"admin_chrome": admin}
            | {
                **user_tables({}, {name: rows[name]() for name in names}),
                "base_digest": "0" * 64,
                "roles": [(role, ROLE_LABELS[role]) for role in ROLE_ORDER],
                "assignable": [(4, "Choir"), (9, "Lectors")],
            },
        )

    def preview(**values):
        """The review page for one proposed change, with a sample signed token."""
        return render_to_string(
            "stewardship/user-rule-preview.html",
            context
            | {"admin_chrome": admin}
            | dict(
                kind="address",
                identity="new@example.org",
                created=False,
                removed=False,
                before=["Staff"],
                after=["Administrator", "Staff"],
                expansion="administrator",
                recorded=2,
                self_affected=False,
                preview="signed-sample",
            )
            | values,
        )

    return {
        "/portal-users": ("text/html", page(records, identities)),
        "/ministry-assignments": (
            "text/html",
            page(records, identities, section="assignments"),
        ),
        "/chairpersons": (
            "text/html",
            page(records, identities, section="chairpersons"),
        ),
        "/portal-users-confirmed": (
            "text/html",
            page(records, identities, frozenset({seeded["id"]})),
        ),
        # Only the mandatory Administrator: both optional tables are empty.
        "/portal-users-minimal": ("text/html", page([address()], [])),
        "/portal-users-review": (
            "text/html",
            page(records, identities, reviews=[review], section="chairpersons"),
        ),
        "/portal-users-suggestions": (
            "text/html",
            page(records, identities, suggestions=[suggestion], section="chairpersons"),
        ),
        "/portal-users-preview": ("text/html", preview()),
        "/portal-users-preview-deny": (
            "text/html",
            preview(after=[], expansion=None),
        ),
        "/portal-users-preview-remove": (
            "text/html",
            preview(
                kind="domain",
                identity="workspace.example",
                removed=True,
                after=None,
                expansion=None,
                recorded=0,
            ),
        ),
        "/portal-users-refused": (
            "text/html",
            render_to_string(
                "stewardship/user-rule-error.html",
                context | {"admin_chrome": admin} | {"code": "consumer"},
            ),
        ),
    }
