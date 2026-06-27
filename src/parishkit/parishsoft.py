"""ParishSoft API and reusable data helper functions.

Upstream API docs are hard to find and are not linked from most public
ParishSoft pages. Preserve both documentation URLs seen during migration from
the old Epiphany ``ParishSoftv2.py`` helper:

- Current docs/API host:
  https://ps-fs-external-api-prod.azurewebsites.net/index.html
- Older, still relevant docs/API host:
  https://fsapi.parishsoft.app/index.html
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from parishkit.config import ConfigError
from parishkit.files import atomic_write_text
from parishkit.retry import RetryError, RetryPolicy, TransientRetryError, retry_call

DEFAULT_API_BASE_URL = "https://ps-fs-external-api-prod.azurewebsites.net/api/v2"


class ParishSoftAPIError(RuntimeError):
    def __init__(self, status_code: int, endpoint: str, response_text: str):
        self.status_code = status_code
        self.endpoint = endpoint
        self.response_text = response_text
        super().__init__(f"ParishSoft API error on {endpoint}: HTTP {status_code}")


def parse_cache_limit(cache_limit: str | int | float | None) -> float | None:
    if cache_limit in (None, ""):
        return None
    if isinstance(cache_limit, bool):
        raise ConfigError("cache limit must be a duration string or seconds")
    if isinstance(cache_limit, int | float):
        if cache_limit < 0:
            raise ConfigError("cache limit must be non-negative")
        return float(cache_limit)
    if not isinstance(cache_limit, str):
        raise ConfigError("cache limit must be a duration string or seconds")
    value = cache_limit.strip()
    if not value:
        return None
    unit = value[-1]
    number = value[:-1]
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if unit not in multipliers or not number.isdigit():
        raise ConfigError("cache limit must look like 24s, 15m, 7h, or 2d")
    return float(int(number) * multipliers[unit])


@dataclass(frozen=True)
class ParishSoftConfig:
    api_key: str
    cache_dir: Path
    expected_organization: str | None = None
    cache_limit: float | None = None
    api_base_url: str = DEFAULT_API_BASE_URL
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key:
            raise ConfigError("ParishSoft api_key is required")
        if not isinstance(self.cache_dir, Path):
            raise ConfigError("ParishSoft cache_dir must be a pathlib.Path")
        if self.expected_organization is not None and not isinstance(
            self.expected_organization, str
        ):
            raise ConfigError("ParishSoft expected_organization must be a string")
        if not isinstance(self.api_base_url, str):
            raise ConfigError("ParishSoft api_base_url must be a string")
        if not isinstance(self.timeout, int | float) or isinstance(self.timeout, bool):
            raise ConfigError("ParishSoft timeout must be a number")
        if self.timeout <= 0:
            raise ConfigError("ParishSoft timeout must be positive")


class ParishSoftClient:
    def __init__(
        self,
        config: ParishSoftConfig,
        *,
        session: requests.Session | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"x-api-key": config.api_key})
        self.retry_policy = retry_policy or RetryPolicy(attempts=3, initial_delay=0.2)
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config.cache_dir.chmod(0o700)

    def validate_organization(self) -> int:
        organizations = self.post("organizations/search", {})
        if len(organizations) != 1:
            raise ConfigError(
                f"expected one ParishSoft organization, got {len(organizations)}"
            )
        organization = organizations[0]
        name = organization.get("organizationReportName")
        if (
            self.config.expected_organization
            and name != self.config.expected_organization
        ):
            raise ConfigError(
                "unexpected ParishSoft organization: "
                f"{name!r} (expected {self.config.expected_organization!r})"
            )
        return int(organization["organizationID"])

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        cached = self._load_cache(endpoint, params)
        if cached is not None:
            return cached
        url = self._url(endpoint)
        response = self._request(
            lambda: self.session.get(url, params=params, timeout=self.config.timeout)
        )
        data = response.json() if response.text else []
        self._save_cache(endpoint, params, data)
        return data

    def post(self, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        cached = self._load_cache(endpoint, payload)
        if cached is not None:
            return cached
        data = self.post_uncached(endpoint, payload)
        self._save_cache(endpoint, payload, data)
        return data

    def post_uncached(
        self, endpoint: str, payload: dict[str, Any] | None = None
    ) -> Any:
        """POST without cache semantics, for future mutation-style API calls."""

        url = self._url(endpoint)
        response = self._request(
            lambda: self.session.post(
                url, json=payload or {}, timeout=self.config.timeout
            )
        )
        return response.json() if response.text else []

    def get_paginated(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        limit_name: str = "Limit",
        limit: int = 100,
        offset_name: str = "Offset",
        offset_type: str = "index",
    ) -> list[dict[str, Any]]:
        cache_params = dict(params or {})
        cache_params.update({limit_name: limit, offset_name: offset_type})
        cached = self._load_cache(endpoint, cache_params)
        if cached is not None:
            return cached
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            request_params = dict(params or {})
            request_params[limit_name] = limit
            request_params[offset_name] = len(items) if offset_type == "index" else page
            response = self._request(
                lambda request_params=request_params: self.session.get(
                    self._url(endpoint),
                    params=request_params,
                    timeout=self.config.timeout,
                )
            )
            data = response.json()
            page_items, done = _extract_page(data)
            items.extend(page_items)
            if done:
                break
            page += 1
        self._save_cache(endpoint, cache_params, items)
        return items

    def post_paginated(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        limit_name: str = "Limit",
        limit: int = 100,
        offset_name: str = "Offset",
        offset_type: str = "index",
    ) -> list[dict[str, Any]]:
        base_payload = dict(payload or {})
        cache_payload = dict(base_payload)
        cache_payload.update({limit_name: limit, offset_name: offset_type})
        cached = self._load_cache(endpoint, cache_payload)
        if cached is not None:
            return cached
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            request_payload = dict(base_payload)
            request_payload[limit_name] = limit
            request_payload[offset_name] = (
                len(items) if offset_type == "index" else page
            )
            response = self._request(
                lambda request_payload=request_payload: self.session.post(
                    self._url(endpoint),
                    json=request_payload,
                    timeout=self.config.timeout,
                )
            )
            data = response.json()
            page_items, done = _extract_page(data)
            items.extend(page_items)
            if done:
                break
            page += 1
        self._save_cache(endpoint, cache_payload, items)
        return items

    def _request(self, func: Any) -> requests.Response:
        def call() -> requests.Response:
            response = func()
            if response.status_code in {429, 500, 502, 503, 504}:
                raise _TransientParishSoftAPIError(
                    response.status_code,
                    response.url,
                    response.text,
                    f"transient ParishSoft HTTP {response.status_code}",
                )
            if not 200 <= response.status_code <= 299:
                raise ParishSoftAPIError(
                    response.status_code, response.url, response.text
                )
            return response

        try:
            return retry_call(call, policy=self.retry_policy)
        except RetryError as exc:
            if isinstance(exc.last_exception, _TransientParishSoftAPIError):
                raise ParishSoftAPIError(
                    exc.last_exception.status_code,
                    exc.last_exception.endpoint,
                    exc.last_exception.response_text,
                ) from exc
            raise

    def _url(self, endpoint: str) -> str:
        return f"{self.config.api_base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def _cache_path(self, endpoint: str, params: dict[str, Any] | None) -> Path:
        suffix = ""
        if params:
            suffix = "-" + urlencode(sorted(params.items()), doseq=True)
        name = f"cache-v2-{endpoint}{suffix}.json".replace("/", "-")
        return self.config.cache_dir / name

    def _load_cache(self, endpoint: str, params: dict[str, Any] | None) -> Any | None:
        cache_path = self._cache_path(endpoint, params)
        if not cache_path.exists():
            return None
        if self.config.cache_limit is not None:
            oldest = time.time() - self.config.cache_limit
            if cache_path.stat().st_mtime < oldest:
                return None
        return json.loads(cache_path.read_text(encoding="utf-8"))

    def _save_cache(
        self, endpoint: str, params: dict[str, Any] | None, data: Any
    ) -> None:
        cache_path = self._cache_path(endpoint, params)
        atomic_write_text(
            cache_path,
            json.dumps(data, sort_keys=True, indent=2),
        )


class _TransientParishSoftAPIError(TransientRetryError):
    def __init__(
        self,
        status_code: int,
        endpoint: str,
        response_text: str,
        message: str,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.response_text = response_text


def _extract_page(data: Any) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(data, list):
        return data, len(data) == 0
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        paging = data.get("pagingInfo") or {}
        done = paging.get("pageNumber", 1) >= paging.get("totalPages", 1)
        return data["data"], done
    raise ParishSoftAPIError(500, "pagination", f"unexpected page shape: {data!r}")


def normalize_family_email(family: dict[str, Any]) -> None:
    value = family.get("eMailAddress")
    if value:
        family["eMailAddress"] = value.lower()
        family["py eMailAddresses"] = [
            item.strip() for item in value.lower().split(";")
        ]


def normalize_member_email(member: dict[str, Any]) -> None:
    value = member.get("emailAddress")
    if value:
        member["emailAddress"] = value.lower()
        member["py emailAddresses"] = [
            item.strip() for item in value.lower().split(";")
        ]


def member_email_addresses(member: dict[str, Any]) -> list[str]:
    emails = member.get("py emailAddresses")
    if isinstance(emails, list):
        return emails
    value = member.get("emailAddress")
    if value:
        return [item.strip().lower() for item in value.split(";") if item.strip()]
    return []


def normalize_dates(elements: list[dict[str, Any]], fields: list[str]) -> None:
    for element in elements:
        for field in fields:
            if field not in element or element[field] in (None, ""):
                continue
            element[field] = _parse_optional_date(element[field])


def link_families_and_members(
    families: dict[int, dict[str, Any]],
    members: dict[int, dict[str, Any]],
) -> None:
    for family in families.values():
        family["py members"] = []
    for member in members.values():
        family_duid = int(member["familyDUID"])
        family = families.get(family_duid)
        member["py family"] = family
        if family is not None:
            family["py members"].append(member)


def load_families(client: ParishSoftClient, org_id: int) -> dict[int, dict[str, Any]]:
    elements = client.post_paginated(
        "families/search",
        {"organizationIDs": [org_id]},
        offset_name="PageNumber",
        offset_type="page",
    )
    normalize_dates(elements, ["dateModified"])
    families = {int(element["familyDUID"]): element for element in elements}
    for family in families.values():
        normalize_family_email(family)
    return families


def load_members(client: ParishSoftClient, org_id: int) -> dict[int, dict[str, Any]]:
    elements = client.post_paginated(
        "members/search",
        {"organizationIDs": [org_id]},
        limit_name="maximumRows",
        offset_name="startRowIndex",
        offset_type="page",
    )
    normalize_dates(elements, ["birthdate", "dateModified", "dateOfDeath"])
    members = {int(element["memberDUID"]): element for element in elements}
    for member in members.values():
        normalize_member_email(member)
    return members


def load_family_workgroups(client: ParishSoftClient) -> dict[int, dict[str, Any]]:
    elements = client.get_paginated(
        "families/workgroup/list",
        offset_name="PageNumber",
        offset_type="page",
    )
    return {
        int(element["workgroupDUID"]): {
            "name": element["workgroupName"],
            "duid": element["workgroupDUID"],
            "id": element["workgroupID"],
        }
        for element in elements
    }


def load_family_groups(client: ParishSoftClient) -> dict[int, str]:
    elements = client.get("families/group/lookup/list")
    return {int(element["famGroupID"]): element["famGroup"] for element in elements}


def load_family_workgroup_memberships(
    client: ParishSoftClient,
    family_workgroups: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for duid, workgroup in family_workgroups.items():
        elements = client.get_paginated(
            f"families/workgroup/{duid}/list",
            offset_name="PageNumber",
            offset_type="page",
        )
        for element in elements:
            if element.get("email"):
                element["email"] = element["email"].lower()
                element["py emails"] = [
                    item.strip() for item in element["email"].split(";")
                ]
            _copy_first_duid(element, ("familyDUID", "familyId"), "py family duid")
        results[duid] = {
            "duid": duid,
            "id": workgroup["id"],
            "name": workgroup["name"],
            "membership": elements,
        }
    return results


def load_member_contactinfos(
    client: ParishSoftClient, org_id: int
) -> dict[int, dict[str, Any]]:
    elements = client.post_paginated(
        "members/contact/list",
        {"organizationIDs": [org_id]},
        offset_type="page",
    )
    normalize_dates(elements, ["dateOfBirth", "dateOfDeath"])
    return {int(element["memberDUID"]): element for element in elements}


def load_member_workgroups(client: ParishSoftClient) -> dict[int, dict[str, Any]]:
    elements = client.get_paginated(
        "members/workgroup/lookup/list",
        offset_name="PageNumber",
        offset_type="page",
    )
    return {
        int(element["id"]): {
            "name": element["name"],
            "duid": int(element["id"]),
            "id": int(element["id"]),
        }
        for element in elements
    }


def load_member_workgroup_memberships(
    client: ParishSoftClient,
    member_workgroups: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for duid, workgroup in member_workgroups.items():
        elements = client.get_paginated(
            f"members/workgroup/{duid}/list",
            offset_name="PageNumber",
            offset_type="page",
        )
        for element in elements:
            _copy_first_duid(element, ("memberDUID", "memberId"), "py member duid")
            _copy_first_duid(element, ("familyDUID", "familyId"), "py family duid")
            if element.get("emailAddress"):
                element["emailAddress"] = element["emailAddress"].lower()
                element["py emailAddresses"] = [
                    item.strip() for item in element["emailAddress"].split(";")
                ]
        results[duid] = {
            "duid": duid,
            "id": workgroup["id"],
            "name": workgroup["name"],
            "membership": elements,
        }
    return results


def load_ministry_types(client: ParishSoftClient) -> dict[int, dict[str, Any]]:
    elements = client.get_paginated(
        "ministry/type/list",
        offset_name="PageNumber",
        offset_type="page",
    )
    ministry_types = {}
    for element in elements:
        name = element["name"]
        ministry_id = int(element["id"])
        ministry_types[ministry_id] = {"id": ministry_id, "name": name}
    return ministry_types


def load_ministry_type_memberships(
    client: ParishSoftClient,
    ministry_types: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for ministry_id, ministry_type in ministry_types.items():
        elements = client.get_paginated(
            f"ministry/{ministry_id}/minister/list",
            offset_name="PageNumber",
            offset_type="page",
        )
        for element in elements:
            _copy_first_duid(element, ("memberDUID", "memberId"), "py member duid")
            _copy_first_duid(element, ("familyDUID", "familyId"), "py family duid")
        normalize_dates(elements, ["startDate", "endDate"])
        results[ministry_id] = {
            "id": ministry_id,
            "name": ministry_type.get("name") or ministry_type.get("ministryTypeName"),
            "membership": elements,
        }
    return results


def load_funds(client: ParishSoftClient, org_id: int) -> dict[int, dict[str, Any]]:
    elements = client.get(f"offering/{org_id}/funds")
    return {int(element["fundId"]): element for element in elements}


def load_pledges(
    client: ParishSoftClient,
    funds: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    elements = client.get_paginated(
        "offering/pledge/list",
        limit_name="PageSize",
        limit=500,
        offset_name="PageNumber",
        offset_type="page",
    )
    normalize_dates(elements, ["pledgeDate", "pledgeStartDate"])
    for element in elements:
        fund_id = element.get("fundID") or element.get("fundId")
        element["py fund"] = funds.get(int(fund_id)) if fund_id is not None else None
    return {int(element["pledgeID"]): element for element in elements}


def load_contribution_details(
    client: ParishSoftClient,
    funds: dict[int, dict[str, Any]],
    pledges: dict[int, dict[str, Any]],
    *,
    start_date: str | None = None,
) -> dict[int, dict[str, Any]]:
    params = {"startDate": start_date} if start_date else None
    elements = client.get_paginated(
        "offering/contributiondetail/list",
        params,
        limit_name="PageSize",
        limit=500,
        offset_name="PageNumber",
        offset_type="page",
    )
    normalize_dates(elements, ["contributionDate"])
    for element in elements:
        fund_id = element.get("fundId") or element.get("fundID")
        pledge_id = element.get("pledgeId") or element.get("pledgeID")
        element["py fund"] = funds.get(int(fund_id)) if fund_id is not None else None
        element["py pledge"] = (
            pledges.get(int(pledge_id)) if pledge_id is not None else None
        )
    return {int(element["contributionID"]): element for element in elements}


def _copy_duid(element: dict[str, Any], source: str, target: str) -> None:
    if source in element:
        element[target] = int(element[source])


def _copy_first_duid(
    element: dict[str, Any],
    sources: tuple[str, ...],
    target: str,
) -> None:
    for source in sources:
        if source in element:
            element[target] = int(element[source])
            return


@dataclass(frozen=True)
class ParishSoftData:
    organization_id: int
    families: dict[int, dict[str, Any]]
    members: dict[int, dict[str, Any]]
    family_groups: dict[int, str]
    family_workgroups: dict[int, dict[str, Any]]
    family_workgroup_memberships: dict[int, dict[str, Any]]
    member_contactinfos: dict[int, dict[str, Any]]
    member_workgroups: dict[int, dict[str, Any]]
    member_workgroup_memberships: dict[int, dict[str, Any]]
    ministry_types: dict[int, dict[str, Any]]
    ministry_type_memberships: dict[int, dict[str, Any]]
    funds: dict[int, dict[str, Any]]
    pledges: dict[int, dict[str, Any]]
    contributions: dict[int, dict[str, Any]]


def load_families_and_members(
    client: ParishSoftClient,
    *,
    active_only: bool = True,
    parishioners_only: bool = True,
    include_deceased: bool = False,
    load_contributions: bool | str = False,
) -> ParishSoftData:
    org_id = client.validate_organization()
    funds: dict[int, dict[str, Any]] = {}
    pledges: dict[int, dict[str, Any]] = {}
    contributions: dict[int, dict[str, Any]] = {}
    if load_contributions:
        start_date = (
            load_contributions
            if isinstance(load_contributions, str)
            else one_year_ago().isoformat()
        )
        funds = load_funds(client, org_id)
        pledges = load_pledges(client, funds)
        contributions = load_contribution_details(
            client,
            funds,
            pledges,
            start_date=start_date,
        )
    families = load_families(client, org_id)
    family_groups = load_family_groups(client)
    members = load_members(client, org_id)
    family_workgroups = load_family_workgroups(client)
    family_workgroup_memberships = load_family_workgroup_memberships(
        client, family_workgroups
    )
    member_contactinfos = load_member_contactinfos(client, org_id)
    member_workgroups = load_member_workgroups(client)
    member_workgroup_memberships = load_member_workgroup_memberships(
        client, member_workgroups
    )
    ministry_types = load_ministry_types(client)
    ministry_type_memberships = load_ministry_type_memberships(client, ministry_types)
    link_families_and_members(families, members)
    link_family_groups(families, family_groups)
    link_family_workgroups(families, family_workgroup_memberships)
    link_family_pledges(families, pledges)
    link_family_contributions(families, contributions)
    link_member_contactinfos(members, member_contactinfos)
    link_member_workgroups(members, member_workgroup_memberships)
    link_member_ministries(members, ministry_type_memberships)
    make_member_friendly_names(members)
    _filter_families_and_members(
        families,
        members,
        family_workgroup_memberships=family_workgroup_memberships,
        member_workgroup_memberships=member_workgroup_memberships,
        ministry_type_memberships=ministry_type_memberships,
        org_id=org_id,
        active_only=active_only,
        parishioners_only=parishioners_only,
        include_deceased=include_deceased,
    )
    return ParishSoftData(
        organization_id=org_id,
        families=families,
        members=members,
        family_groups=family_groups,
        family_workgroups=family_workgroups,
        family_workgroup_memberships=family_workgroup_memberships,
        member_contactinfos=member_contactinfos,
        member_workgroups=member_workgroups,
        member_workgroup_memberships=member_workgroup_memberships,
        ministry_types=ministry_types,
        ministry_type_memberships=ministry_type_memberships,
        funds=funds,
        pledges=pledges,
        contributions=contributions,
    )


def link_family_groups(
    families: dict[int, dict[str, Any]],
    family_groups: dict[int, str],
) -> None:
    for family in families.values():
        group_id = family.get("famGroupID") or family.get("familyGroupID")
        if group_id is not None:
            family["py family group"] = family_groups.get(int(group_id))


def link_family_workgroups(
    families: dict[int, dict[str, Any]],
    memberships: dict[int, dict[str, Any]],
) -> None:
    for family in families.values():
        family["py family workgroups"] = []
        family["py workgroups"] = {}
    for workgroup in memberships.values():
        for element in workgroup["membership"]:
            family_duid = (
                element.get("py family duid")
                or element.get("familyDUID")
                or element.get("familyId")
            )
            if family_duid is not None and int(family_duid) in families:
                family = families[int(family_duid)]
                family["py family workgroups"].append(workgroup)
                family["py workgroups"][workgroup["name"]] = workgroup


def link_family_pledges(
    families: dict[int, dict[str, Any]],
    pledges: dict[int, dict[str, Any]],
) -> None:
    for family in families.values():
        family["py pledges"] = []
    for pledge in pledges.values():
        family_duid = pledge.get("familyID") or pledge.get("familyId")
        if family_duid is not None and int(family_duid) in families:
            families[int(family_duid)]["py pledges"].append(pledge)


def link_family_contributions(
    families: dict[int, dict[str, Any]],
    contributions: dict[int, dict[str, Any]],
) -> None:
    for family in families.values():
        family["py contributions"] = []
    for contribution in contributions.values():
        family_duid = contribution.get("familyId") or contribution.get("familyID")
        if family_duid is not None and int(family_duid) in families:
            families[int(family_duid)]["py contributions"].append(contribution)


def link_member_contactinfos(
    members: dict[int, dict[str, Any]],
    contactinfos: dict[int, dict[str, Any]],
) -> None:
    for member_id, member in members.items():
        contactinfo = contactinfos.get(member_id)
        if contactinfo:
            member["py contactInfo"] = contactinfo


def link_member_workgroups(
    members: dict[int, dict[str, Any]],
    memberships: dict[int, dict[str, Any]],
) -> None:
    for member in members.values():
        member["py member workgroups"] = []
        member["py workgroups"] = {}
    for workgroup in memberships.values():
        for element in workgroup["membership"]:
            member_duid = element.get("py member duid")
            if member_duid in members:
                member = members[member_duid]
                member["py member workgroups"].append(workgroup)
                member["py workgroups"][workgroup["name"]] = workgroup


def link_member_ministries(
    members: dict[int, dict[str, Any]],
    memberships: dict[int, dict[str, Any]],
) -> None:
    for member in members.values():
        member["py ministries"] = {}
    for ministry in memberships.values():
        for element in ministry["membership"]:
            member_duid = element.get("py member duid")
            if member_duid in members and ministry_membership_is_current(element):
                member = members[member_duid]
                family = member.get("py family") or {}
                member["py ministries"][ministry["name"]] = {
                    "id": ministry["id"],
                    "name": ministry["name"],
                    "role": element.get("ministryRoleName"),
                    "start date": element.get("startDate"),
                    "end date": element.get("endDate"),
                    "member duid": member_duid,
                    "family duid": element.get("py family duid")
                    or family.get("familyDUID"),
                    "record": element,
                }


def make_member_friendly_names(members: dict[int, dict[str, Any]]) -> None:
    for member in members.values():
        first = get_member_preferred_first(member)
        last = member.get("lastName", "")
        member["py friendly name FL"] = f"{first} {last}".strip()
        member["py friendly name LF"] = f"{last}, {first}".strip(", ")


def _filter_families_and_members(
    families: dict[int, dict[str, Any]],
    members: dict[int, dict[str, Any]],
    *,
    family_workgroup_memberships: dict[int, dict[str, Any]],
    member_workgroup_memberships: dict[int, dict[str, Any]],
    ministry_type_memberships: dict[int, dict[str, Any]],
    org_id: int,
    active_only: bool,
    parishioners_only: bool,
    include_deceased: bool,
) -> None:
    for member in members.values():
        member["py active"] = True
    for member_id, member in list(members.items()):
        if (not include_deceased and member_is_deceased(member)) or (
            active_only and not member_is_active(member)
        ):
            member["py active"] = False
            family = member.get("py family")
            if family is not None:
                family["py members"] = [
                    item
                    for item in family.get("py members", [])
                    if int(item["memberDUID"]) != member_id
                ]
            _remove_memberships_for_member(
                member_id,
                member_workgroup_memberships,
                ministry_type_memberships,
            )
            del members[member_id]
    for family_id, family in list(families.items()):
        retained_members = any(
            member.get("py active") for member in family.get("py members", [])
        )
        remove_family = (
            not retained_members
            or (active_only and not family_is_active(family))
            or (parishioners_only and not family_is_parishioner(family, org_id))
        )
        if remove_family:
            for member in family.get("py members", []):
                members.pop(int(member["memberDUID"]), None)
            _remove_memberships_for_family(
                family_id,
                family_workgroup_memberships,
                member_workgroup_memberships,
                ministry_type_memberships,
            )
            del families[family_id]


def _remove_memberships_for_family(
    family_id: int,
    family_workgroup_memberships: dict[int, dict[str, Any]],
    member_workgroup_memberships: dict[int, dict[str, Any]],
    ministry_type_memberships: dict[int, dict[str, Any]],
) -> None:
    for collection in (
        family_workgroup_memberships,
        member_workgroup_memberships,
        ministry_type_memberships,
    ):
        for group in collection.values():
            group["membership"] = [
                item
                for item in group["membership"]
                if int(
                    item.get("py family duid")
                    or item.get("familyDUID")
                    or item.get("familyId")
                    or -1
                )
                != family_id
            ]


def _remove_memberships_for_member(
    member_id: int,
    member_workgroup_memberships: dict[int, dict[str, Any]],
    ministry_type_memberships: dict[int, dict[str, Any]],
) -> None:
    for collection in (member_workgroup_memberships, ministry_type_memberships):
        for group in collection.values():
            group["membership"] = [
                item
                for item in group["membership"]
                if int(item.get("py member duid") or item.get("memberDUID") or -1)
                != member_id
            ]


def family_is_active(family: dict[str, Any]) -> bool:
    if family.get("py family group") == "Inactive":
        return False
    return any(member_is_active(member) for member in family.get("py members", []))


BUSINESS_LOGISTICS_WORKGROUP_NAME = "Business Logistics Email"


def family_business_logistics_emails(
    family: dict[str, Any],
    member_workgroups: dict[int, dict[str, Any]],
    log: Any | None = None,
) -> list[str]:
    _members, emails = family_workgroup_emails(
        family,
        member_workgroups,
        BUSINESS_LOGISTICS_WORKGROUP_NAME,
        log=log,
    )
    return emails


def family_business_logistics_emails_members(
    family: dict[str, Any],
    member_workgroups: dict[int, dict[str, Any]],
    log: Any | None = None,
) -> list[dict[str, Any]]:
    members, _emails = family_workgroup_emails(
        family,
        member_workgroups,
        BUSINESS_LOGISTICS_WORKGROUP_NAME,
        log=log,
    )
    return members


def family_workgroup_emails(
    family: dict[str, Any],
    member_workgroups: dict[int, dict[str, Any]],
    workgroup_name: str,
    *,
    log: Any | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    target = next(
        (
            workgroup
            for workgroup in member_workgroups.values()
            if workgroup["name"] == workgroup_name
        ),
        None,
    )
    if target is None:
        if log is not None:
            log.error("DID NOT FIND %s MEMBER WORKGROUP!", workgroup_name)
        return [], []
    selected_members: list[dict[str, Any]] = []
    emails: dict[str, bool] = {}
    family_members = {
        int(member["memberDUID"]): member for member in family.get("py members", [])
    }
    for row in target.get("membership", []):
        member_duid = (
            row.get("py member duid") or row.get("memberDUID") or row.get("memberId")
        )
        if member_duid is None or int(member_duid) not in family_members:
            continue
        member = family_members[int(member_duid)]
        member_emails = member_email_addresses(member)
        if member_emails:
            selected_members.append(member)
            for email in member_emails:
                emails[email] = True
    if emails:
        return selected_members, list(emails)

    for member in get_family_heads(family).values():
        member_emails = member_email_addresses(member)
        if member_emails:
            selected_members.append(member)
            for email in member_emails:
                emails[email] = True
    if emails:
        return selected_members, list(emails)

    for member in family.get("py members", []):
        member_emails = member_email_addresses(member)
        if member_emails:
            selected_members.append(member)
            for email in member_emails:
                emails[email] = True
    if emails:
        return selected_members, list(emails)

    for email in family.get("py eMailAddresses", []):
        emails[email.lower()] = True
    return selected_members, list(emails)


def ministry_membership_is_current(
    membership: dict[str, Any],
    *,
    today: dt.date | None = None,
) -> bool:
    current = today or dt.date.today()
    start_date = _parse_optional_date(membership.get("startDate"))
    end_date = _parse_optional_date(membership.get("endDate"))
    if start_date is None and end_date is None:
        return False
    if start_date and start_date > current:
        return False
    return not (end_date and end_date <= current)


def one_year_ago(today: dt.date | None = None) -> dt.date:
    current = today or dt.date.today()
    try:
        return current.replace(year=current.year - 1)
    except ValueError:
        return current.replace(year=current.year - 1, day=28)


def _parse_optional_date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return dt.datetime.fromisoformat(value).date()
    raise ConfigError(f"invalid date value: {value!r}")


def family_is_parishioner(family: dict[str, Any], org_id: int | str | None) -> bool:
    if org_id is None or family.get("registeredOrganizationID") is None:
        return False
    return int(family["registeredOrganizationID"]) == int(org_id)


def get_family_heads(family: dict[str, Any]) -> dict[int, dict[str, Any]]:
    target_roles = {"Head", "Husband", "Wife"}
    return {
        int(member["memberDUID"]): member
        for member in family.get("py members", [])
        if member.get("memberType") in target_roles
    }


def member_is_deceased(member: dict[str, Any]) -> bool:
    return member.get("memberStatus") == "Deceased"


def member_is_active(member: dict[str, Any]) -> bool:
    return member.get("memberStatus") != "Inactive" and not member_is_deceased(member)


def get_member_public_phones(member: dict[str, Any]) -> list[dict[str, str]]:
    if not member.get("family_PublishPhone"):
        return []
    phones = []
    for key, phone_type in (("mobilePhone", "cell"), ("homePhone", "home")):
        if member.get(key):
            phones.append({"number": member[key], "type": phone_type})
    return phones


def get_member_public_email(member: dict[str, Any]) -> str | None:
    if not member.get("family_PublishEMail"):
        return None
    emails = member.get("py emailAddresses") or []
    return emails[0] if emails else None


def get_member_preferred_first(member: dict[str, Any]) -> str:
    contact_info = member.get("py contactInfo") or {}
    return contact_info.get("nickName") or member["firstName"]


def salutation_for_members(members: list[dict[str, Any]]) -> tuple[str, str]:
    if not members:
        raise ConfigError("salutation requires at least one member")
    if len(members) == 1:
        return get_member_preferred_first(members[0]), members[0]["lastName"]
    first_names = [get_member_preferred_first(member) for member in members]
    all_same_last = all(
        member["lastName"] == members[0]["lastName"] for member in members
    )
    if all_same_last:
        if len(first_names) == 2:
            first = " and ".join(first_names)
        else:
            first = ", ".join(first_names[:-1]) + f", and {first_names[-1]}"
        return first, members[0]["lastName"]
    if len(members) == 2:
        return (
            f"{first_names[0]} {members[0]['lastName']} and {first_names[1]}",
            members[1]["lastName"],
        )
    names = [
        f"{first_names[index]} {member['lastName']}"
        for index, member in enumerate(members[:-1])
    ]
    return ", ".join(names) + f", and {first_names[-1]}", members[-1]["lastName"]
