"""Security alert content is closed, escaped and worded from the event alone."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.jobs.security_content import (
    KINDS,
    SecurityAlert,
    render_security_alert,
)

MOMENT = datetime(2026, 9, 21, 14, 30, 5, tzinfo=UTC)


def alert(**values):
    """One Administrator grant recorded by the activation trigger."""
    return SecurityAlert(
        **{
            "event_id": UUID(int=7),
            "kind": "administrator_granted",
            "target": "new@example.org",
            "actor": "admin@example.org",
            "mode": SystemMode.PRODUCTION,
            "occurred_at": MOMENT,
            "before_roles": ("staff",),
            "after_roles": ("administrator", "staff"),
        }
        | values
    )


def test_renders_every_fact_in_fixed_order_and_escapes_markup():
    """Subject, instruction and rows come only from the event's own facts."""
    content = render_security_alert(alert(target="<b>new</b>@example.org"))
    assert (
        content.subject
        == "[PRODUCTION] SECURITY: Administrator added to an exact address"
    )
    assert content.text.startswith("Administrator added to an exact address\n\n")
    assert "Target: <b>new</b>@example.org" in content.text
    assert "Roles before: Staff\nRoles after: Administrator, Staff" in content.text
    assert "By: admin@example.org\nWhen: 09/21/2026 14:30:05 UTC" in content.text
    assert "Deployment mode: Production" in content.text
    assert content.text.endswith(
        "Event reference: 00000000-0000-0000-0000-000000000007"
    )
    assert "<dd>&lt;b&gt;new&lt;/b&gt;@example.org</dd>" in content.html
    assert "<b>" not in content.html.replace("<b>new</b>", "")


def test_recovery_and_empty_roles_are_worded():
    """No portal actor reads as operator recovery; no roles read as none."""
    content = render_security_alert(
        alert(
            actor=None, kind="domain_created", before_roles=(), after_roles=("staff",)
        )
    )
    assert content.subject == "[PRODUCTION] SECURITY: Hosted-domain rule created"
    assert (
        "Roles before: none\nRoles after: Staff\nBy: Operator recovery" in content.text
    )


def test_mode_is_visible_and_times_are_canonical_utc():
    """A Testing deployment says so; only a canonical UTC time is accepted."""
    content = render_security_alert(alert(mode=SystemMode.TESTING))
    assert content.subject.startswith("[TESTING] SECURITY:")
    assert "Deployment mode: Testing" in content.text
    with pytest.raises(ValueError):
        alert(occurred_at=MOMENT.astimezone(timezone(timedelta(hours=-4))))


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "other"},
        {"target": ""},
        {"actor": 5},
        {"occurred_at": datetime(2026, 9, 21, 14, 30)},
        {"before_roles": ["staff"]},
        {"mode": "production"},
    ],
)
def test_untyped_or_unknown_facts_are_refused(values):
    """The compiler never guesses at a kind, a naive time or a list of roles."""
    with pytest.raises(ValueError):
        alert(**values)


def test_role_words_match_the_page_labels_in_their_order():
    """The helper's plain words are the page's translated labels, in its order."""
    from parishkit.stewardship.accounts.user_rows import ROLE_LABELS
    from parishkit.stewardship.jobs.security_content import ROLE_WORDS

    assert [role for role, _ in ROLE_WORDS] == list(ROLE_LABELS)
    assert [word for _, word in ROLE_WORDS] == [
        str(label) for label in ROLE_LABELS.values()
    ]


def test_kinds_match_the_dashboard_wording():
    """The email names each expansion exactly as the dashboard does."""
    from parishkit.stewardship.accounts.security_events import KINDS as DASHBOARD

    assert dict(KINDS) == DASHBOARD
