"""Coherent shared-loader contract tests using wholly synthetic provider responses."""

import json

import pytest
from test_parishsoft import Session

from parishkit.parishsoft import ParishSoftConfig, load_families_and_members
from parishkit.parishsoft_pagination import IncompleteSourceCollection
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.parishsoft_transport import ExactSourceResponse


def response(value):
    """Use the actual strict JSON decoder without making any network request."""
    result = ExactSourceResponse()
    result.status_code, result.url, result.encoding = (
        200,
        "https://example.invalid",
        "utf-8",
    )
    result._content = json.dumps(value).encode()
    result._content_consumed = True
    return result


def source(tmp_path, values=(), **options):
    """Start with one exact synthetic organization and no other inherited authority."""
    return CoherentParishSoftClient(
        ParishSoftConfig(api_key="SYNTHETIC", cache_dir=tmp_path / "unused-cache"),
        organization_id=5,
        session=Session([response(value) for value in values]),
        **options,
    )


def initialized(tmp_path, values=(), **options):
    """Validation is real client logic; only the HTTP Session is replaced."""
    client = source(tmp_path, [[{"organizationID": 5}], *values], **options)
    assert client.validate_organization() == 5
    return client


def family(identifier=1, *, total=1, row=1):
    """Include the documented matching-total/row ordinal fields."""
    return {"familyDUID": identifier, "totalResults": total, "rowNumber": row}


def member(identifier=1, *, total=1, row=1):
    """A member search row uses different count and ordinal field names."""
    return {"memberDUID": identifier, "recordCount": total, "rowNum": row}


def page(rows, *, position=1, size=500, total=None):
    """Create a complete published paging envelope with no permissive defaults."""
    total = len(rows) if total is None else total
    return {
        "data": rows,
        "pagingInfo": {
            "pageNumber": position,
            "pageSize": size,
            "totalRecords": total,
            "totalPages": (total + size - 1) // size,
        },
    }


def test_family_search_uses_actual_wire_names_and_disables_all_cache(tmp_path):
    """Shared loader options cannot send obsolete Limit/PageNumber spellings."""
    client = initialized(tmp_path, [[family()], []])
    assert client.post_paginated(
        "families/search",
        {"organizationIDs": [5]},
        offset_name="PageNumber",
        offset_type="page",
    ) == [family()]
    calls = [call[2]["json"] for call in client.session.calls[1:]]
    assert calls == [
        {"organizationIDs": [5], "pageSize": 500, "pageNumber": 1},
        {"organizationIDs": [5], "pageSize": 500, "pageNumber": 2},
    ]
    assert not client.config.cache_enabled
    assert not client.config.cache_dir.exists()


@pytest.mark.parametrize(
    "endpoint,rows,size_name,position_name",
    [
        ("members/search", [member()], "maximumRows", "startRowIndex"),
        ("members/contact/list", [{"memberDUID": 1}], "limit", "offset"),
    ],
)
def test_zero_origin_searches_probe_without_skipping_first_record(
    tmp_path, endpoint, rows, size_name, position_name
):
    """Documented default-zero fields do not inherit the legacy one-origin loop."""
    client = initialized(tmp_path, [rows, []])
    assert client.post_paginated(endpoint, offset_type="page") == rows
    assert [call[2]["json"] for call in client.session.calls[1:]] == [
        {"organizationIDs": [5], size_name: 500, position_name: position}
        for position in (0, 1)
    ]


@pytest.mark.parametrize(
    "endpoint,row",
    [
        ("families/workgroup/4/list", {"familyId": 1}),
        ("members/workgroup/lookup/list", {"id": 1}),
        ("members/workgroup/4/list", {"memberId": 1}),
        ("ministry/type/list", {"id": 1}),
        ("offering/pledge/list", {"pledgeID": 1}),
        ("offering/contributiondetail/list", {"contributionID": 1}),
    ],
)
def test_get_collections_use_exact_envelope_and_identity_contract(
    tmp_path, endpoint, row
):
    """Default legacy parameter names cannot undermine envelope completeness."""
    client = initialized(tmp_path, [page([row])])
    assert client.get_paginated(endpoint, offset_name="PageNumber") == [row]
    parameters = client.session.calls[-1][2]["params"]
    assert parameters["PageSize"] == 500 and parameters["PageNumber"] == 1
    assert "Limit" not in parameters
    if endpoint in {"ministry/type/list", "members/workgroup/lookup/list"}:
        assert parameters["organizationId"] == 5


