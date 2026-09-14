"""Adapter queries bind every financial row to one snapshot, Family and fund set."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.responses import source_inputs
from parishkit.stewardship.responses.financial_inputs import financial_definition
from parishkit.stewardship.responses.inputs import FormInputsUnavailable

from .financial_factory import CAMPAIGN, configuration, cursor, record


@pytest.mark.parametrize("complete", [True, False])
def test_financial_adapter_qualifies_each_query_and_skips_unavailable_details(
    monkeypatch, complete
):
    """Model spies validate actual query construction without any provider or DB IO."""
    snapshot_id = uuid4()
    definition = financial_definition(configuration(), campaign_id=CAMPAIGN)
    selected = Mock()
    selected.values_list.return_value.get.return_value = (
        cursor(definition) if complete else {}
    )
    monkeypatch.setattr(source_inputs.SourceSnapshot, "objects", selected)
    stores = []
    for model in (source_inputs.SnapshotPledge, source_inputs.SnapshotContribution):
        store = Mock()
        store.filter.return_value.select_related.return_value.iterator.return_value = (
            iter([SimpleNamespace(payload=SimpleNamespace(payload=record()))])
        )
        monkeypatch.setattr(model, "objects", store)
        stores.append(store)
    value = source_inputs.load_financial_inputs(
        snapshot_id, 1, configuration=configuration(), campaign_id=CAMPAIGN
    )
    selected.values_list.assert_called_once_with("cursor", flat=True)
    selected.values_list.return_value.get.assert_called_once_with(pk=snapshot_id)
    assert value.family_duid == 1
    for store in stores:
        if complete:
            store.filter.assert_called_once_with(
                snapshot_id=snapshot_id,
                payload__family_key="1",
                payload__fund_key__in=["9"],
            )
            store.filter.return_value.select_related.assert_called_once_with("payload")
            store.filter.return_value.select_related.return_value.iterator.assert_called_once_with(
                chunk_size=200
            )
        else:
            store.filter.assert_not_called()
    assert (
        value.pledge.canonical
        == value.contributions.canonical
        == ("1.00" if complete else None)
    )


def test_financial_adapter_rejects_foreign_row_even_if_query_backend_misbehaves(
    monkeypatch,
):
    """The pure boundary independently rejects an accidental cross-household result."""
    definition = financial_definition(configuration(), campaign_id=CAMPAIGN)
    selected = Mock()
    selected.values_list.return_value.get.return_value = cursor(definition)
    monkeypatch.setattr(source_inputs.SourceSnapshot, "objects", selected)
    for model in (source_inputs.SnapshotPledge, source_inputs.SnapshotContribution):
        store = Mock()
        store.filter.return_value.select_related.return_value.iterator.return_value = (
            iter(
                [
                    SimpleNamespace(
                        payload=SimpleNamespace(payload=record(family_key="2"))
                    )
                ]
            )
        )
        monkeypatch.setattr(model, "objects", store)
    with pytest.raises(FormInputsUnavailable) as failure:
        source_inputs.load_financial_inputs(
            uuid4(), 1, configuration=configuration(), campaign_id=CAMPAIGN
        )
    assert str(failure.value) == "The Family form inputs are unavailable."
