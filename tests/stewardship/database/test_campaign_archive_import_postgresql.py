"""Importing past-campaign archives (#1018, slice 2), on PostgreSQL.

Every workbook is synthetic (``archive_factory``). The workbook check runs
in-process here (``in_process``): the isolated helper is covered by the
slice-1 tests, and this suite is about storage. Changes run as the
restricted web login, where the tables' guards and grants apply; the
command-line tests run the real ``pk-stewardship admin history`` commands.
"""

import hashlib
import json
import re
import threading
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.archive import imports
from parishkit.stewardship.archive.check_worker import document
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.archive_models import (
    CampaignArchive,
    CampaignArchiveBand,
    CampaignArchiveDay,
    CampaignArchiveFamily,
)
from parishkit.stewardship.storage import StaleRecordError

from ..archive_factory import campaign, set_metadata, workbook_bytes
from .auth_builders import signed_in
from .automation_builders import paired
from .test_admin_files_change_cli_postgresql import as_staff
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "src/parishkit/stewardship/schema/migrations/0059_campaign_archives.sql"
OLD_BODY = (
    ROOT
    / "src/parishkit/stewardship/schema/migrations/0050_directory_response_filters.sql"
)


def in_process(data, on_timeout=None):
    """The helper's document, computed here, as JSON would carry it."""
    return json.loads(json.dumps(document(data)))


@pytest.fixture(autouse=True)
def checked_here(monkeypatch):
    """Check workbooks in this process; no current campaign by default."""
    monkeypatch.setattr(imports, "check_archive", in_process)
    monkeypatch.setattr(imports, "stewardship_year", lambda: None)


def workbook(year=2054, *, final=True, note="Synthetic data for tests"):
    """A consistent synthetic archive for ``year``; ``note`` varies its bytes."""
    metadata, sheets = campaign()
    metadata = set_metadata(metadata, "campaign_year", year)
    metadata = set_metadata(metadata, "campaign_name", f"Stewardship {year}")
    metadata = set_metadata(metadata, "is_final", final)
    metadata = set_metadata(metadata, "note.01", note)
    return workbook_bytes(metadata, sheets)


@pytest.fixture
def administrator(auth_service, google):
    """The signed-in Administrator's id, and the store the service checks."""
    _, login = signed_in()
    assert login.status_code == 302
    return auth_service.store, PortalUser.objects.get(email="admin@example.org").pk


