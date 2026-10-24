"""A ParishSoft key that reaches several organizations (#1554), with synthetic data.

ParishKit loads only the configured organization: every scoped request still
names it alone, other organizations' records are dropped and counted, and a
record whose organization field is missing or malformed fails the load. A key
that reaches only the configured organization is read exactly as before.
"""

from pathlib import Path

import pytest
from test_parishsoft import Response, Session
from test_parishsoft_source import member, page, response

from parishkit.config import ConfigError
from parishkit.parishsoft import (
    ParishSoftClient,
    ParishSoftConfig,
    select_organization,
)
from parishkit.parishsoft_pagination import IncompleteSourceCollection
from parishkit.parishsoft_source import (
    MAXIMUM_ORGANIZATIONS,
    CoherentParishSoftClient,
    SourceOrganizationMismatch,
)

OURS, OTHER = 5, 6
REACHES_TWO = [
    {"organizationID": OTHER, "organizationReportName": "Neighbor"},
    {"organizationID": OURS, "organizationReportName": "Parish"},
]


def coherent(tmp_path, values, *, expected_organization=None):
    """A coherent client for organization 5 whose Session replays ``values``."""
    return CoherentParishSoftClient(
        ParishSoftConfig(
            api_key="SYNTHETIC",
            cache_dir=tmp_path / "unused-cache",
            expected_organization=expected_organization,
        ),
        organization_id=OURS,
        session=Session([response(value) for value in values]),
    )


def test_a_key_that_also_reaches_others_validates_for_the_configured_one(tmp_path):
    """The configured organization among several passes; the count is kept."""
    client = coherent(tmp_path, [REACHES_TWO], expected_organization="Parish")
    assert client.validate_organization() == OURS
    assert client.organizations_reached == 2


def test_report_name_is_checked_against_the_configured_record_only(tmp_path):
    """Another organization's name matching the setting does not pass the check."""
    client = coherent(tmp_path, [REACHES_TWO], expected_organization="Neighbor")
    with pytest.raises(SourceOrganizationMismatch):
        client.validate_organization()
    assert client._organization_id is None


def test_a_key_that_does_not_reach_it_is_a_mismatch(tmp_path):
    """Several organizations without the configured one: refused for good."""
    client = coherent(tmp_path, [[{"organizationID": 6}, {"organizationID": 7}]])
    with pytest.raises(SourceOrganizationMismatch):
        client.validate_organization()


def test_an_unbounded_organization_list_is_incomplete(tmp_path):
    """The record count is capped, whatever the byte bound allows."""
    rows = [{"organizationID": n} for n in range(1, MAXIMUM_ORGANIZATIONS + 2)]
    client = coherent(tmp_path, [rows])
    with pytest.raises(IncompleteSourceCollection) as error:
        client.validate_organization()
    assert not isinstance(error.value, SourceOrganizationMismatch)


def members_and_contacts(tmp_path, organizations, members, contacts):
    """Validate, then read Members and their contact rows through the client."""
    client = coherent(tmp_path, [organizations, members, [], contacts, []])
    client.validate_organization()
    found = client.post_paginated("members/search", offset_type="page")
    return client, found, client.post_paginated("members/contact/list")


def test_other_organizations_members_and_their_contacts_are_dropped(tmp_path):
    """Members are kept on organizationID; a dropped Member's contact row too."""
    ours = member(1, total=2, row=1) | {"organizationID": OURS, "familyDUID": 10}
    theirs = member(2, total=2, row=2) | {"organizationID": OTHER, "familyDUID": 20}
    client, members, contacts = members_and_contacts(
        tmp_path,
        REACHES_TWO,
        [ours, theirs],
        [
            {"memberDUID": 1, "ownerOrganizationID": OURS},
            {"memberDUID": 2, "ownerOrganizationID": OTHER},
        ],
    )
    assert members == [ours]
    assert contacts == [{"memberDUID": 1, "ownerOrganizationID": OURS}]
    assert client.other_organization_skipped == {
        "members": 1,
        "member_contactinfos": 1,
    }
    assert client.other_organization_families == {20}
    # Every scoped request still names the configured organization alone.
    assert all(
        call[2]["json"]["organizationIDs"] == [OURS]
        for call in client.session.calls[1:]
    )


