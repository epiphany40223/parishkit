"""The setup ParishSoft organization lookup and choice (#1554, frozen 0174).

The web stages a key with no organization and queues its lookup; the
ParishSoft installer answers it under its own grants (with a canned helper
answer, never ParishSoft); the Administrator chooses. These tests run the
real services under their logins, each SQL refusal of the lookup's guard,
the load's organization check, the end-of-setup scrub, the page's states and
the migration's own DO block.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.accounts.setup_credentials import stage_credential
from parishkit.stewardship.accounts.setup_drafts import save_section, view_draft
from parishkit.stewardship.accounts.setup_lookup_models import (
    OPEN_STATES,
    SetupParishSoftLookup,
)
from parishkit.stewardship.accounts.setup_lookups import (
    choose_organization,
    chosen_name,
    current_lookup,
    discard_key,
    recover_pending,
    retry_lookup,
)
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential
from parishkit.stewardship.accounts.setup_source import start_source_load
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.accounts.setup_wizard import wizard_for
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from ..test_setup_forms import VALUES
from .setup_lookup_builders import answer_lookup, choose, found
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import CANDIDATE, staged
from .test_setup_exchange_postgresql import target_login
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0174_parishsoft_organization_choice.sql"
URL = "/admin/setup/credentials/parishsoft"
THREE = [
    {"id": 7, "name": "St. Example Parish", "families": True},
    {"id": 8, "name": "Diocesan School", "families": False},
    {"id": 9, "name": "Mission Chapel", "families": None},
]


def pending(service):
    """A staged key whose lookup has not been answered, with its request."""
    request, attempt, receipt, private = staged(service, choose=False)
    return request, attempt.attempt_id, receipt, private


def version(attempt_id):
    """The attempt's current version."""
    return SetupAttempt.objects.get(pk=attempt_id).version


def test_a_key_is_staged_without_an_organization_and_looked_up(setup_service):
    """Staging seals no settings and queues one lookup bound to the key."""
    request, attempt_id, receipt, private = pending(setup_service)
    assert receipt.settings == {}
    lookup = SetupParishSoftLookup.objects.get()
    assert (lookup.state, lookup.credential_id, lookup.credential_version) == (
        "queued",
        receipt.identifier,
        receipt.version,
    )
    assert lookup.fingerprint == receipt.fingerprint
    # The step is not done until an organization is chosen.
    with web_login():
        wizard = wizard_for(view_draft(request, setup_service, attempt_id))
    assert wizard.step("parishsoft").state != "done"
    assert answer_lookup(private) is True
    row = SetupParishSoftLookup.objects.get()
    assert row.state == "ready" and row.organizations == found()["organizations"]
    assert row.worker_id is not None and row.finished_at is not None


def test_choosing_writes_the_organization_scrubs_the_list_and_audits_counts(
    setup_service,
):
    """Only counts are audited; the other organizations are scrubbed."""
    request, attempt_id, _, private = pending(setup_service)
    answer_lookup(private, found(THREE))
    with web_login():
        lookup = current_lookup(attempt_id)
        assert [item.identifier for item in lookup.organizations] == [8, 9, 7]
        assert lookup.unknown
        with pytest.raises(ValueError):
            choose_organization(
                request,
                setup_service,
                attempt_id,
                lookup_id=lookup.identifier,
                organization_id=8,
                expected_version=version(attempt_id),
            )
        with pytest.raises(ValueError):
            choose_organization(
                request,
                setup_service,
                attempt_id,
                lookup_id=lookup.identifier,
                organization_id=1234,
                expected_version=version(attempt_id),
            )
    # Choosing an unknown organization is allowed.
    choose(request, setup_service, attempt_id, 9)
    credential = SetupSealedCredential.objects.defer("ciphertext").get()
    assert credential.settings == {"organization_id": 9}
    row = SetupParishSoftLookup.objects.get()
    assert (row.state, row.chosen_id, row.chosen_name) == (
        "chosen",
        9,
        "Mission Chapel",
    )
    assert row.organizations is None and row.scrubbed_at is not None
    event = AuditEvent.objects.get(event_type="setup_parishsoft_organization_chosen")
    assert event.subject_id == row.pk
    assert AuditContext.objects.get(event=event).context == {
        "organization": 9,
        "organizations_reached": 3,
        "organizations_without_families": 1,
        "organizations_unknown": 1,
    }
    with web_login():
        wizard = wizard_for(view_draft(request, setup_service, attempt_id))
    assert wizard.step("parishsoft").state == "done"


