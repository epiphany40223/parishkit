"""The corpus evidence checks start from the snapshot they check (#629).

``stewardship_source_corpus_evidence`` proves a snapshot's relationships and
contact/address owners resolve inside that snapshot. The snapshot guard calls
it for every ``ready`` (full refresh) and ``unchanged`` (quarter-hourly quick
update) transition. Before migration 0044 its checks were planned from every
retained payload, so an unchanged snapshot (no rows) read every membership row
of every retained snapshot. These tests run each refusal (including references
into an empty collection) against both the new and the previous definition
(kept as test data beside this file), show the two return the same evidence,
show an empty snapshot now reads none of the history and a full refresh a
bounded multiple of one corpus, and check the frozen file's DO block refuses
the previous body.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.source.leases import _now, acquire_source
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.snapshots import begin_snapshot, stage_entities
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from .refresh_harness import force_unchanged, promoted_full
from .source_builders import running_source_task, source_corpus
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    ROOT
    / "src/parishkit/stewardship/schema/migrations"
    / "0044_corpus_evidence_from_snapshot.sql"
)
PREVIOUS = Path(__file__).with_name("corpus_evidence_before_0044.sql")


def definition(text, keyword):
    """The function definition in ``text``, from ``keyword`` to ``$_$;``."""
    start = text.index(keyword + " public.stewardship_source_corpus_evidence(")
    return text[start : text.index("$_$;", start) + len("$_$;")]


def new_definition():
    """The frozen file's CREATE OR REPLACE of the function."""
    return definition(
        MIGRATION.read_text(encoding="utf-8"), "CREATE OR REPLACE FUNCTION"
    )


def previous_definition():
    """The pre-0044 body, as a CREATE OR REPLACE."""
    return "CREATE OR REPLACE " + definition(
        PREVIOUS.read_text(encoding="utf-8"), "CREATE FUNCTION"
    ).removeprefix("CREATE ")


@contextmanager
def installed(version):
    """Run with ``version`` ("new" or "previous") installed; restore the new one.

    The function lives in the shared test database, which transactional tests
    do not roll back, so the new body is always put back afterwards.
    """
    with connection.cursor() as cursor:
        cursor.execute(new_definition() if version == "new" else previous_definition())
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(new_definition())


def evidence(snapshot_id):
    """The function's evidence for one snapshot."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_source_corpus_evidence(%s)", [snapshot_id]
        )
        return json.loads(cursor.fetchone()[0])


def stage_all(snapshot, claim, corpus):
    """Stage every collection of ``corpus`` in bounded batches."""
    for kind, entities in corpus.items():
        items = list(entities.items())
        for start in range(0, len(items), 500):
            stage_entities(
                snapshot.pk,
                claim,
                kind=kind,
                entities=dict(items[start : start + 500]),
                admit=permit,
            )


# Each reference the function resolves: the collection, the field set to an
# unknown key, and the refusal it must raise.
REFERENCES = {
    "member.family_key": ("member", "family_key", "unresolved relationships"),
    "roster.member_key": ("roster", "member_key", "unresolved relationships"),
    "roster.ministry_key": ("roster", "ministry_key", "unresolved relationships"),
    "pledge.family_key": ("pledge", "family_key", "unresolved relationships"),
    "pledge.fund_key": ("pledge", "fund_key", "unresolved relationships"),
    "contribution.family_key": (
        "contribution",
        "family_key",
        "unresolved relationships",
    ),
    "contribution.fund_key": ("contribution", "fund_key", "unresolved relationships"),
    "contact.owner_key": ("contact", "owner_key", "unresolved contact owner"),
    "address.owner_key": ("address", "owner_key", "unresolved contact owner"),
    "contact.owner_kind": ("contact", "owner_kind", "unresolved contact owner"),
}


@pytest.mark.parametrize("version", ["new", "previous"])
@pytest.mark.parametrize("reference", [None, *REFERENCES])
def test_each_unresolved_reference_is_refused_alike(version, reference):
    """Both definitions refuse the same corpora, called directly and by the guard.

    A staged delta snapshot holds a corpus with one broken reference (or
    none); see ``assert_refused`` for what is checked.
    """
    corpus = source_corpus()
    message = None
    if reference is not None:
        kind, field, message = REFERENCES[reference]
        # "family" is a valid owner kind whose key the snapshot lacks.
        broken = "family" if field == "owner_kind" else "missing"
        next(iter(corpus[kind].values()))[field] = broken
    assert_refused(version, corpus, message)


# Corpora whose parent collection is entirely empty while a child still
# references it: the collections to empty and the refusal expected. The new
# body skips a check only when its child collection is empty, never its
# parent, so these must still be refused.
EMPTY_PARENTS = {
    # Roster rows (and nothing else) name Members; there are none.
    "roster without members": (("member", "contact"), "unresolved relationships"),
    # A Member's contact with no Families or Members at all; everything else
    # that references them is emptied too, so only the owner check fails.
    "contact without families or members": (
        ("family", "member", "address", "roster", "pledge", "contribution"),
        "unresolved contact owner",
    ),
    # The same for a Family's address.
    "address without families or members": (
        ("family", "member", "contact", "roster", "pledge", "contribution"),
        "unresolved contact owner",
    ),
}


@pytest.mark.parametrize("version", ["new", "previous"])
@pytest.mark.parametrize("case", EMPTY_PARENTS)
def test_a_reference_into_an_empty_collection_is_refused_alike(version, case):
    """An empty parent collection does not skip its children's checks."""
    emptied, message = EMPTY_PARENTS[case]
    corpus = source_corpus()
    for kind in emptied:
        corpus[kind] = {}
    assert_refused(version, corpus, message)


