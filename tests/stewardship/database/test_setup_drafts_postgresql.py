"""Public wizard drafts stay isolated, and expiry atomically removes their values."""

# ruff: noqa: F811 -- pytest injects imported fixtures by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.accounts.sessions import end_admin
from parishkit.stewardship.accounts.setup_drafts import save_section, view_draft
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.accounts.setup_staging import begin_setup, cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from ..test_setup_forms import VALUES
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_expiry_postgresql import sweep
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def saved(service, *, step="parish"):
    """Create one real original-login attempt and save through its owning service."""
    request = login(service)
    attempt = begin_setup(request, service)
    result = save_section(
        request,
        service,
        attempt.attempt_id,
        step=step,
        values=VALUES[step],
        expected_version=attempt.version,
    )
    return request, result


@pytest.mark.parametrize("step", VALUES)
def test_real_web_role_can_save_and_revisit_without_activation(setup_service, step):
    """SQL admission works with runtime grants; a draft is never a Parish row."""
    base = setup_service.store.active()
    with web_login():
        request, result = saved(setup_service, step=step)
        view = view_draft(request, setup_service, result.attempt_id)
        assert view.sections == {step: VALUES[step]}
        assert view.status == result
    assert not Parish.objects.exists()
    assert setup_service.store.active().digest == base.digest
    assert not setup_service.configured()


def test_second_tab_cannot_overwrite_a_saved_step(setup_service):
    """The shared attempt version catches edits even when tabs show different steps."""
    request, result = saved(setup_service)
    with pytest.raises(StaleRecordError):
        save_section(
            request,
            setup_service,
            result.attempt_id,
            step="mail",
            values=VALUES["mail"],
            expected_version=result.version - 1,
        )
    assert SetupDraftSection.objects.count() == 1
    saved_again = save_section(
        request,
        setup_service,
        result.attempt_id,
        step="parish",
        values=VALUES["parish"] | {"name": "Corrected Parish"},
        expected_version=result.version,
    )
    assert saved_again.version == result.version + 1
    assert SetupDraftSection.objects.get().values["name"] == "Corrected Parish"


def test_equality_evidence_is_database_owned_and_changes_with_public_values(
    setup_service,
):
    """Even the web's draft writer cannot substitute a digest for different settings."""
    with web_login():
        request, result = saved(setup_service)
        original = SetupDraftSection.objects.get().scope_digest
        assert len(original) == 64
        with (
            pytest.raises(DatabaseError) as denied,
            work_transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE stewardship_setup_draft_section "
                "SET scope_digest=%s,version=version+1",
                ["0" * 64],
            )
        assert denied.value.__cause__.sqlstate == "428C9"
        save_section(
            request,
            setup_service,
            result.attempt_id,
            step="parish",
            values=VALUES["parish"] | {"name": "Corrected Parish"},
            expected_version=result.version,
        )
        assert SetupDraftSection.objects.get().scope_digest != original


def test_new_login_cannot_read_or_adopt_the_original_draft(setup_service):
    """Opaque attempt IDs do not grant authority, including to the same Admin."""
    request, result = saved(setup_service)
    newcomer = login(setup_service)
    assert view_draft(newcomer, setup_service) is None
    with pytest.raises(LookupError):
        view_draft(newcomer, setup_service, result.attempt_id)
    with pytest.raises(LookupError):
        save_section(
            newcomer,
            setup_service,
            result.attempt_id,
            step="parish",
            values=VALUES["parish"],
            expected_version=result.version,
        )
    assert view_draft(request, setup_service).sections["parish"] == VALUES["parish"]


@pytest.mark.parametrize("automatic", [False, True])
def test_expiry_scrubs_all_public_values_in_the_same_transaction(
    setup_service, automatic
):
    """Neither a cancelled browser nor a lost worker can leave public settings live."""
    request, result = saved(setup_service)
    save_section(
        request,
        setup_service,
        result.attempt_id,
        step="mail",
        values=VALUES["mail"],
        expected_version=result.version,
    )
    if automatic:
        end_admin(request)
        assert sweep() == 1
        assert sweep() == 0
    else:
        with web_login():
            cancel_setup(request, setup_service, result.attempt_id)
        assert view_draft(request, setup_service).sections == {}
    assert SetupDraftSection.objects.count() == 2
    assert all(
        row.values == {} and row.scrubbed_at for row in SetupDraftSection.objects.all()
    )
    assert SetupAttempt.objects.get().state == "expired"


@pytest.mark.parametrize(
    "mutation",
    [{"attempt_id": uuid4()}, {"step": "mail"}, {"values": {"api_key": "private"}}],
)
def test_sql_refuses_rebinding_and_undeclared_public_fields(setup_service, mutation):
    """The SQL barrier survives a service bypass and broad schema-owner privileges."""
    saved(setup_service)
    with pytest.raises(DatabaseError), work_transaction():
        SetupDraftSection.objects.update(**mutation, version=F("version") + 1)


def test_scrubbed_draft_cannot_be_repopulated_or_deleted(setup_service):
    """Retained tombstones prove cleanup but contain no prior field values."""
    request, result = saved(setup_service)
    cancel_setup(request, setup_service, result.attempt_id)
    with pytest.raises(DatabaseError), work_transaction():
        SetupDraftSection.objects.update(
            values=VALUES["parish"], scrubbed_at=None, version=F("version") + 1
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        SetupDraftSection.objects.all().delete()
    with pytest.raises(PermissionError):
        save_section(
            request,
            setup_service,
            result.attempt_id,
            step="parish",
            values=VALUES["parish"],
            expected_version=SetupAttempt.objects.get().version,
        )