@pytest.mark.parametrize(
    "answer,code",
    [
        ({"outcome": "refused"}, "parishsoft_key_refused"),
        ({"outcome": "unavailable"}, "parishsoft_unavailable"),
        ({"outcome": "unreadable"}, "parishsoft_organizations_unreadable"),
        (found([]), "parishsoft_no_organizations"),
        (
            found([{"id": 1, "name": "School", "families": False}]),
            "parishsoft_no_family_organizations",
        ),
    ],
)
def test_a_failed_lookup_keeps_only_its_code(setup_service, answer, code):
    """No list is kept; the key stays staged without an organization."""
    _, _, _, private = pending(setup_service)
    answer_lookup(private, answer)
    row = SetupParishSoftLookup.objects.get()
    assert (row.state, row.error_code, row.organizations) == ("failed", code, None)


def test_the_load_needs_a_chosen_organization(setup_service):
    """Python and SQL both refuse a load for a key with no organization."""
    request, attempt_id, _, private = pending(setup_service)
    with web_login():
        status = save_section(
            request,
            setup_service,
            attempt_id,
            step="parish",
            values=VALUES["parish"],
            expected_version=version(attempt_id),
        )
        with pytest.raises(ValueError):
            start_source_load(
                request, setup_service, attempt_id, expected_version=status.version
            )
    from .test_setup_progress_postgresql import bind_load

    with pytest.raises(IntegrityError, match="chosen ParishSoft organization"):
        bind_load(attempt_id, claimed=False)
    answer_lookup(private)
    choose(request, setup_service, attempt_id)
    with web_login():
        task = start_source_load(
            request, setup_service, attempt_id, expected_version=version(attempt_id)
        )
    assert SetupAttempt.objects.get().source_task_id == task.run_id


def test_no_and_cancel_discard_a_new_key_but_keep_a_chosen_one(setup_service):
    """A key with no organization is scrubbed; the saved key pasted again stays."""
    request, attempt_id, _, private = pending(setup_service)
    answer_lookup(private)
    with web_login():
        discard_key(
            request,
            setup_service,
            attempt_id,
            lookup_id=current_lookup(attempt_id).identifier,
            expected_version=version(attempt_id),
        )
        assert current_lookup(attempt_id) is None
    credential = SetupSealedCredential.objects.get()
    assert credential.scrubbed_at is not None and credential.ciphertext is None
    # A new key, chosen; then the same key pasted again and cancelled.
    with web_login():
        stage_credential(
            request,
            setup_service,
            attempt_id,
            target="parishsoft",
            candidate=CANDIDATE,
            expected_version=version(attempt_id),
        )
    answer_lookup(private)
    choose(request, setup_service, attempt_id)
    kept = SetupSealedCredential.objects.get(scrubbed_at=None)
    with web_login():
        stage_credential(
            request,
            setup_service,
            attempt_id,
            target="parishsoft",
            candidate=CANDIDATE,
            expected_version=version(attempt_id),
        )
        discard_key(
            request,
            setup_service,
            attempt_id,
            lookup_id=current_lookup(attempt_id).identifier,
            expected_version=version(attempt_id),
        )
    again = SetupSealedCredential.objects.get(scrubbed_at=None)
    assert (again.pk, again.version, again.settings) == (
        kept.pk,
        kept.version,
        {"organization_id": 1},
    )


def test_check_again_repeats_the_lookup_with_the_staged_key(setup_service):
    """The finished lookup is discarded and a new one queued for the same key."""
    request, attempt_id, receipt, private = pending(setup_service)
    answer_lookup(private, {"outcome": "unavailable"})
    first = current_lookup(attempt_id)
    with web_login():
        retry_lookup(
            request,
            setup_service,
            attempt_id,
            lookup_id=first.identifier,
            expected_version=version(attempt_id),
        )
        with pytest.raises(StaleRecordError):
            retry_lookup(
                request,
                setup_service,
                attempt_id,
                lookup_id=first.identifier,
                expected_version=version(attempt_id),
            )
    assert SetupParishSoftLookup.objects.get(pk=first.identifier).state == "discarded"
    queued = SetupParishSoftLookup.objects.get(state="queued")
    assert queued.credential_version == receipt.version