def test_a_kept_members_contact_row_for_another_owner_fails(tmp_path):
    """Contact rows are not filtered on their owner: a mismatch is a failure."""
    ours = member(1) | {"organizationID": OURS}
    with pytest.raises(IncompleteSourceCollection, match="organization"):
        members_and_contacts(
            tmp_path,
            REACHES_TWO,
            [ours],
            [{"memberDUID": 1, "ownerOrganizationID": OTHER}],
        )


@pytest.mark.parametrize("value", [None, "5", True, 0])
def test_a_missing_or_malformed_organization_field_fails_the_load(tmp_path, value):
    """A record that cannot be proved to be the parish's is never kept or dropped."""
    row = member(1) | ({} if value is None else {"organizationID": value})
    client = coherent(tmp_path, [REACHES_TWO, [row], []])
    client.validate_organization()
    with pytest.raises(IncompleteSourceCollection, match="organization"):
        client.post_paginated("members/search", offset_type="page")


@pytest.mark.parametrize(
    "endpoint,identity,field,parameter",
    [
        ("ministry/type/list", "id", "organizationId", "organizationId"),
        (
            "members/workgroup/lookup/list",
            "id",
            "ownerOrganizationId",
            "organizationId",
        ),
        ("offering/pledge/list", "pledgeID", "organizationID", "OrganizationID"),
        (
            "offering/contributiondetail/list",
            "contributionID",
            "organizationId",
            "OrganizationId",
        ),
    ],
)
def test_scoped_lists_drop_and_count_other_organizations(
    tmp_path, endpoint, identity, field, parameter
):
    """Each collection with an organization field keeps only the parish's rows."""
    rows = [{identity: 1, field: OURS}, {identity: 2, field: OTHER}]
    client = coherent(tmp_path, [REACHES_TWO, page(rows)])
    client.validate_organization()
    assert client.get_paginated(endpoint) == [rows[0]]
    assert sum(client.other_organization_skipped.values()) == 1
    assert client.session.calls[1][2]["params"][parameter] == OURS


def test_family_workgroups_are_kept_on_their_owner(tmp_path):
    """The Family WorkGroup list has no organization parameter, only its owner."""
    rows = [
        {
            "workgroupDUID": duid,
            "ownerOrganizationID": owner,
            "recordCount": 2,
            "rowNum": duid,
        }
        for duid, owner in ((1, OURS), (2, OTHER))
    ]
    client = coherent(tmp_path, [REACHES_TWO, rows, []])
    client.validate_organization()
    assert client.get_paginated("families/workgroup/list") == [rows[0]]
    assert client.other_organization_skipped == {"family_workgroups": 1}


def test_a_single_organization_key_reads_exactly_as_before(tmp_path):
    """No filtering, no field requirement and no counts for a one-organization key."""
    rows = [{"id": 1}, {"id": 2, "organizationId": OTHER}]
    client = coherent(tmp_path, [[{"organizationID": OURS}], page(rows)])
    client.validate_organization()
    assert client.organizations_reached == 1
    assert client.get_paginated("ministry/type/list") == rows
    assert client.other_organization_skipped == {}


# -- Non-Stewardship tools ----------------------------------------------------


def plain(tmp_path, responses, **settings):
    """A ParishSoftClient for the shared tools, with a disabled cache age."""
    return ParishSoftClient(
        ParishSoftConfig(
            api_key="key",
            cache_dir=tmp_path,
            cache_limit=0,
            api_base_url="https://example/api",
            **settings,
        ),
        session=Session(responses),
    )


def test_tools_still_refuse_a_multi_organization_key_without_a_selection(tmp_path):
    """The refusal says how many it reaches and how to pick one, with no names."""
    ps = plain(tmp_path, [Response(REACHES_TWO)])
    with pytest.raises(ConfigError) as error:
        ps.validate_organization()
    message = str(error.value)
    assert "reaches 2 organizations" in message
    assert "parishsoft.organization_id" in message
    assert "--ps-organization-id" in message
    assert "Neighbor" not in message


