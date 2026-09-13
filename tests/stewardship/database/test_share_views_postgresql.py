"""Financial share-option changes pass through the real installer and web grants."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.share_forms import default_share_options
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..campaign_factory import campaign, financial
from ..test_share_forms import data_for
from .auth_builders import signed_in
from .campaign_builders import add_draft, command
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import apply, fields, post
from .test_parish_views_postgresql import token

pytestmark = pytest.mark.django_db(transaction=True)


def setup(store):
    """Use actual immutable applied configuration with saved financial options."""
    previous = default_share_options()
    result, _, _ = add_draft(
        store,
        store.active(),
        uuid4(),
        campaign(modules=["financial"], financial=financial(), share_options=previous),
    )
    assert result.state == "applied"
    row = Campaign.objects.get()
    return row, previous, f"/admin/campaign/{row.pk}/share-options"


def test_share_option_apply_preserves_ids_and_produces_idempotent_receipt(
    auth_service, google
):
    """Changing one label and order never allocates a replacement campaign option."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    assert browser.get(path).status_code == 200
    data = data_for(previous) | {
        "base_digest": store.active().digest,
        "options-0-label": "Updated option",
        "options-0-ORDER": "2",
        "options-1-ORDER": "1",
    }
    proposal = token(post(browser, path, data))
    response = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, response)
    row.refresh_from_db()
    options = row.active_configuration.values["share_options"]
    assert [item["id"] for item in options[:2]] == [
        previous[1]["id"],
        previous[0]["id"],
    ]
    assert options[1]["label"] == "Updated option"
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal})["Location"]
        == response["Location"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"options-0-label": "{{ family_code }}"},
        {"options-0-id": str(uuid4())},
        {"options-INITIAL_FORMS": "0"},
        {"options-99-label": "Ignored?"},
        {"mode": "production"},
        {"options-0-label": ["First", "Second"]},
    ],
)
def test_share_editor_rejects_invalid_and_hidden_values(auth_service, google, changes):
    """The form parser and server-held ID inventory reject unoffered intent."""
    store = auth_service.store
    _, previous, path = setup(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    response = post(
        browser,
        path,
        data_for(previous) | {"base_digest": store.active().digest} | changes,
    )
    assert response.status_code == 400
    assert ConfigurationChangeRequest.objects.count() == before


def test_share_preview_and_direct_mutation_are_rejected_after_live_lock(
    auth_service, google
):
    """A valid signature is not authority to modify an activated campaign."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    data = data_for(previous) | {
        "base_digest": store.active().digest,
        "options-0-label": "Changed",
    }
    proposal = token(post(browser, path, data))
    command(row, uuid4(), Action.ACTIVATE)
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal}).status_code
        == 409
    )
    assert post(browser, path, data).status_code == 409


def test_share_editor_supports_restricted_web_and_explicit_empty_options(
    auth_service, google
):
    """Web may enqueue deletions but cannot install the resulting YAML itself."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    data = data_for(previous) | {"base_digest": store.active().digest}
    data.update({f"options-{index}-DELETE": "on" for index in range(len(previous))})
    with task_login(ServiceRole.WEB):
        assert browser.get(path).status_code == 200
        proposal = token(post(browser, path, data))
        response = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.values["share_options"] == []


def test_share_noop_stale_base_and_wrong_route_cannot_apply(auth_service, google):
    """The signed receipt is tied to this campaign's share editor, not any route."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    data = data_for(previous) | {"base_digest": store.active().digest}
    response = post(browser, path, data)
    assert (
        response.status_code == 200
        and b"No share options have changed" in response.content
    )
    assert post(browser, path, data | {"base_digest": "a" * 64}).status_code == 409
    proposal = token(post(browser, path, data | {"options-0-label": "Changed"}))
    assert (
        post(
            browser,
            f"/admin/campaign/{uuid4()}/share-options",
            {"action": "confirm", "preview": proposal},
        ).status_code
        == 400
    )
    assert browser.get(path + "?extra=1").status_code == 400
    assert post(browser, path, fields(store, row)).status_code == 400


def test_census_only_has_no_share_controls(auth_service, google):
    """Hidden Financial controls cannot create a selection in a census-only draft."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    assert browser.get(f"/admin/campaign/{row.pk}/share-options").status_code == 409
