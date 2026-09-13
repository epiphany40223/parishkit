"""First campaign stays temporary and uses only its original staged source result."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.setup_campaign import campaign_catalog
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from ..campaign_factory import campaign, financial
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_loading_postgresql import pages, prepared, run
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_setup_views_postgresql import post, setup_http  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


def completed(service, monkeypatch):
    """Exercise actual private handoff and loading rather than inventing a result."""
    values, request = prepared(service, with_request=True)
    fake_provider(monkeypatch, pages())
    run(*values, complete=True)
    return request, SetupAttempt.objects.get()


def test_original_web_prepares_first_campaign_without_publishing(
    setup_service, monkeypatch
):
    """Temporary source choices do not require or create a current parish corpus."""
    request, attempt = completed(setup_service, monkeypatch)
    with web_login():
        catalog = campaign_catalog(request, setup_service, attempt.pk)
        assert catalog.funds == (("9", "Offertory"),)
        assert catalog.ministries == ()
        values = {
            "source_result": str(catalog.result_id),
            "campaign": campaign(timezone=catalog.timezone)["values"],
        }
        saved = save_section(
            request,
            setup_service,
            attempt.pk,
            step="campaign",
            values=values,
            expected_version=attempt.version,
        )
        assert saved.version == attempt.version + 1
        assert SetupDraftSection.objects.get(step="campaign").values == values
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT ciphertext FROM stewardship_setup_source_exchange")
        cancel_setup(request, setup_service, attempt.pk)
        assert SetupDraftSection.objects.get(step="campaign").values == {}
    assert not Campaign.objects.exists()
    assert SourceCurrent.objects.get().snapshot_id is None
    assert not setup_service.configured()


@pytest.mark.parametrize("invalid", ["result", "ministry", "timezone"])
def test_campaign_save_rechecks_result_catalog_and_initial_timezone(
    setup_service, monkeypatch, invalid
):
    """A source UUID or hidden field is not authority to select unrelated data."""
    request, attempt = completed(setup_service, monkeypatch)
    with web_login():
        catalog = campaign_catalog(request, setup_service, attempt.pk)
        values = campaign(timezone=catalog.timezone)["values"]
        if invalid == "ministry":
            values.update(modules=["census", "ministry"], ministry_duids=[999])
        if invalid == "timezone":
            values["timezone"] = "Pacific/Honolulu"
        with pytest.raises(ValueError, match="catalog"):
            save_section(
                request,
                setup_service,
                attempt.pk,
                step="campaign",
                values={
                    "source_result": str(uuid4())
                    if invalid == "result"
                    else str(catalog.result_id),
                    "campaign": values,
                },
                expected_version=attempt.version,
            )
    assert not SetupDraftSection.objects.filter(step="campaign").exists()


def test_other_login_cannot_read_staged_catalog(setup_service, monkeypatch):
    """Even the same Admin's new login cannot resume another wizard attempt."""
    _, attempt = completed(setup_service, monkeypatch)
    with web_login(), pytest.raises(LookupError):
        campaign_catalog(login(setup_service), setup_service, attempt.pk)


@pytest.mark.parametrize("direct_sql", [False, True])
def test_source_bound_timezone_cannot_strand_first_campaign(
    setup_service, monkeypatch, direct_sql
):
    """Service and SQL preserve the loaded timezone while other profile edits work."""
    request, attempt = completed(setup_service, monkeypatch)
    original = SetupDraftSection.objects.get(attempt=attempt, step="parish").values
    changed = original | {"timezone": "Pacific/Honolulu"}
    with web_login():
        if direct_sql:
            with pytest.raises(DatabaseError) as failure, work_transaction():
                SetupDraftSection.objects.filter(attempt=attempt, step="parish").update(
                    values=changed, version=F("version") + 1
                )
            assert failure.value.__cause__.sqlstate == "23514"
        else:
            with pytest.raises(ValueError, match="timezone"):
                save_section(
                    request,
                    setup_service,
                    attempt.pk,
                    step="parish",
                    values=changed,
                    expected_version=attempt.version,
                )
        saved = save_section(
            request,
            setup_service,
            attempt.pk,
            step="parish",
            values=original | {"name": "Corrected Parish"},
            expected_version=attempt.version,
        )
        assert saved.version == attempt.version + 1
        assert (
            campaign_catalog(request, setup_service, attempt.pk).timezone
            == original["timezone"]
        )
        cancel_setup(request, setup_service, attempt.pk)
        assert (
            SetupDraftSection.objects.get(attempt=attempt, step="parish").values == {}
        )