def as_web(call, *args, **kwargs):
    """Run one import call on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return call(*args, **kwargs)


def events(kind):
    """The contexts of the audit events of ``kind``, oldest first."""
    return [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(event_type=kind).order_by("created_at")
    ]


def test_add_already_replace_remove_and_reimport(administrator):
    """The whole lifecycle through the service, with its audit and rows."""
    store, actor = administrator
    first = workbook(2054)
    review = as_web(imports.review, store, actor, first, file_name="C:\\x\\a.xlsx")
    assert review.action == "add" and review.current is None
    archive = review.archive
    assert archive.state == "pending" and archive.file_name == "a.xlsx"
    assert archive.campaign_year == 2054 and archive.is_final
    days = CampaignArchiveDay.objects.filter(archive=archive).count()
    assert days == archive.days == len(campaign()[1]["daily"])
    assert CampaignArchiveBand.objects.filter(archive=archive).count() == 8
    applied = as_web(imports.apply, store, actor, archive.pk)
    assert applied.outcome == "imported"
    archive.refresh_from_db()
    assert archive.state == "active" and archive.version == 2
    assert archive.imported_by_id == actor and archive.imported_at is not None
    # The same file again: already imported, and its preview goes.
    again = as_web(imports.review, store, actor, first)
    assert again.action == "already" and again.current.pk == archive.pk
    applied = as_web(imports.apply, store, actor, again.archive.pk)
    assert applied.outcome == "already" and applied.archive.pk == archive.pk
    assert not CampaignArchive.objects.filter(pk=again.archive.pk).exists()
    # A corrected file for the year replaces it, only with the acknowledgement.
    corrected = as_web(imports.review, store, actor, workbook(2054, note="Corrected"))
    assert corrected.action == "replace" and corrected.current.pk == archive.pk
    with pytest.raises(imports.ReplaceNotAcknowledged):
        as_web(imports.apply, store, actor, corrected.archive.pk)
    applied = as_web(
        imports.apply, store, actor, corrected.archive.pk, replace_acknowledged=True
    )
    assert applied.outcome == "replaced" and applied.replaced.pk == archive.pk
    archive.refresh_from_db()
    assert archive.state == "removed" and archive.removed_by_id == actor
    # A tombstone keeps the year, checksum and counts, not the rows.
    for model in (CampaignArchiveDay, CampaignArchiveBand, CampaignArchiveFamily):
        assert not model.objects.filter(archive=archive).exists()
    assert archive.families_responded and archive.checksum
    # Remove needs the current version; then the year can be imported again.
    current = applied.archive
    with pytest.raises(StaleRecordError):
        as_web(imports.remove, store, actor, 2054, expected_version=1)
    as_web(imports.remove, store, actor, 2054, expected_version=current.version)
    with pytest.raises(LookupError):
        as_web(imports.remove, store, actor, 2054, expected_version=3)
    review = as_web(imports.review, store, actor, first)
    assert review.action == "add"
    assert as_web(imports.apply, store, actor, review.archive.pk).outcome == "imported"
    assert [row.state for row in imports.archives()] == [
        "active",
        "removed",
        "removed",
    ]
    # Audit: year, checksum, counts and outcome; never a note or a Family.
    imported = events("campaign_archive_imported")
    assert len(imported) == 3
    assert set(imported[0]) == {
        "outcome",
        "archive_year",
        "file_fingerprint",
        "count",
        "archive_days",
        "archive_family_rows",
        "version",
    }
    assert imported[0]["archive_year"] == 2054
    assert len(events("campaign_archive_replaced")) == 1
    assert len(events("campaign_archive_removed")) == 1
    assert len(events("campaign_archive_previewed")) == 4


def test_family_rows_hold_only_the_renewal_fields(administrator):
    """Kept rows are id system, id and first day; unticked keeps none."""
    store, actor = administrator
    kept = as_web(imports.review, store, actor, workbook(2053)).archive
    rows = CampaignArchiveFamily.objects.filter(archive=kept)
    assert rows.count() == len(campaign()[1]["families"])
    stored = {
        field.column
        for field in CampaignArchiveFamily._meta.get_fields()
        if getattr(field, "column", None)
    }
    assert stored == {
        "id",
        "created_at",
        "actor_id",
        "correlation_id",
        "archive_id",
        "family_id_system",
        "family_id",
        "first_campaign_day",
    }
    assert {row.family_id_system for row in rows} == {"parishsoft_duid"}
    none = as_web(
        imports.review, store, actor, workbook(2052), keep_family_rows=False
    ).archive
    assert not none.family_rows_kept
    assert not CampaignArchiveFamily.objects.filter(archive=none).exists()
    # Bands and quartiles are the helper's, from the families sheet.
    pledges = sorted(
        family["annual_pledge"]
        for family in campaign()[1]["families"]
        if family["pledge_status"] == "pledged"
    )
    assert kept.pledging_families == len(pledges)
    assert kept.annual_pledged == sum(pledges, Decimal(0))
    bands = CampaignArchiveBand.objects.filter(archive=kept)
    assert sum(band.families for band in bands) == len(pledges)
    assert kept.first_quartile <= kept.median <= kept.third_quartile


def test_a_partial_file_never_replaces_a_final_one(administrator):
    """Review refuses it with the reason; Apply refuses it too."""
    store, actor = administrator
    final = as_web(imports.review, store, actor, workbook(2051)).archive
    as_web(imports.apply, store, actor, final.pk)
    partial = workbook(2051, final=False)
    with pytest.raises(imports.ArchiveRefusal) as refused:
        as_web(imports.review, store, actor, partial)
    assert refused.value.problems[0]["code"] == "partial_over_final"
    # The refusal is recorded as a denied Review, with its year; no preview.
    assert events("campaign_archive_previewed")[-1] == {
        "outcome": "denied",
        "file_fingerprint": hashlib.sha256(partial).hexdigest(),
        "archive_year": 2051,
    }
    assert CampaignArchive.objects.filter(state="pending").count() == 0
    # A final file replaces a partial one.
    as_web(imports.remove, store, actor, 2051, expected_version=2)
    partial = as_web(imports.review, store, actor, workbook(2051, final=False))
    as_web(imports.apply, store, actor, partial.archive.pk)
    later = as_web(imports.review, store, actor, workbook(2051, note="Final"))
    assert later.action == "replace"


def test_an_unresolvable_year_is_a_denied_review(administrator, monkeypatch):
    """year_unresolved is recorded like any refused Review, with no year."""
    store, actor = administrator

    def unresolved():
        """The current campaign's year cannot be resolved."""
        raise imports.refusal("year_unresolved", "cannot be resolved")

    monkeypatch.setattr(imports, "stewardship_year", unresolved)
    data = workbook(2040)
    with pytest.raises(imports.ArchiveRefusal) as refused:
        as_web(imports.review, store, actor, data)
    assert refused.value.problems[0]["code"] == "year_unresolved"
    assert events("campaign_archive_previewed") == [
        {"outcome": "denied", "file_fingerprint": hashlib.sha256(data).hexdigest()}
    ]
    assert not CampaignArchive.objects.exists()