def assert_refused(version, corpus, message):
    """Stage ``corpus`` and check ``version`` refuses it with ``message``.

    The function itself, the guard's ``ready`` transition and its
    ``unchanged`` transition must all refuse it with the same message; with
    ``message`` None the corpus is valid, passes the function and is refused
    by the guard only for its deliberately wrong evidence.
    """
    promoted_full()
    claim = acquire_source(**running_source_task(), phase="delta")
    snapshot = begin_snapshot(claim, organization_id=5, admit=permit)
    stage_all(snapshot, claim, corpus)
    with installed(version):
        if message is None:
            counts = evidence(snapshot.pk)["counts"]
            assert counts == {kind: len(rows) for kind, rows in corpus.items()}
        else:
            with pytest.raises(DatabaseError, match=message), transaction.atomic():
                evidence(snapshot.pk)
        with (
            pytest.raises(
                DatabaseError, match=message or "differs from its complete corpus"
            ),
            transaction.atomic(),
        ):
            SourceSnapshot.objects.filter(pk=snapshot.pk).update(
                version=F("version") + 1,
                state="ready",
                completed_at=_now(),
                counts={kind: 0 for kind in ENTITY_MODELS},
                content_digest="0" * 64,
                validation={"schema": "source-corpus-v1", "complete": True},
            )
        with pytest.raises(
            DatabaseError, match=message or "unchanged quick update must match"
        ):
            force_unchanged(snapshot)
    assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "staging"


def history_corpus(families):
    """A synthetic corpus with every collection and every reference kind."""
    corpus = {kind: {} for kind in ENTITY_MODELS}
    corpus["ministry"] = {f"n{j}": {"name": f"Ministry {j}"} for j in range(10)}
    corpus["fund"] = {"f1": {"name": "Fund"}}
    for i in range(families):
        family = str(i)
        corpus["family"][family] = {"name": f"Family {i}"}
        corpus["address"][f"family:{i}:home"] = {
            "owner_kind": "family",
            "owner_key": family,
            "street": f"{i} Example St",
        }
        corpus["pledge"][f"p{i}"] = {
            "family_key": family,
            "fund_key": "f1",
            "amount": "100.00",
        }
        corpus["contribution"][f"c{i}"] = {
            "family_key": family,
            "fund_key": "f1",
            "amount": "25.00",
        }
        for suffix in "ab":
            member = f"m{i}{suffix}"
            corpus["member"][member] = {"name": member, "family_key": family}
            corpus["contact"][f"member:{member}:email"] = {
                "owner_kind": "member",
                "owner_key": member,
                "email": f"{member}@example.invalid",
            }
            corpus["roster"][f"{member}:n{i % 10}"] = {
                "member_key": member,
                "ministry_key": f"n{i % 10}",
                "role": "Member",
            }
    return corpus


