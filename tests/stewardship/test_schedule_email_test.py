"""Send a test of a scheduled email's unsaved edit (#446, slice 3c).

Without a database: which saved email and mail type a test names, how a
refused text is reported, and what the page draws for the button. The send
itself (admission, limits, nothing saved) is in
database/test_schedule_email_test_postgresql.py, and the page script in
browser/test_schedule_list.py.
"""

import json

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import schedule_email
from parishkit.stewardship.accounts.schedule_email_test import (
    NO_EMAIL,
    POSTED,
    _chosen,
    _errors,
)
from parishkit.stewardship.accounts.schedule_entry_views import _fields
from parishkit.stewardship.web.refusals import UserFacingError, UserFacingStale

from .campaign_factory import campaign
from .content_factory import content

CAMPAIGN = campaign()["id"]


def records():
    """A reminder, an invitation, a daily digest and the confirmation email."""
    return [
        content(CAMPAIGN, kind="email", slot="reminder", subject="Reminder"),
        content(CAMPAIGN, kind="email", slot="initial", subject="Invitation"),
        content(CAMPAIGN, kind="email", slot="daily_digest", subject="Digest"),
        content(CAMPAIGN, kind="email", slot="confirmation", subject="Thanks"),
    ]


def test_the_chosen_email_names_its_own_mail_type():
    """The text is checked against the chosen email's type, the page's."""
    saved = records()
    kinds = ("reminder", "initial")
    for row, kind in zip(saved[:2], kinds, strict=True):
        assert _chosen(saved, row["id"]) == (row["id"], kind)
    # An upper-case ID is the same email.
    assert _chosen(saved, saved[0]["id"].upper()) == (saved[0]["id"], "reminder")


@pytest.mark.parametrize("value", [None, "", "not-an-id"])
def test_no_chosen_email_is_refused_with_the_page_reason(value):
    """Without an email the test has nothing to name; the reason is the page's."""
    with pytest.raises(UserFacingError) as refused:
        _chosen(records(), value)
    assert refused.value.refusal.message == NO_EMAIL


def test_an_email_no_schedule_can_send_or_no_longer_saved_is_stale():
    """Only a saved email of a schedulable type can be the base of a test.

    A retired Daily digest email is not one: report emails replaced the
    digests (#1786), and no schedule sends it.
    """
    saved = records()
    for identifier in (
        saved[2]["id"],
        saved[3]["id"],
        "f" * 8 + "-0000-4000-8000-000000000009",
    ):
        with pytest.raises(UserFacingStale):
            _chosen(saved, identifier)


def test_a_refused_text_names_each_field_by_its_label():
    """Every field problem, a missing Family code included, carries its label."""
    data = QueryDict(mutable=True)
    data.update({"subject": "", "html": "<p>Please respond.</p>"})
    form = schedule_email.ScheduleEmailForm(data, kind="email", slot="reminder")
    assert not form.is_valid()
    assert {"field": "Email subject", "message": "This field is required."} in (
        _errors(form)
    )
    data["subject"] = "Reminder"
    form = schedule_email.ScheduleEmailForm(data, kind="email", slot="reminder")
    assert not form.is_valid()
    errors = _errors(form)
    # A missing code or link is shown at the HTML field it is fixed in
    # (#563), so it is labelled with that field.
    assert errors and all(item["field"] for item in errors)
    assert "{{ family_code }}" in " ".join(item["message"] for item in errors)


def test_the_test_posts_only_its_own_fields_and_a_review_never_its_acknowledgment():
    """The send's closed field list, and the Review's without the acknowledgment."""
    fields = {"template_version", "acknowledge_unknown", "subject", "html"}
    assert fields | {"generate_text", "text"} == POSTED
    assert "acknowledge_unknown" not in _fields(True) | _fields(False)


def page(**test):
    """New's email section with the test context ``test``."""
    toggle, form, sources = schedule_email.editor(None, records(), None, CAMPAIGN)
    return render_to_string(
        "stewardship/schedule-email.html",
        {
            "email_toggle": toggle,
            "email": form,
            "email_sources": sources,
            "new": True,
            "email_test": {
                "url": "/admin/campaign/schedules/test-email/",
                "no_email": NO_EMAIL,
            }
            | test,
        },
    )


def test_the_button_has_a_reserved_status_line_and_help_in_its_tip():
    """The line keeps its space; the long help is behind the "i"."""
    drawn = page(closed=None, unknown=False)
    assert "Send a test of this text" in drawn
    assert 'data-url="/admin/campaign/schedules/test-email/"' in drawn
    # The script's reason is the server's own refusal text.
    assert f'data-no-email="{NO_EMAIL}"' in drawn
    assert 'aria-describedby="schedule-email-test-status"' in drawn
    assert 'class="help reserve-line schedule-email-test-status"' in drawn
    assert 'id="schedule-email-test-tip" hidden>Sends the text above' in drawn
    assert "data-closed" not in drawn
    # The acknowledgment is drawn, hidden, with no name, so no form posts it.
    start = drawn.index("data-schedule-email-test-acknowledge")
    assert drawn[start:].startswith("data-schedule-email-test-acknowledge hidden>")
    assert 'id="schedule-email-test-acknowledge" data-change-ignore>' in drawn
    # The test sits inside the fields' panel, so unticked it is not shown.
    assert drawn.index('data-show-when="edit_email=on"') < start
    assert drawn.index("data-schedule-email-test ") < drawn.index("</fieldset>")


def test_a_closed_campaign_and_an_uncertain_test_are_drawn_for_the_script():
    """data-closed carries the reason; an uncertain test shows the box."""
    drawn = page(closed="Test emails can be sent only now.", unknown=True)
    assert 'data-closed="Test emails can be sent only now."' in drawn
    assert "data-schedule-email-test-acknowledge>" in drawn


def test_configuration_gone_while_sending_is_unavailable_not_an_error(monkeypatch):
    """A saved email or mail setting gone under the send is a 404, as on
    Preview and test email, never a server error."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from django.test import RequestFactory

    from parishkit.stewardship.accounts import schedule_email_test as view
    from parishkit.stewardship.accounts.content_models import ContentVersion

    saved = records()
    campaign = SimpleNamespace(pk=CAMPAIGN)

    def gone(*args, **kwargs):
        """The applied configuration no longer holds the chosen email."""
        raise ContentVersion.DoesNotExist

    for name, value in {
        "runtime": lambda: None,
        "principal": lambda *args, **kwargs: None,
        "read_transaction": nullcontext,
        "schedule_state": lambda service, campaign_id, page=False: (
            (None,),
            campaign,
            False,
        ),
        "_records": lambda state, campaign_id: saved,
        "request_edited": gone,
    }.items():
        monkeypatch.setattr(view, name, value)
    request = RequestFactory().post(
        "/admin/campaign/schedules/test-email/",
        {
            "template_version": saved[0]["id"],
            "subject": "Reminder",
            "html": "<p>Respond. {{ family_code }} {{ family_url }}</p>",
            "generate_text": "on",
        },
    )
    response = view.schedule_email_test(request, CAMPAIGN)
    assert response.status_code == 404
    assert json.loads(response.content)["errors"][0]["code"] == "unavailable"