def test_ending_setup_scrubs_an_open_lookup(setup_service):
    """Cancelling setup discards the lookup's list with the key."""
    request, attempt_id, _, private = pending(setup_service)
    answer_lookup(private, found(THREE))
    with web_login():
        cancel_setup(request, setup_service, attempt_id)
    row = SetupParishSoftLookup.objects.get()
    assert (row.state, row.organizations) == ("discarded", None)
    assert row.scrubbed_at is not None


def test_recovery_fails_a_claim_past_its_deadline(setup_service):
    """A stopped installer's claim ends as unavailable, for Try again."""
    _, _, _, _ = pending(setup_service)
    row = SetupParishSoftLookup.objects.get()
    now = database_now()
    with target_login("parishsoft"), work_transaction():
        SetupParishSoftLookup.objects.filter(pk=row.pk).update(
            state="running",
            worker_id=uuid4(),
            started_at=now - timedelta(minutes=5),
            deadline_at=now - timedelta(minutes=1),
            actor_id=None,
            version=F("version") + 1,
        )
        assert recover_pending() == 1
    row.refresh_from_db()
    assert (row.state, row.error_code) == ("failed", "parishsoft_unavailable")


@pytest.mark.parametrize(
    "change",
    [
        {"state": "ready", "organizations": found()["organizations"]},
        {"state": "chosen", "chosen_id": 1, "chosen_name": "St. Example Parish"},
    ],
)
def test_only_each_moves_owner_may_make_it(setup_service, change):
    """The web cannot answer a lookup; the installer cannot choose."""
    request, attempt_id, _, private = pending(setup_service)
    if change["state"] == "chosen":
        answer_lookup(private)
        login = target_login("parishsoft")
    else:
        login = web_login()
    with login, pytest.raises(DatabaseError), work_transaction():
        SetupParishSoftLookup.objects.update(
            **change, scrubbed_at=database_now(), version=F("version") + 1
        )


@pytest.mark.parametrize(
    "change",
    [
        {"chosen_id": 8, "chosen_name": "Diocesan School"},
        {"chosen_id": 1234, "chosen_name": None},
        {"chosen_id": 7, "chosen_name": "Another Parish"},
    ],
)
def test_sql_admits_only_a_listed_organization_with_its_name(setup_service, change):
    """Bypassing Python cannot choose a greyed, unlisted or renamed one."""
    request, attempt_id, _, private = pending(setup_service)
    answer_lookup(private, found(THREE))
    owner = SetupAttempt.objects.get().owner_id
    with web_login(), pytest.raises(DatabaseError), work_transaction():
        SetupParishSoftLookup.objects.update(
            state="chosen",
            organizations=None,
            scrubbed_at=database_now(),
            actor_id=owner,
            version=F("version") + 1,
            **change,
        )


def test_lookups_need_the_work_lock_and_are_never_deleted(setup_service):
    """Every write needs ordered ownership; rows are retained."""
    pending(setup_service)
    with pytest.raises(DatabaseError), transaction.atomic():
        SetupParishSoftLookup.objects.update(version=F("version") + 1)
    with pytest.raises(DatabaseError), transaction.atomic():
        SetupParishSoftLookup.objects.all().delete()


