"""Copy campaign is refused until the single-campaign change (#145).

Navigation rule 10 and decision 18: Campaign settings shows Copy campaign
greyed out, and the server refuses the page, its preview and its
confirmation, even with a valid signed preview. ``setup`` (an archived
campaign through its genuine lifecycle owners) is shared with other tests.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing
from django.test import Client

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import return_to_testing

from ..campaign_factory import campaign as campaign_record
from ..content_factory import content
from .auth_builders import signed_in
from .campaign_builders import (
    add_draft,
    admit_test_work,
    campaign_clock,
    change,
    close_campaign,
    command,
)
from .test_campaign_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

TIP = "Disabled; will be removed with the single-campaign change (#145)"


def setup(store, *, archived=True, before_archive=None):
    """Archive with genuine lifecycle owners rather than editing protected state.

    ``before_archive(owner_id)`` may apply extra source content first.
    """
    actor = uuid4()
    result, owner, mail = add_draft(store, store.active(), actor)
    assert result.state == "applied"
    if before_archive:
        before_archive(owner["id"])
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


def administrator():
    """The signed-in Administrator, as an editor's preview names its actor."""
    from parishkit.stewardship.accounts.policy_models import PortalUser

    return SimpleNamespace(identity=PortalUser.objects.get().pk)


def signed_preview(salt, patch):
    """A preview signed exactly as an editor signs one for the Administrator.

    It binds the signed-in Administrator, the applied configuration and the
    same scope fingerprint Campaign settings and Copy campaign confirm
    against, so it is valid in every respect but its patch.
    """
    from parishkit.stewardship.accounts.admin_editing import sign_preview
    from parishkit.stewardship.accounts.authentication import runtime
    from parishkit.stewardship.accounts.campaign_views import _scope
    from parishkit.stewardship.campaigns.work_locks import read_transaction

    with read_transaction():
        configuration, snapshot = _scope(runtime())
    return sign_preview(
        actor=administrator(),
        configuration=configuration,
        patch=patch,
        salt=salt,
        snapshot=snapshot,
    )


def added_campaign():
    """The patch a new draft or a copy adds: one new campaign record."""
    return [{"operation": "add", "section": "campaigns", **campaign_record()}]


def test_copy_campaign_is_refused_even_with_a_valid_preview(auth_service, google):
    """The page, a seeded preview and a signed confirmation are all refused."""
    store = auth_service.store
    source, path = setup(store)
    browser, _ = signed_in()
    requests = ConfigurationChangeRequest.objects.count()
    salt = f"stewardship-campaign-clone-v1:{source.pk}"
    proposal = signed_preview(salt, added_campaign())
    seed = signing.dumps(
        {
            "actor": str(administrator().identity),
            "base": store.active().digest,
            "target": str(uuid4()),
        },
        salt=salt + ":seed",
    )
    for response in (
        browser.get(path),
        browser.head(path),
        post(browser, path, {"action": "preview", "clone_seed": seed}),
        post(browser, path, {"action": "confirm", "preview": proposal}),
        # The refusal names nothing about any campaign, so it needs no sign-in.
        Client().get(path),
    ):
        assert response.status_code == 410
        assert response["Cache-Control"] == "no-store"
    refusal = browser.get(path).json()
    assert refusal["errors"][0]["code"] == "gone"
    assert refusal["refusal"]["message"] == "Copying a campaign is disabled."
    assert refusal["refusal"]["fix"] == (
        "It will be removed with the single-campaign change (#145)."
    )
    # A person sees a plain page, never a sign-in prompt.
    page = browser.get(path, HTTP_ACCEPT="text/html")
    assert page.status_code == 410
    assert b"Page unavailable" in page.content
    assert b"Copying a campaign is disabled." in page.content
    # (The error page's own sign-in link; the session dialog has another.)
    assert b'<p><a href="/admin/login">Sign in again</a></p>' not in page.content
    assert ConfigurationChangeRequest.objects.count() == requests
    assert Campaign.objects.count() == 1


def test_campaign_settings_show_copy_campaign_greyed_out(auth_service, google):
    """Copy campaign is an unavailable control with the #145 tip, not a link."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    body = browser.get(f"/admin/campaign/{row.pk}/settings").content.decode()
    assert (
        '<a class="disabled-control-link" role="link" aria-disabled="true" '
        'tabindex="0" aria-describedby="copy-campaign-tip" data-menu-tip>'
        "Copy campaign</a>"
    ) in body
    assert f'id="copy-campaign-tip" role="tooltip">{TIP}</span>' in body
    assert "/clone" not in body and "Create campaign draft" not in body
