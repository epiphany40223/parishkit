"""A fake ParishSoft edit followed by a full refresh is what promotion sees (#1780).

pk-local's ``fake-parishsoft`` edits change the fake's served parish; the
Administrator then runs a full refresh to exercise the review pipeline's
refresh checks. These tests run the real compiled refresh handler under the
restricted worker login, twice, against the fake's own request handler (only
the helper subprocess's pipe exchange is replaced), and read the promoted
corpus back.
"""

from datetime import date

import pytest

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.local.fake_edits import edit_document
from parishkit.stewardship.local.fake_parishsoft import (
    FakeConfiguration,
    FakeParishSoft,
)
from parishkit.stewardship.local.synthetic_parish import generate
from parishkit.stewardship.runtime_background import bind_authority
from parishkit.stewardship.source.requests import TASK_TYPE
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.source.snapshots import reconstruct_snapshot

from .refresh_harness import compiled_handler, short_drain
from .test_background_grants_postgresql import task_login
from .test_setup_loading_postgresql import fake_parishsoft_exchange
from .test_source_attempts_postgresql import configured
from .test_source_execution_postgresql import run
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_leases_postgresql import delay
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)

ANCHOR = date(2026, 9, 16)
# The configured test tenant's ParishSoft organization.
ORGANIZATION = 12345
DOCUMENT = {
    "seed": 3,
    "families": 12,
    "anchor_date": ANCHOR.isoformat(),
    "release_at": None,
}


class FakeRefreshes:
    """A configured tenant whose real worker runs full refreshes of the fake."""

    def __init__(self, tmp_path, monkeypatch):
        credential, self.store, *_ = configured(tmp_path)
        short_drain(monkeypatch)
        self.monkeypatch = monkeypatch
        self.compiled = compiled_handler(tmp_path, credential, set())
        self.count = 0

    def full(self, document):
        """Start the fake from ``document`` and run one full refresh of it.

        Returns the promoted snapshot. The fake is started afresh each time,
        as the operator script recreates the service after an edit.
        """
        if self.count:
            delay(2.05)  # The previous refresh's request drain window.
        self.count += 1
        configuration = FakeConfiguration.parse(document)
        parish = generate(
            configuration.seed,
            configuration.families,
            configuration.anchor_date,
            organization_id=ORGANIZATION,
        )
        fake_parishsoft_exchange(
            self.monkeypatch, FakeParishSoft(configuration, parish=parish)
        )
        receipt = command()
        with task_login(ServiceRole.WORKER, reconnect=True):
            bound = bind_authority({TASK_TYPE: self.compiled}, self.store)[TASK_TYPE]
            assert run(receipt, bound)
        assert TaskRun.objects.get(pk=receipt.task_root_id).state == "succeeded"
        current = SourceCurrent.objects.get().snapshot_id
        return SourceSnapshot.objects.get(pk=current)


def adult_and_other_family(parish):
    """An active adult Member, an unrelated Family and a Ministry they are not on."""
    member = next(
        m
        for m in parish.members
        if m["memberType"] in ("Husband", "Wife")
        and m["memberStatus"] == "Active"
        and m["familyDUID"] != parish.late_family_id
    )
    family = next(
        f["familyDUID"]
        for f in parish.families
        if f["familyDUID"] not in (member["familyDUID"], parish.late_family_id)
    )
    ministry = next(
        identifier
        for identifier, rows in parish.ministry_rosters.items()
        if all(row["memberId"] != member["memberDUID"] for row in rows)
    )
    return member, family, ministry


def test_an_edit_then_a_full_refresh_promotes_the_changed_census_and_roster(
    tmp_path, monkeypatch
):
    """Email, suffix, a move and a new Ministry entry all reach the promoted corpus."""
    refreshes = FakeRefreshes(tmp_path, monkeypatch)
    before = refreshes.full(DOCUMENT)
    parish = generate(3, 12, ANCHOR)
    member, family, ministry = adult_and_other_family(parish)
    key = str(member["memberDUID"])
    corpus = reconstruct_snapshot(before.pk, kinds=("member", "contact", "roster"))
    assert corpus["member"][key]["suffix"] is None
    assert corpus["member"][key]["family_key"] == str(member["familyDUID"])

    document = DOCUMENT
    for words in (
        ["member", key, "email=changed@example.test", "suffix=Jr."],
        ["member", key, f"family={family}"],
        ["roster", "add", str(ministry), key],
    ):
        document = edit_document(words, document)
    after = refreshes.full(document)

    assert after.pk != before.pk and after.kind == "full"
    assert after.content_digest != before.content_digest
    corpus = reconstruct_snapshot(after.pk, kinds=("member", "contact", "roster"))
    assert corpus["member"][key]["suffix"] == "Jr."
    assert corpus["member"][key]["family_key"] == str(family)
    emails = corpus["contact"][f"member:{key}"]["emails"]
    assert [e["value"] for e in emails] == ["changed@example.test"]
    entries = [
        row
        for row in corpus["roster"].values()
        if row["member_key"] == key and row["ministry_key"] == str(ministry)
    ]
    assert len(entries) == 1 and entries[0]["current"] is True


def test_an_unedited_refresh_promotes_the_same_corpus(tmp_path, monkeypatch):
    """Without edits a second full refresh sees exactly the first one's data."""
    refreshes = FakeRefreshes(tmp_path, monkeypatch)
    first = refreshes.full(DOCUMENT)
    second = refreshes.full({**DOCUMENT, "edits": []})
    assert second.content_digest == first.content_digest
