"""The critical-events banner explains itself and an Administrator can clear it."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from parishkit.stewardship.audit.models import (
    AuditEvent,
    CriticalEventAcknowledgement,
    OperationalLog,
)
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.observability import Event

from ..log_samples import sample as log_sample
from ..policy_factory import address
from .auth_builders import signed_in
from .test_security_events_postgresql import home, signed_in_as
from .test_user_rule_views_postgresql import web
from .test_user_views_postgresql import add_rules

pytestmark = pytest.mark.django_db(transaction=True)
ROUTE = "/admin/critical-events/acknowledge"
BANNER = "Critical problems in the past 24 hours"


def critical(event):
    """Record one CRITICAL operational event the way producers do."""
    with transaction.atomic():
        operational(event, level="CRITICAL", **log_sample(event))


def shown(page):
    """The signed id list the page's Acknowledge form carries."""
    return page.split('name="shown" value="', 1)[1].split('"', 1)[0]


def acknowledge(browser, token=None):
    """Post the banner's own form with the genuine CSRF cookie.

    Without ``token``, the form is the one on this Administrator's current
    home page, as a click on Acknowledge would post it.
    """
    if token is None:
        token = shown(home(browser))
    with web():
        return browser.post(
            ROUTE,
            {
                "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
                "shown": token,
            },
        )


def audits():
    """How many acknowledgements the audit log holds."""
    return AuditEvent.objects.filter(event_type="critical_events_acknowledged").count()


def test_banner_names_events_and_acknowledgement_clears_it_for_everyone(
    auth_service, google
):
    """Grouped plain-language labels, a shared acknowledgement, and a return."""
    add_rules(auth_service.store, address("second@example.org"))
    browser, login = signed_in()
    assert login.status_code == 302
    assert BANNER not in home(browser)
    critical(Event.SOURCE_INVALID)
    critical(Event.SOURCE_INVALID)
    critical(Event.MAIL_PROVIDER_FAILED)
    page = home(browser)
    assert BANNER in page
    assert "ParishSoft data refresh failed (2×)" in page
    assert "Email sending failed" in page
    assert ROUTE in page and 'name="critical" value="yes"' in page
    response = acknowledge(browser)
    assert response.status_code == 302 and response["Location"] == "/admin/"
    assert CriticalEventAcknowledgement.objects.count() == 3
    assert audits() == 1
    event = AuditEvent.objects.get(event_type="critical_events_acknowledged")
    assert event.auditcontext.context == {"outcome": "succeeded", "count": 3}
    # Hidden for this Administrator and for another one.
    assert BANNER not in home(browser)
    other = signed_in_as(google, "second@example.org", "second-subject")
    assert BANNER not in home(other)
    # Replaying the acknowledged list records nothing new.
    assert acknowledge(other, shown(page)).status_code == 302
    assert CriticalEventAcknowledgement.objects.count() == 3 and audits() == 1
    # A newer CRITICAL event brings the banner back, naming only itself.
    critical(Event.TASK_FAILED)
    page = home(other)
    assert BANNER in page and "Background task failed" in page
    assert "ParishSoft data refresh failed" not in page
    assert acknowledge(other).status_code == 302
    assert BANNER not in home(other)
    # A row a long transaction inserted before the acknowledgement but
    # committed after it (an earlier created_at) still reaches the banner.
    earliest = OperationalLog.objects.filter(level="CRITICAL").earliest("created_at")
    with transaction.atomic():
        OperationalLog.objects.create(
            level="CRITICAL",
            event=Event.FACT_DRIFT.value,
            schema="exception",
            context={},
            created_at=earliest.created_at,
        )
    page = home(other)
    assert BANNER in page and "Report figures did not verify" in page