def test_the_page_lists_and_chooses_several_organizations(setup_http, google):
    """Greyed and unknown organizations, Check again, and a refused choice."""
    from .test_setup_credentials_postgresql import publish

    private = publish("parishsoft")
    with web_login():
        browser = started()
        post(
            browser,
            URL,
            {"candidate": CANDIDATE.decode(), "version": str(version_of())},
        )
    answer_lookup(private, found(THREE))
    with web_login():
        page = browser.get(URL)
        body = page.content.decode()
        assert "This key reaches 3 ParishSoft organizations." in body
        # Each note describes its radio, so a screen reader says why one
        # cannot be chosen.
        assert re.search(
            r'value="8" required disabled aria-describedby="organization-note-8">'
            r" Diocesan School \(8\)",
            body,
        )
        assert '<span class="help" id="organization-note-8">No Families in' in body
        assert 'aria-describedby="organization-note-9"' in body
        assert 'aria-describedby="organization-note-7"' not in body
        assert "No Families in ParishSoft" in body
        assert "ParishSoft did not say whether it has Families" in body
        assert 'value="retry"' in body and "Check again" in body
        assert "Use this organization" in body and ">Cancel<" in body
        assert "data-choice-filter" not in body
        lookup = current_lookup(SetupAttempt.objects.get().pk)
        values = {
            "action": "choose",
            "lookup": str(lookup.identifier),
            "version": str(version_of()),
        }
        refused = post(browser, URL, values | {"organization": "8"})
        assert refused.status_code == 400
        assert b"Choose one of the listed organizations that has Families" in (
            refused.content.replace(b"&#x27;", b"'")
        )
        chosen = post(browser, URL, values | {"organization": "7"})
        assert chosen.status_code == 302
        page = browser.get(URL)
        assert b"A ParishSoft API key is saved for St. Example Parish (7)." in (
            page.content
        )


def test_a_failed_lookup_is_shown_at_the_key_field(setup_http, google):
    """The error's words at the key field; Try again only when unreachable."""
    from .test_setup_credentials_postgresql import publish

    private = publish("parishsoft")
    with web_login():
        browser = started()
        post(
            browser,
            URL,
            {"candidate": CANDIDATE.decode(), "version": str(version_of())},
        )
    answer_lookup(private, {"outcome": "unavailable"})
    with web_login():
        page = browser.get(URL).content.replace(b"&#x27;", b"'")
        assert b"ParishSoft could not be reached. Try again in a few minutes." in page
        assert b'name="candidate"' in page and b"Try again" in page
        lookup = current_lookup(SetupAttempt.objects.get().pk)
        retried = post(
            browser,
            URL,
            {
                "action": "retry",
                "lookup": str(lookup.identifier),
                "version": str(version_of()),
            },
        )
        assert retried.status_code == 302 and retried["Location"] == URL
    answer_lookup(private, {"outcome": "refused"})
    with web_login():
        page = browser.get(URL).content.replace(b"&#x27;", b"'")
        assert b"ParishSoft did not accept this key." in page
        assert b"Try again" not in page


def test_more_than_twenty_organizations_add_a_filter_box(setup_http, google):
    """The type-to-filter box appears only above 20 organizations."""
    from .test_setup_credentials_postgresql import publish

    private = publish("parishsoft")
    with web_login():
        browser = started()
        post(
            browser,
            URL,
            {"candidate": CANDIDATE.decode(), "version": str(version_of())},
        )
    many = [{"id": n, "name": f"Parish {n}", "families": True} for n in range(1, 22)]
    answer_lookup(private, found(many))
    with web_login():
        body = browser.get(URL).content.decode()
    assert 'data-choice-filter="organization-choices"' in body
    assert body.count('name="organization"') == 21


