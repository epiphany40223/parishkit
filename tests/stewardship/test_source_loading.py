"""Whole-load validation never treats a partial/large-loss source as new truth."""

from dataclasses import replace
from uuid import uuid4

import pytest
from test_parishsoft_source import family, member, page
from test_parishsoft_source import source as client_factory

from parishkit.parishsoft_pagination import (
    ShiftedSourceScan,
    SourceLoadBudgetExceeded,
)
from parishkit.parishsoft_source import SourceOrganizationMismatch
from parishkit.stewardship.source import loading
from parishkit.stewardship.source.canonical import (
    InvalidSourcePayload,
    SourceReferenceSkew,
)
from parishkit.stewardship.source.corpus import KINDS
from parishkit.stewardship.source.loading import (
    CONTACT_COVERAGE_KEY,
    DERIVED_COUNTS,
    DROP_OVERRIDE_VARIABLE,
    TREND_COLLECTIONS,
    CountCheck,
    DestructiveSourceChange,
    check_member_contact_coverage,
    check_source_counts,
    contact_missing_allowance,
    count_checks,
    derived_baseline,
    derived_checks,
    derived_counts,
    load_full_source,
    maximum_drop_percent,
    member_contact_coverage,
    recorded_contact_missing,
    refuse_drops,
)
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
        [],  # end of Members
        [],  # member contact information: zero-origin probe, page 0
        [],  # ... and page 1 (family/member workgroups are not requested)
        page([]),  # ministry types
        [{"fundId": 9, "name": "Offertory", "active": True}],
    ]


def validate_count_trend(counts, *, previous_full_counts, maximum_drop_percent=25):
    """The record-count half of the loader's check, refused on its own."""
    checks = count_checks(
        counts,
        previous_full_counts=previous_full_counts,
        maximum_drop_percent=maximum_drop_percent,
    )
    empty = counts is None or counts["family"] == 0 or counts["member"] == 0
    refuse_drops(checks, empty=empty)


def validate_derived_trend(counts, *, baseline_counts, maximum_drop_percent=25):
    """The eligibility-count half of the loader's check, refused on its own."""
    refuse_drops(
        derived_checks(
            counts,
            baseline_counts=baseline_counts,
            maximum_drop_percent=maximum_drop_percent,
        )
    )


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
    with pytest.raises(DestructiveSourceChange, match="count loss"):
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
    with pytest.raises(DestructiveSourceChange, match="empty"):
        validate_count_trend(counts(**{kind: 0}), previous_full_counts=None)


@pytest.mark.parametrize("value", [True, -1, 101, 25.0])
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
    with pytest.raises(DestructiveSourceChange, match="count loss"):
        load_full_source(
            client,
            window=RefreshWindow(None, ()),
            as_of=TODAY,
            previous_full_counts=counts(),
        )


def test_wrong_tenant_retains_specific_classification_through_full_loader(tmp_path):
    """The generic ValueError redactor must not swallow the safe tenant signal."""
    client = client_factory(tmp_path, [[{"organizationID": 999}]])
    with pytest.raises(SourceOrganizationMismatch):
        load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)
    assert len(client.session.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        ShiftedSourceScan("Source collection repeats an identity."),
        SourceReferenceSkew("Source Member has no retained Family."),
        SourceLoadBudgetExceeded("Source load exceeds its time bound."),
    ],
)
def test_shifted_scan_keeps_its_retryable_type_through_full_loader(
    tmp_path, monkeypatch, error
):
    """The generic redactor must not turn a transient failure into bad data."""
    from parishkit.stewardship.source import loading

    def shifted(*args, **kwargs):
        raise error

    monkeypatch.setattr(loading, "load_families_and_members", shifted)
    client = client_factory(tmp_path, provider_pages())
    with pytest.raises(type(error)):
        load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)


@pytest.mark.parametrize("rows", [None, {}, [], [None], [{"organizationID": True}]])
def test_malformed_organization_is_invalid_data_not_a_proven_mismatch(tmp_path, rows):
    """An unreadable identity must not claim a positively observed other parish."""
    client = client_factory(tmp_path, [rows])
    with pytest.raises(InvalidSourcePayload):
        load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)


