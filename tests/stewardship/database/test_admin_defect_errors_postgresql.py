"""Admin views answer a KeyError or IndexError defect with a 500 (#2044).

Each view below catches ``LookupError`` as its "not found" and hands it to
``admin_editing.error_response``. ``KeyError`` and ``IndexError`` are its
subclasses, so a broad catch once answered a programming error (#2029) as
an unknown page. Every intentional not-found raises a plain ``LookupError``
or a dedicated subclass, which keeps its 404.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import (
    artwork_views,
    automation_views,
    branding_views,
    hosted_file_views,
    integration_views,
    ministry_views,
    parish_views,
    schedule_views,
    user_views,
)
from parishkit.stewardship.reports import (
    past_campaign_views,
    report_email_editor_views,
    report_email_views,
)

from .auth_builders import signed_in
from .campaign_builders import add_draft
from .test_branding_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

# Django's closed 500 answer: never the exception or its text.
SERVER_ERROR = b"The request could not be completed.\n"

# One representative page per module group: the module whose ``runtime``
# the view calls first inside its error-mapping ``try``, and the address.
VIEWS = [
    (user_views, "/admin/users/"),
    (automation_views, "/admin/users/automation/"),
    (integration_views, "/admin/system/integrations/parishsoft/"),
    (ministry_views, f"/admin/changes/{uuid4()}/"),
    (parish_views, "/admin/parish/settings/"),
    (hosted_file_views, "/admin/parish/files/"),
    (branding_views, f"/admin/parish/logos/assets/{uuid4()}.png"),
    (schedule_views, "/admin/campaign/schedules/"),
    (past_campaign_views, "/admin/parish/past-campaigns/"),
    (report_email_views, "/admin/mail/report-emails/"),
    (report_email_editor_views, "/admin/mail/report-emails/new/"),
]


def _session(store):
    """A signed-in Administrator over a real applied draft campaign.

    The client answers a server error as Django's closed 500 page instead
    of raising the exception into the test.
    """
    result, _, _ = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    browser, _ = signed_in()
    browser.raise_request_exception = False
    return browser


@pytest.mark.parametrize("error", [KeyError, IndexError])
@pytest.mark.parametrize(
    ("module", "path"), VIEWS, ids=[module.__name__ for module, _ in VIEWS]
)
def test_a_defect_is_a_server_error_not_an_unknown_page(
    auth_service, google, monkeypatch, module, path, error
):
    """A KeyError or IndexError inside the view's ``try`` is a 500."""
    browser = _session(auth_service.store)

    def failed():
        """Simulate a defect inside the view, not a missing page."""
        raise error("PRIVATE-DEFECT")

    monkeypatch.setattr(module, "runtime", failed)
    response = browser.get(path)
    assert response.status_code == 500
    assert response.content == SERVER_ERROR


@pytest.mark.parametrize("error", [KeyError, IndexError])
def test_an_artwork_review_defect_is_not_an_unreviewable_upload(
    auth_service, google, monkeypatch, error
):
    """Campaign images' own refusal of a review keeps defects out (#2044).

    The removal review maps a caught ``LookupError`` to its 409 refusal; a
    slot with no image still gets that refusal, a defect is a 500.
    """
    store = auth_service.store
    browser = _session(store)
    digest = store.active().digest
    fields = {"action": "preview", "slot": "banner", "base_digest": digest}
    path = "/admin/campaign/images/"
    nothing = post(browser, path, fields)
    assert nothing.status_code == 409
    assert nothing.json()["refusal"]["message"]

    def failed(*args):
        """Simulate a defect inside the review, not a stale upload."""
        raise error("PRIVATE-DEFECT")

    monkeypatch.setattr(artwork_views, "_review", failed)
    response = post(browser, path, fields)
    assert response.status_code == 500
    assert response.content == SERVER_ERROR


def test_unknown_addresses_are_still_unavailable(auth_service, google):
    """Real unknown ids and names keep their 404 and refusal codes."""
    browser = _session(auth_service.store)
    for path in (
        "/admin/system/integrations/unknown/",
        f"/admin/changes/{uuid4()}/",
        f"/admin/parish/logos/assets/{uuid4()}.png",
    ):
        response = browser.get(path)
        assert response.status_code == 404, path
        codes = [item["code"] for item in response.json()["errors"]]
        assert codes == ["unavailable"], path
    # An unknown report email is Django's own 404 (Http404), unchanged.
    assert browser.get(f"/admin/mail/report-emails/{uuid4()}/").status_code == 404
    # A deliberate UserFacingMissing keeps its explanation as well.
    missing = browser.get(f"/admin/campaign/schedules/{uuid4()}/")
    assert missing.status_code == 404
    assert missing.json()["refusal"]["message"] == (
        "That scheduled email no longer exists."
    )