def test_banner_log_link_lists_the_critical_entries(auth_service, google):
    """The banner's System logs form is accepted and shows the CRITICAL rows.

    Its From day is a day in the browser's zone (#558), which the page script
    fills into the form's zone field; the day starts early enough that the
    entry is listed even far west or east of UTC.
    """
    browser, login = signed_in()
    assert login.status_code == 302
    critical(Event.SOURCE_INVALID)
    page = home(browser)
    start = page.split('name="start" value="', 1)[1].split('"', 1)[0]
    # Critical operational entries only: no audit tick and no retired Source
    # (#601).
    form = page[page.index('action="/admin/system/logs/"') :]
    form = form[: form.index("</form>")]
    assert 'name="critical" value="yes"' in form
    assert 'name="audit"' not in form and 'name="source"' not in form
    assert '<input type="hidden" name="zone" value="" data-browser-zone>' in page
    for zone in ("Pacific/Honolulu", "Pacific/Kiritimati"):
        with web():
            response = browser.post(
                "/admin/system/logs/",
                {
                    "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
                    "applied": "yes",
                    "critical": "yes",
                    "start": start,
                    "zone": zone,
                },
            )
        assert response.status_code == 200
        body = response.content.decode()
        assert Event.SOURCE_INVALID.value in body
        # The banner's view is itself audited, but audit records are not listed.
        assert "level-icon-audit" not in body.split("<tbody>", 1)[1]


