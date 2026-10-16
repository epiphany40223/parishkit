"""Setup's staged parish data load stays private and bound to its original attempt.

Before #142 these tests also covered the first campaign's choices from the
staged load; setup collects no campaign any more, so they check the load
itself, which the Review and final confirmation require.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.setup_campaign import (
    require_staged_load,
    staged_source_result,
)
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.web.refusals import UserFacingMissing

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


def test_staged_load_is_private_and_unpublished(setup_service, monkeypatch):
    """The staged load serves the Review without publishing a parish corpus."""
    request, attempt = completed(setup_service, monkeypatch)
    with web_login():
        require_staged_load(request, setup_service, attempt.pk)
        with work_transaction():
            assert staged_source_result(attempt.pk)
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT ciphertext FROM stewardship_setup_source_exchange")
    assert not Campaign.objects.exists()
    assert SourceCurrent.objects.get().snapshot_id is None
    assert not setup_service.configured()


def test_other_login_cannot_use_staged_load(setup_service, monkeypatch):
    """Even the same Admin's new login cannot resume another wizard attempt."""
    _, attempt = completed(setup_service, monkeypatch)
    with web_login(), pytest.raises(LookupError):
        require_staged_load(login(setup_service), setup_service, attempt.pk)


@pytest.mark.parametrize("direct_sql", [False, True])
def test_source_bound_timezone_stays_fixed(setup_service, monkeypatch, direct_sql):
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
            SetupDraftSection.objects.get(attempt=attempt, step="parish").values[
                "timezone"
            ]
            == original["timezone"]
        )
        cancel_setup(request, setup_service, attempt.pk)
        assert (
            SetupDraftSection.objects.get(attempt=attempt, step="parish").values == {}
        )


def test_replaced_key_invalidates_the_staged_load(setup_service, monkeypatch):
    """Replacing the ParishSoft key after the load requires loading again."""
    from parishkit.stewardship.accounts.setup_credentials import stage_credential

    request, attempt = completed(setup_service, monkeypatch)
    with web_login():
        stage_credential(
            request,
            setup_service,
            attempt.pk,
            target="parishsoft",
            candidate=b"synthetic-replacement",
            organization_id=1,
            expected_version=attempt.version,
        )
        with pytest.raises(UserFacingMissing, match="Load the parish data"):
            require_staged_load(request, setup_service, attempt.pk)
    assert not Campaign.objects.exists()


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
