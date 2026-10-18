"""Pages and emails' item states and Keep as is and go back (#1404 PR 6b).

Real Google sessions, the restricted web role, the SQL guard and the
installer: inside the campaign wizard, Pages and emails' editor offers Keep
as is and go back while its form holds no change, for an item the list
shows. It posts the step and the item to the step Continue, which stores one
``CampaignSetupReview`` row for that item with its audit event and returns
to Pages and emails, where the item reads Kept as is in place of Default
text. An item the list no longer shows is refused with nothing stored, an
item mark never completes the step, and outside the wizard nothing of this
is drawn.
"""

import re
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import campaign_wizard
from parishkit.stewardship.accounts.campaign_wizard_models import CampaignSetupReview
from parishkit.stewardship.accounts.content_defaults import default_initial
from parishkit.stewardship.audit.models import AuditEvent

from .auth_builders import signed_in
from .test_campaign_setup_review_postgresql import CONTINUE, web
from .test_campaign_subpage_direct_save_postgresql import drawn_key
from .test_campaign_views_postgresql import apply, post
from .test_content_views_postgresql import setup as content_setup

pytestmark = pytest.mark.django_db(transaction=True)

CATALOG = "/admin/campaign/content/"
EDITOR = CATALOG + "page/welcome/"


def keep_form(body):
    """The editor's Keep as is and go back form, or None when not drawn."""
    match = re.search(r"<form [^>]*data-keep-item[^>]*>.*?</form>", body, re.S)
    return match and match.group(0)


def drawn_item(body):
    """The item key the editor's Keep as is and go back posts."""
    return re.search(r'name="item" value="([^"]+)"', keep_form(body)).group(1)


def save_default(browser, store, kind, slot):
    """Save the slot's default text through its editor's Save and go back.

    The test campaign starts with every slot empty; this gives a slot the
    saved default text a created campaign has, which the list shows as
    Default text.
    """
    path = f"{CATALOG}{kind}/{slot}/"
    default = default_initial(kind, slot)
    with web():
        key = drawn_key(browser.get(path).content.decode())
        response = post(
            browser,
            path,
            {
                "base_digest": store.active().digest,
                "html": default["html"],
                "text": default["text"],
                "action": "save",
                "request_key": str(key),
            }
            | ({"generate_text": "on"} if default["generate_text"] else {})
            | ({"subject": default["subject"]} if kind == "email" else {}),
        )
    apply(store, response)


def keep(browser, item, step="content"):
    """Press Keep as is and go back, as its form posts it."""
    with web():
        return post(browser, CONTINUE, {"step": step, "item": item})


def test_keep_as_is_marks_the_item_and_returns_to_the_list(auth_service, google):
    """The mark is stored once, read as Kept as is, and never completes the step."""
    store = auth_service.store
    campaign, _, _ = content_setup(store)
    browser, _ = signed_in()
    save_default(browser, store, "page", "welcome")
    with web():
        listing = browser.get(CATALOG).content.decode()
        body = browser.get(EDITOR).content.decode()
    assert 'id="content-page-welcome" tabindex="-1"><a href="' in listing
    assert "Family welcome</a> — Default text" in listing
    form = keep_form(body)
    assert f'action="{CONTINUE}"' in form
    assert 'data-kept-text="Family welcome kept as is."' in form
    assert 'data-kept-focus="content-page-welcome"' in form
    assert f'data-kept-return="{CATALOG}"' in form
    assert ">Keep as is and go back</button>" in form
    # Save and go back shares its place, shown once the form holds a change.
    assert re.search(
        r'<button type="submit" form="content-form" [^>]*data-campaign-save'
        r"[^>]*data-step-inactive[^>]*>Save and go back</button>",
        body,
    )
    assert 'data-direct-focus="content-page-welcome"' in body
    item = drawn_item(body)
    assert re.fullmatch(r"page:welcome_[0-9a-f]{32}", item)
    response = keep(browser, item)
    assert response.status_code == 303
    assert response["Location"] == CATALOG
    review = CampaignSetupReview.objects.get()
    assert (review.step, review.item, review.campaign_id) == (
        "content",
        item,
        campaign.pk,
    )
    assert AuditEvent.objects.filter(
        event_type="campaign_setup_step_reviewed", subject_id=review.pk
    ).exists()
    # A repeat stores nothing again.
    assert keep(browser, item).status_code == 303
    assert CampaignSetupReview.objects.count() == 1
    with web():
        listing = browser.get(CATALOG).content.decode()
    assert "Family welcome</a> — Kept as is" in listing
    # Other pages are untouched, and no step review was stored: the item
    # mark is not the step's Continue (the saved default above completed
    # the step already, as any applied content change does).
    assert "Family login help</a> — Empty" in listing
    assert not CampaignSetupReview.objects.filter(item="").exists()


