"""How a full load honors an Administrator's one-time acceptance (#530).

The binding rule (``drop_acceptance.honors``), and the full loader asking
for an acceptance only when its drop check refuses a count that fell. The
SQL guard, the acceptance's lifetime and the worker's real path are covered
by ``database/test_source_drop_acceptance_postgresql.py``.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.source.drop_acceptance import (
    ACCEPTANCE_KEY,
    honors,
    reviewed_counts,
)
from parishkit.stewardship.source.loading import (
    DEFAULT_MAXIMUM_DROP_PERCENT,
    CountCheck,
    DestructiveSourceChange,
    load_full_source,
)
from parishkit.stewardship.source.windows import RefreshWindow

from .test_source_corpus import TODAY
from .test_source_loading import client_factory, counts, provider_pages


def check(measure, before, after, failed):
    """One count a load was checked on, at the default limit."""
    return CountCheck(measure, before, after, DEFAULT_MAXIMUM_DROP_PERCENT, failed)


# A refusal: Families fell from 100 to 60 (failed); Members passed.
REFUSAL = (check("family", 100, 60, True), check("member", 200, 190, False))
REVIEWED = reviewed_counts(REFUSAL)


def test_reviewed_counts_copy_every_count_as_the_guard_rebuilds_them():
    """Before, after and failed for each measure, as the SQL guard compares."""
    assert REVIEWED == {
        "family": {"before": 100, "after": 60, "failed": True},
        "member": {"before": 200, "after": 190, "failed": False},
    }


def test_the_same_change_is_honored_and_before_values_are_not_compared():
    """The reviewed values again, or better, against any baseline."""
    assert honors(REFUSAL, REVIEWED)
    assert honors(
        (check("family", 140, 61, True), check("member", 150, 200, False)), REVIEWED
    )


@pytest.mark.parametrize(
    "checks",
    [
        # Families fell further than the Administrator saw.
        (check("family", 100, 59, True), check("member", 200, 190, False)),
        # A count that passed in the refusal now falls too far: its limit holds.
        (check("family", 100, 60, True), check("member", 200, 100, True)),
        # A count that passed now falls below its reviewed value, even within
        # the limit.
        (check("family", 100, 60, True), check("member", 200, 189, False)),
        # A reviewed count is not checked again, so it cannot be shown not to
        # have fallen further.
        (check("family", 100, 60, True),),
        # A count the refusal did not record now fails.
        (*REFUSAL, check("ministry", 10, 0, True)),
    ],
)
def test_anything_beyond_the_reviewed_change_is_refused(checks):
    """Nothing falls below what the Administrator reviewed."""
    assert not honors(checks, REVIEWED)


def test_a_count_the_refusal_did_not_record_may_pass():
    """It keeps the limit in effect, so a passing one is no reason to refuse."""
    assert honors((*REFUSAL, check("ministry", 10, 9, False)), REVIEWED)


@pytest.mark.parametrize(
    "reviewed",
    [
        None,
        [],
        {"family": {"before": 100, "after": "60", "failed": True}},
        {"family": {"before": 100, "after": 60, "failed": 1}},
        {"family": None},
    ],
)
def test_malformed_reviewed_counts_honor_nothing(reviewed):
    """Only the shape the guard admits is trusted."""
    assert not honors((check("family", 100, 60, True),), reviewed)


def refused_load(tmp_path, accept, previous=None):
    """The one-Family fixture load against a baseline of 100 Families."""
    return load_full_source(
        client_factory(tmp_path, provider_pages()),
        window=RefreshWindow(None, ()),
        as_of=TODAY,
        previous_full_counts=previous or counts(),
        accept=accept,
    )


def test_an_accepted_refusal_is_loaded_and_recorded_in_the_manifest(tmp_path):
    """The loader asks with every check and records what it was given."""
    asked = []
    evidence = {"id": str(uuid4()), "actor_id": str(uuid4())}

    def accept(checks):
        """Accept whatever is asked, keeping the checks."""
        asked.append(checks)
        return evidence

    result = refused_load(tmp_path, accept)
    assert result.counts["family"] == 1
    assert result.evidence[ACCEPTANCE_KEY] == evidence
    # The limit itself is unchanged.
    assert result.evidence["maximum_drop_percent"] == DEFAULT_MAXIMUM_DROP_PERCENT
    (checks,) = asked
    family = next(each for each in checks if each.measure == "family")
    assert (family.before, family.after, family.failed) == (100, 1, True)


def test_a_declined_acceptance_leaves_the_refusal(tmp_path):
    """No acceptance (or one that does not honor the load): refused as before."""
    with pytest.raises(DestructiveSourceChange, match="count loss"):
        refused_load(tmp_path, lambda checks: None)


def test_an_empty_load_is_never_offered(tmp_path):
    """A load with no Members is refused without asking."""
    asked = []
    with pytest.raises(DestructiveSourceChange, match="empty"):
        load_full_source(
            client_factory(
                tmp_path,
                [
                    page if index != 4 else []
                    for index, page in enumerate(provider_pages())
                ],
            ),
            window=RefreshWindow(None, ()),
            as_of=TODAY,
            previous_full_counts=counts(),
            accept=asked.append,
        )
    assert not asked


def test_a_load_that_passes_never_asks(tmp_path):
    """No refusal, nothing to accept, and nothing recorded."""
    asked = []
    result = refused_load(tmp_path, asked.append, previous=counts(family=1, member=1))
    assert not asked and ACCEPTANCE_KEY not in result.evidence


def test_a_lapsed_acceptance_is_a_drop_refusal_naming_it():
    """Classified like any drop refusal (permanent, CRITICAL), carrying the
    acceptance's UUID for the process log; a malformed id carries none."""
    from parishkit.stewardship.source.drop_acceptance import (
        AcceptanceLapsed,
        require_live,
    )
    from parishkit.stewardship.source.failures import classify_read_failure

    acceptance = uuid4()
    failure = classify_read_failure(
        AcceptanceLapsed("lapsed", acceptance=acceptance), has_source_claim=True
    )
    assert (failure.failure, failure.retry, failure.lapsed) == (
        "destructive_change",
        False,
        acceptance,
    )
    assert failure.loss is None and failure.checks == ()
    assert AcceptanceLapsed("lapsed", acceptance="not-a-uuid").acceptance is None
    # A manifest naming no acceptance is never checked (no database needed).
    require_live({"load": {"maximum_drop_percent": 25}})
