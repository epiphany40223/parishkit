"""A campaign wizard sub-page saves directly with Save and go back (#1404 PR 4b).

Real Google sessions, the restricted web role and the installer: inside the
campaign wizard, Pages and emails' editor and New and Edit scheduled email
draw no Preview changes, review button or Cancel of their own. Their form
posts ``save`` with the page's request key, from the action row's Save and
go back, which names the step an applied save opens again. The server checks
the form as Preview changes or Review changes does and records the same
configuration request at once; a repeat of the same save returns that
change's status, a different change from the same page is refused as stale
once the first moved the version, a refusal at the form carries the error
summary the page shows, send work that blocks a schedule change refuses it,
and once the wizard no longer applies the pages draw their ordinary buttons
again and a direct save is refused with nothing recorded.
"""

import re
from html import unescape
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from django.http import QueryDict
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard, schedule_entry_views
from parishkit.stewardship.accounts.admin_editing import direct_key
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from ..test_content_forms import fields
from .auth_builders import signed_in
from .test_campaign_setup_review_postgresql import web
from .test_campaign_views_postgresql import apply, post, requested
from .test_content_views_postgresql import setup as content_setup
from .test_schedule_entry_postgresql import NEW, entry
from .test_schedule_entry_postgresql import setup as schedule_setup

pytestmark = pytest.mark.django_db(transaction=True)

CATALOG = "/admin/campaign/content/"
EDITOR = CATALOG + "page/welcome/"
LIST = "/admin/campaign/schedules/"


def drawn_key(body):
    """The request key a direct-save page drew into its form."""
    return UUID(re.search(r'name="request_key" value="([^"]+)"', body).group(1))


def assert_direct(body, form, step):
    """The sub-page as the wizard draws it: Save and go back saves its form."""
    assert "data-direct-save" in body
    assert '<input type="hidden" name="action" value="save">' in body
    tag = re.search(rf'<form [^>]*id="{form}"[^>]*>', body).group(0)
    assert "data-review-open" not in tag
    assert '<dialog id="schedule-review"' not in body
    assert re.search(
        rf'<button type="submit" form="{form}" [^>]*data-campaign-save'
        r"[^>]*>Save and go back</button>",
        body,
    )
    assert f'data-next-url="{step}" data-next-fixed' in body
    assert 'id="campaign-step-status" role="status"' in body


def test_the_editor_saves_directly_and_goes_back(auth_service, google):
    """Save and go back records the edit at once; a repeat is the same one."""
    store = auth_service.store
    content_setup(store)
    browser, _ = signed_in()
    with web():
        body = browser.get(EDITOR).content.decode()
    assert_direct(body, "content-form", CATALOG)
    assert ">Preview changes</button>" not in body
    assert 'data-direct-save data-direct-saved="Family welcome saved."' in body
    data = fields(base_digest=store.active().digest, html="<p>Saved directly</p>") | {
        "action": "save",
        "request_key": str(drawn_key(body)),
    }
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        response = post(browser, EDITOR, data)
    status_url = reverse("admin:configuration_request", args=[requested(response)])
    assert response["Location"] == status_url
    recorded = ConfigurationChangeRequest.objects.get(pk=requested(response))
    assert recorded.request_key == direct_key(QueryDict(urlencode(data, doseq=True)))
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    apply(store, response)
    saved = SystemConfiguration.objects.get().active_configuration.content_versions
    assert saved.filter(html="<p>Saved directly</p>").exists()
    # A repeat (its answer lost), now that its version is out of date, is
    # answered with the same change.
    with web():
        again = post(browser, EDITOR, data)
    assert again.status_code == 302 and again["Location"] == status_url
    # A different change from that same page is a save of its own, refused
    # as stale: the version it was drawn at is no longer the current one.
    with web():
        refused = post(browser, EDITOR, data | {"html": "<p>A later change</p>"})
    assert refused.status_code == 409
    assert refused.stewardship_reason == "stale_page"
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # Nothing changed, or a value refused: the page again (400) with the
    # error summary the page shows, nothing recorded.
    with web():
        fresh = browser.get(EDITOR).content.decode()
        unchanged = fields(
            base_digest=store.active().digest, html="<p>Saved directly</p>"
        ) | {"action": "save", "request_key": str(drawn_key(fresh))}
        same = post(browser, EDITOR, unchanged)
        wrong = post(browser, EDITOR, unchanged | {"html": "{{ unknown }}"})
    for answer in (same, wrong):
        assert answer.status_code == 400
        assert 'id="content-form"' in answer.content.decode()
        assert "data-error-summary" in answer.content.decode()
    assert "No content has changed." in same.content.decode()
    assert ConfigurationChangeRequest.objects.count() == requests + 1


