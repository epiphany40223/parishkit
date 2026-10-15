"""Resume needs a sender check only after real sender trouble (#1288).

An ordinary administrative pause resumes with no test email. After sender
or delivery trouble, Pause and resume mail shows Resume unavailable with the
reason and the test-send link in place, and a refused preview or
confirmation draws that page again, never the generic "This information
changed" page. A test the mail service accepted after the trouble allows
Resume for 30 minutes.
"""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .test_delivery_control_postgresql import accepted_sender_check, sender_check
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)

pytestmark = pytest.mark.django_db(transaction=True)

PATH = reverse("admin:delivery_control")
SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
MIGRATION = SCHEMA / "migrations/0097_resume_sender_check.sql"
STALE = "This information changed"

# The view as the baseline had it before #1288: every pause needed a test
# accepted after it and less than five minutes before.
OLD_VIEW = (Path(__file__).parent / "pre_1288_delivery_control_health.sql").read_text(
    encoding="utf-8"
)


def pause(item):
    """Pause live delivery through the real preview and confirmation."""
    with web_login():
        _, token = commands.preview_pause(*item.arguments, reason="Ordinary pause")
        commands.confirm(*item.arguments, token=token)


def provider_event(
    health, *, reason=None, minutes_ago=0, submitted_ago=None, identity=None
):
    """Record a live provider outcome of the current (or given) sender.

    Real outcomes come only from a mail worker's fenced submission; this
    synthetic one is written as the schema owner with triggers paused for the
    statement, as test_mail_daily_limit_postgresql does, so the view sees a
    typed outcome without a provider. ``health`` is the evidence's health
    (``healthy``, ``unavailable`` or ``systemic``) and ``reason`` the stored
    ``smtp_*`` reason, which need not match it. The outcome is recorded
    ``minutes_ago`` and was submitted ``submitted_ago`` minutes ago (by
    default three seconds before it was recorded). Returns its id.
    """
    if reason is None:
        reason = {"healthy": "smtp_accepted", "unavailable": "smtp_unavailable"}.get(
            health, "smtp_systemic"
        )
    if submitted_ago is None:
        submitted_ago = minutes_ago + 0.05
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "INSERT INTO stewardship_outbox_event (id,created_at,correlation_id,"
            "command_id,command_digest,action,provider_key_digest,"
            "provider_message_digest,evidence_digest,evidence_note,reason,"
            "message_id,version,previous_state,state,attempt,render_id,"
            "not_before,submitted_at,provider_identity) "
            "SELECT gen_random_uuid(),"
            "statement_timestamp()-make_interval(secs=>%s),gen_random_uuid(),"
            "gen_random_uuid(),repeat('a',64),%s,'','','',%s,%s,"
            "gen_random_uuid(),1,'submitting',%s,1,gen_random_uuid(),"
            "statement_timestamp(),statement_timestamp()-make_interval(secs=>%s),"
            "coalesce(%s,"
            "stewardship_mail_provider_identity_v1(active_configuration_id)) "
            "FROM stewardship_system_configuration RETURNING id",
            [
                minutes_ago * 60,
                "accept" if health == "healthy" else "fail_unaccepted",
                f'{{"health":"{health}","synthetic":true}}',
                reason,
                "delivered" if health == "healthy" else "permanent_failure",
                submitted_ago * 60,
                identity,
            ],
        )
        return cursor.fetchone()[0]