def test_refusals_store_nothing(administrator, monkeypatch):
    """A refused file, the current campaign's year and a non-Administrator."""
    store, actor = administrator
    with pytest.raises(imports.ArchiveRefusal) as refused:
        as_web(imports.review, store, actor, b"not a workbook")
    assert refused.value.problems[0]["code"] in {"unreadable", "not_zip"}
    monkeypatch.setattr(imports, "stewardship_year", lambda: 2054)
    with pytest.raises(imports.ArchiveRefusal) as refused:
        as_web(imports.review, store, actor, workbook(2054))
    assert refused.value.problems[0]["code"] == "current_campaign_year"
    assert not CampaignArchive.objects.exists()
    # Both refusals are recorded, as denied, with no year for the unreadable.
    denied = events("campaign_archive_previewed")
    assert [entry["outcome"] for entry in denied] == ["denied", "denied"]
    assert "archive_year" not in denied[0] and denied[1]["archive_year"] == 2054

    def staff(store, actor_id):
        """A principal with Staff alone."""
        raise PermissionError("Campaign archives are the Administrator's.")

    monkeypatch.setattr(imports, "authorize", staff)
    with pytest.raises(PermissionError):
        as_web(imports.review, store, actor, workbook(2050))


def test_an_expired_preview_is_cleaned_and_stale(administrator, monkeypatch):
    """The next Review deletes it, through the trigger's admitted delete."""
    store, actor = administrator
    old = as_web(imports.review, store, actor, workbook(2049)).archive
    monkeypatch.setattr(imports, "PREVIEW_SECONDS", 0)
    with pytest.raises(StaleRecordError):
        as_web(imports.apply, store, actor, old.pk)
    as_web(imports.review, store, actor, workbook(2048))
    assert not CampaignArchive.objects.filter(pk=old.pk).exists()
    assert not CampaignArchiveDay.objects.filter(archive_id=old.pk).exists()


def test_a_preview_is_the_reviewers_own(administrator, monkeypatch):
    """Another Administrator's token is stale, not applied."""
    store, actor = administrator
    preview = as_web(imports.review, store, actor, workbook(2047)).archive
    # Any Administrator passes the role check; the preview stays bound.
    monkeypatch.setattr(imports, "authorize", lambda store, actor_id: None)
    with pytest.raises(StaleRecordError):
        as_web(imports.apply, store, uuid4(), preview.pk)


def test_racing_applies_have_one_winner(administrator, monkeypatch):
    """Two previews of the same year applied at once: the second is stale."""
    store, actor = administrator
    first = as_web(imports.review, store, actor, workbook(2046)).archive
    second = as_web(imports.review, store, actor, workbook(2046, note="B")).archive
    holding, release = threading.Event(), threading.Event()
    real_audit = imports._audit
    outcomes = {}

    def slow_audit(action, *args):
        """Hold the first Apply's transaction open after its update."""
        real_audit(action, *args)
        if threading.current_thread().name == "first":
            holding.set()
            release.wait(10)

    monkeypatch.setattr(imports, "_audit", slow_audit)

    def apply(name, archive):
        """Apply on this thread's own connection (the web login's), keeping
        the outcome or the error."""
        try:
            outcomes[name] = imports.apply(store, actor, archive.pk)
        except Exception as error:
            outcomes[name] = error
        finally:
            connection.close()

    # The login is switched once, here; each thread connects as it.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        one = threading.Thread(target=apply, args=("first", first), name="first")
        one.start()
        assert holding.wait(10)
        two = threading.Thread(target=apply, args=("second", second), name="second")
        two.start()
        two.join(2)
        release.set()
        one.join(10)
        two.join(10)
    assert outcomes["first"].outcome == "imported"
    assert isinstance(outcomes["second"], StaleRecordError)
    assert CampaignArchive.objects.filter(state="active").count() == 1


