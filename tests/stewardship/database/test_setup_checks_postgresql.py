"""Setup's quick parish data check (#2201) under the exact logins and SQL guard.

The web queues and discards a check, only the isolated ParishSoft installer
answers it, a check counts only for the staged key and organization it
checked, ending setup discards a waiting one, and the final confirmation
binds the newest ready check, which the readiness guard repeats in SQL.
"""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts import setup_confirmation
from parishkit.stewardship.accounts.setup_check_models import SetupSourceCheck
from parishkit.stewardship.accounts.setup_checks import (
    checked_source,
    current_check,
    start_check,
)
from parishkit.stewardship.accounts.setup_confirmation import freeze_setup
from parishkit.stewardship.accounts.setup_drafts import save_section, view_draft
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_readiness_models import SetupReadinessBinding
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole

from ..test_setup_forms import VALUES
from .setup_check_builders import (
    CHECKED,
    answer_check,
    checked,
    checked_setup,
    queue_check,
)
from .setup_lookup_builders import answered_and_chosen
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_confirmation_postgresql import prepared
from .test_setup_credentials_postgresql import CANDIDATE, staged
from .test_setup_exchange_postgresql import target_login
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def collecting(service):
    """A collecting attempt with its Parish profile and chosen ParishSoft key."""
    request, attempt, _, private = staged(service)
    with web_login():
        save_section(
            request,
            service,
            attempt.attempt_id,
            step="parish",
            values=VALUES["parish"],
            expected_version=attempt.version,
        )
    return request, SetupAttempt.objects.get(pk=attempt.attempt_id), private


def test_a_check_is_queued_by_the_web_and_answered_by_the_installer(setup_service):
    """The installer records the name and totals; the step is then done."""
    request, attempt, private = collecting(setup_service)
    queued = queue_check(request, setup_service, attempt.pk)
    assert queued.state == "queued" and queued.waiting and not queued.checked
    # The attempt's version advanced, so a Review of the earlier one is due.
    assert SetupAttempt.objects.get(pk=attempt.pk).version == attempt.version + 1
    # A second press while it waits returns the same check.
    again = queue_check(request, setup_service, attempt.pk)
    assert again.identifier == queued.identifier
    assert answer_check(private)
    row = SetupSourceCheck.objects.get()
    assert (row.state, row.organization_name, row.family_count, row.member_count) == (
        "ready",
        "St. Example Parish",
        812,
        2034,
    )
    assert row.organization_id == 1 and row.actor_id is None
    assert row.worker_id is not None and row.finished_at is not None
    assert current_check(SetupAttempt.objects.get(pk=attempt.pk)).checked
    with web_login():
        draft = view_draft(request, setup_service, attempt.pk)
    assert draft.source_checked
    # Nothing else is waiting for the installer.
    assert not answer_check(private)


@pytest.mark.parametrize(
    "answer,code",
    [
        ({"outcome": "refused"}, "parishsoft_key_refused"),
        ({"outcome": "not_reached"}, "parishsoft_organization_not_reached"),
        ({"outcome": "unreadable"}, "parishsoft_check_unreadable"),
        ({"outcome": "unavailable"}, "parishsoft_unavailable"),
        (CHECKED | {"families": 0}, "parishsoft_no_families"),
    ],
)
def test_a_failed_check_keeps_only_its_code_and_check_again_repeats_it(
    setup_service, answer, code
):
    """A failure is not done; Check again queues a new check of the same key."""
    request, attempt, private = collecting(setup_service)
    attempt = checked(request, setup_service, attempt.pk, private, answer)
    failed = SetupSourceCheck.objects.get()
    assert (failed.state, failed.error_code, failed.family_count) == (
        "failed",
        code,
        None,
    )
    assert checked_source(attempt) is None
    attempt = checked(request, setup_service, attempt.pk, private)
    assert checked_source(attempt).state == "ready"
    assert SetupSourceCheck.objects.count() == 2


def test_a_check_counts_only_for_the_key_and_organization_it_checked(setup_service):
    """A new key discards a waiting check; a finished one no longer counts."""
    from parishkit.stewardship.accounts.setup_credentials import stage_credential

    request, attempt = checked_setup(setup_service)
    assert checked_source(attempt) is not None
    # A waiting check is discarded when a key is pasted, here the same key.
    queue_check(request, setup_service, attempt.pk)
    with web_login():
        stage_credential(
            request,
            setup_service,
            attempt.pk,
            target="parishsoft",
            candidate=CANDIDATE,
            expected_version=SetupAttempt.objects.get(pk=attempt.pk).version,
        )
    assert SetupSourceCheck.objects.filter(state="discarded").count() == 1
    attempt = SetupAttempt.objects.get(pk=attempt.pk)
    # The same key and the same organization chosen again keep the check.
    answered_and_chosen(request, setup_service, attempt.pk)
    attempt = SetupAttempt.objects.get(pk=attempt.pk)
    assert checked_source(attempt) is not None
    # Another organization for the same key does not.
    with web_login():
        stage_credential(
            request,
            setup_service,
            attempt.pk,
            target="parishsoft",
            candidate=CANDIDATE,
            expected_version=attempt.version,
        )
    answered_and_chosen(request, setup_service, attempt.pk, organization_id=2)
    attempt = SetupAttempt.objects.get(pk=attempt.pk)
    assert checked_source(attempt) is None
    assert not current_check(attempt).current
    assert checked(request, setup_service, attempt.pk).pk == attempt.pk
    assert checked_source(SetupAttempt.objects.get(pk=attempt.pk)).organization_id == 2


