"""The portal users review reuses the shared browser server and real row builders."""

from datetime import UTC, datetime

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.user_rows import (
    address_rows,
    domain_assignment_rows,
    domain_rows,
)

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
        assignment("stray@elsewhere.example", ministry=8),
    ]
    users = [
        dict(
            email="admin@example.org",
            hosted_domain=None,
            verified_at=moment,
            disabled=False,
        ),
        dict(
            email="leader@workspace.example",
            hosted_domain="workspace.example",
            verified_at=moment,
            disabled=True,
        ),
    ]

    def page(rules, identities, active=frozenset()):
        """Render exactly the context the view builds."""
        return render_to_string(
            "stewardship/users.html",
            context
            | {"admin_chrome": admin}
            | {
                "domains": domain_rows(rules, identities),
                "addresses": address_rows(rules, identities, active),
                "domain_assignments": domain_assignment_rows(rules, active),
            },
        )

    return {
        "/portal-users": ("text/html", page(records, users)),
        "/portal-users-confirmed": (
            "text/html",
            page(records, users, frozenset({seeded["id"]})),
        ),
        # Only the mandatory Administrator: both optional tables are empty.
        "/portal-users-minimal": ("text/html", page([address()], [])),
    }
