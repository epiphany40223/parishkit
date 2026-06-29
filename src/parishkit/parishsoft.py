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
import hashlib
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from parishkit.config import ConfigError
from parishkit.files import atomic_write_text
from parishkit.retry import RetryError, RetryPolicy, TransientRetryError, retry_call

DEFAULT_API_BASE_URL = "https://ps-fs-external-api-prod.azurewebsites.net/api/v2"
LOGGER = logging.getLogger(__name__)


class ParishSoftAPIError(RuntimeError):
    """Raised when the ParishSoft API returns a non-success HTTP status.

    Carries the originating status code, endpoint, and raw response body so
    callers can inspect or log the failure without re-issuing the request.
    """

    def __init__(self, status_code: int, endpoint: str, response_text: str):
        """Store request failure details and build a human-readable message."""
        self.status_code = status_code
        self.endpoint = endpoint
        self.response_text = response_text
        detail = response_text[:500] if response_text else ""
        message = f"ParishSoft API error on {endpoint}: HTTP {status_code}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


def parse_cache_limit(cache_limit: str | int | float | None) -> float | None:
    """Normalize a cache freshness limit into seconds.

    Accepts either a numeric count of seconds or a duration string with a
    single trailing unit suffix (``s``, ``m``, ``h``, ``d``), such as ``15m``
    or ``2d``. ``None`` or an empty/blank value means "no limit" and returns
    ``None``. ``bool`` is rejected explicitly because it is a subtype of
    ``int`` but is never a meaningful duration. Raises ConfigError for
    negative numbers or malformed strings.
    """
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
    """Immutable connection and caching settings for a ParishSoftClient.

    Bundles the API key, on-disk cache location, request timeout, and the
    optional expected organization name used to guard against pointing at the
    wrong ParishSoft tenant.
    """

    api_key: str
    cache_dir: Path
    expected_organization: str | None = None
    cache_limit: float | None = None
    api_base_url: str = DEFAULT_API_BASE_URL
    timeout: float = 30.0

    def __post_init__(self) -> None:
        """Validate field types and value ranges, raising ConfigError on bad input."""
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
    """HTTP client for the ParishSoft external API with on-disk response caching.

    Wraps a requests Session preconfigured with the API key header, applies a
    retry policy to transient failures, and transparently caches GET/POST
    responses under the configured cache directory.
    """

    def __init__(
        self,
        config: ParishSoftConfig,
        *,
        session: requests.Session | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Configure the session, retry policy, and private cache directory.

        A caller-supplied session or retry policy may be injected (useful for
        testing); otherwise sensible defaults are created. The cache directory
        is created if needed and locked down to owner-only (0o700) because
        cached responses can contain personal contact information.
        """
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"x-api-key": config.api_key})
        self.retry_policy = retry_policy or RetryPolicy(attempts=3, initial_delay=0.2)
        self._organization_id: int | None = None
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config.cache_dir.chmod(0o700)

    def validate_organization(self) -> int:
        """Confirm the API key maps to exactly one organization and return its ID.

        Raises ConfigError if the key resolves to zero or multiple
        organizations, or if ``expected_organization`` is configured and does
        not match the organization's report name. This guards against
        accidentally operating on the wrong ParishSoft tenant.
        """
        LOGGER.info("Validating ParishSoft organization")
        organizations = self.post_uncached("organizations/search", {})
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
        LOGGER.info(
            "Validated ParishSoft organization %s (%s)",
            name,
            organization["organizationID"],
        )
        self._organization_id = int(organization["organizationID"])
        return self._organization_id

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Send a GET request, returning cached data when a fresh copy exists.

        On a cache miss the response is fetched, parsed as JSON (an empty body
        is treated as an empty list), stored in the cache, and returned.
        """
        cached = self._load_cache(endpoint, params)
        if cached is not None:
            return cached
        url = self._url(endpoint)
        LOGGER.debug("Fetching ParishSoft GET %s", endpoint)
        response = self._request(
            lambda: self.session.get(url, params=params, timeout=self.config.timeout)
        )
        # An empty response body is normalized to [] so callers always get JSON.
        data = _response_json(response, endpoint)
        self._save_cache(endpoint, params, data)
        return data

    def post(self, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        """Send a POST request, returning cached data when a fresh copy exists.

        Used for ParishSoft "search" style endpoints that are read-only despite
        using POST; the payload participates in the cache key.
        """
        cached = self._load_cache(endpoint, payload)
        if cached is not None:
            return cached
        LOGGER.debug("Fetching ParishSoft POST %s", endpoint)
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
        return _response_json(response, endpoint)

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
        """Fetch every page of a GET endpoint and return the combined items.

        ParishSoft endpoints disagree on pagination conventions, so the
        parameter names and offset style are configurable: ``offset_type``
        ``"index"`` sends the running item count as the offset, while
        ``"page"`` sends a 1-based page number. Pages are requested until
        ``_extract_page`` reports the data is exhausted.

        The cache key folds in the limit and offset *style* (not the running
        offset) so the whole result set caches under a single stable key. The
        steps are kept explicit so operational behavior remains easy to audit
        and test.
        """
        cache_params = dict(params or {})
        cache_params.update({limit_name: limit, offset_name: offset_type})
        cached = self._load_cache(endpoint, cache_params)
        if cached is not None:
            return cached
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            # Rebuild per-request params each loop so the offset reflects either
            # how many items we have so far (index style) or the page counter.
            request_params = dict(params or {})
            request_params[limit_name] = limit
            request_params[offset_name] = len(items) if offset_type == "index" else page
            # Bind request_params as a default arg so each retry/closure uses
            # this iteration's params rather than the loop's final value.
            response = self._request(
                lambda request_params=request_params: self.session.get(
                    self._url(endpoint),
                    params=request_params,
                    timeout=self.config.timeout,
                )
            )
            data = _response_json(response, endpoint)
            page_items, done = _extract_page(data)
            items.extend(page_items)
            LOGGER.debug(
                "Fetched ParishSoft GET page for %s: %s items (%s total)",
                endpoint,
                len(page_items),
                len(items),
            )
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
        """Fetch every page of a POST endpoint and return the combined items.

        Mirrors get_paginated but carries pagination fields in the JSON body
        instead of the query string. ``offset_type`` selects index-style
        (running item count) versus page-number offsets, and the cache key
        folds in the limit and offset style so the full result set caches
        under one stable key. The steps are kept explicit so operational
        behavior remains easy to audit and test.
        """
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
            # Bind request_payload as a default arg so each retry/closure uses
            # this iteration's payload rather than the loop's final value.
            response = self._request(
                lambda request_payload=request_payload: self.session.post(
                    self._url(endpoint),
                    json=request_payload,
                    timeout=self.config.timeout,
                )
            )
            data = _response_json(response, endpoint)
            page_items, done = _extract_page(data)
            items.extend(page_items)
            LOGGER.debug(
                "Fetched ParishSoft POST page for %s: %s items (%s total)",
                endpoint,
                len(page_items),
                len(items),
            )
            if done:
                break
            page += 1
        self._save_cache(endpoint, cache_payload, items)
        return items

    def _request(self, func: Any) -> requests.Response:
        """Execute an HTTP call with retries and uniform error handling.

        ``func`` is a zero-argument callable returning a requests Response.
        Retryable statuses (429 plus 5xx gateway/timeout codes) are raised as
        transient errors so the retry policy can re-attempt them; other
        non-2xx statuses raise ParishSoftAPIError immediately. If retries are
        exhausted on a transient error, it is converted into a regular
        ParishSoftAPIError so callers see a single exception type.
        """

        def call() -> requests.Response:
            """Issue one attempt and translate the HTTP status into exceptions."""
            response = func()
            # 429 and these 5xx codes are worth retrying; signal transience so
            # retry_call backs off and tries again.
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
            # Retries exhausted: surface a transient HTTP failure as the public
            # ParishSoftAPIError type instead of leaking the internal one.
            if isinstance(exc.last_exception, _TransientParishSoftAPIError):
                raise ParishSoftAPIError(
                    exc.last_exception.status_code,
                    exc.last_exception.endpoint,
                    exc.last_exception.response_text,
                ) from exc
            raise

    def _url(self, endpoint: str) -> str:
        """Join the configured base URL with an endpoint, avoiding double slashes."""
        return f"{self.config.api_base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def _cache_path(self, endpoint: str, params: dict[str, Any] | None) -> Path:
        """Return the cache file path for a request, derived from tenant + request.

        The prefix includes a non-secret fingerprint of the API key/base URL
        plus the validated organization ID when known, so one deployment cannot
        reuse another tenant's cached ParishSoft data. Params are sorted and
        URL-encoded into the filename so identical requests map to one file.
        """
        suffix = ""
        if params:
            suffix = "-" + urlencode(sorted(params.items()), doseq=True)
        name = f"cache-v2-{self._cache_scope()}-{endpoint}{suffix}.json".replace(
            "/", "-"
        )
        return self.config.cache_dir / name

    def _cache_scope(self) -> str:
        """Return a stable, non-secret cache namespace for this client."""
        fingerprint_source = f"{self.config.api_base_url}\0{self.config.api_key}"
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[
            :16
        ]
        organization = (
            f"org-{self._organization_id}"
            if self._organization_id is not None
            else "org-unvalidated"
        )
        return f"{fingerprint}-{organization}"

    def _load_cache(self, endpoint: str, params: dict[str, Any] | None) -> Any | None:
        """Return cached response data, or None if absent or stale.

        When ``cache_limit`` is set, a file whose modification time predates
        the limit window is treated as a miss so callers re-fetch fresh data.
        """
        cache_path = self._cache_path(endpoint, params)
        if not cache_path.exists():
            LOGGER.debug("ParishSoft cache miss for %s", endpoint)
            return None
        if self.config.cache_limit is not None:
            oldest = time.time() - self.config.cache_limit
            if cache_path.stat().st_mtime < oldest:
                LOGGER.debug("ParishSoft cache stale for %s", endpoint)
                return None
        LOGGER.debug("ParishSoft cache hit for %s", endpoint)
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning(
                "Ignoring invalid ParishSoft cache file %s for %s",
                cache_path,
                endpoint,
            )
            return None

    def _save_cache(
        self, endpoint: str, params: dict[str, Any] | None, data: Any
    ) -> None:
        """Write response data to the cache as pretty, key-sorted JSON.

        Uses an atomic write so a partial file is never left behind, and sorts
        keys so cached files are stable and diff-friendly.
        """
        cache_path = self._cache_path(endpoint, params)
        LOGGER.debug("Writing ParishSoft cache for %s", endpoint)
        atomic_write_text(
            cache_path,
            json.dumps(data, sort_keys=True, indent=2),
        )


class _TransientParishSoftAPIError(TransientRetryError):
    """Internal retryable error carrying the failed response details.

    Subclasses TransientRetryError so the retry policy will re-attempt the
    request; on exhaustion the details are repackaged as a ParishSoftAPIError.
    """

    def __init__(
        self,
        status_code: int,
        endpoint: str,
        response_text: str,
        message: str,
    ):
        """Store response details alongside the retry message."""
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.response_text = response_text


def _response_json(response: requests.Response, endpoint: str) -> Any:
    """Parse a ParishSoft JSON response or raise the public API error type."""
    if not response.text:
        return []
    try:
        return response.json()
    except ValueError as exc:
        raise ParishSoftAPIError(
            response.status_code,
            endpoint,
            f"invalid JSON response from ParishSoft: {exc}",
        ) from exc


def _extract_page(data: Any) -> tuple[list[dict[str, Any]], bool]:
    """Return one page's items and whether pagination is complete.

    Handles the two shapes ParishSoft returns: a bare list (where an empty
    list signals the end) or an envelope dict with a ``data`` list plus
    ``pagingInfo`` (where the current page reaching the total page count
    signals the end). Any other shape raises ParishSoftAPIError.
    """
    if isinstance(data, list):
        # A bare list has no paging metadata, so an empty page means "done".
        return data, len(data) == 0
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        paging = data.get("pagingInfo") or {}
        done = paging.get("pageNumber", 1) >= paging.get("totalPages", 1)
        return data["data"], done
    raise ParishSoftAPIError(500, "pagination", f"unexpected page shape: {data!r}")


def normalize_family_email(family: dict[str, Any]) -> None:
    """Lowercase a family's email field and split it into a derived address list.

    ParishSoft stores possibly multiple addresses in one semicolon-delimited
    string. This lowercases the original field in place and adds a derived
    ``"py eMailAddresses"`` list (the ``py`` prefix marks fields synthesized by
    this library). Does nothing when no email is present.
    """
    value = family.get("eMailAddress")
    if value:
        addresses = split_email_addresses(value)
        family["eMailAddress"] = "; ".join(addresses)
        family["py eMailAddresses"] = addresses


def normalize_member_email(member: dict[str, Any]) -> None:
    """Lowercase a member's email field and split it into a derived address list.

    Mirrors normalize_family_email for member records, adding a derived
    ``"py emailAddresses"`` list. Does nothing when no email is present.
    """
    value = member.get("emailAddress")
    if value:
        addresses = split_email_addresses(value)
        member["emailAddress"] = "; ".join(addresses)
        member["py emailAddresses"] = addresses


def member_email_addresses(member: Mapping[str, Any]) -> list[str]:
    """Return all email addresses for a member as a cleaned, lowercased list.

    Prefers the pre-split ``"py emailAddresses"`` list when present; otherwise
    falls back to splitting the raw semicolon-delimited ``emailAddress`` field.
    Returns an empty list when the member has no email.
    """
    emails = member.get("py emailAddresses")
    if isinstance(emails, list):
        return split_email_addresses([str(email) for email in emails])
    value = member.get("emailAddress")
    if value:
        return split_email_addresses(value)
    return []


def split_email_addresses(value: str | Sequence[str]) -> list[str]:
    """Split, lowercase, de-duplicate, and drop blank email fragments."""
    raw_items: list[str] = []
    if isinstance(value, str):
        raw_items = value.split(";")
    else:
        for item in value:
            raw_items.extend(item.split(";"))
    addresses: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        email = item.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        addresses.append(email)
    return addresses


def normalize_dates(elements: list[dict[str, Any]], fields: list[str]) -> None:
    """Convert the named date string fields to date objects, in place.

    Each element in ``elements`` has every field in ``fields`` parsed via
    ``_parse_optional_date``. Missing, None, or empty fields are skipped so
    records with absent dates are left untouched.
    """
    for element in elements:
        for field in fields:
            if field not in element or element[field] in (None, ""):
                continue
            try:
                element[field] = _parse_optional_date(element[field])
            except ConfigError as exc:
                identifier = element.get("memberDUID") or element.get(
                    "familyDUID",
                    element.get("id", "unknown"),
                )
                raise ConfigError(
                    f"invalid ParishSoft date in {field} for record {identifier}: {exc}"
                ) from exc


def link_families_and_members(
    families: dict[int, dict[str, Any]],
    members: dict[int, dict[str, Any]],
) -> None:
    """Cross-link families and members via their DUIDs, in place.

    Each member gains a ``"py family"`` back-reference (None if its family is
    not in ``families``), and each family gains a ``"py members"`` list. Family
    lists are reset first so the linking is idempotent across repeated calls.
    """
    for family in families.values():
        family["py members"] = []
    for member in members.values():
        family_duid = int(member["familyDUID"])
        family = families.get(family_duid)
        member["py family"] = family
        if family is not None:
            family["py members"].append(member)


def load_families(client: ParishSoftClient, org_id: int) -> dict[int, dict[str, Any]]:
    """Load ParishSoft family records keyed by DUID."""
    LOGGER.info("Loading ParishSoft families")
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
    LOGGER.info("Loaded %s ParishSoft families", len(families))
    return families


def load_members(client: ParishSoftClient, org_id: int) -> dict[int, dict[str, Any]]:
    """Load ParishSoft member records keyed by DUID."""
    LOGGER.info("Loading ParishSoft members")
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
    LOGGER.info("Loaded %s ParishSoft members", len(members))
    return members


def load_family_workgroups(client: ParishSoftClient) -> dict[int, dict[str, Any]]:
    """Load ParishSoft family workgroups keyed by DUID."""
    LOGGER.info("Loading ParishSoft family workgroups")
    elements = client.get_paginated(
        "families/workgroup/list",
        offset_name="PageNumber",
        offset_type="page",
    )
    family_workgroups = {
        int(element["workgroupDUID"]): {
            "name": element["workgroupName"],
            "duid": element["workgroupDUID"],
            "id": element["workgroupID"],
        }
        for element in elements
    }
    LOGGER.info("Loaded %s ParishSoft family workgroups", len(family_workgroups))
    return family_workgroups


def load_family_groups(client: ParishSoftClient) -> dict[int, str]:
    """Load ParishSoft family group names keyed by ID."""
    LOGGER.info("Loading ParishSoft family groups")
    elements = client.get("families/group/lookup/list")
    family_groups = {
        int(element["famGroupID"]): element["famGroup"] for element in elements
    }
    LOGGER.info("Loaded %s ParishSoft family groups", len(family_groups))
    return family_groups


def load_family_workgroup_memberships(
    client: ParishSoftClient,
    family_workgroups: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Load each family workgroup's membership rows, keyed by workgroup DUID.

    Iterates the known family workgroups and fetches the member list for each.
    Per row it lowercases the email, adds a derived ``"py emails"`` list, and
    copies the family DUID into a normalized ``"py family duid"`` field. Each
    result entry bundles the workgroup id/name with its membership rows.
    """
    LOGGER.info("Loading ParishSoft family workgroup memberships")
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
        LOGGER.debug(
            "Loaded %s family memberships for workgroup %s",
            len(elements),
            workgroup["name"],
        )
    LOGGER.info(
        "Loaded ParishSoft family workgroup memberships for %s workgroups",
        len(results),
    )
    return results


def load_member_contactinfos(
    client: ParishSoftClient, org_id: int
) -> dict[int, dict[str, Any]]:
    """Load member contact records keyed by contact DUID."""
    LOGGER.info("Loading ParishSoft member contact info")
    elements = client.post_paginated(
        "members/contact/list",
        {"organizationIDs": [org_id]},
        offset_type="page",
    )
    normalize_dates(elements, ["dateOfBirth", "dateOfDeath"])
    contactinfos = {int(element["memberDUID"]): element for element in elements}
    LOGGER.info("Loaded %s ParishSoft member contact records", len(contactinfos))
    return contactinfos


def load_member_workgroups(client: ParishSoftClient) -> dict[int, dict[str, Any]]:
    """Load ParishSoft member workgroups keyed by DUID."""
    LOGGER.info("Loading ParishSoft member workgroups")
    elements = client.get_paginated(
        "members/workgroup/lookup/list",
        offset_name="PageNumber",
        offset_type="page",
    )
    member_workgroups = {
        int(element["id"]): {
            "name": element["name"],
            "duid": int(element["id"]),
            "id": int(element["id"]),
        }
        for element in elements
    }
    LOGGER.info("Loaded %s ParishSoft member workgroups", len(member_workgroups))
    return member_workgroups


def load_member_workgroup_memberships(
    client: ParishSoftClient,
    member_workgroups: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Load each member workgroup's membership rows, keyed by workgroup DUID.

    For every known member workgroup, fetches its membership list and
    normalizes each row: copies the member and family DUIDs into ``"py member
    duid"``/``"py family duid"``, lowercases the email, and adds a derived
    ``"py emailAddresses"`` list. Each result entry bundles the workgroup
    id/name with its membership rows.
    """
    LOGGER.info("Loading ParishSoft member workgroup memberships")
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
        LOGGER.debug(
            "Loaded %s member memberships for workgroup %s",
            len(elements),
            workgroup["name"],
        )
    LOGGER.info(
        "Loaded ParishSoft member workgroup memberships for %s workgroups",
        len(results),
    )
    return results


def load_ministry_types(client: ParishSoftClient) -> dict[int, dict[str, Any]]:
    """Load ParishSoft ministry type records keyed by DUID."""
    LOGGER.info("Loading ParishSoft ministry types")
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
    LOGGER.info("Loaded %s ParishSoft ministry types", len(ministry_types))
    return ministry_types


def load_ministry_type_memberships(
    client: ParishSoftClient,
    ministry_types: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Load each ministry's roster of ministers, keyed by ministry id.

    For every ministry type, fetches the minister list and normalizes each
    row: copies member/family DUIDs into ``"py member duid"``/``"py family
    duid"`` and parses the start/end dates. Each result entry bundles the
    ministry id/name (tolerating either ``name`` or ``ministryTypeName``) with
    its membership rows.
    """
    LOGGER.info("Loading ParishSoft ministry memberships")
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
        LOGGER.debug(
            "Loaded %s ministry memberships for %s",
            len(elements),
            ministry_type.get("name") or ministry_type.get("ministryTypeName"),
        )
    LOGGER.info(
        "Loaded ParishSoft ministry memberships for %s ministries",
        len(results),
    )
    return results


def load_funds(client: ParishSoftClient, org_id: int) -> dict[int, dict[str, Any]]:
    """Load ParishSoft fund names keyed by fund ID."""
    LOGGER.info("Loading ParishSoft funds")
    elements = client.get(f"offering/{org_id}/funds")
    funds = {int(element["fundId"]): element for element in elements}
    LOGGER.info("Loaded %s ParishSoft funds", len(funds))
    return funds


def load_pledges(
    client: ParishSoftClient,
    funds: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Load pledge records keyed by pledge ID, linked to their funds.

    Pledge and start dates are parsed, and each pledge gets a derived ``"py
    fund"`` reference resolved from ``funds`` (None when no fund id is present).
    The fund id key is matched case-insensitively because ParishSoft is
    inconsistent about ``fundID`` vs ``fundId``.
    """
    LOGGER.info("Loading ParishSoft pledges")
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
    pledges = {int(element["pledgeID"]): element for element in elements}
    LOGGER.info("Loaded %s ParishSoft pledges", len(pledges))
    return pledges


def load_contribution_details(
    client: ParishSoftClient,
    funds: dict[int, dict[str, Any]],
    pledges: dict[int, dict[str, Any]],
    *,
    start_date: str | None = None,
    family_id: int | None = None,
    member_id: int | None = None,
) -> dict[int, dict[str, Any]]:
    """Load contribution detail records keyed by contribution ID.

    Optional ``start_date``, ``family_id``, and ``member_id`` filters are sent
    only when provided. Contribution dates are parsed, and each record is
    linked to its fund and pledge via derived ``"py fund"``/``"py pledge"``
    fields (None when the referenced id is missing). Field names are matched
    case-insensitively because ParishSoft is inconsistent about ``Id`` vs
    ``ID``. The steps are kept explicit so operational behavior remains easy
    to audit and test.
    """
    LOGGER.info("Loading ParishSoft contribution details")
    params = {}
    if start_date:
        params["startDate"] = start_date
    if family_id is not None:
        params["FamilyId"] = family_id
    if member_id is not None:
        params["MemberId"] = member_id
    elements = client.get_paginated(
        "offering/contributiondetail/list",
        params or None,
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
    contributions = {int(element["contributionID"]): element for element in elements}
    LOGGER.info("Loaded %s ParishSoft contribution details", len(contributions))
    return contributions


def _copy_duid(element: dict[str, Any], source: str, target: str) -> None:
    """Copy ``source`` into ``target`` as an int, only if ``source`` exists."""
    if source in element:
        element[target] = int(element[source])


def _copy_first_duid(
    element: dict[str, Any],
    sources: tuple[str, ...],
    target: str,
) -> None:
    """Copy the first present DUID from ``sources`` into ``target`` as an int.

    ParishSoft labels the same identifier differently across endpoints (for
    example ``familyDUID`` vs ``familyId``), so the candidate keys are tried in
    order and the first match wins. If none are present, ``target`` is left
    unset.
    """
    for source in sources:
        if source in element:
            element[target] = int(element[source])
            return


@dataclass(frozen=True)
class ParishSoftData:
    """A fully loaded, cross-linked snapshot of one organization's ParishSoft data.

    Aggregates families, members, their workgroups, ministries, contact info,
    and (optionally) giving records (funds, pledges, contributions). The
    records carry the derived ``"py ..."`` link fields populated during
    loading, so callers can traverse relationships without re-querying.
    """

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
    """Load, cross-link, and filter a full ParishSoft dataset for one org.

    Validates the organization, loads each entity type, links them together
    (members to families, workgroups, ministries, giving records, etc.),
    builds friendly display names, then prunes records per the flags before
    returning a ParishSoftData snapshot.

    Flags:
      - ``active_only``: drop inactive families and members.
      - ``parishioners_only``: keep only families registered at this org.
      - ``include_deceased``: retain deceased members when True.
      - ``load_contributions``: when truthy, also load funds, pledges, and
        contributions; a string value is used as the contribution start date,
        otherwise giving from one year ago is loaded.

    This is the central ParishSoft aggregation path, so it keeps the
    individual load and link steps explicit for easier operational
    troubleshooting.
    """
    LOGGER.info("Loading full ParishSoft family/member dataset")
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
    LOGGER.info("Cross-linking ParishSoft family/member dataset")
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
    LOGGER.info(
        "Loaded full ParishSoft dataset: %s families, %s members",
        len(families),
        len(members),
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
    """Attach family group names to family records."""
    for family in families.values():
        group_id = family.get("famGroupID") or family.get("familyGroupID")
        if group_id is not None:
            family["py family group"] = family_groups.get(int(group_id))


def link_family_workgroups(
    families: dict[int, dict[str, Any]],
    memberships: dict[int, dict[str, Any]],
) -> None:
    """Attach each family's workgroups via its membership rows, in place.

    Resets and populates two derived fields per family: ``"py family
    workgroups"`` (a list) and ``"py workgroups"`` (keyed by workgroup name).
    A row's family is resolved from whichever DUID field is present, since
    endpoints label it inconsistently.
    """
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
    """Attach pledge records to family records."""
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
    """Attach contribution records to family records."""
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
    """Attach contact records to member records."""
    for member_id, member in members.items():
        contactinfo = contactinfos.get(member_id)
        if contactinfo:
            member["py contactInfo"] = contactinfo


def link_member_workgroups(
    members: dict[int, dict[str, Any]],
    memberships: dict[int, dict[str, Any]],
) -> None:
    """Attach each member's workgroups via its membership rows, in place.

    Resets and populates two derived fields per member: ``"py member
    workgroups"`` (a list) and ``"py workgroups"`` (keyed by workgroup name),
    matching rows on the normalized ``"py member duid"``.
    """
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
    """Attach each member's current ministry roles under ``"py ministries"``.

    Resets every member's ministry map first, then adds an entry per ministry
    that the member currently belongs to (per ``ministry_membership_is_current``;
    expired or future memberships are skipped). Each entry records the role,
    start/end dates, member/family DUIDs, and the raw source row. Keyed by
    ministry name, so a member can hold at most one entry per ministry.
    """
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
    """Populate first-last and last-first display names on each member.

    Uses the member's preferred first name (nickname when available). Adds
    ``"py friendly name FL"`` ("First Last") and ``"py friendly name LF"``
    ("Last, First"), trimming stray separators so a missing first or last name
    does not leave a dangling space or comma.
    """
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
    """Prune out-of-scope members and families and their dangling memberships.

    Runs in two passes. First, members that are deceased (unless
    ``include_deceased``) or inactive (when ``active_only``) are flagged
    inactive, detached from their family, removed from workgroup/ministry
    rosters, and deleted. Second, families are dropped when they have no
    retained members, or (per the flags) when the family itself is inactive or
    is not a registered parishioner of ``org_id``; their remaining members and
    membership rows are cleaned up too. Mutates all passed collections in
    place.

    The steps are kept explicit so operational behavior remains easy to audit
    and test.
    """
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
    """Drop membership rows in every collection that reference the given family.

    Filters each group's membership list across the three workgroup/ministry
    collections, removing rows tied to ``family_id`` so excluded families leave
    no orphaned references behind.
    """
    for collection in (
        family_workgroup_memberships,
        member_workgroup_memberships,
        ministry_type_memberships,
    ):
        for group in collection.values():
            group["membership"] = [
                item
                for item in group["membership"]
                # -1 is a sentinel for "no family id on this row"; it can never
                # equal a real family_id, so such rows are always retained.
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
    """Drop membership rows in member-scoped collections for the given member.

    Filters the workgroup and ministry membership lists so an excluded member
    leaves no orphaned references behind.
    """
    for collection in (member_workgroup_memberships, ministry_type_memberships):
        for group in collection.values():
            group["membership"] = [
                item
                for item in group["membership"]
                # -1 is a sentinel for "no member id"; it never matches a real
                # member_id, so rows lacking an id are always kept.
                if int(item.get("py member duid") or item.get("memberDUID") or -1)
                != member_id
            ]


def family_is_active(family: dict[str, Any]) -> bool:
    """Return True if the family is active in ParishSoft.

    A family in the "Inactive" family group is always inactive; otherwise it
    is considered active when at least one of its members is active.
    """
    if family.get("py family group") == "Inactive":
        return False
    return any(member_is_active(member) for member in family.get("py members", []))


BUSINESS_LOGISTICS_WORKGROUP_NAME = "Business Logistics Email"


def family_business_logistics_emails(
    family: dict[str, Any],
    member_workgroups: dict[int, dict[str, Any]],
    log: Any | None = None,
) -> list[str]:
    """Return the family's best business-logistics contact email addresses.

    Convenience wrapper over family_workgroup_emails for the
    "Business Logistics Email" workgroup, returning only the address list.
    """
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
    """Return the member records behind a family's business-logistics emails.

    Convenience wrapper over family_workgroup_emails for the
    "Business Logistics Email" workgroup, returning only the member list.
    """
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
    """Return (members, emails) for a family using a layered precedence search.

    Resolves the named workgroup, then tries successive sources and returns at
    the first that yields any address: (1) family members who belong to that
    workgroup, (2) the family heads, (3) any family member, and finally (4) the
    family-level email field. Emails are de-duplicated while preserving order
    (a dict is used as an ordered set). If the workgroup is not found, logs an
    error when ``log`` is provided and returns empty lists. This preserves the
    legacy precedence order.
    """
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
    """Return True if a ministry membership is active as of ``today``.

    ``today`` defaults to the current date (injectable for testing). A
    membership with neither a start nor an end date is treated as not current.
    Otherwise it is current when its start date is not in the future and its
    end date (if any) has not yet passed; the end date is treated as
    exclusive, so a membership ending today is no longer current.
    """
    current = today or dt.date.today()
    start_date = _parse_optional_date(membership.get("startDate"))
    end_date = _parse_optional_date(membership.get("endDate"))
    if start_date is None and end_date is None:
        return False
    if start_date and start_date > current:
        return False
    return not (end_date and end_date <= current)


def one_year_ago(today: dt.date | None = None) -> dt.date:
    """Return the date one year before ``today`` (defaults to the current date).

    Handles the Feb 29 edge case: in a non-leap target year the prior year has
    no Feb 29, so the day is clamped to Feb 28.
    """
    current = today or dt.date.today()
    try:
        return current.replace(year=current.year - 1)
    except ValueError:
        return current.replace(year=current.year - 1, day=28)


def _parse_optional_date(value: Any) -> dt.date | None:
    """Coerce a value into a date, accepting several input forms.

    Returns None for None/empty. Passes through existing date objects (taking
    the date part of a datetime) and parses ISO-format strings. Any other type
    raises ConfigError. Being tolerant of already-parsed values makes
    normalization idempotent.
    """
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value).date()
        except ValueError as exc:
            raise ConfigError(f"invalid date value: {value!r}") from exc
    raise ConfigError(f"invalid date value: {value!r}")


def family_is_parishioner(family: dict[str, Any], org_id: int | str | None) -> bool:
    """Return True if the family is registered at the given organization.

    Compares the family's registered organization ID against ``org_id``
    (coercing both to int). Returns False if either side is missing, so a
    family with no registration is never treated as a parishioner.
    """
    if org_id is None or family.get("registeredOrganizationID") is None:
        return False
    return int(family["registeredOrganizationID"]) == int(org_id)


def get_family_heads(family: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Return the family's head members keyed by member DUID.

    Heads are members whose ``memberType`` is Head, Husband, or Wife. Returns
    an empty dict when the family has no such members.
    """
    target_roles = {"Head", "Husband", "Wife"}
    return {
        int(member["memberDUID"]): member
        for member in family.get("py members", [])
        if member.get("memberType") in target_roles
    }


def member_is_deceased(member: dict[str, Any]) -> bool:
    """Return True if the member's status is "Deceased"."""
    return member.get("memberStatus") == "Deceased"


def member_is_active(member: dict[str, Any]) -> bool:
    """Return True if the member is neither inactive nor deceased."""
    return member.get("memberStatus") != "Inactive" and not member_is_deceased(member)


def get_member_public_phones(member: dict[str, Any]) -> list[dict[str, str]]:
    """Return the member's publishable phone numbers as ``{number, type}`` dicts.

    Honors the family's "publish phone" privacy flag: returns an empty list
    when publishing is disallowed. Includes the mobile ("cell") and home
    numbers when present.
    """
    if not member.get("family_PublishPhone"):
        return []
    phones = []
    for key, phone_type in (("mobilePhone", "cell"), ("homePhone", "home")):
        if member.get(key):
            phones.append({"number": member[key], "type": phone_type})
    return phones


def get_member_public_email(member: dict[str, Any]) -> str | None:
    """Return the member's primary publishable email, or None.

    Honors the family's "publish email" privacy flag, returning None when
    publishing is disallowed or when the member has no email; otherwise returns
    the first normalized address.
    """
    if not member.get("family_PublishEMail"):
        return None
    emails = member.get("py emailAddresses") or []
    return emails[0] if emails else None


def get_member_preferred_first(member: dict[str, Any]) -> str:
    """Return the member's preferred first name, favoring a nickname.

    Uses the nickname from linked contact info when available, otherwise falls
    back to the member's ``firstName``.
    """
    contact_info = member.get("py contactInfo") or {}
    return contact_info.get("nickName") or member["firstName"]


def salutation_for_members(members: list[dict[str, Any]]) -> tuple[str, str]:
    """Build a (first-name phrase, last-name) salutation for one or more members.

    The two returned parts are typically joined as "<first part> <last part>"
    by the caller. Behavior by case:
      - one member: their preferred first and last name.
      - all members share a last name: their first names joined ("A and B", or
        an Oxford-comma list for three or more) with the shared last name.
      - exactly two members with differing last names: the first member's full
        name plus the second's first name, returning the second's last name.
      - three or more with mixed last names: full "First Last" names for all but
        the last member, then the final member's first and last name.

    Preferred first names (nicknames) are used throughout. Raises ConfigError
    when ``members`` is empty.
    """
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