def age_tests(item, minutes):
    """Move the campaign's test emails (and the pause before them) back."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_campaign_mail_test SET "
            "created_at=created_at-make_interval(mins=>%s),"
            "submitted_at=submitted_at-make_interval(mins=>%s),"
            "finished_at=finished_at-make_interval(mins=>%s) WHERE campaign_id=%s",
            [minutes, minutes, minutes, item.campaign.pk],
        )
        cursor.execute(
            "UPDATE stewardship_campaign_control SET "
            "created_at=least(created_at,statement_timestamp()-interval '2 hours') "
            "WHERE campaign_id=%s AND action='pause'",
            [item.campaign.pk],
        )


def assert_resume_unavailable(page, reason):
    """Preview resume is shown unavailable, with the reason in place."""
    text = page.content.decode()
    assert STALE not in text
    assert "disabled-control-link" in text
    assert 'aria-describedby="resume-unavailable-reason"' in text
    assert 'name="action" value="preview_resume"' not in text
    assert reason in text
    assert page.context["health"]["required"] and not page.context["health"]["ready"]


def resumed(item):
    """True once live delivery is no longer paused."""
    item.campaign.refresh_from_db()
    return not item.campaign.delivery_paused


def test_ordinary_pause_resumes_without_a_test_email(scheduled):
    """No trouble since the pause: Resume needs no sender check."""
    item = scheduled
    tests = CampaignMailTest.objects.count()  # The go-live test.
    pause(item)
    with web_login():
        page = item.browser.get(PATH)
        assert page.status_code == 200
        assert page.context["health"] == {
            "ready": True,
            "required": False,
            "trouble": None,
            "proof": None,
            "checked_at": None,
            "expires_at": None,
        }
        assert "No sender check is needed" in page.content.decode()
        page = post(
            item.browser, PATH, {"action": "preview_resume", "reason": "All clear"}
        )
        assert page.status_code == 200, page.content
        token = page.context["control_token"]
        response = post(item.browser, PATH, {"action": "confirm", "preview": token})
        assert response.status_code == 302, response.content
    assert resumed(item)
    assert CampaignMailTest.objects.count() == tests


def test_provider_failure_requires_a_check_said_in_place(
    scheduled, monkeypatch, tmp_path
):
    """A typed failure of the current sender greys Resume until a test passes."""
    item = scheduled
    pause(item)
    provider_event("systemic")
    with web_login():
        page = item.browser.get(PATH)
        assert page.status_code == 200
        assert page.context["health"]["trouble"] == "provider_failure"
        assert_resume_unavailable(page, "reported an outage")
        # A preview posted anyway (another tab, or the check expiring) draws
        # this page again with the reason, never the generic stale page.
        page = post(item.browser, PATH, {"action": "preview_resume", "reason": "Go"})
        assert page.status_code == 409
        assert_resume_unavailable(page, "Send a test email first")
        assert "Nothing was resumed or released" in page.content.decode()
        with pytest.raises(commands.SenderCheckRequired):
            commands.preview_resume(*item.arguments, reason="Go")
    accepted_sender_check(item, monkeypatch, tmp_path)
    with web_login():
        page = item.browser.get(PATH)
        health = page.context["health"]
        assert health["ready"] and health["required"]
        assert health["trouble"] == "provider_failure"
        assert "Sender checked at" in page.content.decode()
        _, token = commands.preview_resume(*item.arguments, reason="Sender fixed")
        commands.confirm(*item.arguments, token=token)
    assert resumed(item)


def test_trouble_before_the_pause_and_a_changed_sender(scheduled):
    """A failure later followed by a healthy send is history, not trouble; a
    sender other than the one behind the last send before the pause is."""
    item = scheduled
    provider_event("unavailable", minutes_ago=120)
    provider_event("healthy", minutes_ago=60)
    pause(item)
    health = commands.health(item.campaign.pk)
    assert health["ready"] and not health["required"]
    # The last live outcome before the pause came from another sender.
    provider_event("healthy", minutes_ago=30, identity="f" * 64)
    health = commands.health(item.campaign.pk)
    assert not health["ready"] and health["trouble"] == "sender_changed"


# Every pairing a mail result can store (family_delivery._RESULT_HEALTH):
# what counts is the provider health, never the reason. A Gmail limit
# refusal is a temporary refusal with healthy health.
OUTCOMES = [
    ("smtp_systemic", "systemic", True),
    ("smtp_delivery_unknown", "systemic", True),
    ("smtp_delivery_unknown", "unavailable", True),
    ("smtp_transient", "unavailable", True),
    ("smtp_unavailable", "unavailable", True),
    ("smtp_transient", "healthy", False),
    ("smtp_permanent", "healthy", False),
    ("smtp_delivery_unknown", "healthy", False),
    ("smtp_accepted", "healthy", False),
]


@pytest.mark.parametrize("reason,health,trouble", OUTCOMES)
def test_provider_health_not_reason_decides_trouble(scheduled, reason, health, trouble):
    """An outcome after the pause is trouble exactly when mail_health.py
    counts it as a failure, so the circuit, its alert and recovery, and this
    check share one definition."""
    from parishkit.stewardship.family_delivery import ProviderHealth
    from parishkit.stewardship.jobs.mail_health import health_filter, observed_results

    item = scheduled
    pause(item)
    event = provider_event(health, reason=reason)
    configuration = SystemConfiguration.objects.get().active_configuration_id
    failed = (
        observed_results(configuration)
        .filter(health_filter((ProviderHealth.UNAVAILABLE, ProviderHealth.SYSTEMIC)))
        .filter(pk=event)
        .exists()
    )
    assert failed is trouble
    result = commands.health(item.campaign.pk)
    assert result["required"] is trouble and result["ready"] is not trouble
    assert result["trouble"] == ("provider_failure" if trouble else None)


def test_failure_after_the_last_healthy_send_counts(scheduled):
    """A failure after the last healthy send before the pause is trouble."""
    item = scheduled
    provider_event("healthy", minutes_ago=60)
    provider_event("unavailable", reason="smtp_transient", minutes_ago=30)
    pause(item)
    assert commands.health(item.campaign.pk)["trouble"] == "provider_failure"


def test_a_late_acceptance_does_not_clear_a_later_failure(scheduled):
    """A send already in flight when the failure came (submitted before it
    was recorded, accepted after) is not proof, as in mail_health.py; a send
    submitted after the failure is."""
    item = scheduled
    provider_event("systemic", minutes_ago=10)
    provider_event("healthy", minutes_ago=9.8, submitted_ago=10.2)
    pause(item)
    assert commands.health(item.campaign.pk)["trouble"] == "provider_failure"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_campaign_control SET created_at=statement_timestamp()"
            " WHERE campaign_id=%s AND action='pause'",
            [item.campaign.pk],
        )
    provider_event("healthy", minutes_ago=0.5, submitted_ago=0.6)
    health = commands.health(item.campaign.pk)
    assert health["ready"] and not health["required"]


def test_a_test_in_progress_holds_resume_until_answered(
    scheduled, monkeypatch, tmp_path
):
    """A test sent since the pause and not yet answered holds Resume, with
    a reason that says to wait; its acceptance releases it."""
    item = scheduled
    pause(item)
    sender_check(item, monkeypatch, tmp_path, None)
    with web_login():
        page = item.browser.get(PATH)
        assert page.context["health"]["trouble"] == "test_in_progress"
        assert_resume_unavailable(page, "accepts the test email being sent now")
        assert "is still being sent" in page.content.decode()
    from .test_campaign_mail_postgresql import deliver

    monkeypatch.setattr(
        "parishkit.stewardship.accounts.campaign_mail_tasks.submit_sample",
        lambda *args, **kwargs: DeliveryOutcome.ACCEPTED,
    )
    deliver(
        (item.arguments[1], None, None, tmp_path / "google_workspace" / "credential")
    )
    health = commands.health(item.campaign.pk)
    assert health["ready"] and not health["required"]


def test_a_check_lasts_thirty_minutes(scheduled, monkeypatch, tmp_path):
    """After a failed test, an accepted one allows Resume for 30 minutes; a
    confirmation after that is refused in place, not as a stale preview."""
    item = scheduled
    pause(item)
    sender_check(item, monkeypatch, tmp_path, DeliveryOutcome.NOT_SENT)
    health = commands.health(item.campaign.pk)
    assert not health["ready"] and health["trouble"] == "test_failed"
    accepted_sender_check(item, monkeypatch, tmp_path)
    with web_login():
        page = post(item.browser, PATH, {"action": "preview_resume", "reason": "Ok"})
        assert page.status_code == 200, page.content
        token = page.context["control_token"]
    age_tests(item, 31)
    assert not commands.health(item.campaign.pk)["ready"]
    with web_login():
        page = post(item.browser, PATH, {"action": "confirm", "preview": token})
        assert page.status_code == 409
        assert_resume_unavailable(page, "Send a test email first")
    assert not resumed(item)
    # 29 minutes old: still a current check.
    age_tests(item, -2)
    health = commands.health(item.campaign.pk)
    assert health["ready"] and health["trouble"] == "test_failed"
    with web_login():
        _, token = commands.preview_resume(*item.arguments, reason="Ok again")
        commands.confirm(*item.arguments, token=token)
    assert resumed(item)


def test_baseline_view_matches_the_frozen_file():
    """The fresh-install baseline carries the frozen file's view."""
    frozen = MIGRATION.read_text(encoding="utf-8")
    start = frozen.index("CREATE OR REPLACE VIEW public.")
    end = frozen.index("LEFT JOIN proof p ON r.code IS NOT NULL;", start)
    view = frozen[start:end]
    baseline = (SCHEMA / "delivery_control.sql").read_text(encoding="utf-8")
    assert view.replace("CREATE OR REPLACE VIEW", "CREATE VIEW", 1) in baseline


def test_migration_check_refuses_the_old_view_and_admits_the_new():
    """The frozen file's DO block fails against the pre-#1288 view."""
    text = MIGRATION.read_text(encoding="utf-8")
    acl = text[text.index("SELECT set_config(") : text.index("true);") + len("true);")]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(acl)
        cursor.execute(check)  # The installed (new) view passes.
        with (
            pytest.raises(DatabaseError, match="was not replaced"),
            transaction.atomic(),
        ):
            cursor.execute(OLD_VIEW)
            cursor.execute(check)
        cursor.execute(check)  # Rolled back to the new view.
        with (
            pytest.raises(DatabaseError, match="lost or changed its grants"),
            transaction.atomic(),
        ):
            cursor.execute(
                "GRANT SELECT ON stewardship_delivery_control_health TO PUBLIC"
            )
            cursor.execute(check)