def test_housekeeping_skips_a_preview_an_apply_holds(administrator, monkeypatch):
    """An Apply at the edge of a preview's lifetime keeps the preview's rows.

    The Apply locks the preview and makes it active; while its transaction
    is open, another Review's housekeeping finds the preview expired. It
    must skip the locked row, not delete the rows of what is about to be an
    active import.
    """
    store, actor = administrator
    preview = as_web(imports.review, store, actor, workbook(2038)).archive
    days = CampaignArchiveDay.objects.filter(archive=preview).count()
    families = CampaignArchiveFamily.objects.filter(archive=preview).count()
    assert days and families
    holding, release = threading.Event(), threading.Event()
    real_audit = imports._audit
    outcomes = {}

    def slow_audit(action, *args):
        """Hold the Apply's transaction open after it made the preview active."""
        real_audit(action, *args)
        if threading.current_thread().name == "apply":
            holding.set()
            release.wait(10)

    monkeypatch.setattr(imports, "_audit", slow_audit)

    def run(name, call, *args):
        """Run one service call on this thread's own (web) connection."""
        try:
            outcomes[name] = call(store, actor, *args)
        except Exception as error:
            outcomes[name] = error
        finally:
            connection.close()

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        applying = threading.Thread(
            target=run, args=("apply", imports.apply, preview.pk), name="apply"
        )
        applying.start()
        assert holding.wait(10)
        # Every preview now counts as expired for the next Review.
        monkeypatch.setattr(imports, "PREVIEW_SECONDS", 0)
        reviewing = threading.Thread(
            target=run, args=("review", imports.review, workbook(2037)), name="review"
        )
        reviewing.start()
        reviewing.join(5)
        # Housekeeping skipped the locked preview instead of waiting on it.
        assert not reviewing.is_alive()
        release.set()
        applying.join(10)
    assert outcomes["apply"].outcome == "imported", outcomes
    assert isinstance(outcomes["review"], imports.Review), outcomes
    preview.refresh_from_db()
    assert preview.state == "active"
    assert CampaignArchiveDay.objects.filter(archive=preview).count() == days
    assert CampaignArchiveFamily.objects.filter(archive=preview).count() == families


def test_a_child_delete_waits_for_the_apply_and_is_refused(administrator, monkeypatch):
    """The child guard locks the archive, so a racing delete cannot strip it.

    A direct delete of a preview's rows, while an Apply holds the preview,
    waits for the Apply and then finds the archive active: refused.
    """
    store, actor = administrator
    preview = as_web(imports.review, store, actor, workbook(2036)).archive
    days = CampaignArchiveDay.objects.filter(archive=preview).count()
    holding, release = threading.Event(), threading.Event()
    real_audit = imports._audit
    outcomes = {}

    def slow_audit(action, *args):
        """Hold the Apply's transaction open after it made the preview active."""
        real_audit(action, *args)
        if threading.current_thread().name == "apply":
            holding.set()
            release.wait(10)

    monkeypatch.setattr(imports, "_audit", slow_audit)

    def apply():
        """The Apply, on its own (web) connection."""
        try:
            outcomes["apply"] = imports.apply(store, actor, preview.pk)
        finally:
            connection.close()

    def delete():
        """The racing delete of the preview's day rows."""
        try:
            outcomes["delete"] = refusal_of(
                executing(
                    "DELETE FROM stewardship_campaign_archive_day WHERE archive_id=%s",
                    [preview.pk],
                )
            )
        except BaseException as error:
            outcomes["delete"] = error
        finally:
            connection.close()

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        applying = threading.Thread(target=apply, name="apply")
        applying.start()
        assert holding.wait(10)
        deleting = threading.Thread(target=delete, name="delete")
        deleting.start()
        deleting.join(2)
        # The delete waits on the Apply's lock.
        assert deleting.is_alive()
        release.set()
        applying.join(10)
        deleting.join(10)
    assert outcomes["apply"].outcome == "imported"
    code, message = outcomes["delete"]
    assert code == GUARDED and CHILD_CHANGE in message
    assert CampaignArchiveDay.objects.filter(archive=preview).count() == days


