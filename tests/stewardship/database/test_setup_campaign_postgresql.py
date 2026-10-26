"""Setup's parish data stays private and bound to its original attempt.

Before #142 these tests also covered the first campaign's choices from the
staged load; setup collects no campaign any more. Since #2201 Review and the
final confirmation require the quick parish data check, not a staged load:
these tests check that gate, and that a load an attempt bound earlier stays
private and keeps the timezone it fixed.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.setup_campaign import (
    current_source_check,
    require_source_check,
)
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.web.refusals import UserFacingMissing

from .legacy_setup_loading import staged_load
from .setup_check_builders import checked, checked_setup
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
    """A load bound before #2201 publishes no parish corpus and no longer
    opens Review: only the parish data check does."""
    request, attempt = completed(setup_service, monkeypatch)
    with web_login():
        with work_transaction():
            assert staged_load(attempt) is not None
        with pytest.raises(UserFacingMissing, match="Check the parish data"):
            require_source_check(request, setup_service, attempt.pk)
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT ciphertext FROM stewardship_setup_source_exchange")
    assert not Campaign.objects.exists()
    assert SourceCurrent.objects.get().snapshot_id is None
    assert not setup_service.configured()
    # The parish data check opens Review, with the load still bound.
    checked(request, setup_service, attempt.pk)
    with web_login():
        require_source_check(request, setup_service, attempt.pk)


def test_other_login_cannot_use_the_check(setup_service):
    """Even the same Admin's new login cannot resume another wizard attempt."""
    request, attempt = checked_setup(setup_service)
    with web_login():
        require_source_check(request, setup_service, attempt.pk)
        with pytest.raises(LookupError):
            require_source_check(login(setup_service), setup_service, attempt.pk)


def test_source_bound_timezone_stays_fixed_in_sql(setup_service, monkeypatch):
    """SQL keeps a legacy load's timezone fixed while other profile edits work.

    The web no longer refuses the change itself (#2233): no load can bind now.
    """
    request, attempt = completed(setup_service, monkeypatch)
    original = SetupDraftSection.objects.get(attempt=attempt, step="parish").values
    changed = original | {"timezone": "Pacific/Honolulu"}
    with web_login():
        with pytest.raises(DatabaseError) as failure, work_transaction():
            SetupDraftSection.objects.filter(attempt=attempt, step="parish").update(
                values=changed, version=F("version") + 1
            )
        assert failure.value.__cause__.sqlstate == "23514"
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


def test_replaced_key_needs_the_check_again(setup_service):
    """Replacing the ParishSoft key after the check requires checking again."""
    from parishkit.stewardship.accounts.setup_credentials import stage_credential

    request, attempt = checked_setup(setup_service)
    with web_login():
        with work_transaction():
            assert current_source_check(attempt.pk)
        stage_credential(
            request,
            setup_service,
            attempt.pk,
            target="parishsoft",
            candidate=b"synthetic-replacement",
            expected_version=attempt.version,
        )
        with pytest.raises(UserFacingMissing, match="Check the parish data"):
            require_source_check(request, setup_service, attempt.pk)
        with work_transaction(), pytest.raises(ValueError, match="Check the parish"):
            current_source_check(attempt.pk)
    assert not Campaign.objects.exists()


def test_a_checked_setup_keeps_its_timezone_editable(setup_service):
    """The check reads no parish data, so it fixes no timezone (#2201)."""
    request, attempt = checked_setup(setup_service)
    original = SetupDraftSection.objects.get(attempt=attempt, step="parish").values
    with web_login():
        saved = save_section(
            request,
            setup_service,
            attempt.pk,
            step="parish",
            values=original | {"timezone": "Pacific/Honolulu"},
            expected_version=attempt.version,
        )
    assert saved.version == attempt.version + 1
    assert (
        SetupDraftSection.objects.get(attempt=attempt, step="parish").values["timezone"]
        == "Pacific/Honolulu"
    )