def test_new_and_edit_scheduled_email_save_directly(auth_service, google):
    """New and Edit record their schedule change at once, then go back."""
    store = auth_service.store
    _, emails = schedule_setup(store)
    browser, _ = signed_in()
    with web():
        body = unescape(browser.get(NEW).content.decode())
    assert_direct(body, "schedule-entry-form", LIST)
    assert ">Add the scheduled email</button>" not in body
    assert f'<a href="{LIST}">Cancel</a>' not in body
    assert 'data-direct-saved="New scheduled email saved."' in body
    assert "Fill in the scheduled email to save it." in body
    data = entry(
        store,
        kind="reminder",
        date="2054-10-15",
        time="9am",
        template_version=emails["reminder"]["id"],
    ) | {"action": "save", "request_key": str(drawn_key(body))}
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        response = post(browser, NEW, data)
    status_url = reverse("admin:configuration_request", args=[requested(response)])
    assert response["Location"] == status_url
    apply(store, response)
    dates = sorted(
        row.current_revision.values["date"]
        for row in ScheduleDefinition.objects.filter(
            kind="reminder", current_revision__isnull=False
        )
    )
    assert dates == ["2054-10-15", "2054-10-25"]
    with web():
        again = post(browser, NEW, data)
    assert again["Location"] == status_url
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # Edit saves the same way, from its own page.
    reminder = ScheduleDefinition.objects.get(
        kind="reminder",
        current_revision__isnull=False,
        current_revision__values__date="2054-10-25",
    )
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    with web():
        page = unescape(browser.get(path).content.decode())
    assert_direct(page, "schedule-entry-form", LIST)
    assert 'data-direct-saved="Scheduled email saved."' in page
    edit = entry(
        store,
        date="2054-10-25",
        time="10:30",
        template_version=emails["reminder"]["id"],
    ) | {"action": "save", "request_key": str(drawn_key(page))}
    with web():
        saved = post(browser, path, edit)
    apply(store, saved)
    reminder.refresh_from_db()
    assert reminder.current_revision.values["time"] == "10:30:00"
    # A value refused at its field: the page again, nothing recorded.
    with web():
        refused = post(
            browser,
            NEW,
            data
            | {
                "base_digest": store.active().digest,
                "request_key": str(uuid4()),
                "schedule-date": "2099-01-01",
            },
        )
    assert refused.status_code == 400
    assert "data-error-summary" in refused.content.decode()
    assert ConfigurationChangeRequest.objects.count() == requests + 2


def test_send_work_that_blocks_a_schedule_change_refuses_it(
    auth_service, google, monkeypatch
):
    """With sends blocking the change, the review has no preview to save."""
    store = auth_service.store
    _, emails = schedule_setup(store)
    browser, _ = signed_in()
    with web():
        body = browser.get(NEW).content.decode()
    original = schedule_entry_views.build_preview

    def blocked(*args, **kwargs):
        """The real review, as if two sends blocked it."""
        context = original(*args, **kwargs)
        return context and context | {"preview": None, "blocking": 2}

    monkeypatch.setattr(schedule_entry_views, "build_preview", blocked)
    requests = ConfigurationChangeRequest.objects.count()
    data = entry(
        store,
        kind="reminder",
        date="2054-10-15",
        time="9am",
        template_version=emails["reminder"]["id"],
    ) | {"action": "save", "request_key": str(drawn_key(body))}
    with web():
        refused = post(browser, NEW, data)
    assert refused.status_code == 409
    assert refused.stewardship_reason == "send_work_blocks"
    text = refused.content.decode()
    assert "is being sent right now, or has an uncertain result" in text
    assert "so nothing was saved." in text
    # The page script's request is answered in JSON, whose link the status
    # line draws (ui-v1.js saveStep).
    with web():
        script = browser.post(
            NEW,
            data | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
            HTTP_X_REQUESTED_WITH="fetch",
        )
    assert script.status_code == 409
    assert script.json()["refusal"]["link"] == {
        "url": reverse("admin:deliveries"),
        "label": "Sent mail",
    }
    assert ConfigurationChangeRequest.objects.count() == requests


def test_outside_the_wizard_the_sub_pages_keep_their_own_buttons(
    auth_service, google, monkeypatch
):
    """Once the wizard no longer applies, nothing saves directly any more.

    A started go-live (or a campaign no longer a Testing draft) ends it;
    here ``shown`` says so, as it would then, for the page and under the
    work lock alike.
    """
    store = auth_service.store
    _, emails = schedule_setup(store)
    browser, _ = signed_in()
    with web():
        body = browser.get(EDITOR).content.decode()
    key = str(drawn_key(body))
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        editor = browser.get(EDITOR).content.decode()
        new = unescape(browser.get(NEW).content.decode())
        late = post(
            browser,
            EDITOR,
            fields(base_digest=store.active().digest, html="<p>Too late</p>")
            | {"action": "save", "request_key": key},
        )
        late_schedule = post(
            browser,
            NEW,
            entry(
                store,
                kind="reminder",
                date="2054-10-15",
                time="9am",
                template_version=emails["reminder"]["id"],
            )
            | {"action": "save", "request_key": key},
        )
    for page in (editor, new):
        assert "data-direct-save" not in page and "data-campaign-save" not in page
    assert ">Preview changes</button>" in editor
    assert 'data-review-open="schedule-review"' in new
    assert ">Add the scheduled email</button>" in new
    for answer in (late, late_schedule):
        assert answer.status_code == 409
        assert "Campaign setup has ended, so nothing was saved." in (
            answer.content.decode()
        )
    assert ConfigurationChangeRequest.objects.count() == requests