def test_keeping_the_saved_key_after_a_failed_lookup_clears_the_lookup(
    setup_http, google
):
    """The saved key pasted again, its lookup failed, then kept by leaving
    the field empty: the failed lookup goes, the key keeps its organization,
    and nothing holds Check parish data back."""
    from .test_setup_credentials_postgresql import publish

    private = publish("parishsoft")
    with web_login():
        browser = started()
        post(
            browser,
            URL,
            {"candidate": CANDIDATE.decode(), "version": str(version_of())},
        )
    answer_lookup(
        private, found([{"id": 7, "name": "St. Example Parish", "families": True}])
    )
    with web_login():
        lookup = current_lookup(SetupAttempt.objects.get().pk)
        chosen = post(
            browser,
            URL,
            {
                "action": "choose",
                "lookup": str(lookup.identifier),
                "organization": "7",
                "version": str(version_of()),
            },
        )
        assert chosen.status_code == 302
        post(
            browser,
            URL,
            {"candidate": CANDIDATE.decode(), "version": str(version_of())},
        )
    kept = SetupSealedCredential.objects.get(scrubbed_at=None)
    answer_lookup(private, {"outcome": "refused"})
    with web_login():
        page = browser.get(URL).content.replace(b"&#x27;", b"'")
        assert b"ParishSoft did not accept this key." in page
        assert b"To keep it, leave the key field empty" in page
        # One region in every state, so the wait's re-read swaps this in.
        assert b'id="parishsoft-connection" data-in-place-region' in page
        answered = post(browser, URL, {"candidate": "", "version": str(version_of())})
        assert answered.status_code == 302 and answered["Location"] != URL
    assert not SetupParishSoftLookup.objects.filter(state__in=OPEN_STATES).exists()
    again = SetupSealedCredential.objects.get(scrubbed_at=None)
    assert (again.pk, again.version, again.settings) == (
        kept.pk,
        kept.version,
        {"organization_id": 7},
    )


@pytest.mark.parametrize("revoked", [False, True])
def test_web_lookup_writes_need_the_owners_live_sign_in(setup_service, revoked):
    """Bypassing Python, the web can neither queue a lookup for the staged
    key nor choose while the owner's setup sign-in is revoked (#1554)."""
    request, attempt_id, _, private = pending(setup_service)
    answer_lookup(private)
    choose(request, setup_service, attempt_id)
    credential = SetupSealedCredential.objects.defer("ciphertext").get()
    owner = SetupAttempt.objects.get().owner_id
    if revoked:
        PortalSession.objects.filter(revoked_at__isnull=True).update(
            revoked_at=database_now(), version=F("version") + 1
        )

    def queue():
        """Queue a lookup for the staged key, as the saved key pasted again does."""
        with web_login(), work_transaction():
            return SetupParishSoftLookup.objects.create(
                id=uuid4(),
                attempt_id=attempt_id,
                credential_id=credential.pk,
                credential_version=credential.version,
                fingerprint=credential.fingerprint,
                actor_id=owner,
            )

    if revoked:
        with pytest.raises(DatabaseError, match="staged key and Administrator"):
            queue()
        return
    row = queue()
    answer_lookup(private)
    row.refresh_from_db()
    assert row.state == "ready"
    # The organization the key already has, chosen again: live sign-in
    # only (revoked now, so refused).
    PortalSession.objects.filter(revoked_at__isnull=True).update(
        revoked_at=database_now(), version=F("version") + 1
    )
    with (
        web_login(),
        pytest.raises(DatabaseError, match="Only the setup's Administrator"),
        work_transaction(),
    ):
        SetupParishSoftLookup.objects.filter(pk=row.pk).update(
            state="chosen",
            chosen_id=1,
            chosen_name="St. Example Parish",
            organizations=None,
            scrubbed_at=database_now(),
            actor_id=owner,
            version=F("version") + 1,
        )


def test_a_new_choice_clears_the_name_an_earlier_one_kept(setup_service):
    """Choosing again (here another organization for the saved key pasted
    again) keeps only the latest choice's name; SQL clears no other."""
    request, attempt_id, _, private = pending(setup_service)
    answer_lookup(private)
    choose(request, setup_service, attempt_id)
    first = SetupParishSoftLookup.objects.get(state="chosen")
    assert chosen_name(attempt_id, 1) == "St. Example Parish"
    owner = SetupAttempt.objects.get().owner_id
    # The only choice keeps its name: nothing later replaced it.
    with web_login(), pytest.raises(DatabaseError), work_transaction():
        SetupParishSoftLookup.objects.filter(pk=first.pk).update(
            chosen_name=None, actor_id=owner, version=F("version") + 1
        )
    with web_login():
        stage_credential(
            request,
            setup_service,
            attempt_id,
            target="parishsoft",
            candidate=CANDIDATE,
            expected_version=version(attempt_id),
        )
    answer_lookup(private, found([{"id": 2, "name": "Other Parish", "families": True}]))
    choose(request, setup_service, attempt_id, 2)
    first.refresh_from_db()
    assert (first.state, first.chosen_id, first.chosen_name) == ("chosen", 1, None)
    assert chosen_name(attempt_id, 1) is None
    assert chosen_name(attempt_id, 2) == "Other Parish"
    latest = SetupParishSoftLookup.objects.get(state="chosen", chosen_id=2)
    # A chosen lookup changes in no other way, and the latest keeps its name.
    for pk, change in (
        (first.pk, {"chosen_id": 3}),
        (latest.pk, {"chosen_name": None}),
    ):
        with web_login(), pytest.raises(DatabaseError), work_transaction():
            SetupParishSoftLookup.objects.filter(pk=pk).update(
                **change, actor_id=owner, version=F("version") + 1
            )


