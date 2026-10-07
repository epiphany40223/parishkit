"""A quick update that changed nothing is recorded, not staged and promoted (#630).

Each empty quick update used to stage a full copy of the corpus (about 30,000
membership rows in Production), promote it, rewrite every Family row with the
new generation, and leave the old copy for compaction to delete (#629). These
tests run the real compiled handler with the real Phase 2 effects, under the
restricted worker login, and only fake ParishSoft's exchange.
"""

import json
from datetime import UTC, timedelta
from functools import partial
from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.db.models import F

from parishkit import parishsoft_transport
from parishkit.stewardship.accounts.chair_models import ChairReconciliation
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.task_wording import refresh_result
from parishkit.stewardship.runtime_background import bind_authority
from parishkit.stewardship.source import health, refreshing, transport
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.data_age import connection_state, source_facts
from parishkit.stewardship.source.effects import (
    refresh_reconciler,
    refresh_unchanged,
)
from parishkit.stewardship.source.execution import refresh_handler
from parishkit.stewardship.source.leases import _now as lease_now
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.refresh_status import full_refresh_status
from parishkit.stewardship.source.requests import TASK_TYPE
from parishkit.stewardship.source.snapshot_models import (
    SourceCurrent,
    SourceSnapshot,
)
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_unchanged,
    promote_snapshot,
    stage_entities,
)
from parishkit.stewardship.source.version_models import ENTITY_MODELS
from parishkit.stewardship.source.windows import RefreshWindow

from ..policy_factory import address
from ..test_source_corpus import source
from .campaign_builders import add_draft, change
from .credential_builders import keys
from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_source_attempts_postgresql import configured
from .test_source_delta_postgresql import stage
from .test_source_execution_postgresql import handler, run
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_leases_postgresql import delay
from .test_source_refreshing_postgresql import fake_provider, pages
from .test_source_requests_postgresql import command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

HEAD = "example@example.org"


def membership_rows():
    """Every snapshot membership row, across all nine collections."""
    return sum(membership.objects.count() for _, membership in ENTITY_MODELS.values())


def family_rows():
    """Each Family row's version and generation, to prove nothing was rewritten."""
    return sorted(
        FamilyCampaign.objects.values_list("id", "version", "source_generation")
    )


def quick_pages():
    """ParishSoft answers a quick update with an empty change list.

    The organization probe, the empty change list, and the Family group
    definitions, which must match the current corpus's labels.
    """
    from parishkit.stewardship.source.snapshots import reconstruct_snapshot

    groups = {
        family["famGroupID"]: family["family_group"]
        for family in reconstruct_snapshot(kinds=("family",))["family"].values()
        if family.get("famGroupID") is not None
    }
    return [
        [{"organizationID": 12345}],
        [],
        [{"famGroupID": key, "famGroup": value} for key, value in groups.items()],
    ]


def compiled_handler(tmp_path, credential, suppressed):
    """The worker's real handler: real effects and the real unchanged check.

    ``suppressed`` is a mutable set standing in for provider mail refusals,
    so a test can add a bounce between refreshes.
    """
    ring = keys()

    def suppressions(scope):
        """Synthetic suppression owner; returns the current refusals."""
        return frozenset(suppressed)

    path = tmp_path / "source-key"
    path.write_bytes(credential.value)
    path.chmod(0o600)
    return refresh_handler(
        credential_path=path,
        reconcile=refresh_reconciler(
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressions=suppressions,
        ),
        unchanged=refresh_unchanged(suppressions=suppressions),
    )


def short_drain(monkeypatch):
    """Shorten each request's drain window, so a second refresh can start.

    Every ParishSoft request reserves the source lease until its timeout plus
    a safety margin has passed; the quick update would otherwise wait that
    long behind the full refresh. Shortened, the tests wait two seconds.
    """
    real = transport.reserve_source_request

    def reserve(claim, **_):
        """Reserve the lease for one second plus one second of safety."""
        return real(claim, timeout_seconds=1, safety_seconds=1)

    monkeypatch.setattr(transport, "reserve_source_request", reserve)


