"""Finite read/download budgets and stable advisory identities, without a DB."""

from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.campaigns.read_guards import (
    CampaignReadGuard,
    DownloadBusy,
    DownloadPool,
    ReadLimits,
    campaign_lock_key,
)


@pytest.mark.parametrize(
    "field,value",
    [
        ("interactive_seconds", 0),
        ("download_seconds", True),
        ("download_seconds", 901),
        ("download_idle_seconds", 300),
        ("drain_seconds", 330),
        ("lock_seconds", 60),
        ("process_pool_size", 5),
        ("download_capacity", 33),
    ],
)
def test_invalid_read_budgets(field, value):
    """No unlimited or contradictory lifetime/capacity is admitted."""
    with pytest.raises(ValueError):
        ReadLimits(**{field: value})


def test_keys_are_stable_and_input_is_canonical():
    """Stable deterministic ordering is independent of Python hash randomization."""
    identifier = UUID("12345678-1234-1234-1234-123456789abc")
    assert campaign_lock_key(identifier) == campaign_lock_key(UUID(str(identifier)))
    assert -(2**31) <= campaign_lock_key(identifier) < 2**31
    assert campaign_lock_key(UUID(int=0)) == 927402239
    first, second = UUID(int=1), UUID(int=2)
    reader = CampaignReadGuard(
        [second, first, second], authorize=lambda _: None, abort=lambda: None
    )
    assert reader.campaigns == (first, second)
    with pytest.raises(TypeError):
        campaign_lock_key(str(identifier))
    for values in [[], ["private"], [None]]:
        with pytest.raises(TypeError):
            CampaignReadGuard(values, authorize=lambda _: None, abort=lambda: None)
    with pytest.raises(TypeError):
        CampaignReadGuard([uuid4()], authorize=None, abort=lambda: None)


def test_pool_rejects_without_waiting_and_recovers_capacity():
    """Per-process limits complement, rather than replace, SQL-wide slots."""
    pool = DownloadPool(ReadLimits(process_pool_size=1))
    pool.acquire()
    with pytest.raises(DownloadBusy) as error:
        pool.acquire()
    assert error.value.status_code == 503 and error.value.retry_after == 5
    pool.release()
    pool.acquire()
    pool.release()
