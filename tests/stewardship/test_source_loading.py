"""Whole-load validation never treats a partial/large-loss source as new truth."""

from uuid import uuid4

import pytest
from test_parishsoft_source import family, member, page
from test_parishsoft_source import source as client_factory

from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.corpus import KINDS
from parishkit.stewardship.source.loading import load_full_source, validate_count_trend
from parishkit.stewardship.source.windows import RefreshWindow

from .test_source_corpus import TODAY
from .test_source_giving import contribution, pledge, window


def provider_pages(*, family_change=None, member_change=None):
    """All core collections are present; the HTTP responses contain no real PII."""
    return [
        [{"organizationID": 5}],
        [
            family()
            | {
                "familyID": 11,
                "registeredOrganizationID": 5,
                "lastName": "Example",
                **(family_change or {}),
            }
        ],
        [],
        [],
        [
            member()
            | {
                "familyDUID": 1,
                "firstName": "Example",
                "memberType": "Head",
                "emailAddress": "example@example.org",
                **(member_change or {}),
            }
        ],
        [],
        [],
        [],
        [],
        page([]),
        page([]),
        [{"fundId": 9, "name": "Offertory", "active": True}],
    ]


def counts(**values):
    """Every collection has explicit completeness evidence, including optional zero."""
    return {**dict.fromkeys(KINDS, 0), "family": 100, "member": 200, **values}


def test_full_shared_pipeline_loads_catalogs_without_unscoped_giving(tmp_path):
    """Initial setup can select funds before any financial window is configured."""
    client = client_factory(tmp_path, provider_pages())
    selected = RefreshWindow(uuid4(), ())
    result = load_full_source(client, window=selected, as_of=TODAY)
    assert result.counts == {
        **dict.fromkeys(KINDS, 0),
        "family": 1,
        "member": 1,
        "contact": 1,
        "fund": 1,
    }
    assert result.corpus["family"]["1"]["email_eligible"] is True
    assert result.evidence["window_digest"] == selected.digest
    assert result.evidence["requests"] == len(provider_pages())
    assert result.evidence["response_bytes"] > 0
    assert not client.session.responses and not client.config.cache_dir.exists()


def test_full_pipeline_adds_only_scoped_exact_giving(tmp_path):
    """Financial collections share the validated core Family/fund identity maps."""
    client = client_factory(
        tmp_path,
        [*provider_pages(), page([pledge()]), page([contribution(memberId=1)])],
    )
    result = load_full_source(client, window=window(), as_of=TODAY)
    assert result.counts["pledge"] == result.counts["contribution"] == 1
    assert result.corpus["contribution"]["1"]["amount"] == "100.25"
    assert not client.session.responses


@pytest.mark.parametrize(
    "field,value",
    [
        ("birthdate", "PRIVATE-DATE"),
        ("emailAddress", ["private@example.org"]),
        ("familyDUID", None),
    ],
)
def test_shared_parser_errors_have_no_private_app_diagnostic(tmp_path, field, value):
    """ParishKit tools retain their diagnostics; the campaign adapter redacts them."""
    client = client_factory(tmp_path, provider_pages(member_change={field: value}))
    with pytest.raises(InvalidSourcePayload) as failure:
        load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)
    assert "PRIVATE" not in str(failure.value) and "private@" not in str(failure.value)


@pytest.mark.parametrize("kind", ["family", "member", "ministry", "roster", "fund"])
def test_loss_threshold_is_compared_to_last_full_counts(kind):
    """The configured percent is inclusive, uses integers and rejects larger drops."""
    before = counts(**{kind: 100})
    validate_count_trend(counts(**{kind: 75}), previous_full_counts=before)
    with pytest.raises(InvalidSourcePayload, match="count loss"):
        validate_count_trend(counts(**{kind: 74}), previous_full_counts=before)


def test_giving_window_and_contact_edits_do_not_trigger_identity_loss_alarm():
    """Legitimate financial scope changes are validated separately from core counts."""
    validate_count_trend(
        counts(),
        previous_full_counts=counts(
            pledge=100, contribution=100, contact=100, address=100
        ),
    )


@pytest.mark.parametrize("kind", ["family", "member"])
def test_initial_unexpected_empty_corpus_fails_closed(kind):
    """First setup cannot certify a blank parish from an incomplete load."""
    with pytest.raises(InvalidSourcePayload, match="empty"):
        validate_count_trend(counts(**{kind: 0}), previous_full_counts=None)


@pytest.mark.parametrize("value", [True, -1, 91, 25.0])
def test_invalid_loss_threshold_is_not_coerced(value):
    """Operator configuration cannot silently disable loss detection."""
    with pytest.raises(ValueError):
        validate_count_trend(
            counts(), previous_full_counts=None, maximum_drop_percent=value
        )


@pytest.mark.parametrize("value", [{}, counts(member=True), counts(family=-1)])
def test_incomplete_count_evidence_cannot_establish_a_comparison(value):
    """Unknown and zero are not interchangeable during source validation."""
    with pytest.raises(InvalidSourcePayload, match="evidence"):
        validate_count_trend(counts(), previous_full_counts=value)


def test_large_loss_result_is_not_returned_to_staging(tmp_path):
    """Successful transport and valid rows do not override the corpus safety check."""
    client = client_factory(tmp_path, provider_pages())
    with pytest.raises(InvalidSourcePayload, match="count loss"):
        load_full_source(
            client,
            window=RefreshWindow(None, ()),
            as_of=TODAY,
            previous_full_counts=counts(),
        )