def test_no_check_starts_without_a_chosen_organization_or_profile(setup_service):
    """An open lookup or a missing Parish profile refuses, in Python and SQL."""
    request, attempt, _, _ = staged(setup_service, choose=False)
    with web_login(), pytest.raises(ValueError, match="ParishSoft connection first"):
        start_check(
            request,
            setup_service,
            attempt.attempt_id,
            expected_version=attempt.version,
        )
    assert not SetupSourceCheck.objects.exists()


def test_ending_setup_discards_a_waiting_check(setup_service):
    """Cancel scrubs the staging, and the installer never claims its check."""
    request, attempt, private = collecting(setup_service)
    queue_check(request, setup_service, attempt.pk)
    with web_login():
        cancel_setup(request, setup_service, attempt.pk)
    assert SetupSourceCheck.objects.get().state == "discarded"
    assert not answer_check(private)


def guard_refuses(statement, parameters=(), sqlstate="23514"):
    """Run one direct SQL write under the work lock; it must be refused.

    ``sqlstate`` 23514 is the guard's refusal, 42501 a missing grant.
    """
    with (
        pytest.raises(DatabaseError) as failure,
        transaction.atomic(),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement, parameters)
    assert failure.value.__cause__.sqlstate == sqlstate, failure.value


def test_the_sql_guard_admits_each_move_only_for_its_owner(setup_service):
    """The web cannot answer, the installer cannot discard or rebind, nothing
    finished changes, and no check is deleted."""
    request, attempt, private = collecting(setup_service)
    queue_check(request, setup_service, attempt.pk)
    row = SetupSourceCheck.objects.get()
    ready = (
        "UPDATE stewardship_setup_source_check SET state='ready', family_count=1, "
        "member_count=1, finished_at=now(), version=version+1 WHERE id=%s"
    )
    with web_login():
        # The web holds no grant on the answer's columns at all.
        guard_refuses(ready, [row.pk], sqlstate="42501")
        guard_refuses(
            "UPDATE stewardship_setup_source_check SET state='discarded', "
            "actor_id=NULL, version=version+1 WHERE id=%s",
            [row.pk],
        )
        # A second waiting check of the same attempt is refused by its index.
        with pytest.raises(DatabaseError), transaction.atomic(), work_transaction():
            SetupSourceCheck.objects.create(
                attempt_id=row.attempt_id,
                credential_id=row.credential_id,
                credential_version=row.credential_version,
                fingerprint=row.fingerprint,
                organization_id=row.organization_id,
                actor_id=attempt.owner_id,
            )
    with target_login("parishsoft"):
        # The installer must claim before it answers, and never as a person.
        guard_refuses(ready, [row.pk])
        guard_refuses(
            "UPDATE stewardship_setup_source_check SET state='discarded', "
            "version=version+1 WHERE id=%s",
            [row.pk],
        )
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        work_transaction(),
    ):
        SetupSourceCheck.objects.filter(pk=row.pk).update(
            state="discarded", version=F("version") + 1
        )
    assert answer_check(private)
    with web_login():
        guard_refuses(
            "UPDATE stewardship_setup_source_check SET state='discarded', "
            "actor_id=%s, version=version+1 WHERE id=%s",
            [attempt.owner_id, row.pk],
        )
    guard_refuses("DELETE FROM stewardship_setup_source_check WHERE id=%s", [row.pk])
    guard_refuses(
        "UPDATE stewardship_setup_source_check SET organization_id=2, "
        "version=version+1 WHERE id=%s",
        [row.pk],
    )


def test_the_web_cannot_queue_a_check_of_another_organization(setup_service):
    """SQL binds the check to the staged key's chosen organization."""
    request, attempt, _ = collecting(setup_service)
    from parishkit.stewardship.accounts.setup_secret_models import (
        SetupSealedCredential,
    )

    credential = SetupSealedCredential.objects.get(
        attempt=attempt, target="parishsoft", scrubbed_at=None
    )
    with (
        web_login(),
        pytest.raises(DatabaseError) as failure,
        transaction.atomic(),
        work_transaction(),
    ):
        SetupSourceCheck.objects.create(
            attempt=attempt,
            credential_id=credential.pk,
            credential_version=credential.version,
            fingerprint=credential.fingerprint,
            organization_id=2,
            actor_id=attempt.owner_id,
        )
    assert "chosen organization" in str(failure.value)