def test_staged_financial_funds_and_replaced_key_require_current_evidence(
    setup_service, monkeypatch
):
    """Financial periods use the ready catalog; replacing its key invalidates it."""
    from parishkit.stewardship.accounts.setup_credentials import stage_credential
    from parishkit.stewardship.accounts.share_forms import default_share_options

    request, attempt = completed(setup_service, monkeypatch)
    with web_login():
        catalog = campaign_catalog(request, setup_service, attempt.pk)
        values = campaign(
            modules=["financial"],
            financial=financial(fund_duids=[9], comparison_fund_duids=[9]),
            share_options=default_share_options(),
            timezone=catalog.timezone,
        )["values"]
        saved = save_section(
            request,
            setup_service,
            attempt.pk,
            step="campaign",
            values={"source_result": str(catalog.result_id), "campaign": values},
            expected_version=attempt.version,
        )
        stage_credential(
            request,
            setup_service,
            attempt.pk,
            target="parishsoft",
            candidate=b"synthetic-replacement",
            organization_id=1,
            expected_version=saved.version,
        )
        with pytest.raises(LookupError, match="source load"):
            campaign_catalog(request, setup_service, attempt.pk)
    assert not Campaign.objects.exists()


def test_first_campaign_http_is_private_versioned_and_csrf_protected(
    setup_http, monkeypatch
):
    """Use the original issued browser session and actual web SQL grants."""
    from parishkit.stewardship.accounts.campaign_forms import initial_fields

    request, attempt = completed(setup_http, monkeypatch)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    with web_login():
        response = browser.get("/admin/setup/campaign")
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        assert (
            b"First campaign" in response.content and b"Offertory" in response.content
        )
        fields = initial_fields(campaign()["values"], digest="")
        fields.pop("base_digest")
        fields = {
            key: "on" if value is True else value
            for key, value in fields.items()
            if value is not False and value is not None
        }
        fields["version"] = str(attempt.version)
        assert browser.post("/admin/setup/campaign", fields).status_code == 403
        bad = post(browser, "/admin/setup/campaign", fields | {"end_date": "invalid"})
        assert bad.status_code == 400
        assert not SetupDraftSection.objects.filter(step="campaign").exists()
        accepted = post(browser, "/admin/setup/campaign", fields)
        assert accepted.status_code == 302, accepted.content
        assert post(browser, "/admin/setup/campaign", fields).status_code == 409
        assert browser.get("/admin/setup/campaign").status_code == 200
    assert not Campaign.objects.exists() and not setup_http.configured()


def test_loaded_parish_form_explains_fixed_timezone_and_preserves_other_edits(
    setup_http, monkeypatch
):
    """Disabled browser fields retain their trusted initial value even if forged."""
    request, attempt = completed(setup_http, monkeypatch)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    original = SetupDraftSection.objects.get(attempt=attempt, step="parish").values
    with web_login():
        page = browser.get("/admin/setup/parish")
        assert page.status_code == 200, page.content
        assert page.context["form"].fields["timezone"].disabled
        assert b"Cancel and start a new setup" in page.content
        for supplied in ({}, {"timezone": "Pacific/Honolulu"}):
            fields = {
                key: value for key, value in original.items() if key != "timezone"
            }
            fields.update(
                supplied, name="Corrected Parish", version=str(attempt.version)
            )
            response = post(browser, "/admin/setup/parish", fields)
            assert response.status_code == 302, response.content
            attempt.refresh_from_db()
            assert SetupDraftSection.objects.get(
                attempt=attempt, step="parish"
            ).values == (original | {"name": "Corrected Parish"})