@pytest.mark.parametrize("name", [None, True, "", "  "])
def test_missing_expected_name_is_not_a_proven_name_mismatch(tmp_path, name):
    """A missing name is malformed evidence rather than a positive other parish."""
    client = client_factory(
        tmp_path, [[{"organizationID": 5, "organizationReportName": name}]]
    )
    client.config = replace(client.config, expected_organization="Expected Parish")
    with pytest.raises(InvalidSourcePayload):
        load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)


def test_different_valid_id_proves_mismatch_even_when_optional_name_is_missing(
    tmp_path,
):
    """Positive numeric evidence takes precedence over an absent descriptive name."""
    client = client_factory(tmp_path, [[{"organizationID": 999}]])
    client.config = replace(client.config, expected_organization="Expected Parish")
    with pytest.raises(SourceOrganizationMismatch):
        load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)


def eligibility(**values):
    """A synthetic corpus whose derived counts are exactly ``values`` (default 100)."""
    wanted = dict.fromkeys(DERIVED_COUNTS, 100) | values
    families = {
        str(index): {
            "portal_eligible": index < wanted["portal_eligible_families"],
            "email_eligible": index < wanted["email_eligible_families"],
            "active_head_duids": [index]
            if index < wanted["active_head_families"]
            else [],
        }
        for index in range(100)
    }
    contacts = {
        str(index): {
            "emails": [{"value": "x", "valid": index < wanted["valid_email_contacts"]}]
        }
        for index in range(100)
    }
    return {"family": families, "contact": contacts}


@pytest.mark.parametrize("name", DERIVED_COUNTS)
def test_eligibility_loss_uses_the_record_loss_threshold(name):
    """#320: rows can all stay while the facts eligibility needs disappear."""
    baseline = derived_counts(eligibility())
    assert baseline == dict.fromkeys(DERIVED_COUNTS, 100)
    validate_derived_trend(
        derived_counts(eligibility(**{name: 75})), baseline_counts=baseline
    )
    for after in (74, 0):
        with pytest.raises(DestructiveSourceChange, match="eligibility") as refused:
            validate_derived_trend(
                derived_counts(eligibility(**{name: after})), baseline_counts=baseline
            )
        # The refusal names the count and its before/after values, nothing else.
        assert refused.value.loss == (name, 100, after)
    # Growth, and a first load with nothing to compare, are never refused.
    validate_derived_trend(baseline, baseline_counts=baseline | {name: 50})
    validate_derived_trend(
        derived_counts(eligibility(**{name: 0})), baseline_counts=None
    )


def test_baseline_bounds_the_drop_from_both_the_last_full_and_current():
    """Step-by-step erosion over deltas is bounded by the last full snapshot."""
    full = dict.fromkeys(DERIVED_COUNTS, 100)
    current = dict.fromkeys(DERIVED_COUNTS, 80)
    baseline = derived_baseline(full, current)
    assert baseline == full
    # 64 is within 25% of the current 80, but not of the last full 100.
    with pytest.raises(DestructiveSourceChange) as refused:
        validate_derived_trend(
            dict.fromkeys(DERIVED_COUNTS, 64), baseline_counts=baseline
        )
    assert refused.value.loss == ("portal_eligible_families", 100, 64)
    # A delta that grew raises the bar for the next full load.
    assert derived_baseline(current, full | {"valid_email_contacts": 200}) == (
        full | {"valid_email_contacts": 200}
    )
    # Missing or malformed evidence is skipped rather than trusted.
    assert derived_baseline(None, {"portal_eligible_families": 1}) is None
    assert derived_baseline(None, current) == current


@pytest.mark.parametrize("value", [{}, {"portal_eligible_families": 1}, []])
def test_incomplete_eligibility_baseline_is_refused(value):
    with pytest.raises(InvalidSourcePayload, match="evidence"):
        validate_derived_trend(derived_counts(eligibility()), baseline_counts=value)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 25),
        ("", 25),
        ("40", 40),
        (" 100 ", 100),
        ("0", 0),
        ("101", 25),
        ("-1", 25),
        ("off", 25),
        ("٣٠", 25),
    ],
)
def test_threshold_override_is_read_from_the_environment(monkeypatch, value, expected):
    """Only a whole percent from 0 to 100 overrides; anything else keeps 25."""
    monkeypatch.delenv(DROP_OVERRIDE_VARIABLE, raising=False)
    if value is not None:
        monkeypatch.setenv(DROP_OVERRIDE_VARIABLE, value)
    assert maximum_drop_percent() == expected