GUARDED = "23514"
UNGRANTED = "42501"
# Each guard's own words, so a check constraint (also 23514) cannot pass
# for a guard.
ARCHIVE_UPDATE = "only becomes active, then removed"
ARCHIVE_DELETE = "kept until removed"
CHILD_CHANGE = "kept until their archive goes"
CHILD_INSERT = "added only to its preview"


def refusal_of(call):
    """The SQLSTATE and message of the database error ``call()`` raises."""
    with pytest.raises(DatabaseError) as caught, transaction.atomic():
        call()
    cause = caught.value.__cause__
    return cause.sqlstate, str(cause)


def executing(statement, parameters):
    """A call that runs one statement on the current connection."""

    def call():
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)

    return call


def test_guards_refuse_every_other_change(administrator):
    """The guards refuse each change, on a login whose grants allow it.

    Each statement runs on the owning login, which holds every grant, so
    only the guard can refuse it (SQLSTATE 23514 with the guard's words);
    then on the web login, where a column it is not granted is refused by
    the grants (42501) before any guard runs.
    """
    store, actor = administrator
    review = as_web(imports.review, store, actor, workbook(2045))
    archive = review.archive
    as_web(imports.apply, store, actor, archive.pk)
    # (statement, parameters, the guard's words, the web login's SQLSTATE)
    statements = [
        # An active archive's figures, state or attribution never change.
        (
            "UPDATE stewardship_campaign_archive SET version=version+1,"
            " active_families=1 WHERE id=%s",
            [archive.pk],
            ARCHIVE_UPDATE,
            UNGRANTED,
        ),
        (
            "UPDATE stewardship_campaign_archive SET version=version+1,"
            " state='pending', imported_by_id=NULL, imported_at=NULL WHERE id=%s",
            [archive.pk],
            ARCHIVE_UPDATE,
            GUARDED,
        ),
        # Removal without a new version, or by someone who is no Administrator.
        (
            "UPDATE stewardship_campaign_archive SET state='removed',"
            " removed_by_id=%s, removed_at=now() WHERE id=%s",
            [actor, archive.pk],
            ARCHIVE_UPDATE,
            GUARDED,
        ),
        (
            "UPDATE stewardship_campaign_archive SET version=version+1,"
            " state='removed', removed_by_id=%s, removed_at=now() WHERE id=%s",
            [uuid4(), archive.pk],
            ARCHIVE_UPDATE,
            GUARDED,
        ),
        (
            "DELETE FROM stewardship_campaign_archive WHERE id=%s",
            [archive.pk],
            ARCHIVE_DELETE,
            GUARDED,
        ),
        (
            "DELETE FROM stewardship_campaign_archive_day WHERE archive_id=%s",
            [archive.pk],
            CHILD_CHANGE,
            GUARDED,
        ),
        (
            "DELETE FROM stewardship_campaign_archive_family WHERE archive_id=%s",
            [archive.pk],
            CHILD_CHANGE,
            GUARDED,
        ),
        (
            "UPDATE stewardship_campaign_archive_band SET families=families+1"
            " WHERE archive_id=%s",
            [archive.pk],
            CHILD_CHANGE,
            UNGRANTED,
        ),
        # Rows are added only to one's own preview.
        (
            "INSERT INTO stewardship_campaign_archive_family"
            " (id,created_at,actor_id,correlation_id,archive_id,family_id_system,"
            "family_id,first_campaign_day)"
            " VALUES (gen_random_uuid(),now(),%s,gen_random_uuid(),%s,"
            "'parishsoft_duid',99,1)",
            [actor, archive.pk],
            CHILD_INSERT,
            GUARDED,
        ),
    ]
    for statement, parameters, words, web in statements:
        code, message = refusal_of(executing(statement, parameters))
        assert (code, words in message) == (GUARDED, True), statement
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            code, message = refusal_of(executing(statement, parameters))
        assert code == web, statement
        if web == GUARDED:
            assert words in message, statement

    # A pending archive is started only by a current Administrator.
    def start():
        CampaignArchive.objects.create(
            **{
                field.attname: getattr(archive, field.attname)
                for field in CampaignArchive._meta.concrete_fields
                if field.attname
                not in {
                    "id",
                    "state",
                    "version",
                    "imported_by_id",
                    "imported_at",
                    "created_at",
                    "updated_at",
                }
            }
            | {"actor_id": uuid4()}
        )

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        code, message = refusal_of(start)
    assert code == GUARDED and "starts as a preview" in message
    archive.refresh_from_db()
    assert archive.state == "active" and archive.version == 2


