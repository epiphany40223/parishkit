"""The portal users review reuses the shared browser server and real row builders."""

from datetime import UTC, datetime

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.user_rows import (
    Policy,
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
        policy = Policy(rules, known, active)
        return render_to_string(
            "stewardship/users.html",
            context
            | {"admin_chrome": admin}
            | {
                "domains": domain_rows(policy),
                "addresses": address_rows(policy),
                "domain_assignments": domain_assignment_rows(policy),
            },
        )

    return {
        "/portal-users": ("text/html", page(records, identities)),
        "/portal-users-confirmed": (
            "text/html",
            page(records, identities, frozenset({seeded["id"]})),
        ),
        # Only the mandatory Administrator: both optional tables are empty.
        "/portal-users-minimal": ("text/html", page([address()], [])),
    }