def test_tools_select_the_configured_organization_by_id(tmp_path):
    """``organization_id`` loads that one and drops the others' records."""
    ps = plain(
        tmp_path,
        [
            Response(REACHES_TWO),
            Response(
                {
                    "data": [
                        {"id": 1, "organizationId": 5},
                        {"id": 2, "organizationId": 6},
                    ]
                }
            ),
        ],
        organization_id=OURS,
    )
    assert ps.validate_organization() == OURS
    assert ps.get_paginated("ministry/type/list") == [{"id": 1, "organizationId": 5}]
    assert ps.other_organization_skipped == {"ministry_types": 1}


def test_tools_select_by_a_unique_report_name(tmp_path):
    """Without an ID, a unique ``expected_organization`` match selects it."""
    ps = plain(tmp_path, [Response(REACHES_TWO)], expected_organization="Parish")
    assert ps.validate_organization() == OURS


@pytest.mark.parametrize(
    "organizations,settings,message",
    [
        ([], {}, "expected one ParishSoft organization, got 0"),
        (REACHES_TWO, {"organization_id": 9}, "does not reach organization 9"),
        (
            [{"organizationID": 7, "organizationReportName": "Parish"}],
            {"organization_id": 9},
            "does not reach organization 9",
        ),
        (
            REACHES_TWO + [{"organizationID": 7, "organizationReportName": "Parish"}],
            {"report_name": "Parish"},
            "reaches 3 organizations",
        ),
        ({"organizationID": 5}, {}, "response is invalid"),
    ],
)
def test_selection_refuses_anything_but_one_clear_choice(
    organizations, settings, message
):
    """No guess: a missing, repeated or ambiguous choice is a ConfigError."""
    with pytest.raises(ConfigError, match=message):
        select_organization(organizations, **settings)


def test_a_single_organization_tool_key_is_unchanged(tmp_path):
    """The historical one-organization path selects it and filters nothing."""
    ps = plain(
        tmp_path,
        [
            Response([{"organizationID": 7, "organizationReportName": "Parish"}]),
            Response({"data": [{"id": 1}]}),
        ],
    )
    assert ps.validate_organization() == 7
    assert ps.get_paginated("ministry/type/list") == [{"id": 1}]
    assert ps.organizations_reached == 1


def test_organization_id_setting_is_validated():
    """A non-integer or out-of-range selection is refused at configuration time."""
    for value in ("5", 0, True, 2**31):
        with pytest.raises(ConfigError):
            ParishSoftConfig(api_key="key", cache_dir=Path("."), organization_id=value)


def test_contact_rows_are_kept_only_for_kept_members(tmp_path):
    """A contact row for a Member the search did not keep is never loaded."""
    ours = member(1) | {"organizationID": OURS}
    client, members, contacts = members_and_contacts(
        tmp_path,
        REACHES_TWO,
        [ours],
        [
            {"memberDUID": 1, "ownerOrganizationID": OURS},
            {"memberDUID": 3, "ownerOrganizationID": OURS},
        ],
    )
    assert contacts == [{"memberDUID": 1, "ownerOrganizationID": OURS}]
    assert client.other_organization_skipped == {"member_contactinfos": 1}


def test_contact_rows_before_members_fail_with_a_multi_organization_key(tmp_path):
    """Without the kept Members, no contact row can be shown to be the parish's."""
    client = coherent(tmp_path, [REACHES_TWO, [{"memberDUID": 1}], []])
    client.validate_organization()
    with pytest.raises(IncompleteSourceCollection, match="organization"):
        client.post_paginated("members/contact/list")


def test_the_tools_cache_never_holds_another_organizations_records(tmp_path):
    """Records are filtered before the shared client writes its cache."""
    ps = plain(
        tmp_path,
        [
            Response(REACHES_TWO),
            Response(
                {
                    "data": [
                        {"id": 1, "organizationId": 5},
                        {"id": 2, "organizationId": 6},
                    ]
                }
            ),
        ],
        organization_id=OURS,
    )
    object.__setattr__(ps.config, "cache_limit", None)
    ps.validate_organization()
    assert ps.get_paginated("ministry/type/list") == [{"id": 1, "organizationId": 5}]
    cached = "".join(path.read_text() for path in tmp_path.rglob("*.json"))
    assert '"organizationId": 6' not in cached and '"organizationId":6' not in cached
    assert "organizationId" in cached
    # A cache hit answers the same, with no request.
    assert ps.get_paginated("ministry/type/list") == [{"id": 1, "organizationId": 5}]
    assert len(ps.session.calls) == 2