class Refreshes:
    """A configured tenant whose real worker runs a full refresh, then quick ones.

    ``suppressed`` stands in for provider mail refusals, so a test can add a
    bounce between refreshes. ``campaign`` adds a draft campaign first.
    """

    def __init__(self, tmp_path, monkeypatch, *, campaign=True):
        credential, self.store, version, actor = configured(tmp_path)
        if campaign:
            add_draft(self.store, version, actor)
        short_drain(monkeypatch)
        self.monkeypatch = monkeypatch
        self.suppressed = set()
        self.compiled = compiled_handler(tmp_path, credential, self.suppressed)
        self._run(command(), pages())
        self.full = SourceSnapshot.objects.get(state="promoted")

    def _run(self, receipt, values):
        """Run one refresh as the restricted worker against fake ParishSoft."""
        remaining, calls = fake_provider(self.monkeypatch, values)
        with task_login(ServiceRole.WORKER, reconnect=True):
            bound = bind_authority({TASK_TYPE: self.compiled}, self.store)[TASK_TYPE]
            assert run(receipt, bound)
        assert not remaining
        assert TaskRun.objects.get(pk=receipt.task_root_id).state == "succeeded"
        return calls

    def quick(self):
        """Run one quick update; return its snapshot, the row counts before it
        (membership rows, Family rows) and its task receipt."""
        before = membership_rows(), family_rows()
        delay(2.05)  # The previous refresh's request drain window.
        receipt = command(cause="delta", actor_id=None)
        self._run(receipt, quick_pages())
        delta = SourceSnapshot.objects.filter(kind="delta").latest("started_at")
        return delta, before, receipt


def full_then_quick(tmp_path, monkeypatch, *, quick=None, campaign=True):
    """Promote a full refresh, then run one quick update as the worker.

    ``quick(refreshes)`` runs between the two. Returns the full snapshot, the
    quick update's snapshot, the row counts taken just before the quick
    update, and its receipt.
    """
    refreshes = Refreshes(tmp_path, monkeypatch, campaign=campaign)
    if quick is not None:
        quick(refreshes)
    return (refreshes.full, *refreshes.quick())


def test_empty_quick_update_writes_no_snapshot_or_family_rows(tmp_path, monkeypatch):
    """Nothing is staged or promoted, and the attempt still counts as an answer."""
    full, delta, (memberships, families), receipt = full_then_quick(
        tmp_path, monkeypatch
    )
    assert delta.state == "unchanged" and delta.base_id == full.pk
    assert delta.generation is None and delta.promoted_at is None
    # No membership row was written, and no Family row was rewritten.
    assert membership_rows() == memberships
    assert family_rows() == families
    assert SourceCurrent.objects.get().snapshot_id == full.pk
    # It carries the current corpus's evidence and counts no change.
    assert delta.counts == full.counts
    assert delta.content_digest == full.content_digest
    assert delta.cursor["changes"] == dict.fromkeys(ENTITY_MODELS, 0)
    assert delta.cursor["full_snapshot_id"] == str(full.pk)
    assert delta.cursor["watermark"] == delta.started_at.isoformat()
    # The task page says how many records were checked and that none changed.
    assert "0 changed" in refresh_result(receipt.task_root_id)
    # The connection line counts it as ParishSoft answering; data age does not.
    facts = source_facts()
    assert facts.success_at == delta.started_at
    assert facts.answered_at == delta.completed_at
    assert facts.data_as_of == full.started_at
    assert facts.changed_delta_at is None
    state = connection_state(facts, now=delta.completed_at, threshold=None)
    assert state.state == "working" and state.at == delta.completed_at
    # The refresh page's quick-update health sees it as a success.
    status = full_refresh_status()
    assert status.delta_succeeded_at == delta.completed_at


