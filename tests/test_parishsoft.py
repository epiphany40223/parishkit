from __future__ import annotations

import datetime as dt
import json
import logging
import stat
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.parishsoft import (
    ParishSoftAPIError,
    ParishSoftClient,
    ParishSoftConfig,
    family_business_logistics_emails,
    family_business_logistics_emails_members,
    family_is_active,
    get_family_heads,
    get_member_public_email,
    get_member_public_phones,
    link_families_and_members,
    load_contribution_details,
    load_families_and_members,
    member_is_active,
    ministry_membership_is_current,
    normalize_family_email,
    normalize_member_email,
    parse_cache_limit,
    salutation_for_members,
)


class Response:
    """Minimal stand-in for a ``requests`` response backed by a fixed payload."""

    def __init__(
        self,
        payload,
        *,
        status_code=200,
        url="https://example/api",
        json_error: Exception | None = None,
    ):
        """Store the canned payload, status, and URL the client will read."""
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.url = url
        self.json_error = json_error

    def json(self):
        """Return the canned payload, mirroring ``requests.Response.json``."""
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class Session:
    """Fake HTTP session that replays queued responses and records calls.

    Each ``get``/``post`` pops the next queued ``Response`` in order, so tests
    line up one ``Response`` per expected request and inspect ``calls`` to
    assert on the URLs and keyword arguments the client sent.
    """

    def __init__(self, responses):
        """Seed the response queue and the empty recorded-call log."""
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        """Record a GET and return the next queued response."""
        self.calls.append(("get", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        """Record a POST and return the next queued response."""
        self.calls.append(("post", url, kwargs))
        return self.responses.pop(0)


def client(tmp_path, responses):
    """Build a ParishSoftClient wired to a fake session of canned responses."""
    return ParishSoftClient(
        ParishSoftConfig(
            api_key="key",
            cache_dir=tmp_path,
            cache_limit=None,
            api_base_url="https://example/api",
        ),
        session=Session(responses),
    )


def test_parse_cache_limit():
    """Cache-limit strings parse to seconds; non-string input is rejected."""
    assert parse_cache_limit("15m") == 900
    assert parse_cache_limit("2d") == 172800
    with pytest.raises(ConfigError):
        parse_cache_limit(True)


def test_validate_organization(tmp_path):
    """validate_organization returns the org ID whose report name matches.

    The config is swapped after construction (via object.__setattr__ because
    ParishSoftConfig is frozen) so the expected organization name is in place.
    """
    ps = client(
        tmp_path,
        [
            Response(
                [{"organizationID": 7, "organizationReportName": "Parish"}],
            )
        ],
    )
    object.__setattr__(
        ps,
        "config",
        ParishSoftConfig(
            api_key="key",
            cache_dir=tmp_path,
            expected_organization="Parish",
            cache_limit=0,
            api_base_url="https://example/api",
        ),
    )

    assert ps.validate_organization() == 7


def test_parishsoft_config_rejects_malformed_types(tmp_path):
    """ParishSoftConfig raises ConfigError for wrongly typed fields.

    Covers a non-numeric timeout, a cache_dir that is a str instead of a Path,
    and a non-string api_base_url.
    """
    with pytest.raises(ConfigError, match="timeout must be a number"):
        ParishSoftConfig(api_key="key", cache_dir=tmp_path, timeout="30")
    with pytest.raises(ConfigError, match="cache_dir"):
        ParishSoftConfig(api_key="key", cache_dir=str(tmp_path))
    with pytest.raises(ConfigError, match="api_base_url"):
        ParishSoftConfig(api_key="key", cache_dir=tmp_path, api_base_url=123)


def test_paginated_get_handles_dict_pages(tmp_path):
    """get_paginated follows pagingInfo across pages and concatenates rows.

    Also confirms the request carries the default 30-second timeout.
    """
    ps = client(
        tmp_path,
        [
            Response(
                {"data": [{"id": 1}], "pagingInfo": {"pageNumber": 1, "totalPages": 2}}
            ),
            Response(
                {"data": [{"id": 2}], "pagingInfo": {"pageNumber": 2, "totalPages": 2}}
            ),
        ],
    )

    assert ps.get_paginated("families/search") == [{"id": 1}, {"id": 2}]
    assert ps.session.calls[0][2]["timeout"] == 30.0


def test_paginated_get_logs_page_progress(tmp_path, caplog):
    """DEBUG logging reports page-level ParishSoft loading progress."""
    ps = client(
        tmp_path,
        [
            Response(
                {"data": [{"id": 1}], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}
            )
        ],
    )

    with caplog.at_level(logging.DEBUG, logger="parishkit.parishsoft"):
        assert ps.get_paginated("families/search") == [{"id": 1}]

    assert "Fetched ParishSoft GET page for families/search" in caplog.text


def test_cache_round_trip(tmp_path):
    """A cached GET is served from disk on repeat and uses restrictive perms.

    Only one queued response is supplied, so the second get() must hit the
    cache. The cache dir (0o700) and file (0o600) must stay owner-only because
    they can hold personal contact data.
    """
    ps = client(tmp_path, [Response([{"id": 1}])])

    assert ps.get("lookup") == [{"id": 1}]
    # Second call returns the same data without a new request being recorded.
    assert ps.get("lookup") == [{"id": 1}]
    assert len(ps.session.calls) == 1
    cache_file = next(tmp_path.glob("cache-v2-*-lookup*.json"))
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600


def test_validate_organization_bypasses_stale_cache(tmp_path):
    """Organization validation always calls ParishSoft, never stale cache data."""
    ps = client(
        tmp_path,
        [
            Response(
                [{"organizationID": 8, "organizationReportName": "Fresh"}],
            )
        ],
    )
    object.__setattr__(
        ps,
        "config",
        ParishSoftConfig(
            api_key="key",
            cache_dir=tmp_path,
            expected_organization="Fresh",
            cache_limit=None,
            api_base_url="https://example/api",
        ),
    )
    stale_cache = (
        tmp_path
        / "cache-v2-deadbeef-org-unvalidated-organizations-search-post-999.json"
    )
    stale_cache.write_text(
        json.dumps(
            {
                "created": dt.datetime.now(dt.UTC).isoformat(),
                "data": [{"organizationID": 7, "organizationReportName": "Stale"}],
            }
        ),
        encoding="utf-8",
    )

    assert ps.validate_organization() == 8
    assert len(ps.session.calls) == 1


def test_cache_file_scope_includes_validated_organization(tmp_path):
    """After validation, cache files include the resolved organization ID."""
    ps = client(
        tmp_path,
        [
            Response([{"organizationID": 7, "organizationReportName": "Parish"}]),
            Response([{"id": 1}]),
        ],
    )

    assert ps.validate_organization() == 7
    assert ps.get("lookup") == [{"id": 1}]

    assert next(tmp_path.glob("cache-v2-*-org-7-lookup*.json"))


def test_post_uncached_bypasses_cache(tmp_path):
    """post_uncached re-requests every time instead of reusing a cached result.

    Identical calls return the two distinct queued responses in order, proving
    the second call was not served from cache.
    """
    ps = client(tmp_path, [Response([{"id": 1}]), Response([{"id": 2}])])

    assert ps.post_uncached("mutation", {"id": 1}) == [{"id": 1}]
    assert ps.post_uncached("mutation", {"id": 1}) == [{"id": 2}]
    assert len(ps.session.calls) == 2


def test_exhausted_transient_response_raises_typed_api_error(tmp_path):
    """A persistent transient (503) surfaces as ParishSoftAPIError once retries run out.

    The retry policy is replaced with a single, zero-delay attempt so the test
    exhausts retries immediately instead of waiting on backoff.
    """
    ps = client(tmp_path, [Response({}, status_code=503)])
    ps.retry_policy = type(ps.retry_policy)(attempts=1, initial_delay=0)

    with pytest.raises(ParishSoftAPIError, match="503"):
        ps.get("lookup")


def test_malformed_json_response_raises_typed_api_error(tmp_path):
    """A successful HTTP status with invalid JSON stays a user-facing API error."""
    ps = client(tmp_path, [Response("not json", json_error=ValueError("not json"))])

    with pytest.raises(ParishSoftAPIError, match="invalid JSON"):
        ps.get("lookup")


def test_load_contribution_details_passes_optional_filters(tmp_path):
    """Optional start_date/family_id/member_id become the expected query params.

    Also confirms returned contributions are keyed by contribution ID and that
    fund and pledge lookups are attached under the ``py fund``/``py pledge``
    keys. The setup stays local to this test so fixtures remain easy to
    understand and change.
    """
    ps = client(
        tmp_path,
        [
            Response(
                {
                    "data": [
                        {
                            "contributionID": 9,
                            "contributionDate": "2026-01-15T00:00:00",
                            "fundId": 3,
                            "pledgeId": 4,
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            )
        ],
    )

    contributions = load_contribution_details(
        ps,
        {3: {"id": 3}},
        {4: {"id": 4}},
        start_date="2026-01-01",
        family_id=10,
        member_id=20,
    )

    params = ps.session.calls[0][2]["params"]
    assert params["startDate"] == "2026-01-01"
    assert params["FamilyId"] == 10
    assert params["MemberId"] == 20
    assert contributions[9]["py fund"] == {"id": 3}
    assert contributions[9]["py pledge"] == {"id": 4}


def test_load_families_and_members_aggregate(tmp_path):
    """load_families_and_members stitches every sub-resource into one dataset.

    The queued responses model the full fan-out of calls the loader makes
    (organization, families, family groups, members, workgroups, contact info,
    member workgroups, and ministries). The assertions check that families and
    members are cross-linked and that derived ``py *`` fields (family group,
    workgroups, contact info, active flag, ministries, and friendly names) are
    populated. The setup stays local to this test so fixtures remain easy to
    understand and change.
    """
    ps = client(
        tmp_path,
        [
            Response([{"organizationID": 7, "organizationReportName": "Parish"}]),
            Response(
                {
                    "data": [
                        {
                            "familyDUID": 1,
                            "registeredOrganizationID": 7,
                            "famGroupID": 10,
                            "eMailAddress": "HOME@EXAMPLE.ORG",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response([{"famGroupID": 10, "famGroup": "Active"}]),
            Response(
                {
                    "data": [
                        {
                            "memberDUID": 2,
                            "familyDUID": 1,
                            "memberStatus": "Active",
                            "memberType": "Head",
                            "emailAddress": "ANN@EXAMPLE.ORG",
                            "firstName": "Ann",
                            "lastName": "Smith",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {
                            "workgroupDUID": 100,
                            "workgroupName": "Families",
                            "workgroupID": 101,
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [{"familyDUID": 1, "email": "HOME@EXAMPLE.ORG"}],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [{"memberDUID": 2, "nickName": "Annie"}],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {
                            "id": 200,
                            "name": "Members",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [{"memberId": 2, "familyId": 1}],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {"id": 300, "name": "001-Readers"},
                        {"id": 301, "name": "Retired Ministry"},
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    # Two memberships for the same member: one already started and
                    # one starting far in the future. Only the current one should
                    # survive the date filter below.
                    "data": [
                        {"memberId": 2, "familyId": 1, "startDate": "2000-01-01"},
                        {"memberId": 2, "familyId": 1, "startDate": "2999-01-01"},
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
        ],
    )

    data = load_families_and_members(ps)

    assert data.organization_id == 7
    assert list(data.families) == [1]
    assert list(data.members) == [2]
    assert data.families[1]["py members"] == [data.members[2]]
    assert data.families[1]["py family group"] == "Active"
    assert data.families[1]["py workgroups"]["Families"]["id"] == 101
    assert data.members[2]["py contactInfo"]["nickName"] == "Annie"
    assert data.members[2]["py member workgroups"][0]["name"] == "Members"
    assert data.members[2]["py workgroups"]["Members"]["id"] == 200
    assert data.members[2]["py active"]
    assert data.members[2]["py ministries"]["001-Readers"]["id"] == 300
    assert data.members[2]["py ministries"]["001-Readers"]["start date"] == dt.date(
        2000, 1, 1
    )
    assert data.members[2]["py friendly name FL"] == "Annie Smith"
    assert data.members[2]["py friendly name LF"] == "Smith, Annie"
    assert data.families[1]["py pledges"] == []
    assert data.families[1]["py contributions"] == []
    endpoints = [call[1] for call in ps.session.calls]
    # Different endpoints use different pagination parameter spellings, so pull
    # the first request to each and confirm the loader used the right names.
    member_search_payload = next(
        call[2]["json"] for call in ps.session.calls if "members/search" in call[1]
    )
    contact_payload = next(
        call[2]["json"]
        for call in ps.session.calls
        if "members/contact/list" in call[1]
    )
    assert member_search_payload["maximumRows"] == 100
    assert member_search_payload["startRowIndex"] == 1
    assert contact_payload["Limit"] == 100
    assert contact_payload["Offset"] == 1
    assert any("members/workgroup/lookup/list" in endpoint for endpoint in endpoints)
    # Every ministry type is queried for its members, including the retired one
    # (301); membership currency is decided later by the date filter, not here.
    assert any("ministry/300/minister/list" in endpoint for endpoint in endpoints)
    assert any("ministry/301/minister/list" in endpoint for endpoint in endpoints)


def test_filter_removes_non_parishioner_family_members_and_memberships(tmp_path):
    """Families outside the expected organization (and their members) are dropped.

    The lone family belongs to organization 99 rather than the parish org 7, so
    it, its members, and its workgroup memberships must all be filtered out. The
    setup stays local to this test so fixtures remain easy to understand and
    change.
    """
    ps = client(
        tmp_path,
        [
            Response([{"organizationID": 7, "organizationReportName": "Parish"}]),
            Response(
                {
                    "data": [
                        {
                            "familyDUID": 1,
                            "registeredOrganizationID": 99,
                            "eMailAddress": "HOME@EXAMPLE.ORG",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response([]),
            Response(
                {
                    "data": [
                        {
                            "memberDUID": 2,
                            "familyDUID": 1,
                            "memberStatus": "Active",
                            "memberType": "Head",
                            "emailAddress": "ANN@EXAMPLE.ORG",
                            "firstName": "Ann",
                            "lastName": "Smith",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {
                            "workgroupDUID": 100,
                            "workgroupName": "Families",
                            "workgroupID": 101,
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [{"familyId": 1, "email": "HOME@EXAMPLE.ORG"}],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
        ],
    )

    data = load_families_and_members(ps)

    assert data.families == {}
    assert data.members == {}
    assert data.family_workgroup_memberships[100]["membership"] == []


def test_filter_removes_inactive_member_from_membership_lists(tmp_path):
    """Inactive (e.g. deceased) members are pruned from all membership lists.

    Two members share a family: an active head and a deceased child. Only the
    active member should remain in the member map and in the workgroup and
    ministry membership lists. The setup stays local to this test so fixtures
    remain easy to understand and change.
    """
    ps = client(
        tmp_path,
        [
            Response([{"organizationID": 7, "organizationReportName": "Parish"}]),
            Response(
                {
                    "data": [
                        {
                            "familyDUID": 1,
                            "registeredOrganizationID": 7,
                            "eMailAddress": "HOME@EXAMPLE.ORG",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response([]),
            Response(
                {
                    "data": [
                        {
                            "memberDUID": 2,
                            "familyDUID": 1,
                            "memberStatus": "Active",
                            "memberType": "Head",
                            "emailAddress": "ANN@EXAMPLE.ORG",
                            "firstName": "Ann",
                            "lastName": "Smith",
                        },
                        {
                            "memberDUID": 3,
                            "familyDUID": 1,
                            "memberStatus": "Deceased",
                            "memberType": "Child",
                            "emailAddress": "BOB@EXAMPLE.ORG",
                            "firstName": "Bob",
                            "lastName": "Smith",
                        },
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response(
                {
                    "data": [{"id": 200, "name": "Members"}],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {"memberId": 2, "familyId": 1},
                        {"memberId": 3, "familyId": 1},
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [{"id": 300, "name": "001-Readers"}],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {"memberId": 2, "familyId": 1},
                        {"memberId": 3, "familyId": 1},
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
        ],
    )

    data = load_families_and_members(ps)

    assert list(data.members) == [2]
    assert data.members[2]["py active"]
    assert data.member_workgroup_memberships[200]["membership"] == [
        {"memberId": 2, "familyId": 1, "py member duid": 2, "py family duid": 1}
    ]
    assert data.ministry_type_memberships[300]["membership"] == [
        {"memberId": 2, "familyId": 1, "py member duid": 2, "py family duid": 1}
    ]


def test_ministry_membership_current_date_filter():
    """A membership is current only while today falls within its start/end dates.

    Covers an in-range span, a not-yet-started span, an already-ended span, an
    end date equal to today (treated as ended), and a record with no dates.
    """
    today = dt.date(2026, 1, 1)

    assert ministry_membership_is_current(
        {"startDate": "2000-01-01", "endDate": "2099-01-01"},
        today=today,
    )
    assert not ministry_membership_is_current({"startDate": "2027-01-01"}, today=today)
    assert not ministry_membership_is_current({"endDate": "2025-01-01"}, today=today)
    assert not ministry_membership_is_current({"endDate": "2026-01-01"}, today=today)
    assert not ministry_membership_is_current({}, today=today)


def test_family_business_logistics_email_helpers():
    """Business-logistics helpers return the emails and members of flagged members.

    The child is the sole member of the "Business Logistics Email" workgroup, so
    both helpers should resolve to that child: the email helper returns each of
    the child's addresses, and the members helper returns the child record.
    """
    head = {
        "memberDUID": 2,
        "memberType": "Head",
        "emailAddress": "head@example.org",
    }
    child = {
        "memberDUID": 3,
        "memberType": "Child",
        "emailAddress": "child@example.org; alt@example.org",
        "py emailAddresses": ["child@example.org", "alt@example.org"],
    }
    family = {
        "py members": [head, child],
        "py eMailAddresses": ["family@example.org"],
    }
    workgroups = {
        1: {
            "name": "Business Logistics Email",
            "membership": [{"memberId": 3}],
        }
    }

    assert family_business_logistics_emails(family, workgroups) == [
        "child@example.org",
        "alt@example.org",
    ]
    assert family_business_logistics_emails_members(family, workgroups) == [child]


def test_family_business_logistics_fallback_splits_multiple_emails():
    """With an empty workgroup, fall back to heads and split combined addresses.

    No member is flagged, so the helper falls back to family heads and splits
    the head's semicolon-separated address string into individual emails.
    """
    head = {
        "memberDUID": 2,
        "memberType": "Head",
        "emailAddress": "head@example.org; alt@example.org",
    }
    family = {
        "py members": [head],
        "py eMailAddresses": ["family@example.org"],
    }
    workgroups = {1: {"name": "Business Logistics Email", "membership": []}}

    assert family_business_logistics_emails(family, workgroups) == [
        "head@example.org",
        "alt@example.org",
    ]


def test_family_business_logistics_missing_workgroup_returns_empty():
    """Absent the workgroup entirely, both helpers return empty and log an error."""

    class Log:
        """Capturing logger stub that formats and stores each error message."""

        def __init__(self):
            self.messages = []

        def error(self, message, *args):
            """Record the message with %-style args applied, like logging.error."""
            self.messages.append(message % args)

    family = {
        "py members": [
            {
                "memberDUID": 2,
                "memberType": "Head",
                "emailAddress": "head@example.org",
            }
        ],
        "py eMailAddresses": ["family@example.org"],
    }
    log = Log()

    assert family_business_logistics_emails(family, {}, log) == []
    assert family_business_logistics_emails_members(family, {}, log) == []
    assert log.messages == [
        "DID NOT FIND Business Logistics Email MEMBER WORKGROUP!",
        "DID NOT FIND Business Logistics Email MEMBER WORKGROUP!",
    ]


def test_load_families_and_members_can_load_financial_data(tmp_path):
    """Passing load_contributions also loads funds, pledges, and contributions.

    Verifies funds/pledges/contributions are parsed (with dates coerced to
    date objects), cross-linked (pledge to fund, contribution to pledge),
    attached to their family, and that the contribution request forwards the
    requested start date. The setup stays local to this test so fixtures remain
    easy to understand and change.
    """
    ps = client(
        tmp_path,
        [
            Response([{"organizationID": 7, "organizationReportName": "Parish"}]),
            Response([{"fundId": 8, "name": "General"}]),
            Response(
                {
                    "data": [
                        {
                            "pledgeID": 9,
                            "familyID": 1,
                            "fundID": 8,
                            "pledgeDate": "2026-01-02",
                            "pledgeStartDate": "2026-01-01",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {
                            "contributionID": 10,
                            "familyId": 1,
                            "fundId": 8,
                            "pledgeId": 9,
                            "contributionDate": "2026-01-03",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response(
                {
                    "data": [
                        {
                            "familyDUID": 1,
                            "registeredOrganizationID": 7,
                            "eMailAddress": "HOME@EXAMPLE.ORG",
                            "dateModified": "2026-01-04",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response([]),
            Response(
                {
                    "data": [
                        {
                            "memberDUID": 2,
                            "familyDUID": 1,
                            "memberStatus": "Active",
                            "memberType": "Head",
                            "emailAddress": "ANN@EXAMPLE.ORG",
                            "firstName": "Ann",
                            "lastName": "Smith",
                            "birthdate": "1980-01-01",
                        }
                    ],
                    "pagingInfo": {"pageNumber": 1, "totalPages": 1},
                }
            ),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
            Response({"data": [], "pagingInfo": {"pageNumber": 1, "totalPages": 1}}),
        ],
    )

    data = load_families_and_members(ps, load_contributions="2026-01-01")

    assert data.funds[8]["name"] == "General"
    assert data.pledges[9]["py fund"] == data.funds[8]
    assert data.pledges[9]["pledgeDate"] == dt.date(2026, 1, 2)
    assert data.contributions[10]["py pledge"] == data.pledges[9]
    assert data.contributions[10]["contributionDate"] == dt.date(2026, 1, 3)
    assert data.families[1]["dateModified"] == dt.date(2026, 1, 4)
    assert data.members[2]["birthdate"] == dt.date(1980, 1, 1)
    assert data.families[1]["py pledges"] == [data.pledges[9]]
    assert data.families[1]["py contributions"] == [data.contributions[10]]
    contribution_calls = [
        call for call in ps.session.calls if "contributiondetail/list" in call[1]
    ]
    assert contribution_calls[0][2]["params"]["startDate"] == "2026-01-01"


def test_family_member_helpers():
    """Normalization, linking, and accessor helpers work together on one record.

    Exercises email normalization (lowercasing and splitting), family/member
    linking, the active-status checks, head lookup, and the public email and
    phone accessors that honor the family's publish flags.
    """
    family = {"familyDUID": 1, "eMailAddress": "A@EXAMPLE.ORG; B@EXAMPLE.ORG"}
    member = {
        "memberDUID": 2,
        "familyDUID": 1,
        "memberType": "Head",
        "memberStatus": "Active",
        "emailAddress": "A@EXAMPLE.ORG",
        "family_PublishEMail": True,
        "family_PublishPhone": True,
        "mobilePhone": "555",
        "firstName": "Ann",
        "lastName": "Smith",
    }
    normalize_family_email(family)
    normalize_member_email(member)
    link_families_and_members({1: family}, {2: member})

    assert family["py eMailAddresses"] == ["a@example.org", "b@example.org"]
    assert family_is_active(family)
    assert member_is_active(member)
    assert get_family_heads(family) == {2: member}
    assert get_member_public_email(member) == "a@example.org"
    assert get_member_public_phones(member) == [{"number": "555", "type": "cell"}]


def test_email_normalization_drops_blank_fragments_and_deduplicates():
    """Blank semicolon fragments are not exposed as usable email addresses."""
    family = {"eMailAddress": " ; A@EXAMPLE.ORG ; a@example.org ; "}
    member = {"emailAddress": " ; ANN@EXAMPLE.ORG ; ann@example.org ; "}

    normalize_family_email(family)
    normalize_member_email(member)

    assert family["eMailAddress"] == "a@example.org"
    assert family["py eMailAddresses"] == ["a@example.org"]
    assert member["emailAddress"] == "ann@example.org"
    assert member["py emailAddresses"] == ["ann@example.org"]


def test_string_registered_organization_id_is_parishioner():
    """A string registeredOrganizationID still matches the numeric parish org ID.

    Guards against type mismatches: the API may send the org ID as a string,
    but membership should compare equal to the integer expected org.
    """
    family = {"registeredOrganizationID": "7"}
    member = {"memberDUID": 2, "memberStatus": "Active"}
    family["py members"] = [member]

    from parishkit.parishsoft import family_is_parishioner

    assert family_is_parishioner(family, 7)


def test_salutation_logic():
    """Salutations use nicknames, join names grammatically, and share a surname.

    Confirms a nickname is preferred over the first name, and that one, two, and
    three or more members produce the expected comma/"and" joining.
    """
    members = [
        {
            "firstName": "Ann",
            "lastName": "Smith",
            "py contactInfo": {"nickName": "Annie"},
        },
        {"firstName": "Bob", "lastName": "Smith"},
        {"firstName": "Cat", "lastName": "Smith"},
    ]

    assert salutation_for_members(members) == ("Annie, Bob, and Cat", "Smith")
    assert salutation_for_members(members[:2]) == ("Annie and Bob", "Smith")
    assert salutation_for_members([members[0]]) == ("Annie", "Smith")


def test_no_oauth2client_imports():
    """No package source references the deprecated oauth2client library."""
    root = Path(__file__).parents[1] / "src" / "parishkit"
    for path in root.rglob("*.py"):
        assert "oauth2client" not in path.read_text(encoding="utf-8")