def test_the_web_login_reads_no_family_value(administrator):
    """Family rows' id system, id and first day are not the web login's."""
    store, actor = administrator
    archive = as_web(imports.review, store, actor, workbook(2039)).archive
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for column in ("family_id_system", "family_id", "first_campaign_day"):
            code, _ = refusal_of(
                executing(
                    f"SELECT {column} FROM stewardship_campaign_archive_family",
                    [],
                )
            )
            assert code == UNGRANTED, column
        # Counting an archive's rows, as the service does, is allowed.
        assert imports._family_rows(archive) == len(campaign()[1]["families"])


def test_a_killed_check_leaves_a_durable_timeout_entry(administrator, monkeypatch):
    """The Review's kill hook writes helper_timed_out naming archive_check."""
    store, actor = administrator

    def killed(data, on_timeout=None):
        """A helper stopped at its deadline: the hook runs, then too large."""
        import time

        on_timeout(time.monotonic())
        return {
            "valid": False,
            "problems": [
                {
                    "code": "too_large_to_check",
                    "sheet": None,
                    "cell": None,
                    "rule": "the file is too large to check",
                }
            ],
            "more_problems": 0,
        }

    monkeypatch.setattr(imports, "check_archive", killed)
    with pytest.raises(imports.ArchiveRefusal):
        as_web(imports.review, store, actor, workbook(2044))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT context FROM stewardship_operational_log"
            " WHERE event='helper_timed_out'"
        )
        [(context,)] = cursor.fetchall()
    context = context if isinstance(context, dict) else json.loads(context)
    assert context["what"] == "archive_check"
    assert context["helper"] == "archive_check_worker"
    assert context["limit_seconds"] == 60 and "elapsed_seconds" in context