def test_confirmation_binds_the_newest_ready_check(
    setup_service, monkeypatch, tmp_path
):
    """The readiness record names the check, with no staged load (#2201)."""
    request, attempt, token = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        freeze_setup(request, setup_service, preview_token=token)
    proof = SetupReadinessBinding.objects.get()
    check = SetupSourceCheck.objects.get(state="ready")
    assert proof.source_check_id == check.pk and proof.source_result_id is None
    frozen = SetupAttempt.objects.get(pk=attempt.attempt_id)
    assert frozen.state == "frozen" and frozen.source_task_id is None


def test_sql_refuses_a_check_that_is_no_longer_the_newest(
    setup_service, monkeypatch, tmp_path
):
    """A later check supersedes an earlier one, even if Python names it."""
    request, attempt, token = prepared(setup_service, monkeypatch, tmp_path)
    earlier = SetupSourceCheck.objects.get(state="ready").pk
    checked(request, setup_service, attempt.attempt_id)
    from django.core import signing

    from parishkit.stewardship.accounts.setup_preview import (
        PREVIEW_SALT,
        prepare_preview,
    )

    with web_login():
        token = signing.dumps(
            prepare_preview(request, setup_service).binding(), salt=PREVIEW_SALT
        )
    monkeypatch.setattr(setup_confirmation, "current_source_check", lambda _: earlier)
    with web_login(), pytest.raises(DatabaseError, match="current parish data check"):
        freeze_setup(request, setup_service, preview_token=token)
    assert SetupAttempt.objects.get(pk=attempt.attempt_id).state == "collecting"
    assert not SetupReadinessBinding.objects.exists()


def test_the_page_shows_the_check_and_check_again(setup_http):
    """Check parish data posts in place and shows the answer with Continue."""
    request, attempt, private = collecting(setup_http)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    with web_login():
        page = browser.get("/admin/setup/source")
        assert page.status_code == 200, page.content
        html = page.content.decode()
        assert "<h1>Check parish data</h1>" in html
        assert 'name="action" value="check"' in html
        assert "Check that ParishSoft answers for St. Example Parish (1)" in html
        assert "Campaigns offer" not in html and "Load parish data" not in html
        response = post(
            browser,
            "/admin/setup",
            {
                "action": "check",
                "attempt": str(attempt.pk),
                "version": str(attempt.version),
            },
        )
        assert response.status_code == 302
        assert response["Location"] == "/admin/setup/source#setup-source-panel"
        waiting = browser.get("/admin/setup/source").content.decode()
        assert "data-source-check-waiting" in waiting
        assert 'data-reread-every="3000"' in waiting
        assert 'aria-describedby="setup-check-reason"' in waiting
    assert answer_check(private)
    with web_login():
        ready = browser.get("/admin/setup/source").content.decode()
    assert (
        "ParishSoft answered for <strong>St. Example Parish (1)</strong>: "
        "812 Families and 2,034 Members." in ready
    )
    assert (
        'class="setup-continue" data-nav-slot="next" href="/admin/setup/confirm"'
        in (ready)
    )
    assert ">Check again<" in ready and "data-reread-every" not in ready


def test_a_queued_check_of_a_changed_key_is_never_claimed(setup_service):
    """A check whose key changed without a paste is not claimed, so it cannot
    hold up the installer; the database orders checks and bounds a claim."""
    from parishkit.stewardship.accounts.setup_checks import has_pending
    from parishkit.stewardship.accounts.setup_secret_models import (
        SetupSealedCredential,
    )

    request, attempt, private = collecting(setup_service)
    queue_check(request, setup_service, attempt.pk)
    row = SetupSourceCheck.objects.get()
    # The database, not the caller, set the check's creation time.
    assert row.created_at == row.updated_at
    with web_login(), work_transaction():
        SetupSealedCredential.objects.filter(target="parishsoft").update(
            version=F("version") + 1
        )
    assert not has_pending()
    assert not answer_check(private)
    assert SetupSourceCheck.objects.get().state == "queued"
    # A claim's deadline is at most five minutes after it starts.
    with target_login("parishsoft"):
        guard_refuses(
            "UPDATE stewardship_setup_source_check SET state='running', "
            "worker_id=gen_random_uuid(), started_at=now(), "
            "deadline_at=now()+interval '1 hour', version=version+1 WHERE id=%s",
            [row.pk],
        )