def test_keep_as_is_refuses_an_item_the_list_does_not_show(auth_service, google):
    """A stale or unknown item, or an item on another step, stores nothing.

    An empty page is listed (as Empty), so it can be kept, and that mark
    alone never completes the step.
    """
    store = auth_service.store
    campaign, _, _ = content_setup(store)
    browser, _ = signed_in()
    stale = keep(browser, f"page:welcome_{uuid4().hex}")
    assert stale.status_code == 409
    assert stale.stewardship_reason == "stale_page"
    assert "That page or email has changed since this page was opened" in (
        stale.content.decode()
    )
    assert keep(browser, "page:not_a_slot").status_code == 409
    assert keep(browser, "page:welcome", step="settings").status_code == 400
    assert not CampaignSetupReview.objects.exists()
    assert keep(browser, "page:login_help").status_code == 303
    assert CampaignSetupReview.objects.get().item == "page:login_help"
    assert "content" not in campaign_wizard.completed(campaign, None)
    with web():
        listing = browser.get(CATALOG).content.decode()
    assert "Family login help</a> — Empty" in listing


def test_keep_as_is_is_offered_only_for_a_listed_unchanged_item(auth_service, google):
    """An email revision has its own key; a new email or a started default none."""
    store = auth_service.store
    content_setup(store)
    browser, _ = signed_in()
    save_default(browser, store, "email", "initial")
    with web():
        listing = browser.get(CATALOG).content.decode()
        revision = re.search(
            r'href="/admin/campaign/content/email/initial/([0-9a-f-]{36})/"', listing
        ).group(1)
        email = browser.get(f"{CATALOG}email/initial/{revision}/").content.decode()
        new = browser.get(f"{CATALOG}email/initial/").content.decode()
        started = browser.get(EDITOR + "?start=default").content.decode()
    assert f'id="content-email-{revision}" tabindex="-1"' in listing
    assert drawn_item(email) == f"email:initial_{revision.replace('-', '')}"
    assert f'data-kept-focus="content-email-{revision}"' in keep_form(email)
    # A saved email is a new revision: Save and go back focuses its heading.
    assert 'data-direct-focus="email-initial-title"' in email
    for body in (new, started):
        assert keep_form(body) is None
        assert ">Save and go back</button>" in body
    assert keep(browser, drawn_item(email)).status_code == 303
    with web():
        listing = browser.get(CATALOG).content.decode()
    row = re.search(rf'<tr id="content-email-{revision}".*?</tr>', listing, re.S).group(
        0
    )
    assert "<td>Kept as is</td>" in row


def test_outside_the_wizard_the_list_and_editor_are_unchanged(
    auth_service, google, monkeypatch
):
    """With the wizard ended, a stored mark is not shown and Keep is not drawn."""
    store = auth_service.store
    content_setup(store)
    browser, _ = signed_in()
    save_default(browser, store, "page", "welcome")
    with web():
        item = drawn_item(browser.get(EDITOR).content.decode())
    assert keep(browser, item).status_code == 303
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)
    with web():
        listing = browser.get(CATALOG).content.decode()
        editor = browser.get(EDITOR).content.decode()
    assert "Family welcome</a> — Default text" in listing
    assert "Kept as is" not in listing
    assert keep_form(editor) is None and "data-campaign-save" not in editor