def build_history(families, snapshots):
    """One staged corpus and ``snapshots`` copies sharing its payloads.

    Production retains many snapshots, each holding the same payload rows
    for unchanged records (about 190 per payload on 2026-10-10). The first
    snapshot is staged through the storage layer; the copies' membership
    rows are cloned in SQL with triggers off (the test database runs as a
    superuser), which is fast and changes nothing the function reads. Ends
    with ANALYZE, as every Production promotion does. Returns the staged
    snapshot's id and an empty staging snapshot's id.
    """
    claim = acquire_source(**running_source_task(), phase="full", lease_seconds=600)
    first = begin_snapshot(claim, organization_id=5, admit=permit)
    corpus = history_corpus(families)
    stage_all(first, claim, corpus)
    copies = [
        str(begin_snapshot(claim, organization_id=5, admit=permit).pk)
        for _ in range(snapshots)
    ]
    empty = begin_snapshot(claim, organization_id=5, admit=permit)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        for _, membership in ENTITY_MODELS.values():
            table = membership._meta.db_table
            cursor.execute(
                f"INSERT INTO {table} (id, created_at, actor_id, correlation_id, "
                "source_key, payload_id, snapshot_id) "
                "SELECT gen_random_uuid(), m.created_at, m.actor_id, "
                "m.correlation_id, m.source_key, m.payload_id, copy.id "
                f"FROM {table} m, unnest(%s::uuid[]) AS copy(id) "
                "WHERE m.snapshot_id=%s",
                [copies, first.pk],
            )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE")
    return first.pk, empty.pk, sum(len(rows) for rows in corpus.values())


SOURCE_TABLES = (
    "SELECT coalesce(sum(coalesce(seq_tup_read, 0) + coalesce(idx_tup_fetch, 0)), 0) "
    "FROM pg_stat_xact_user_tables WHERE relname LIKE 'stewardship\\_snapshot\\_%%' "
    "OR relname LIKE 'stewardship\\_source\\_%%'"
)


def measured(snapshot_id):
    """The function's evidence, the source rows it read and its time.

    pg_stat_xact_user_tables counts the current transaction's sequential and
    index-fetched rows, so the difference around one call is that call's.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(SOURCE_TABLES)
        before = cursor.fetchone()[0]
        started = perf_counter()
        result = evidence(snapshot_id)
        elapsed = perf_counter() - started
        cursor.execute(SOURCE_TABLES)
        return result, cursor.fetchone()[0] - before, elapsed


# The synthetic history: Families per corpus (about ten rows each across the
# nine collections) and retained copies of it. Small enough for CI; raise
# both to time a Production-sized history by hand.
HISTORY_FAMILIES = 200
HISTORY_COPIES = 40


def test_an_empty_snapshot_reads_none_of_the_history():
    """An unchanged snapshot's evidence no longer reads retained rows.

    With 41 snapshots sharing one ~2,000-row corpus, the previous definition
    reads whole membership tables of the history for an empty snapshot (in
    Production, all of them: about 6.9 M rows and 9.4 s). The new one runs no
    relationship or owner check for it; what it still reads is the unchanged
    count loop's handful of rows from the tiny tables (one fund row per
    snapshot, a few payload rows), so the bound is a tenth of one corpus,
    not zero. For the populated snapshot (the full refresh's ``ready``
    check) both definitions return the same evidence, and the new one reads
    fewer rows than the previous one (the next test bounds it).
    Rows read and timings are printed for information.
    """
    populated, empty, corpus_rows = build_history(
        families=HISTORY_FAMILIES, snapshots=HISTORY_COPIES
    )
    results = {}
    for version in ("previous", "new"):
        with installed(version):
            results[version] = {
                "empty": measured(empty),
                "populated": measured(populated),
            }
    for case in ("empty", "populated"):
        assert results["new"][case][0] == results["previous"][case][0]
    assert set(results["new"]["empty"][0]["counts"].values()) == {0}
    for version, cases in results.items():
        for case, (_, rows, elapsed) in cases.items():
            print(
                f"{version} definition, {case} snapshot: {rows} rows read, "
                f"{elapsed * 1000:.1f} ms ({corpus_rows} rows per snapshot, "
                f"{HISTORY_COPIES + 1} snapshots)"
            )
    new_empty, old_empty = results["new"]["empty"][1], results["previous"]["empty"][1]
    new_full = results["new"]["populated"][1]
    old_full = results["previous"]["populated"][1]
    # The empty snapshot has no rows, so reading several corpora means reading
    # retained history. How much depends on the old plan (25,406 rows alone,
    # 17,006 after other suites' statistics), so the bound is loose.
    assert old_empty >= 4 * corpus_rows
    assert new_empty <= corpus_rows / 10
    assert new_full < old_full


# Rows the new body may read per row of the checked corpus. The count loop
# reads each row's membership row and payload (2). Each reference a row holds
# costs its membership row, its payload and one parent row by index (3), and
# no collection holds more than two (roster, pledge, contribution): 2 + 2 * 3.
# A contact or address holds one owner but probes Families and Members
# (2 + 2), within the same bound. The synthetic corpus measures about 5.6.
READS_PER_CORPUS_ROW = 8


def test_a_full_refresh_reads_a_bounded_multiple_of_its_corpus():
    """The populated (``ready``) check reads at most 8 rows per corpus row.

    The bound is linear in one corpus, so a plan that reads every retained
    snapshot's rows (41 copies here, more than 8) or a quadratic one (a
    parent scan per child: about 2,000 rows per reference here) fails it.
    """
    populated, _, corpus_rows = build_history(
        families=HISTORY_FAMILIES, snapshots=HISTORY_COPIES
    )
    assert HISTORY_COPIES + 1 > READS_PER_CORPUS_ROW
    result, rows, _ = measured(populated)
    assert sum(result["counts"].values()) == corpus_rows
    assert rows <= READS_PER_CORPUS_ROW * corpus_rows


def test_migration_check_refuses_the_previous_definition():
    """The frozen file's DO block fails against the old body or attributes."""
    text = MIGRATION.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)  # The installed definition passes.
        for old in (
            previous_definition(),
            "ALTER FUNCTION public.stewardship_source_corpus_evidence(uuid) "
            "SECURITY DEFINER",
            "ALTER FUNCTION public.stewardship_source_corpus_evidence(uuid) "
            "SET search_path TO pg_catalog, public, pg_temp",
        ):
            with (
                pytest.raises(DatabaseError, match="Migration 0044"),
                transaction.atomic(),
            ):
                cursor.execute(old)
                cursor.execute(check)
        cursor.execute(check)  # Rolled back to the new definition.