def test_next_quick_update_reads_on_from_the_unchanged_watermark(tmp_path, monkeypatch):
    """A second real quick update reads the change list from the unchanged one.

    The window does not grow back to the last promotion while nothing
    changes. The full refresh and both quick updates run on one day, so the
    dates sent are the same either way; the cursor passed in is what proves
    which watermark the window came from.
    """
    refreshes = Refreshes(tmp_path, monkeypatch)
    first, *_ = refreshes.quick()
    assert first.state == "unchanged"
    real = refreshing.load_delta_source
    seen = []

    def spy(client, **options):
        """Record the cursor the real delta loader reads on from."""
        seen.append(options["base_cursor"])
        return real(client, **options)

    monkeypatch.setattr(refreshing, "load_delta_source", spy)
    sent = []
    exchange = None

    def record(payload, **kwargs):
        """Keep each request's parameters, then answer as the fake does."""
        sent.append(json.loads(payload))
        return exchange(payload, **kwargs)

    delay(2.05)
    receipt = command(cause="delta", actor_id=None)
    fake_provider(monkeypatch, quick_pages())
    exchange = parishsoft_transport._exchange
    monkeypatch.setattr(parishsoft_transport, "_exchange", record)
    with task_login(ServiceRole.WORKER, reconnect=True):
        bound = bind_authority({TASK_TYPE: refreshes.compiled}, refreshes.store)
        assert run(receipt, bound[TASK_TYPE])
    second = SourceSnapshot.objects.filter(kind="delta").latest("started_at")
    assert second.state == "unchanged" and second.pk != first.pk
    assert seen == [first.cursor]
    assert second.cursor["full_snapshot_id"] == str(refreshes.full.pk)
    changes = next(item for item in sent if "families/change/list" in item["url"])
    start = first.started_at.astimezone(UTC).date() - timedelta(days=1)
    assert changes["parameters"]["StartDate"] == start.isoformat()
    # Recovery from a refresh failure measures from the same newest read.
    assert health._newest_read(refreshes.full) == second


def test_new_bounce_promotes_as_before(tmp_path, monkeypatch):
    """A mail refusal changes a Family's status, so the quick update promotes."""
    full, delta, (memberships, _), _ = full_then_quick(
        tmp_path, monkeypatch, quick=lambda refreshes: refreshes.suppressed.add(HEAD)
    )
    assert delta.state == "promoted"
    assert SourceCurrent.objects.get().snapshot_id == delta.pk
    assert membership_rows() > memberships
    family = FamilyCampaign.objects.get()
    assert family.source_generation == delta.generation
    assert not family.email_deliverable


def test_changed_corpus_stages_and_promotes_as_before(tmp_path, monkeypatch):
    """Any difference from the current corpus takes the ordinary path."""
    real = refreshing.load_delta_source

    def changed(client, **options):
        """Return the real delta with one Member's middle name changed."""
        loaded = real(client, **options)
        key = next(iter(loaded.corpus["member"]))
        loaded.corpus["member"][key] = {
            **loaded.corpus["member"][key],
            "middle_name": "Changed",
        }
        return loaded

    monkeypatch.setattr(refreshing, "load_delta_source", changed)
    full, delta, (memberships, families), _ = full_then_quick(tmp_path, monkeypatch)
    assert delta.state == "promoted"
    assert membership_rows() > memberships
    assert sum(delta.cursor["changes"].values()) == 1
    assert all(generation == delta.generation for *_, generation in family_rows())
    assert source_facts().changed_delta_at == delta.started_at


def test_without_the_check_every_quick_update_promotes(tmp_path, monkeypatch):
    """A handler bound without the check keeps the old behavior."""
    credential, store, version, actor = configured(tmp_path)
    add_draft(store, version, actor)
    ring = keys()
    compiled = handler(
        tmp_path,
        credential,
        reconcile=refresh_reconciler(
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressions=lambda _: frozenset(),
        ),
    )
    short_drain(monkeypatch)
    fake_provider(monkeypatch, pages())
    assert run(command(), compiled)
    delay(2.05)
    fake_provider(monkeypatch, quick_pages())
    assert run(command(cause="delta", actor_id=None), compiled)
    assert SourceSnapshot.objects.get(kind="delta").state == "promoted"