def test_one_review_at_a_time(administrator):
    """While the deployment-wide lock is held, a Review is busy at once."""
    store, actor = administrator
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s,%s)", list(imports.REVIEW_LOCK))
    held = threading.Event()
    result = {}

    def other():
        """A Review on another connection."""
        try:
            as_web(imports.review, store, actor, workbook(2043))
        except Exception as error:
            result["error"] = error
        finally:
            held.set()
            connection.close()

    thread = threading.Thread(target=other)
    thread.start()
    held.wait(10)
    thread.join(10)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s,%s)", list(imports.REVIEW_LOCK))
    assert isinstance(result["error"], imports.ReviewBusy)
    assert not CampaignArchive.objects.exists()


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def test_migration_self_check_refuses_the_old_context_body():
    """The DO block fails on 0050's context body; with 0059's it passes."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    old = OLD_BODY.read_text(encoding="utf-8")
    start = old.index("CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1")
    old_body = old[start : old.index("$_$;", start) + 4]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('stewardship.migration_0059_acl', (SELECT"
            " string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';'"
            " ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON"
            " n.oid=p.pronamespace WHERE n.nspname='public' AND"
            " p.proname='stewardship_safe_context_v1'), false)"
        )
        cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old_body)
            with (
                pytest.raises(DatabaseError, match="archive values"),
                transaction.atomic(),
            ):
                cursor.execute(check)
            raise Rollback
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(
                "DROP TRIGGER stewardship_campaign_archive_band_guard"
                " ON stewardship_campaign_archive_band"
            )
            with (
                pytest.raises(DatabaseError, match="not guarded as declared"),
                transaction.atomic(),
            ):
                cursor.execute(check)
            raise Rollback
        cursor.execute(check)


# ------------------------------------------------------------ command line


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """The real command line over the authentication service."""
    return runner(auth_service, monkeypatch)


def test_history_commands_through_the_page_service(admin, monkeypatch):
    """preview, confirm, list and remove; prompts, events and no values."""
    _, secret, _ = paired(admin.service)
    data = workbook(2042)
    code, document_, _ = admin("history", "preview", "-", secret=secret, answer=data)
    assert code == 0, document_
    preview = document_["result"]
    assert preview["action"] == "add" and preview["current"] is None
    assert preview["archive"]["year"] == 2042
    assert preview["archive"]["family_rows"] == len(campaign()[1]["families"])
    assert preview["notes_flagged"] == []
    token = preview["preview"]["token"]
    # Output holds no note text, Family key, pledge band, quartile or
    # pledging-Family count.
    text = json.dumps(document_)
    for absent in ("Synthetic data", "duid:", "quartile", "pledging", "band"):
        assert absent not in text, absent
    code, document_, _ = admin("history", "confirm", "--token", token, secret=secret)
    assert code == 0 and document_["result"]["outcome"] == "imported"
    # A replacement asks first; unanswered, nothing changes.
    workbook_new = workbook(2042, note="New")
    code, document_, _ = admin(
        "history", "preview", "-", secret=secret, answer=workbook_new
    )
    token = document_["result"]["preview"]["token"]
    assert document_["result"]["action"] == "replace"
    code, refused, errors = admin("history", "confirm", "--token", token, secret=secret)
    assert code == 4 and "This replaces the 2042 figures" in errors
    code, document_, _ = admin(
        "history", "confirm", "--token", token, secret=secret, answer=b"yes\n"
    )
    assert code == 0 and document_["result"]["outcome"] == "replaced"
    # The same file again: already imported; confirm says so, uses up the
    # preview and records its own event, as each confirm does.
    confirms = AuditEvent.objects.filter(event_type="admin_cmd_history_confirm")
    assert confirms.count() == 2
    code, document_, _ = admin(
        "history", "preview", "-", secret=secret, answer=workbook_new
    )
    assert code == 0 and document_["result"]["action"] == "already"
    token = document_["result"]["preview"]["token"]
    code, document_, _ = admin("history", "confirm", "--token", token, secret=secret)
    assert code == 0 and document_["result"]["outcome"] == "already"
    assert document_["result"]["replaced"] is None
    assert confirms.count() == 3
    code, stale, _ = admin("history", "confirm", "--token", token, secret=secret)
    assert code == 1 and stale["error"]["code"] == "stale_version"
    code, listed, _ = admin("history", "list", secret=secret)
    rows = listed["result"]["archives"]
    assert [row["state"] for row in rows] == ["active", "removed"]
    active = rows[0]
    code, stale, _ = admin(
        "history",
        "remove",
        "2042",
        "--expected-version",
        str(active["version"] + 5),
        "--yes",
        secret=secret,
    )
    assert code == 1 and stale["error"]["code"] == "stale_version"
    code, removed, _ = admin(
        "history",
        "remove",
        "2042",
        "--expected-version",
        str(active["version"]),
        "--yes",
        secret=secret,
    )
    assert code == 0 and removed["result"]["removed"]["state"] == "removed"
    # A refused file is invalid, naming cells and rules, never values.
    code, refused, _ = admin(
        "history", "preview", "-", secret=secret, answer=b"not a workbook"
    )
    assert code == 1 and refused["error"]["code"] == "invalid"
    assert refused["error"]["fields"][0]["field"] == "file"
    for kind in ("preview", "confirm", "remove"):
        assert AuditEvent.objects.filter(
            event_type=f"admin_cmd_history_{kind}"
        ).exists(), kind


def test_history_needs_an_administrator(admin, monkeypatch):
    """A Staff principal is denied before any file is read."""
    _, secret, _ = paired(admin.service)
    as_staff(monkeypatch)
    code, document_, _ = admin("history", "list", secret=secret)
    assert code == 1 and document_["error"]["code"] == "denied"
    code, document_, _ = admin(
        "history", "preview", "-", secret=secret, answer=workbook(2041)
    )
    assert code == 1 and document_["error"]["code"] == "denied"
    assert not CampaignArchive.objects.exists()
