"""Archived-source cloning through actual sessions, YAML intents and installer."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign, ScheduleDefinition
from parishkit.stewardship.campaigns.runtime import return_to_testing

from ..content_factory import content
from ..policy_factory import address
from ..test_campaign_forms import posted
from ..test_schedule_forms import data_for
from .auth_builders import signed_in
from .campaign_builders import (
    add_draft,
    admit_test_work,
    campaign_clock,
    change,
    close_campaign,
    command,
)
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token

pytestmark = pytest.mark.django_db(transaction=True)


def setup(store, *, archived=True):
    """Archive with genuine lifecycle owners rather than editing protected state."""
    actor = uuid4()
    result, owner, mail = add_draft(store, store.active(), actor)
    assert result.state == "applied"
    row = content(owner["id"], kind="email", slot="initial")
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {"operation": "add", "section": "content", **row},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": mail["id"],
                    "values": {
                        "template_version": row["id"],
                        "subject": row["values"]["subject"],
                    },
                },
            ],
        ).state
        == "applied"
    )
    campaign = Campaign.objects.get()
    if archived:
        with campaign_clock(campaign.active_configuration.starts_at):
            command(campaign, actor, Action.ACTIVATE)
        close_campaign(campaign, actor)
        command(campaign, actor, Action.ARCHIVE)
        return_to_testing(
            campaign_id=campaign.pk,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    return campaign, f"/admin/campaign/{campaign.pk}/clone"


def fields(browser, path, store):
    """Use the displayed signed IDs, supplying new dates as a human would."""
    page = browser.get(path)
    assert page.status_code == 200
    rows = page.context["schedules"].previous
    values = posted(
        name="Successor campaign",
        start_date="2027-10-01",
        end_date="2027-10-31",
        base_digest=store.active().digest,
    )
    values.update(data_for(rows), clone_seed=page.context["clone_seed"])
    values["schedules-0-date"] = "2027-10-02"
    return values


def test_clone_installs_new_ids_content_and_mail_without_touching_history(
    auth_service, google
):
    """The request atomically adds a successor; retry retains the same receipt."""
    store = auth_service.store
    source, path = setup(store)
    old_version = source.active_configuration_id
    old_schedule = ScheduleDefinition.objects.get()
    browser, _ = signed_in()
    assert path.encode() in browser.get("/admin/campaign/new").content
    preview = post(browser, path, fields(browser, path, store))
    assert b"Welcome to" in preview.content
    proposal = token(preview)
    accepted = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    new = Campaign.objects.exclude(pk=source.pk).get()
    source.refresh_from_db()
    assert source.active_configuration_id == old_version
    assert new.state == "draft" and not new.structural_locked and not new.ever_active
    assert new.active_configuration.values["start_date"] == "2027-10-01"
    mail = ScheduleDefinition.objects.get(campaign=new)
    assert mail.pk != old_schedule.pk
    assert mail.current_revision.values["date"] == "2027-10-02"
    assert not mail.scheduleoccurrence_set.exists()
    config = SystemConfiguration.objects.get().active_configuration
    assert config.content_versions.filter(campaign_id=new.pk).count() == 1
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal})["Location"]
        == accepted["Location"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"schedules-0-date": ""},
        {"start_date": ""},
        {"state": "draft"},
        {"schedules-0-date": ["2027-10-02", "2027-10-03"]},
        {"timezone": "America/Chicago"},
        {"clone_seed": "forged"},
    ],
)
def test_invalid_clone_never_creates_an_intent(auth_service, google, changes):
    """Missing dates and hidden or repeated controls cannot import source state."""
    store = auth_service.store
    _, path = setup(store)
    browser, _ = signed_in()
    data = fields(browser, path, store)
    before = ConfigurationChangeRequest.objects.count()
    assert post(browser, path, data | changes).status_code == 400
    assert ConfigurationChangeRequest.objects.count() == before


def test_clone_is_not_available_while_the_source_is_current(auth_service, google):
    """A clone is not a way to plan a second concurrent campaign."""
    _, path = setup(auth_service.store, archived=False)
    browser, _ = signed_in()
    assert browser.get(path).status_code == 409


def test_clone_seed_and_confirmation_both_expire(auth_service, google, monkeypatch):
    """Reusing expired form identities or a preview requires another review."""
    from types import SimpleNamespace

    from django.core import signing

    _, path = setup(auth_service.store)
    browser, _ = signed_in()
    data = fields(browser, path, auth_service.store)
    proposal = token(post(browser, path, data))
    future = signing.time.time() + 901
    monkeypatch.setattr(signing, "time", SimpleNamespace(time=lambda: future))
    assert post(browser, path, data).status_code == 409
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal}).status_code
        == 400
    )


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_read_or_submit_clone(auth_service, google, role):
    """Family/source access does not confer campaign-cloning authority."""
    store = auth_service.store
    _, path = setup(store)
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("reader@example.org", roles=(role,)),
                }
            ],
        ).state
        == "applied"
    )
    google[0].update(email="reader@example.org", sub="reader-subject")
    browser, _ = signed_in()
    assert browser.get(path).status_code == 403
    assert (
        post(browser, path, {"action": "confirm", "preview": "forged"}).status_code
        == 403
    )


def test_changed_configuration_invalidates_both_clone_stages(auth_service, google):
    """A seed and final confirmation both pin the actual applied configuration."""
    store = auth_service.store
    _, path = setup(store)
    browser, _ = signed_in()
    data = fields(browser, path, store)
    proposal = token(post(browser, path, data))
    parish = store.active().document()["sections"]["parish"][0]
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "parish",
                    "id": parish["id"],
                    "values": {"name": "Changed parish"},
                }
            ],
        ).state
        == "applied"
    )
    assert post(browser, path, data).status_code == 409
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal}).status_code
        == 409
    )


def test_new_campaign_content_cannot_change_historical_content(auth_service, google):
    """Creation admission allows new text, never a concurrent edit of old text."""
    store = auth_service.store
    source, path = setup(store)
    browser, _ = signed_in()
    data = fields(browser, path, store)
    preview = post(browser, path, data)
    from django.core import signing

    intent = signing.loads(
        token(preview), salt=f"stewardship-campaign-clone-v1:{source.pk}"
    )
    historical = content(str(source.pk), slot="thank_you")
    result = change(
        store,
        store.active(),
        uuid4(),
        intent["patch"] + [{"operation": "add", "section": "content", **historical}],
    )
    assert result.state == "failed"
    assert Campaign.objects.count() == 1
    # The exact same creation proposal succeeds without the historical edit;
    # unrelated clone validation must not make the rejection test pass.
    assert change(store, store.active(), uuid4(), intent["patch"]).state == "applied"
    assert Campaign.objects.count() == 2