def promoted_full():
    """Promote a full corpus through the storage layer alone; return it."""
    window = RefreshWindow(uuid4(), ())
    claim = acquire_source(**running_source_task(), phase="full")
    first = begin_snapshot(claim, organization_id=5, admit=permit)
    corpus = normalize_core(source(), as_of=first.started_at.date())
    cursor = refresh_cursor(
        snapshot_id=first.pk,
        kind="full",
        started_at=first.started_at,
        window_digest=window.digest,
        evidence={
            "giving_as_of_date": first.started_at.date().isoformat(),
            "anonymous_pledges": 0,
            "anonymous_contributions": 0,
        },
    )
    first = stage(first, claim, corpus, cursor)
    promote_snapshot(first.pk, claim, admit=permit, reconcile=permit)
    release_source(claim)
    return first, corpus, cursor


def unchanged_cursor(base_cursor, snapshot, window_digest):
    """A coherent quick-update cursor read against ``base_cursor``."""
    return refresh_cursor(
        snapshot_id=snapshot.pk,
        kind="delta",
        started_at=snapshot.started_at,
        window_digest=window_digest,
        evidence={"schema": "source-load-v1"},
        base_cursor=base_cursor,
    )


@pytest.mark.parametrize("problem", [None, "full", "rows"])
def test_unchanged_state_is_guarded(problem):
    """SQL admits ``unchanged`` only for an empty delta of the current corpus."""
    first, corpus, cursor = promoted_full()
    claim = acquire_source(
        **running_source_task(), phase="full" if problem == "full" else "delta"
    )
    snapshot = begin_snapshot(claim, organization_id=5, admit=permit)
    if problem == "rows":
        family = next(iter(corpus["family"]))
        stage_entities(
            snapshot.pk,
            claim,
            kind="family",
            entities={family: corpus["family"][family]},
            admit=permit,
        )
    if problem is not None:
        with (
            pytest.raises(DatabaseError, match="unchanged quick update must match"),
            transaction.atomic(),
        ):
            finish_unchanged(snapshot.pk, claim, cursor={}, admit=permit)
        return
    result = finish_unchanged(
        snapshot.pk,
        claim,
        cursor=unchanged_cursor(cursor, snapshot, cursor["window_digest"]),
        admit=permit,
    )
    assert result.state == "unchanged" and result.base_id == first.pk
    assert result.counts == first.counts
    assert result.content_digest == first.content_digest
    # Terminal: nothing may leave the state.
    for state in ("ready", "promoted", "rejected", "staging"):
        with pytest.raises(DatabaseError), transaction.atomic():
            SourceSnapshot.objects.filter(pk=result.pk).update(
                state=state, version=result.version + 1
            )


def test_without_a_campaign_only_chairs_are_checked(tmp_path, monkeypatch):
    """With no campaign, a reconciled corpus is all a quick update needs."""
    _, delta, (memberships, _), _ = full_then_quick(
        tmp_path, monkeypatch, campaign=False
    )
    assert delta.state == "unchanged"
    assert membership_rows() == memberships


def test_new_configuration_without_chair_reconciliation_promotes(tmp_path, monkeypatch):
    """An activation that skipped chair reconciliation costs one promotion."""

    def activate(refreshes):
        """Activate a new configuration that leaves the window unchanged."""
        result = change(
            refreshes.store,
            refreshes.store.active(),
            uuid4(),
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("other@example.org"),
                }
            ],
        )
        assert result.state == "applied"

    _, delta, *_ = full_then_quick(tmp_path, monkeypatch, quick=activate)
    assert delta.state == "promoted"
    assert ChairReconciliation.objects.filter(snapshot=delta).exists()


def bump_population(**values):
    """Change the stored population evidence as the owner may."""
    with work_transaction():
        CampaignCredentialState.objects.update(**values, version=F("version") + 1)


def test_dirty_population_promotes(tmp_path, monkeypatch):
    """Unverified population evidence is rebuilt by promotion."""
    _, delta, *_ = full_then_quick(
        tmp_path,
        monkeypatch,
        quick=lambda _: bump_population(population_dirty=True),
    )
    assert delta.state == "promoted"
    assert not CampaignCredentialState.objects.get().population_dirty