def test_acknowledgement_needs_an_administrator_and_post(
    auth_service, google, monkeypatch
):
    """Staff are refused, GET is not served, and restore review refuses."""
    from parishkit.stewardship.accounts import critical_event_views as views

    add_rules(auth_service.store, address("staff@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    critical(Event.SOURCE_INVALID)
    token = shown(home(browser))
    with web():
        assert browser.get(ROUTE).status_code == 405
        assert browser.post(ROUTE).status_code == 403
    with monkeypatch.context() as patched:
        patched.setattr(
            views,
            "coherent_configuration",
            lambda store: SimpleNamespace(restore_review_required=True),
        )
        assert acknowledge(browser, token).status_code == 503
    staff = signed_in_as(google, "staff@example.org", "staff-subject")
    assert BANNER not in home(staff)
    assert acknowledge(staff, token).status_code == 403
    assert not CriticalEventAcknowledgement.objects.exists()
    assert audits() == 0
    assert BANNER in home(browser)


def test_a_critical_event_recorded_after_the_page_survives_acknowledge(
    auth_service, google
):
    """Acknowledge records only the rows the page showed, never a newer one."""
    browser, login = signed_in()
    assert login.status_code == 302
    critical(Event.SOURCE_INVALID)
    page = home(browser)
    assert "ParishSoft data refresh failed" in page
    # A new CRITICAL arrives while the Administrator reads the page.
    critical(Event.MAIL_PROVIDER_FAILED)
    response = acknowledge(browser, shown(page))
    assert response.status_code == 302
    acknowledged = CriticalEventAcknowledgement.objects.get()
    shown_row = OperationalLog.objects.get(event=Event.SOURCE_INVALID.value)
    assert acknowledged.log_id == shown_row.pk
    event = AuditEvent.objects.get(event_type="critical_events_acknowledged")
    assert event.auditcontext.context == {"outcome": "succeeded", "count": 1}
    page = home(browser)
    assert BANNER in page and "Email sending failed" in page
    assert "ParishSoft data refresh failed" not in page


@pytest.mark.parametrize(
    "tamper",
    ["edited", "unsigned", "other_salt", "missing"],
)
def test_a_tampered_id_list_is_refused(auth_service, google, tamper):
    """An altered, forged or missing list acknowledges nothing and audits nothing."""
    from django.core import signing

    from parishkit.stewardship.audit.critical_events import SALT

    browser, login = signed_in()
    assert login.status_code == 302
    critical(Event.SOURCE_INVALID)
    token = shown(home(browser))
    other = OperationalLog.objects.create(
        level="CRITICAL", event=Event.TASK_FAILED.value, schema="exception", context={}
    )
    forged = {
        # Swap one character of the signed payload, keeping its signature.
        "edited": ("A" if token[0] != "A" else "B") + token[1:],
        "unsigned": other.pk.hex,
        "other_salt": signing.dumps([other.pk.hex], salt=SALT + ".other"),
        "missing": "",
    }[tamper]
    assert acknowledge(browser, forged).status_code == 400
    assert not CriticalEventAcknowledgement.objects.exists()
    assert audits() == 0
    assert BANNER in home(browser)


def test_a_late_commit_with_an_earlier_time_survives_acknowledge(auth_service, google):
    """A row not on the page stays, even when its time is older than a shown row."""
    browser, login = signed_in()
    assert login.status_code == 302
    critical(Event.SOURCE_INVALID)
    page = home(browser)
    shown_row = OperationalLog.objects.get()
    # A long transaction inserted this before the page was rendered but
    # committed after it, so its created_at is earlier than the shown row's.
    with transaction.atomic():
        late = OperationalLog.objects.create(
            level="CRITICAL",
            event=Event.FACT_DRIFT.value,
            schema="exception",
            context={},
            created_at=shown_row.created_at - timedelta(minutes=1),
        )
    assert acknowledge(browser, shown(page)).status_code == 302
    assert list(
        CriticalEventAcknowledgement.objects.values_list("log_id", flat=True)
    ) == [shown_row.pk]
    page = home(browser)
    assert BANNER in page and "Report figures did not verify" in page
    assert not CriticalEventAcknowledgement.objects.filter(log_id=late.pk).exists()


def test_more_than_the_limit_acknowledges_the_oldest_and_keeps_the_rest(
    auth_service, google
):
    """One form signs the oldest 500; the banner says so and keeps the rest."""
    from parishkit.stewardship.audit.critical_events import ACKNOWLEDGE_LIMIT

    browser, login = signed_in()
    assert login.status_code == 302
    start = timezone.now() - timedelta(hours=12)
    OperationalLog.objects.bulk_create(
        OperationalLog(
            level="CRITICAL",
            event=Event.TASK_FAILED.value,
            schema="exception",
            context={},
            created_at=start + timedelta(seconds=index),
        )
        for index in range(ACKNOWLEDGE_LIMIT + 1)
    )
    page = home(browser)
    assert f"Background task failed ({ACKNOWLEDGE_LIMIT + 1}×)" in page
    assert f"Only the oldest {ACKNOWLEDGE_LIMIT} are acknowledged at a time" in page
    assert acknowledge(browser, shown(page)).status_code == 302
    newest = OperationalLog.objects.latest("created_at")
    acknowledged = set(
        CriticalEventAcknowledgement.objects.values_list("log_id", flat=True)
    )
    assert len(acknowledged) == ACKNOWLEDGE_LIMIT and newest.pk not in acknowledged
    page = home(browser)
    assert BANNER in page and "Background task failed" in page
    assert "Background task failed (" not in page  # just the one remaining
    assert "Only the oldest" not in page
    assert acknowledge(browser, shown(page)).status_code == 302
    assert BANNER not in home(browser)


def test_an_acknowledgement_names_an_existing_critical_entry():
    """SQL refuses an acknowledgement of a missing, non-CRITICAL or
    unattributed entry, and one from any login but web (#389 L8)."""
    from uuid import uuid4

    from django.db import DatabaseError, connection

    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    critical(Event.SOURCE_INVALID)
    target = OperationalLog.objects.get(level="CRITICAL")
    with transaction.atomic():
        warning = operational(
            Event.SOURCE_INVALID, level="WARNING", **log_sample(Event.SOURCE_INVALID)
        )

    def insert(log_id, actor_id):
        """One plain insert, as a buggy web path could make."""
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO stewardship_critical_event_ack "
                "(id, correlation_id, log_id, actor_id) VALUES (%s, %s, %s, %s)",
                [uuid4(), uuid4(), log_id, actor_id],
            )

    with task_login(ServiceRole.WEB):
        for log_id, actor_id in (
            (uuid4(), uuid4()),
            (warning.pk, uuid4()),
            (target.pk, None),
        ):
            with pytest.raises(DatabaseError, match="existing CRITICAL entry"):
                insert(log_id, actor_id)
        insert(target.pk, uuid4())
    assert CriticalEventAcknowledgement.objects.filter(log_id=target.pk).exists()
    critical(Event.TASK_FAILED)
    other = OperationalLog.objects.get(level="CRITICAL", event="task_failed")
    with task_login(ServiceRole.WORKER), connection.cursor() as cursor:
        cursor.execute("RESET SESSION AUTHORIZATION")
        cursor.execute(
            "GRANT INSERT ON stewardship_critical_event_ack TO pk_stewardship_worker"
        )
        cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_worker")
        with pytest.raises(DatabaseError, match="Only the web login"):
            insert(other.pk, uuid4())