def test_family_workgroup_array_requires_its_own_total_and_ordinal(tmp_path):
    """An array endpoint is not falsely treated as a paging envelope."""
    row = {"workgroupDUID": 9, "recordCount": 1, "rowNum": 1}
    client = initialized(tmp_path, [[row], []])
    assert client.get_paginated("families/workgroup/list") == [row]


@pytest.mark.parametrize("identifier", [None, True, "1", 0, 2**31])
def test_invalid_primary_identity_cannot_be_coerced_or_overwritten(
    tmp_path, identifier
):
    """Provider DTO primary identities must be actual positive int32 values."""
    client = initialized(tmp_path, [[family(identifier)]])
    with pytest.raises(IncompleteSourceCollection):
        client.post_paginated("families/search")


@pytest.mark.parametrize(
    "endpoint,key",
    [("families/group/lookup/list", "famGroupID"), ("offering/5/funds", "fundId")],
)
def test_whole_lookup_duplicates_are_rejected_before_dictionary_indexing(
    tmp_path, endpoint, key
):
    """Non-paged lookups receive the same unique-identity protection."""
    client = initialized(tmp_path, [[{key: 1}, {key: 1}]])
    with pytest.raises(IncompleteSourceCollection, match="repeats"):
        client.get(endpoint)


def test_ministry_roster_identity_includes_role_event_and_start_date(tmp_path):
    """Multiple valid memberships for one Member are not mistaken for duplicates."""
    rows = [
        {
            "memberId": 1,
            "ministryTypeId": 5,
            "ministryRoleId": role,
            "ministryEventId": 7,
            "startDate": "2025-01-01",
        }
        for role in (1, 2)
    ]
    client = initialized(tmp_path, [page(rows)])
    assert client.get_paginated("ministry/5/minister/list") == rows


@pytest.mark.parametrize(
    "row",
    [
        {"memberId": True},
        {"memberId": 1, "ministryRoleId": True},
        {"memberId": 1, "startDate": []},
    ],
)
def test_invalid_roster_identity_fails_closed(tmp_path, row):
    """Malformed role/event/date identity cannot disappear during roster indexing."""
    client = initialized(tmp_path, [page([row])])
    with pytest.raises(IncompleteSourceCollection):
        client.get_paginated("ministry/5/minister/list")


def test_unvalidated_client_cannot_read_any_scoped_collection(tmp_path):
    """A configured ID is not proof that the source API key belongs to that tenant."""
    client = source(tmp_path)
    for read in (
        lambda: client.get("families/group/lookup/list"),
        lambda: client.get_paginated("ministry/type/list"),
        lambda: client.post_paginated("families/search"),
        lambda: client.post_uncached("families/search"),
    ):
        with pytest.raises(IncompleteSourceCollection, match="not been validated"):
            read()
    assert not client.session.calls


@pytest.mark.parametrize(
    "endpoint,parameters",
    [
        ("offering/pledge/list", {"OrganizationID": 6}),
        ("offering/pledge/list", {"organizationId": 5}),
        ("offering/contributiondetail/list", {"OrganizationId": "5"}),
        ("ministry/type/list", {"organizationId": 6}),
        ("ministry/type/list", {"OrganizationId": 5}),
        ("families/workgroup/4/list", {"organizationId": 5}),
    ],
)
def test_ambiguous_or_wrong_tenant_query_cannot_be_silently_overridden(
    tmp_path, endpoint, parameters
):
    """Canonical tenant binding cannot coexist with another provider query alias."""
    client = initialized(tmp_path)
    with pytest.raises(IncompleteSourceCollection, match="tenant"):
        client.get_paginated(endpoint, parameters)
    assert len(client.session.calls) == 1