def test_population_naming_another_snapshot_promotes(tmp_path, monkeypatch):
    """Evidence that names any snapshot but the current one is rebuilt."""
    refreshes = Refreshes(tmp_path, monkeypatch)
    other, *_ = refreshes.quick()
    assert other.state == "unchanged"
    bump_population(source_snapshot_id=other.pk)
    delta, *_ = refreshes.quick()
    assert delta.state == "promoted"
    assert CampaignCredentialState.objects.get().source_snapshot_id == delta.pk


def force_unchanged(snapshot, claim=None, **overrides):
    """Write the unchanged state directly, with optional wrong evidence.

    Mirrors ``finish_unchanged`` without its Python checks, so the SQL guard
    alone decides.
    """
    with transaction.atomic():
        row = SourceSnapshot.objects.select_for_update().get(pk=snapshot.pk)
        base = SourceSnapshot.objects.get(pk=row.base_id)
        row.counts = dict(base.counts)
        row.content_digest = base.content_digest
        row.validation = {"schema": "source-unchanged-v1"}
        row.completed_at = lease_now()
        row.state = "unchanged"
        row.version += 1
        for key, value in overrides.items():
            setattr(row, key, value)
        row.save()


@pytest.mark.parametrize(
    "problem", ["stale_base", "counts", "digest", "validation", "no_owner"]
)
def test_unchanged_evidence_is_checked_in_sql(problem):
    """The guard refuses a stale base, wrong evidence and a dead owner."""
    first, corpus, cursor = promoted_full()
    claim = acquire_source(**running_source_task(), phase="delta")
    snapshot = begin_snapshot(claim, organization_id=5, admit=permit)
    overrides = {}
    message = "unchanged quick update must match"
    if problem == "stale_base":
        # A later read under the same owner promotes first, so this one's
        # base is no longer the current snapshot.
        later = begin_snapshot(claim, organization_id=5, admit=permit)
        stage(
            later,
            claim,
            corpus,
            unchanged_cursor(cursor, later, cursor["window_digest"]),
        )
        promote_snapshot(later.pk, claim, admit=permit, reconcile=permit)
    elif problem == "counts":
        overrides["counts"] = {**first.counts, "family": first.counts["family"] + 1}
    elif problem == "digest":
        overrides["content_digest"] = "0" * 64
    elif problem == "validation":
        overrides["validation"] = {"schema": "source-corpus-v1", "complete": True}
    else:
        release_source(claim)
        message = "live fenced owner"
    with pytest.raises(DatabaseError, match=message):
        force_unchanged(snapshot, **overrides)
    assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "staging"


def test_roster_current_flag_on_a_new_day_stages_as_before(tmp_path, monkeypatch):
    """A roster whose "current" flag flips is a change, so the delta stages."""
    from parishkit.stewardship.source import delta as delta_module

    from .test_source_attempts_postgresql import setup
    from .test_source_refreshing_postgresql import next_delta, seed_full
    from .test_source_refreshing_postgresql import run as observe

    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    assert first.counts["roster"] > 0
    real = delta_module.ministry_membership_is_current
    monkeypatch.setattr(
        delta_module,
        "ministry_membership_is_current",
        lambda row, today: not real(row, today=today),
    )
    always = []

    def unchanged(*args):
        """Would skip promotion if asked; the corpus differs, so it is not."""
        always.append(args)
        return True

    monkeypatch.setattr(
        refreshing,
        "load_and_stage_attempt",
        partial(refreshing.load_and_stage_attempt, unchanged=unchanged),
    )
    execution, lease = next_delta()
    fake_provider(
        monkeypatch,
        [[{"organizationID": 12345}], [], [{"famGroupID": 7, "famGroup": "Active"}]],
    )
    result = observe(credential, execution, lease)
    assert result.state == "ready" and not always
    assert result.cursor["changes"]["roster"] > 0


def test_same_corpus_compares_canonical_payloads():
    """Python-equal but canonically different values are a change."""
    base = {"family": {"1": {"active": True}}, "member": {}}
    assert refreshing.same_corpus({k: dict(v) for k, v in base.items()}, base)
    changed = {"family": {"1": {"active": 1}}, "member": {}}
    assert changed == base
    assert not refreshing.same_corpus(changed, base)
    assert not refreshing.same_corpus({"family": {}, "member": {}}, base)
