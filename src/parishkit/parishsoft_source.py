"""Opt-in coherent-source client using shared loaders and published v2 contracts.

Existing ParishKit tools keep ParishSoftClient's legacy behavior. This client
translates loader pagination options to the endpoint's actual wire fields,
refuses partial collections and disables every disk-cache operation. Pair it
with BoundedSourceSession for finite per-attempt transport and owning fences.
"""

import re
import time
from dataclasses import replace

from parishkit.parishsoft import ParishSoftClient
from parishkit.parishsoft_pagination import (
    IncompleteSourceCollection,
    PageContract,
    read_pages,
)

PAGE = PageContract("PageSize", "PageNumber", envelope=True)
POST_CONTRACTS = {
    "families/search": (
        PageContract(
            "pageSize",
            "pageNumber",
            envelope=False,
            total_field="totalResults",
            ordinal_field="rowNumber",
        ),
        "familyDUID",
    ),
    "members/search": (
        PageContract(
            "maximumRows",
            "startRowIndex",
            envelope=False,
            zero_probe=True,
            total_field="recordCount",
            ordinal_field="rowNum",
        ),
        "memberDUID",
    ),
    "members/contact/list": (
        PageContract("limit", "offset", envelope=False, zero_probe=True),
        "memberDUID",
    ),
}
GET_CONTRACTS = (
    (
        r"families/workgroup/list",
        replace(
            PAGE, envelope=False, total_field="recordCount", ordinal_field="rowNum"
        ),
        "workgroupDUID",
    ),
    (r"families/workgroup/[1-9][0-9]*/list", PAGE, "familyId"),
    (r"members/workgroup/lookup/list", PAGE, "id"),
    (r"members/workgroup/[1-9][0-9]*/list", PAGE, "memberId"),
    (r"ministry/type/list", PAGE, "id"),
    (r"ministry/[1-9][0-9]*/minister/list", PAGE, "roster"),
    (r"offering/pledge/list", PAGE, "pledgeID"),
    (r"offering/contributiondetail/list", PAGE, "contributionID"),
)


def _identifier(row, field):
    """Do not coerce strings, booleans or nullable primary source identities."""
    value = row.get(field)
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise IncompleteSourceCollection("Source record identity is unavailable.")
    return value


def _roster_identity(row):
    """One Member may have several roles/events/dated memberships in a ministry."""
    member = _identifier(row, "memberId")
    parts = []
    for field in ("ministryTypeId", "ministryRoleId", "ministryEventId"):
        value = row.get(field)
        if value is not None and (type(value) is not int or not 0 <= value < 2**31):
            raise IncompleteSourceCollection("Source roster identity is invalid.")
        parts.append(value)
    start = row.get("startDate")
    if start is not None and (type(start) is not str or len(start) > 40):
        raise IncompleteSourceCollection("Source roster date is invalid.")
    return member, *parts, start