def test_nonstring_query_keys_are_rejected_before_transport(tmp_path):
    """Malformed internal options have a static error, not an incidental exception."""
    client = initialized(tmp_path)
    with pytest.raises(ValueError, match="mapping"):
        client.get_paginated("ministry/type/list", {1: "PRIVATE"})
    assert len(client.session.calls) == 1


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"organizationID": 6}],
        [{"organizationID": 5}, {"organizationID": 6}],
        [{"organizationID": True}],
    ],
)
def test_organization_guard_is_exact_and_not_a_cache_shortcut(tmp_path, rows):
    """Another/ambiguous tenant invalidates even a previously successful guard."""
    client = initialized(tmp_path, [rows])
    with pytest.raises(IncompleteSourceCollection):
        client.validate_organization()
    assert client._organization_id is None
    assert len(client.session.calls) == 2


def test_wrong_tenant_or_caller_paging_filters_are_rejected_before_http(tmp_path):
    """Caller-supplied pagination cannot shadow the validated scan contract."""
    client = initialized(tmp_path)
    with pytest.raises(IncompleteSourceCollection):
        client.post_paginated("families/search", {"organizationIDs": [6]})
    with pytest.raises(IncompleteSourceCollection):
        client.get("offering/6/funds")
    with pytest.raises(ValueError):
        client.post_paginated("families/search", {"PAGEsize": 2})
    with pytest.raises(ValueError):
        client.get_paginated("ministry/type/list", unsafe_option=True)
    with pytest.raises(IncompleteSourceCollection):
        client.get_paginated("unknown/list")
    assert len(client.session.calls) == 1


def test_aggregate_request_byte_and_time_bounds_are_not_partial_success(tmp_path):
    """Many individually small pages still cannot accumulate unbounded data."""
    client = initialized(tmp_path, maximum_requests=1)
    with pytest.raises(IncompleteSourceCollection, match="request/time"):
        client.post_paginated("families/search")
    assert len(client.session.calls) == 1
    client = source(tmp_path, [[{"organizationID": 5}]], maximum_bytes=1)
    with pytest.raises(IncompleteSourceCollection, match="byte/time"):
        client.validate_organization()
    client = initialized(tmp_path)
    client.deadline = 0
    with pytest.raises(IncompleteSourceCollection, match="request/time"):
        client.get("families/group/lookup/list")


def test_shared_full_loader_uses_coherent_contracts_and_retains_inactive_source(
    tmp_path,
):
    """Exercise the existing aggregation path with the opt-in strict source client."""
    family_row = family() | {
        "familyParticipationStatus": "Inactive",
        "lastName": "Example",
    }
    member_row = member() | {
        "familyDUID": 1,
        "firstName": "Example",
        "memberStatus": "Inactive",
    }
    client = source(
        tmp_path,
        [
            [{"organizationID": 5}],
            [family_row],
            [],
            [],
            [member_row],
            [],
            [],
            [{"memberDUID": 1}],
            [],
            page([]),
            page([]),
        ],
    )
    corpus = load_families_and_members(
        client,
        active_only=False,
        parishioners_only=False,
        include_deceased=True,
    )
    assert set(corpus.families) == {1} and set(corpus.members) == {1}
    assert corpus.members[1]["py family"] is corpus.families[1]
    assert not corpus.funds and not corpus.pledges and not corpus.contributions
    assert not client.session.responses


@pytest.mark.parametrize("retain_empty", [False, True])
def test_source_load_can_retain_empty_families_without_changing_tool_defaults(
    tmp_path, retain_empty
):
    """An inactive empty household must remain observable to source reconciliation."""
    family_rows = [
        family(identifier, total=2, row=identifier) | {"lastName": "Example"}
        for identifier in (1, 2)
    ]
    member_row = member() | {"familyDUID": 1, "firstName": "Example"}
    client = source(
        tmp_path,
        [
            [{"organizationID": 5}],
            family_rows,
            [],
            [],
            [member_row],
            [],
            [],
            [],
            [],
            page([]),
            page([]),
        ],
    )
    corpus = load_families_and_members(
        client,
        active_only=False,
        parishioners_only=False,
        include_deceased=True,
        retain_empty_families=retain_empty,
    )
    assert set(corpus.families) == ({1, 2} if retain_empty else {1})
    if retain_empty:
        assert corpus.families[2]["py members"] == []