def version_of():
    """The one attempt's current version."""
    return SetupAttempt.objects.get().version


def check_block():
    """The migration file's closing DO block, after its grants snapshot.

    The block compares the replaced functions' grants with the snapshot the
    file takes first, in a transaction-local setting; both run together.
    0177 (#2163) later made the setup load's organization check SECURITY
    DEFINER, so the block first puts back 0174's caller's rights (each
    caller runs it in a transaction that rolls back), or every run would
    fail on that alone and the refusals below would prove nothing.
    """
    text = FROZEN.read_text(encoding="utf-8")
    start = text.index("SELECT set_config('stewardship.migration_0174_acl'")
    snapshot = text[start : text.index(";\n", start) + 1]
    invoker = (
        "ALTER FUNCTION public.stewardship_setup_load_organization_v1() "
        "SECURITY INVOKER;"
    )
    return invoker + "\n" + snapshot + "\n" + text[text.index("DO $check$") :]


def test_the_do_block_passes_installed_and_refuses_each_missing_part(db):
    """Run the block as installed, then against each part taken away."""
    block = check_block()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(block)
        # The block's first statement put back 0174's rights; keep 0177's.
        transaction.set_rollback(True)
    for undo in (
        "DROP TRIGGER stewardship_setup_scrub_lookups "
        "ON public.stewardship_setup_attempt",
        "ALTER FUNCTION public.stewardship_setup_parishsoft_lookup_guard_v1() "
        "SECURITY INVOKER",
        "ALTER TABLE public.stewardship_setup_parishsoft_lookup "
        "DISABLE TRIGGER stewardship_setup_parishsoft_lookup_guard",
    ):
        with (
            pytest.raises(DatabaseError, match="Migration 0174"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(undo)
            cursor.execute(block)
    # The setup credential guard's baseline body before 0174.
    baseline = (SCHEMA / "functions.sql").read_text(encoding="utf-8")
    assert "IF keys IS NOT NULL AND (keys IS DISTINCT FROM" in baseline
    old = (
        Path(__file__).resolve().parents[3]
        / "src/parishkit/stewardship/schema/migrations/0114_no_sign_in_age.sql"
    ).read_text(encoding="utf-8")
    start = old.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_setup_secret_guard_v1()"
    )
    end = old.index("END $_$;", start) + len("END $_$;")
    with (
        pytest.raises(DatabaseError, match="Migration 0174"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(old[start:end])
        cursor.execute(block)
    # The audit context check's body before 0174 (0122's).
    old = (
        Path(__file__).resolve().parents[3]
        / "src/parishkit/stewardship/schema/migrations/0122_automatic_report_views.sql"
    ).read_text(encoding="utf-8")
    start = old.index("CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(")
    end = old.index("END $_$;", start) + len("END $_$;")
    with (
        pytest.raises(DatabaseError, match="Migration 0174"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(old[start:end])
        cursor.execute(block)
    # The lookup guard without its live sign-in checks.
    text = FROZEN.read_text(encoding="utf-8")
    start = text.index(
        "CREATE FUNCTION public.stewardship_setup_parishsoft_lookup_guard_v1()"
    )
    end = text.index("END $$;", start) + len("END $$;")
    weaker = text[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    weaker = weaker.replace(
        "OR NOT public.stewardship_setup_owner_live_v1(NEW.attempt_id)", ""
    )
    assert weaker != text[start:end]
    with (
        pytest.raises(DatabaseError, match="Migration 0174"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(weaker)
        cursor.execute(block)