# The rewritten query text, new then previous, for the test below.
REWRITES = (
    (
        "EXECUTE format('WITH c AS MATERIALIZED (SELECT p.%4$I AS parent_key '\n"
        "            'FROM %1$I m JOIN %2$I p ON p.id=m.payload_id "
        "WHERE m.snapshot_id=$1) '\n"
        "            'SELECT EXISTS(SELECT 1 FROM c WHERE NOT EXISTS("
        "SELECT 1 FROM %3$I parent '\n"
        "            'WHERE parent.snapshot_id=$1 AND "
        "parent.source_key=c.parent_key))',\n",
        "EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I m '\n"
        "            'JOIN %I p ON p.id=m.payload_id '\n"
        "            'WHERE m.snapshot_id=$1 AND NOT EXISTS("
        "SELECT 1 FROM %I parent '\n"
        "            'WHERE parent.snapshot_id=$1 AND "
        "parent.source_key=p.%I))',\n",
    ),
    (
        "EXECUTE format('WITH c AS MATERIALIZED "
        "(SELECT p.owner_kind,p.owner_key '\n"
        "            'FROM %I m JOIN %I p ON p.id=m.payload_id "
        "WHERE m.snapshot_id=$1) '\n"
        "            'SELECT EXISTS(SELECT 1 FROM c '\n",
        "EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I m '\n"
        "            'JOIN %I p ON p.id=m.payload_id '\n",
    ),
    (
        "AND f.source_key=c.owner_key AND c.owner_kind=",
        "AND f.source_key=p.owner_key AND p.owner_kind=",
    ),
    (
        "AND b.source_key=c.owner_key AND c.owner_kind=",
        "AND b.source_key=p.owner_key AND p.owner_kind=",
    ),
    (
        "'WHERE f.id IS NULL AND b.id IS NULL)'",
        "'WHERE m.snapshot_id=$1 AND f.id IS NULL AND b.id IS NULL)'",
    ),
)


def test_the_two_definitions_differ_only_in_the_checks():
    """The new body is the previous one with only the #629 change.

    Dropping the added comment and CONTINUE lines and undoing each rewritten
    query fragment (each found exactly once) gives the previous body back
    exactly, so nothing else changed.
    """
    new = new_definition().removeprefix("CREATE OR REPLACE ")
    previous = definition(PREVIOUS.read_text(encoding="utf-8"), "CREATE FUNCTION")
    restored = "".join(
        line
        for line in new.splitlines(keepends=True)
        if not line.lstrip().startswith(("-- ", "CONTINUE WHEN counts->>"))
    )
    assert len(restored.splitlines()) == len(new.splitlines()) - 8
    for rewritten, original in REWRITES:
        assert restored.count(rewritten) == 1, rewritten
        restored = restored.replace(rewritten, original)
    assert "CREATE " + restored == previous