def test_threshold_of_100_accepts_a_known_large_change_but_not_emptiness():
    """The documented recovery for a legitimate bulk change, e.g. inactivations."""
    baseline = dict.fromkeys(DERIVED_COUNTS, 100)
    validate_derived_trend(
        dict.fromkeys(DERIVED_COUNTS, 0),
        baseline_counts=baseline,
        maximum_drop_percent=100,
    )
    validate_count_trend(
        counts(ministry=0),
        previous_full_counts=counts(ministry=50),
        maximum_drop_percent=100,
    )
    with pytest.raises(DestructiveSourceChange, match="empty") as refused:
        validate_count_trend(
            counts(family=0), previous_full_counts=counts(), maximum_drop_percent=100
        )
    assert refused.value.loss == ("empty", None, 0)


def test_tiny_counts_falling_to_zero_are_refused():
    """On a tiny parish, losing the only one or two of something is still refused."""
    baseline = dict.fromkeys(DERIVED_COUNTS, 2)
    with pytest.raises(DestructiveSourceChange):
        validate_derived_trend(
            baseline | {"active_head_families": 0}, baseline_counts=baseline
        )


@pytest.mark.parametrize(
    "family_change,member_change",
    [
        # The Family is no longer registered here: not portal or email eligible.
        ({"registeredOrganizationID": None}, None),
        # The head's type and email vanish: no active head, no valid email.
        (None, {"memberType": None, "emailAddress": None}),
    ],
)
def test_stable_record_counts_with_collapsed_eligibility_are_refused(
    tmp_path, family_change, member_change
):
    """#320: the full loader refuses the collapse before anything is staged."""
    good = load_full_source(
        client_factory(tmp_path / "good", provider_pages()),
        window=RefreshWindow(None, ()),
        as_of=TODAY,
    )
    # Each load records its derived counts for later comparisons.
    assert good.evidence["derived_counts"] == derived_counts(good.corpus)
    client = client_factory(
        tmp_path / "bad",
        provider_pages(family_change=family_change, member_change=member_change),
    )
    with pytest.raises(DestructiveSourceChange, match="eligibility"):
        load_full_source(
            client,
            window=RefreshWindow(None, ()),
            as_of=TODAY,
            previous_full_counts=good.counts,
            previous_derived_counts=good.evidence["derived_counts"],
        )


def check(before, after, **limit):
    """Run the whole check on full record and derived counts; return the checks."""
    return check_source_counts(
        before["records"] if after is None else after["records"],
        before["derived"] if after is None else after["derived"],
        previous_full_counts=before["records"],
        previous_derived_counts=before["derived"],
        maximum_drop_percent=limit.get("limit", 25),
    )


def measured(records=None, **derived):
    """Record counts (``counts()`` with changes) and derived counts (default 100)."""
    return {
        "records": counts(**(records or {})),
        "derived": dict.fromkeys(DERIVED_COUNTS, 100) | derived,
    }


def test_every_count_is_checked_and_carried_by_the_refusal():
    """ADM-13: the refusal carries every count, not only the first that fell."""
    before = measured({"family": 100, "ministry": 40})
    after = measured(
        {"family": 70, "ministry": 0},
        email_eligible_families=50,
        valid_email_contacts=80,
    )
    with pytest.raises(DestructiveSourceChange, match="count loss") as refused:
        check(before, after)
    checks = refused.value.checks
    assert [item.measure for item in checks] == [*TREND_COLLECTIONS, *DERIVED_COUNTS]
    assert {item.measure for item in checks if item.failed} == {
        "family",
        "ministry",
        "email_eligible_families",
    }
    # The first failing count still names the refusal, as before.
    assert refused.value.loss == ("family", 100, 70)
    assert CountCheck("valid_email_contacts", 100, 80, 25, False) in checks
    assert CountCheck("ministry", 40, 0, 25, True) in checks
    # Growth and a load within the limit pass, and return their checks.
    passed = check(before, measured({"family": 120, "ministry": 30}))
    assert not any(item.failed for item in passed)
    assert len(passed) == len(TREND_COLLECTIONS) + len(DERIVED_COUNTS)


