"""Selected parish instructions reach public lifecycle and private Member pages."""

from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.models import Submission

from ..content_factory import content
from .campaign_builders import campaign_clock, change
from .test_response_http_postgresql import answers_for, load_form, post
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def select_content(harness, slot, html, *, modules=None):
    """Install a fresh immutable revision through the actual configuration owner."""
    store = harness.service.store
    version = store.active()
    campaign = next(
        row
        for row in version.document()["sections"]["campaigns"]
        if row["id"] == str(harness.campaign.pk)
    )
    old_id = next(
        (
            row["id"]
            for row in version.document()["sections"].get("content", [])
            if row["values"]["campaign_id"] == campaign["id"]
            and row["values"]["kind"] == "page"
            and row["values"]["slot"] == slot
        ),
        None,
    )
    revision = content(campaign["id"], slot=slot, html=html, text="Parish instructions")
    patch = (
        [{"operation": "remove", "section": "content", "id": old_id}] if old_id else []
    )
    patch.append({"operation": "add", "section": "content", **revision})
    if modules is not None:
        patch.append(
            {
                "operation": "update",
                "section": "campaigns",
                "id": campaign["id"],
                "values": {"modules": modules},
            }
        )
    assert change(store, version, uuid4(), patch).state == "applied"


def test_member_intro_revision_requires_fresh_review(response_service):
    """Changing selected Member instructions refreshes the form before any write."""
    harness = response_service
    select_content(harness, "member_census", "<p>First Member instructions</p>")
    with web_login():
        form = load_form(harness)
        assert form["content"]["member_census"] == "<p>First Member instructions</p>"
    select_content(harness, "member_census", "<p>Revised Member instructions</p>")
    with web_login():
        result = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers_for(form)},
        )
        assert result.status_code == 409, result.content
        fresh = result.json()["form"]
        assert fresh["content"]["member_census"] == "<p>Revised Member instructions</p>"
        assert not Submission.objects.exists()
        assert (
            post(
                harness.client,
                "/family/submit",
                {"baseline": fresh["baseline"], "answers": answers_for(fresh)},
            ).status_code
            == 200
        )


def test_disabled_census_omits_member_intro(response_service):
    """A selected but unenabled census slot is never sent to a Ministry-only form."""
    select_content(
        response_service,
        "member_census",
        "<p>Hidden Member instructions</p>",
        modules=["ministry"],
    )
    with web_login():
        form = load_form(response_service)
        assert "member_census" not in form["content"]


@pytest.mark.parametrize("slot", ["pre_start", "post_end"])
def test_public_lifecycle_content_uses_only_public_substitutions(
    response_service, slot
):
    """Public pages display selected escaped content but no names, codes or links."""
    harness = response_service
    html = (
        "<p>Lifecycle instructions for <strong>{{ parish_name }}</strong>.</p>"
        "<p>{{ campaign_name }}: {{ campaign_start }} – {{ campaign_end }}</p>"
        "<p>Private [{{ family_name }}][{{ family_member_names }}]"
        "[{{ family_code }}][{{ family_url }}]</p>"
    )
    select_content(harness, slot, html)
    definition = harness.campaign.active_configuration
    moment = (
        definition.starts_at - timedelta(seconds=1)
        if slot == "pre_start"
        else definition.ends_at
    )
    with campaign_clock(moment), web_login():
        result = harness.client.get("/")
        assert result.status_code == 200, result.content
        body = result.content.decode()
        assert "Lifecycle instructions for <strong>" in body
        assert "Private [][][][]" in body
        assert "valid@example.org" not in body and "family-code" not in body
        assert ("starts on" if slot == "pre_start" else "has ended") in body
    select_content(harness, slot, "<p>Replacement lifecycle instructions</p>")
    with campaign_clock(moment), web_login():
        body = harness.client.get("/").content.decode()
        assert "Replacement lifecycle instructions" in body
        assert "Lifecycle instructions for" not in body
