"""Selected parish instructions reach public lifecycle and private Member pages."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.content_forms import LEGACY_PAGE_REFERENCES
from parishkit.stewardship.responses.models import Submission

from ..content_factory import content
from .campaign_builders import campaign_clock, change
from .test_response_http_postgresql import answers_for, load_form, post
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("slot", ["login_help", "access_denied"])
@pytest.mark.parametrize("error", [ConfigError, DatabaseError, ValueError])
def test_optional_public_help_failure_keeps_fixed_safe_fallback(
    response_service, monkeypatch, slot, error
):
    """Fault only optional help after ordinary campaign admission has succeeded."""
    from parishkit.stewardship.responses import availability

    def fail(service, requested):
        """Simulate a private diagnostic from the optional content lookup alone."""
        assert requested == slot
        raise error("synthetic-private-diagnostic")

    monkeypatch.setattr(availability, "public_help", fail)
    harness = response_service
    with web_login():
        response = (
            harness.client.get("/")
            if slot == "login_help"
            else harness.client.post(
                "/",
                {"code": "invalid"},
                HTTP_X_CSRFTOKEN=harness.client.cookies["csrftoken"].value,
            )
        )
        assert response.status_code == (200 if slot == "login_help" else 403)
        assert b"synthetic-private-diagnostic" not in response.content
        assert (
            b"Family campaign sign-in"
            if slot == "login_help"
            else b"Sign-in is unavailable"
        ) in response.content
        assert response["Cache-Control"] == "no-store"


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
    values = {}
    if slot in LEGACY_PAGE_REFERENCES:
        values["content_versions"] = campaign["values"]["content_versions"] | {
            slot: revision["id"]
        }
    if modules is not None:
        values["modules"] = modules
    if values:
        patch.append(
            {
                "operation": "update",
                "section": "campaigns",
                "id": campaign["id"],
                "values": values,
            }
        )
    assert change(store, version, uuid4(), patch).state == "applied"


@pytest.mark.parametrize("modules", [["census"], ["ministry"]])
@pytest.mark.parametrize("displayed", [False, True])
@pytest.mark.parametrize(
    "field,placeholder,value",
    [
        ("website", "parish_website", "https://updated.example.org/"),
        ("phone", "parish_phone", "+12025550199"),
        ("year_label", "campaign_year", "Updated campaign year"),
    ],
)
def test_displayed_public_substitution_change_requires_review(
    response_service, modules, field, placeholder, value, displayed
):
    """A displayed public value is just as review-sensitive as its template ID."""
    harness = response_service
    select_content(
        harness,
        "review",
        "<p>{{ " + placeholder + " }}</p>"
        if displayed
        else "<p>Unchanged instructions</p>",
        modules=modules,
    )
    with web_login():
        form = load_form(harness)
    store = harness.service.store
    version = store.active()
    section = "campaigns" if field == "year_label" else "parish"
    record = version.document()["sections"][section][0]
    assert (
        change(
            store,
            version,
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": section,
                    "id": record["id"],
                    "values": {field: value},
                }
            ],
        ).state
        == "applied"
    )
    with web_login():
        result = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers_for(form)},
        )
        if not displayed:
            assert result.status_code == 200, result.content
            assert Submission.objects.count() == 1
            return
        assert result.status_code == 409, result.content
        fresh = result.json()["form"]
        assert value in fresh["content"]["review"]
        assert not Submission.objects.exists()
        assert (
            post(
                harness.client,
                "/family/submit",
                {"baseline": fresh["baseline"], "answers": answers_for(fresh)},
            ).status_code
            == 200
        )


@pytest.mark.parametrize("slot", ["login_help", "access_denied"])
def test_public_help_is_selected_uniform_and_private_placeholder_free(
    response_service, slot
):
    """Configured contact help never depends on the attempted Family identity."""
    harness = response_service
    select_content(
        harness,
        slot,
        "<p>Public contact for {{ parish_name }}: [{{ family_name }}]"
        "[{{ family_code }}][{{ family_url }}]</p>",
    )

    def responses():
        """Use real CSRF-valid manual failures and the opaque-link denial path."""
        if slot == "login_help":
            return [harness.client.get("/")]
        result = []
        for candidate in ("bad", "ZZZZZZZZ"):
            result.append(
                harness.client.post(
                    "/",
                    {"code": candidate},
                    HTTP_X_CSRFTOKEN=harness.client.cookies["csrftoken"].value,
                )
            )
        result.append(harness.client.get("/access/not-a-token"))
        return result

    with web_login():
        pages = responses()
        assert all(
            page.status_code == (200 if slot == "login_help" else 403) for page in pages
        )
        assert len({page.content for page in pages}) == 1
        body = pages[0].content.decode()
        assert "Public contact for" in body and ": [][][]" in body
        assert "valid@example.org" not in body and "not-a-token" not in body
    select_content(harness, slot, "<p>Changed public contact instructions</p>")
    with web_login():
        assert all(
            b"Changed public contact instructions" in page.content
            for page in responses()
        )


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