def test_an_eligibility_refusal_also_carries_the_record_counts():
    """Records within the limit are recorded with an eligibility collapse."""
    before = measured()
    with pytest.raises(DestructiveSourceChange, match="eligibility") as refused:
        check(before, measured(active_head_families=0))
    assert refused.value.loss == ("active_head_families", 100, 0)
    assert [item.measure for item in refused.value.checks if item.failed] == [
        "active_head_families"
    ]
    assert {item.measure for item in refused.value.checks} >= set(TREND_COLLECTIONS)


def test_an_empty_load_is_refused_with_its_counts_even_at_100_percent():
    """No Families stays refused; its counts are still carried for the record."""
    before = measured()
    with pytest.raises(DestructiveSourceChange, match="empty") as refused:
        check(before, measured({"family": 0}), limit=100)
    assert refused.value.loss == ("empty", None, 0)
    assert not any(item.failed for item in refused.value.checks)
    assert CountCheck("family", before["records"]["family"], 0, 100, False) in (
        refused.value.checks
    )


def test_a_first_load_records_counts_without_a_baseline():
    """With nothing to compare with, every check has no before and none fails."""
    checks = check_source_counts(
        counts(),
        dict.fromkeys(DERIVED_COUNTS, 3),
        previous_full_counts=None,
        previous_derived_counts=None,
        maximum_drop_percent=25,
    )
    assert all(item.before is None and not item.failed for item in checks)
    assert len(checks) == len(TREND_COLLECTIONS) + len(DERIVED_COUNTS)


def test_the_refusal_keeps_only_count_checks():
    """Anything but a CountCheck passed to the error is dropped, never logged."""
    error = DestructiveSourceChange(
        "PRIVATE", measure="family", before=2, after=0, checks=["PRIVATE", None]
    )
    assert error.checks == ()


def test_an_empty_load_is_refused_before_the_eligibility_baseline_is_read():
    """As before ADM-13: emptiness wins over a malformed derived baseline."""
    with pytest.raises(DestructiveSourceChange, match="empty") as refused:
        check_source_counts(
            counts(member=0),
            dict.fromkeys(DERIVED_COUNTS, 0),
            previous_full_counts=counts(),
            previous_derived_counts={"portal_eligible_families": 1},
            maximum_drop_percent=25,
        )
    assert [item.measure for item in refused.value.checks] == list(TREND_COLLECTIONS)


def test_contact_coverage_counts_searched_members_the_list_left_out():
    """#387 M4: counts only; a contact record for an unsearched Member is ignored."""
    members = dict.fromkeys((1, 2, 3), {})
    contactinfos = dict.fromkeys((2, 3, 9), {})
    assert member_contact_coverage(members, contactinfos) == {
        "members": 3,
        "contact_infos": 3,
        "missing": 1,
    }


@pytest.mark.parametrize(
    ("members", "allowance"), [(0, 10), (1, 10), (549, 10), (1000, 20), (6417, 128)]
)
def test_contact_missing_allowance_is_ten_or_two_percent(members, allowance):
    """The larger of the fixed and proportional margins, in whole Members."""
    assert contact_missing_allowance(members) == allowance


def test_contact_coverage_refuses_only_a_rise_past_the_allowance():
    """Up to the allowance above the baseline passes; one more is a shifted scan."""
    coverage = {"members": 6417, "contact_infos": 6253 - 128, "missing": 164 + 128}
    check_member_contact_coverage(coverage, previous_missing=164)
    # Fewer left out than before is never a problem.
    check_member_contact_coverage(coverage | {"missing": 0}, previous_missing=164)
    with pytest.raises(ShiftedSourceScan, match="left out 293 Members"):
        check_member_contact_coverage(coverage | {"missing": 293}, previous_missing=164)