class CoherentParishSoftClient(ParishSoftClient):
    """A single load's tenant, request and byte budget with strict collection reads."""

    def __init__(
        self,
        config,
        *,
        organization_id,
        session,
        retry_policy=None,
        maximum_requests=10000,
        maximum_bytes=128 * 1024 * 1024,
        maximum_seconds=900,
    ):
        """Resource limits fail the whole scan; none truncates a successful result."""
        if (
            type(organization_id) is not int
            or not 1 <= organization_id < 2**31
            or type(maximum_requests) is not int
            or not 1 <= maximum_requests <= 100000
            or type(maximum_bytes) is not int
            or not 1 <= maximum_bytes <= 1024**3
            or type(maximum_seconds) is not int
            or not 1 <= maximum_seconds <= 7200
        ):
            raise ValueError("Invalid coherent source identity or resource bounds.")
        super().__init__(
            replace(config, cache_enabled=False),
            session=session,
            retry_policy=retry_policy,
        )
        self.expected_organization_id = organization_id
        self.maximum_requests, self.maximum_bytes = maximum_requests, maximum_bytes
        self.deadline = time.monotonic() + maximum_seconds
        self.request_count = self.response_bytes = 0

    def _request(self, operation):
        """Apply aggregate body bounds in addition to finite HTTP attempts."""
        if (
            self.request_count >= self.maximum_requests
            or time.monotonic() >= self.deadline
        ):
            raise IncompleteSourceCollection(
                "Source load exceeds its request/time bound."
            )
        self.request_count += 1
        response = super()._request(operation)
        self.response_bytes += len(response.content)
        if (
            self.response_bytes > self.maximum_bytes
            or time.monotonic() >= self.deadline
        ):
            raise IncompleteSourceCollection("Source load exceeds its byte/time bound.")
        return response

    def validate_organization(self):
        """A new load always validates the tenant without cached identity shortcuts."""
        return self.validate_organization_uncached()

    def validate_organization_uncached(self):
        """Require one exact numeric organization before any scoped collection read."""
        self._organization_id = None
        rows = super().post_uncached("organizations/search", {})
        if (
            type(rows) is not list
            or len(rows) != 1
            or type(rows[0]) is not dict
            or _identifier(rows[0], "organizationID") != self.expected_organization_id
            or (
                self.config.expected_organization is not None
                and rows[0].get("organizationReportName")
                != self.config.expected_organization
            )
        ):
            raise IncompleteSourceCollection(
                "Source organization is not the expected tenant."
            )
        self._organization_id = self.expected_organization_id
        return self._organization_id

    def _guard(self):
        """A configured numeric ID is not proof that the key resolves to that tenant."""
        if self._organization_id != self.expected_organization_id:
            raise IncompleteSourceCollection(
                "Source organization has not been validated."
            )

    def get_uncached(self, endpoint, params=None):
        """Validate whole-list lookup identities before shared loaders build dicts."""
        self._guard()
        field = None
        if endpoint == "families/group/lookup/list":
            field = "famGroupID"
        elif re.fullmatch(r"offering/[1-9][0-9]*/funds", endpoint):
            if endpoint != f"offering/{self.expected_organization_id}/funds":
                raise IncompleteSourceCollection(
                    "Source fund catalog has another tenant."
                )
            field = "fundId"
        value = super().get_uncached(endpoint, params)
        if field is not None:
            if type(value) is not list or len(value) > 100000:
                raise IncompleteSourceCollection("Source lookup collection is invalid.")
            identities = set()
            for row in value:
                if type(row) is not dict:
                    raise IncompleteSourceCollection("Source lookup record is invalid.")
                identifier = _identifier(row, field)
                if identifier in identities:
                    raise IncompleteSourceCollection(
                        "Source lookup repeats an identity."
                    )
                identities.add(identifier)
        return value

    def get_paginated(self, endpoint, params=None, **legacy_options):
        """Map each supported shared GET loader to its published envelope contract."""
        self._guard()
        for pattern, contract, identity in GET_CONTRACTS:
            if re.fullmatch(pattern, endpoint):
                return self._pages(
                    "GET", endpoint, params, contract, identity, legacy_options
                )
        raise IncompleteSourceCollection("Unsupported coherent source GET collection.")

    def post_uncached(self, endpoint, payload=None):
        """Direct shared POST reads also require the validated organization guard."""
        self._guard()
        return super().post_uncached(endpoint, payload)

    def post_paginated(self, endpoint, payload=None, **legacy_options):
        """Translate legacy parameter spelling/origin without changing other tools."""
        self._guard()
        if endpoint not in POST_CONTRACTS:
            raise IncompleteSourceCollection(
                "Unsupported coherent source POST collection."
            )
        contract, identity = POST_CONTRACTS[endpoint]
        return self._pages(
            "POST", endpoint, payload, contract, identity, legacy_options
        )

    def _pages(self, method, endpoint, parameters, contract, identity, legacy_options):
        """Use contract paging fields, never permit inherited caller paging filters."""
        if set(legacy_options) - {"limit", "limit_name", "offset_name", "offset_type"}:
            raise ValueError("Unsupported coherent source pagination options.")
        if parameters is not None and (
            type(parameters) is not dict
            or any(type(name) is not str for name in parameters)
        ):
            raise ValueError("Source collection parameters must be a mapping.")
        parameters = dict(parameters or {})
        if any(
            name.casefold()
            in {
                "limit",
                "offset",
                "pagesize",
                "pagenumber",
                "maximumrows",
                "startrowindex",
            }
            for name in parameters
        ):
            raise ValueError("Source collection parameters cannot override pagination.")
        if method == "POST":
            tenant_field = "organizationIDs"
        elif endpoint in {"ministry/type/list", "members/workgroup/lookup/list"}:
            tenant_field = "organizationId"
        elif endpoint.startswith("offering/"):
            tenant_field = (
                "OrganizationID"
                if endpoint == "offering/pledge/list"
                else "OrganizationId"
            )
        else:
            tenant_field = None
        expected = (
            [self.expected_organization_id]
            if method == "POST"
            else self.expected_organization_id
        )
        for name, value in parameters.items():
            if name.casefold() not in {"organizationid", "organizationids"}:
                continue
            if (
                name != tenant_field
                or value != expected
                or (
                    method == "POST"
                    and (
                        type(value) is not list
                        or any(type(key) is not int for key in value)
                    )
                )
                or (method == "GET" and type(value) is not int)
            ):
                raise IncompleteSourceCollection(
                    "Source collection has another or ambiguous tenant."
                )
        if tenant_field:
            parameters[tenant_field] = expected

        def fetch(paging):
            """Every page goes through the aggregate budget and shared retry policy."""
            values = parameters | paging
            if method == "POST":
                return super(CoherentParishSoftClient, self).post_uncached(
                    endpoint, values
                )
            return super(CoherentParishSoftClient, self).get_uncached(endpoint, values)

        rows = read_pages(
            fetch,
            contract=contract,
            identify=_roster_identity
            if identity == "roster"
            else lambda row: _identifier(row, identity),
            page_size=legacy_options.get("limit", 500),
        )
        # Legacy normalization accepts a sequence of email strings for general
        # tool callers. The provider DTO requires one text field: validate it
        # before that helper could erase the evidence of a malformed response.
        email_field = {
            "families/search": "eMailAddress",
            "members/search": "emailAddress",
        }.get(endpoint)
        if email_field and any(
            row.get(email_field) is not None and type(row[email_field]) is not str
            for row in rows
        ):
            raise IncompleteSourceCollection("Source email DTO field is not text.")
        return rows
