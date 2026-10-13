"""Send a test of a scheduled email's unsaved edit, end to end (#446, 3c).

New and Edit scheduled email post the email fields to
``schedule_email_test``. The text is checked as a save checks it and sent as
the test Preview and test email sends (real web identity, real SQL
admission and guard, the real mail handler with only provider I/O
replaced), and nothing is saved: no change request, content record or
schedule change, and no Family code or link.
"""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import ScheduleOccurrence
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .test_campaign_mail_postgresql import (  # noqa: F401
    KEY,
    campaign_test,
    deliver,
)
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

EDITED = {
    "subject": "Edited reminder subject",
    "html": "<p>Edited for {{ family_name }}: {{ family_code }} {{ family_url }}</p>",
    "generate_text": "on",
}


def chosen(campaign_test):
    """The fixture's saved Initial invitation: the email the edit starts from."""
    _service, _browser, path, _credential = campaign_test
    return path.rstrip("/").rsplit("/", 1)[-1]


def send(browser, values):
    """Post the page's test of unsaved text, as its script does."""
    return post(browser, reverse("admin:schedule_email_test"), values)


def saved_state():
    """What a test must never change: the configuration and its records."""
    return (
        SystemConfiguration.objects.get().active_configuration_id,
        ContentVersion.objects.count(),
        ConfigurationChangeRequest.objects.count(),
        ScheduleOccurrence.objects.count(),
    )


def test_unsaved_text_is_sent_as_the_saved_emails_test_and_nothing_is_saved(
    campaign_test, monkeypatch
):
    """The edited text goes out as Preview and test email's fictional test.

    The row names the chosen email, the Testing recipient is the only
    address, the queued test is audited, a second send waits for the first,
    and the configuration is exactly as it was.
    """
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    _service, browser, _path, _credential = campaign_test
    before = saved_state()
    with web_login():
        answer = send(browser, EDITED | {"template_version": chosen(campaign_test)})
        assert answer.status_code == 200, answer.content
        assert answer.json() == {"queued": True}
        assert answer["Cache-Control"] == "no-store"
        # One test at a time, as on Preview and test email.
        again = send(browser, EDITED | {"template_version": chosen(campaign_test)})
        assert again.status_code == 409
        assert again.json()["refusal"]["message"] == (
            "A test email is already on its way."
        )
    row = CampaignMailTest.objects.get()
    assert str(row.template.record_id) == chosen(campaign_test)
    assert row.mail["subject"] == "Edited reminder subject"
    assert row.mail["recipient"] == "test@example.org"
    for body in (row.mail["html"], row.mail["text"]):
        # The fictional sample's Family values, never a placeholder or a
        # real Family's code or link.
        assert "Edited for Sample" in body and "sample-household-link" in body
        assert "{{" not in body
    assert AuditEvent.objects.filter(
        subject_id=row.pk, event_type="campaign_mail_queued"
    ).exists()
    assert saved_state() == before
    seen = []

    def submit(value, settings, mail, *, seconds, check):
        """Replace provider I/O only; the message is the one built for sending."""
        check()
        assert value == KEY and mail.recipient == "test@example.org"
        seen.append(mail.message()["Subject"])
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(tasks, "submit_sample", submit)
    assert deliver(campaign_test).state == "accepted"
    assert seen == ["[TEST] Edited reminder subject"]
    assert saved_state() == before


def test_text_that_would_not_save_is_refused_and_nothing_is_sent(campaign_test):
    """The save's rules for the chosen email's type: an invitation's code."""
    _service, browser, _path, _credential = campaign_test
    before = saved_state()
    with web_login():
        missing = EDITED | {
            "template_version": chosen(campaign_test),
            "html": "<p>Please respond.</p>",
        }
        answer = send(browser, missing)
        assert answer.status_code == 400, answer.content
        messages = " ".join(item["message"] for item in answer.json()["errors"])
        assert "{{ family_code }}" in messages
        blank = send(browser, missing | {"subject": "", "html": EDITED["html"]})
        assert {"field": "Email subject", "message": "This field is required."} in (
            blank.json()["errors"]
        )
        # No email chosen, an email no longer saved, or a field the page
        # never posts: each refused, nothing sent.
        assert send(browser, EDITED).json()["refusal"]["message"] == (
            "Choose an email to send first."
        )
        gone = send(
            browser,
            EDITED | {"template_version": "f" * 8 + "-0000-4000-8000-000000000009"},
        )
        assert gone.status_code == 409
        extra = send(
            browser,
            EDITED | {"template_version": chosen(campaign_test), "edit_email": "on"},
        )
        assert extra.status_code == 400 and "refusal" in extra.json()
    assert not CampaignMailTest.objects.exists()
    assert saved_state() == before


def test_an_uncertain_earlier_test_needs_the_acknowledgment(campaign_test, monkeypatch):
    """The page draws the box once a result is uncertain; the send needs it."""
    from parishkit.stewardship.accounts import campaign_mail_tasks as tasks

    _service, browser, _path, _credential = campaign_test
    values = EDITED | {"template_version": chosen(campaign_test)}
    with web_login():
        new_page = browser.get(reverse("admin:schedule_new"))
        assert b"data-schedule-email-test-acknowledge hidden>" in new_page.content
        assert send(browser, values).status_code == 200
    monkeypatch.setattr(
        tasks, "submit_sample", lambda *args, **kwargs: DeliveryOutcome.UNKNOWN
    )
    assert deliver(campaign_test).state == "delivery_unknown"
    with web_login():
        refused = send(browser, values)
        assert refused.status_code == 409
        assert refused.json()["unknown"] is True
        assert "tick the confirmation below" in refused.json()["refusal"]["fix"]
        assert CampaignMailTest.objects.count() == 1
        new_page = browser.get(reverse("admin:schedule_new"))
        assert b"data-schedule-email-test-acknowledge>" in new_page.content
        answer = send(browser, values | {"acknowledge_unknown": "on"})
        assert answer.status_code == 200, answer.content
    assert CampaignMailTest.objects.count() == 2