def test_contact_coverage_without_a_baseline_or_with_a_raised_limit_records_only():
    """No baseline (first loads) or an operator-raised drop limit never refuses."""
    coverage = {"members": 6417, "contact_infos": 0, "missing": 6417}
    check_member_contact_coverage(coverage, previous_missing=None)
    check_member_contact_coverage(coverage, previous_missing=0, maximum_drop_percent=26)
    with pytest.raises(ShiftedSourceScan):
        check_member_contact_coverage(
            coverage, previous_missing=0, maximum_drop_percent=25
        )


@pytest.mark.parametrize(
    "cursor",
    [
        None,
        {},
        {"load": None},
        {"load": {}},
        {"load": {CONTACT_COVERAGE_KEY: None}},
        {"load": {CONTACT_COVERAGE_KEY: {}}},
        {"load": {CONTACT_COVERAGE_KEY: {"missing": "3"}}},
        {"load": {CONTACT_COVERAGE_KEY: {"missing": -1}}},
        {"load": {CONTACT_COVERAGE_KEY: {"missing": True}}},
    ],
)
def test_an_unrecorded_contact_baseline_is_none(cursor):
    """Snapshots from before #387 M4, or malformed evidence, give no baseline."""
    assert recorded_contact_missing(cursor) is None


def test_a_recorded_contact_baseline_is_read():
    """The count a full load recorded is the next full load's baseline."""
    cursor = {"load": {CONTACT_COVERAGE_KEY: {"members": 9, "missing": 4}}}
    assert recorded_contact_missing(cursor) == 4


def with_contact_list(values, *rows):
    """Replace the fixture's empty contact list pages with ``rows``.

    The zero-origin probe reads page 0 and page 1; equal non-empty pages
    prove a one-origin list, which then ends on an empty page 2.
    """
    probe = 6  # see provider_pages
    contact_pages = [list(rows), list(rows), []] if rows else [[], []]
    return values[:probe] + contact_pages + values[probe + 2 :]


def test_a_member_the_contact_list_left_out_is_counted_missing(tmp_path):
    """#387 M4, the realistic case: a search row with a null email still gives
    the Member a contact row, so only the recorded coverage shows the contact
    list left it out."""
    values = provider_pages(member_change={"emailAddress": None})
    client = client_factory(tmp_path, values)
    result = load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)
    assert [row["owner_kind"] for row in result.corpus["contact"].values()] == [
        "member"
    ]
    assert result.evidence[CONTACT_COVERAGE_KEY] == {
        "members": 1,
        "contact_infos": 0,
        "missing": 1,
    }


def test_a_complete_contact_list_records_no_missing_member(tmp_path):
    """A contact record for the searched Member leaves nobody out."""
    values = provider_pages()
    member_id = values[4][0]["memberDUID"]
    values = with_contact_list(values, {"memberDUID": member_id, "emailAddress": None})
    client = client_factory(tmp_path, values)
    result = load_full_source(client, window=RefreshWindow(None, ()), as_of=TODAY)
    assert result.evidence[CONTACT_COVERAGE_KEY] == {
        "members": 1,
        "contact_infos": 1,
        "missing": 0,
    }


def test_a_contact_list_rise_past_the_baseline_is_refused_before_giving(
    tmp_path, monkeypatch
):
    """The full load refuses as a shifted scan before normalizing or reading
    giving; with no baseline the same load is recorded and returned."""
    monkeypatch.setattr(loading, "CONTACT_MISSING_MARGIN", 0)
    values = provider_pages(member_change={"emailAddress": None})
    client = client_factory(tmp_path, values)
    with pytest.raises(ShiftedSourceScan, match="left out 1 Members"):
        load_full_source(
            client,
            window=RefreshWindow(None, ()),
            as_of=TODAY,
            previous_contact_missing=0,
        )
    # The Fund catalog, read after the shared loader, was never requested.
    assert len(client.session.calls) == len(values) - 1
    again = client_factory(tmp_path, values)
    result = load_full_source(
        again,
        window=RefreshWindow(None, ()),
        as_of=TODAY,
        previous_contact_missing=None,
    )
    assert result.evidence[CONTACT_COVERAGE_KEY]["missing"] == 1
