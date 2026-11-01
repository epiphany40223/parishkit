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
            "timezone": "America/New_York",
            "date_format": "us_long",
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
    assert (
        "By: admin@example.org\nWhen: September 21, 2026 at 10:30 AM EDT"
        in content.text
    )
    assert "UTC" not in content.text and "UTC" not in content.html
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
    "occurred_at,shown",
    [
        # US fall back, November 1, 2026: both 1:30 AMs, told apart by zone.
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "November 1, 2026 at 1:30 AM EDT"),
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "November 1, 2026 at 1:30 AM EST"),
        # US spring forward, March 8, 2026.
        (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "March 8, 2026 at 1:59 AM EST"),
        (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), "March 8, 2026 at 3:00 AM EDT"),
    ],
)
def test_when_names_the_parish_zone_across_daylight_saving_changes(occurred_at, shown):
    """The event time uses the parish offset in force at that instant (#967)."""
    content = render_security_alert(alert(occurred_at=occurred_at))
    assert f"When: {shown}\n" in content.text


def test_when_follows_the_parish_zone_and_date_format():
    """The configured zone and date format decide the text; both are validated."""
    content = render_security_alert(alert(timezone="Europe/Berlin", date_format="iso"))
    assert "When: 2026-09-21 16:30 CEST\n" in content.text
    for values in ({"timezone": "Not/AZone"}, {"date_format": None}):
        with pytest.raises(ValueError):
            alert(**values)


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


def test_a_replaced_backup_key_names_both_key_ids():
    """A key change is worded with its key IDs, not login roles (#198)."""
    content = render_security_alert(
        alert(
            kind="backup_key_replaced",
            target="Backup encryption key",
            before_roles=("0123456789abcdef",),
            after_roles=("fedcba9876543210",),
        )
    )
    assert content.subject == "[PRODUCTION] SECURITY: Backup encryption key replaced"
    assert "only the matching private key can open them" in content.text
    assert (
        "Key before: 0123456789abcdef\nKey after: fedcba9876543210\n"
        "By: admin@example.org" in content.text
    )
    assert "Roles" not in content.text and "Target" not in content.text
    unknown = render_security_alert(
        alert(
            kind="backup_key_replaced",
            target="Backup encryption key",
            before_roles=(),
            after_roles=("fedcba9876543210",),
        )
    )
    assert "Key before: not known\n" in unknown.text
    with pytest.raises(ValueError):
        alert(kind="backup_key_replaced", before_roles=(), after_roles=())


def test_instructions_need_no_escaping():
    """The SQL twin inserts instructions unescaped, so they hold no markup."""
    from html import escape

    from parishkit.stewardship.jobs.security_content import (
        INSTRUCTION,
        KEY_INSTRUCTION,
        MATCH_INSTRUCTION,
    )

    for text in (INSTRUCTION, KEY_INSTRUCTION, MATCH_INSTRUCTION):
        assert escape(text) == text


def matched(**values):
    """One verified-address match's event (#2343), as its guard records it."""
    return alert(
        **{
            "kind": "address_matched",
            "target": "staff@example.org",
            "actor": "staff@example.org",
            "before_roles": (),
            "after_roles": ("ministry_leader", "staff"),
            "account_email": "person@personal.example",
        }
        | values
    )


def test_a_match_names_the_account_the_user_and_the_role():
    """A match's alert has no By row; the account itself is the actor."""
    content = render_security_alert(matched())
    assert content.subject == (
        "[PRODUCTION] SECURITY: Google account matched through another address"
    )
    assert "end the match on the Edit page of that user" in content.text
    assert content.text.endswith(
        "Account: person@personal.example\nUser: staff@example.org\n"
        "Role: Staff, Ministry leader\n"
        "When: September 21, 2026 at 10:30 AM EDT\nDeployment mode: Production\n"
        "Event reference: 00000000-0000-0000-0000-000000000007"
    )
    assert "By:" not in content.text and "Replaced" not in content.text


def test_a_takeover_names_the_replaced_account_and_its_last_sign_in():
    """The Replaced Google account row follows Role, with its time or never."""
    content = render_security_alert(
        matched(
            replaced_account="old@personal.example",
            replaced_login_at=datetime(2026, 8, 1, 16, tzinfo=UTC),
        )
    )
    assert (
        "Role: Staff, Ministry leader\n"
        "Replaced Google account: old@personal.example, last signed in "
        "August 1, 2026 at 12:00 PM EDT\nWhen:"
    ) in content.text
    assert "<dt>Replaced Google account</dt>" in content.html
    never = render_security_alert(matched(replaced_account="old@personal.example"))
    assert "Replaced Google account: old@personal.example, never signed in\n" in (
        never.text
    )


@pytest.mark.parametrize(
    "values",
    [
        {"account_email": None},
        {"account_email": ""},
        {"kind": "administrator_granted"},
        {"replaced_login_at": MOMENT},
        {"replaced_account": "", "replaced_login_at": None},
        {
            "replaced_account": "old@personal.example",
            "replaced_login_at": datetime(2026, 8, 1, 16),
        },
    ],
)
def test_only_a_match_names_its_accounts(values):
    """Another kind names no account; a replaced sign-in needs its account."""
    with pytest.raises(ValueError):
        matched(**values)
    with pytest.raises(ValueError):
        alert(account_email="person@personal.example")
