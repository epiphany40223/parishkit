"""The portal users review reuses the shared browser server and real row builders."""

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

    def page(rules, known, active=frozenset()):
        """Render exactly the context the view builds."""
        policy = AppliedPolicy(rules, known, active)
        return render_to_string(
            "stewardship/users.html",
            context
            | {"admin_chrome": admin}
            | {
                "domains": domain_rows(policy),
                "addresses": address_rows(policy),
                "domain_assignments": domain_assignment_rows(policy),
                "base_digest": "0" * 64,
                "roles": [(role, ROLE_LABELS[role]) for role in ROLE_ORDER],
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
                deny=False,
                expansion="administrator",
                recorded=2,
                self_affected=False,
                preview="signed-sample",
            )
            | values,
        )

    return {
        "/portal-users": ("text/html", page(records, identities)),
        "/portal-users-confirmed": (
            "text/html",
            page(records, identities, frozenset({seeded["id"]})),
        ),
        # Only the mandatory Administrator: both optional tables are empty.
        "/portal-users-minimal": ("text/html", page([address()], [])),
        "/portal-users-preview": ("text/html", preview()),
        "/portal-users-preview-deny": (
            "text/html",
            preview(after=[], deny=True, expansion=None),
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
