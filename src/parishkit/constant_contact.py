"""Constant Contact integration helpers.

Upstream references:

- Constant Contact API overview:
  https://developer.constantcontact.com/api_guide/index.html
- Constant Contact v3 API reference:
  https://developer.constantcontact.com/api_reference/index.html
- OAuth 2.0 authorization:
  https://developer.constantcontact.com/api_guide/auth_overview.html
- OAuth 2.0 device flow:
  https://developer.constantcontact.com/api_guide/device_flow.html
- Contacts and lists guide:
  https://developer.constantcontact.com/api_guide/contacts_overview.html
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import random
import time
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any

import requests

from parishkit.config import ConfigError
from parishkit.files import atomic_write_text
from parishkit.parishsoft import salutation_for_members
from parishkit.retry import RetryError, RetryPolicy, TransientRetryError, retry_call


class CCAPIError(RuntimeError):
    """Raised by API functions on non-2xx Constant Contact responses."""

    def __init__(self, status_code: int, response_text: str, endpoint: str):
        """Record the failing HTTP status, response body, and endpoint."""
        self.status_code = status_code
        self.response_text = response_text
        self.endpoint = endpoint
        detail = response_text[:500] if response_text else ""
        message = f"Constant Contact API error on {endpoint}: HTTP {status_code}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class ConstantContactConfig:
    """Immutable Constant Contact connection settings.

    Bundles the OAuth client metadata, access token, retry policy, and request
    timeout used by :class:`ConstantContactClient`. Values are validated and
    normalized in ``__post_init__`` so a malformed configuration fails fast.
    """

    client_id: dict[str, Any]
    access_token: dict[str, Any]
    retry_policy: RetryPolicy = RetryPolicy(attempts=3, initial_delay=0.2)
    timeout: float = 30.0

    def __post_init__(self) -> None:
        """Validate and normalize initialized values."""
        _validate_client_id(self.client_id)
        _validate_access_token(self.access_token)
        if not isinstance(self.timeout, int | float) or isinstance(self.timeout, bool):
            raise ConfigError("Constant Contact timeout must be a number")
        if self.timeout <= 0:
            raise ConfigError("Constant Contact timeout must be positive")


class ConstantContactClient:
    """Thin Constant Contact v3 REST client with retry and pagination.

    Wraps a :mod:`requests` session and a :class:`ConstantContactConfig` to
    issue authenticated GET/PUT/POST calls, transparently following pagination
    links and retrying transient server responses.

    Endpoint and pagination behavior follows the Constant Contact v3 API
    reference: https://developer.constantcontact.com/api_reference/index.html
    """

    def __init__(
        self,
        config: ConstantContactConfig,
        *,
        session: requests.Session | None = None,
    ) -> None:
        """Store config and reuse the given session, or create a new one."""
        self.config = config
        self.session = session or requests.Session()

    def headers(
        self,
        *,
        include: str | None = None,
        limit: int | None = None,
        status: str | None = None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """Build Constant Contact request headers and query parameters."""
        headers = {
            "Authorization": f"Bearer {self.config.access_token['access_token']}",
            "Cache-Control": "no-cache",
        }
        params: dict[str, Any] = {}
        if include:
            params["include"] = include
        if limit:
            params["limit"] = limit
        if status:
            params["status"] = status
        return headers, params

    def get_all(
        self,
        api_endpoint: str,
        json_response_field: str,
        *,
        include: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch every record from a paginated collection endpoint.

        Repeatedly GETs ``api_endpoint`` (asking for the maximum page size of
        500), accumulating the list found under ``json_response_field`` in each
        page. The optional ``include`` and ``status`` query parameters are only
        applied to the first request. Returns the concatenated list of records.
        """
        headers, params = self.headers(include=include, status=status, limit=500)
        url = self._url(api_endpoint)
        items: list[dict[str, Any]] = []
        while url:
            response = self._request(
                lambda url=url, params=params: self.session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.config.timeout,
                )
            )
            payload = self._json_response(response, api_endpoint)
            items.extend(payload.get(json_response_field, []))
            # Follow the API's "next" pagination link until it is absent. The
            # href is server-relative, so prefix it with the configured API
            # host to form an absolute URL for the next iteration.
            next_link = (payload.get("_links") or {}).get("next")
            url = (
                f"{self.config.client_id['endpoints']['api']}{next_link['href']}"
                if next_link
                else ""
            )
            # The next link already encodes include/status/limit, so clear the
            # explicit params to avoid sending them twice.
            params = {}
        return items

    def put(self, api_endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send a PUT (update) request and return the parsed response."""
        return self._put_or_post("put", api_endpoint, body)

    def post(self, api_endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send a POST (create) request and return the parsed response."""
        return self._put_or_post("post", api_endpoint, body)

    def _put_or_post(
        self, method: str, api_endpoint: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a Constant Contact write request."""
        headers, _ = self.headers()
        headers["Content-Type"] = "application/json"
        action = getattr(self.session, method)
        response = self._request(
            lambda: action(
                self._url(api_endpoint),
                headers=headers,
                data=json.dumps(body),
                timeout=self.config.timeout,
            )
        )
        return self._json_response(response, api_endpoint)

    def _request(self, func: Any) -> requests.Response:
        """Execute an HTTP call under the retry policy, normalizing errors.

        ``func`` is a no-argument callable that performs the actual request.
        Rate-limit (429) and 5xx responses are treated as transient and retried
        per the configured policy; once retries are exhausted they surface as a
        :class:`CCAPIError`. Any other non-2xx response raises immediately.
        """

        def call() -> requests.Response:
            """Run one attempt, raising on retryable or fatal HTTP errors."""
            response = func()
            # 429 (rate limit) and 5xx are transient; raising a transient error
            # lets retry_call back off and try again rather than failing hard.
            if response.status_code in {429, 500, 502, 503, 504}:
                raise _TransientCCAPIError(
                    response.status_code,
                    response.text,
                    response.url,
                    f"transient Constant Contact HTTP {response.status_code}",
                )
            if not 200 <= response.status_code <= 299:
                raise CCAPIError(response.status_code, response.text, response.url)
            return response

        try:
            return retry_call(call, policy=self.config.retry_policy)
        except RetryError as exc:
            # When retries are exhausted on a transient HTTP error, re-raise it
            # as a normal CCAPIError so callers see a single error type.
            if isinstance(exc.last_exception, _TransientCCAPIError):
                raise CCAPIError(
                    exc.last_exception.status_code,
                    exc.last_exception.response_text,
                    exc.last_exception.endpoint,
                ) from exc
            raise

    def _url(self, api_endpoint: str) -> str:
        """Build a Constant Contact API URL."""
        return (
            f"{self.config.client_id['endpoints']['api'].rstrip('/')}/v3/{api_endpoint}"
        )

    def _json_response(
        self,
        response: requests.Response,
        api_endpoint: str,
    ) -> dict[str, Any]:
        """Parse a Constant Contact JSON response or raise CCAPIError."""
        if not response.text:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise CCAPIError(
                response.status_code,
                f"invalid JSON response from Constant Contact: {exc}",
                response.url or api_endpoint,
            ) from exc
        if not isinstance(payload, dict):
            raise CCAPIError(
                response.status_code,
                "Constant Contact response must be a JSON object",
                response.url or api_endpoint,
            )
        return payload


class _TransientCCAPIError(TransientRetryError):
    """Retryable Constant Contact error carrying the original HTTP details.

    Raised internally for 429/5xx responses so the retry machinery can back off
    while preserving the status code, body, and endpoint for a final error.
    """

    def __init__(
        self,
        status_code: int,
        response_text: str,
        endpoint: str,
        message: str,
    ):
        """Record the retryable HTTP status, body, and endpoint."""
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.endpoint = endpoint


def _validate_client_id(client_id: dict[str, Any]) -> None:
    """Validate Constant Contact OAuth client IDs."""
    if not isinstance(client_id, dict):
        raise ConfigError("Constant Contact client_id must be a mapping")
    endpoints = client_id.get("endpoints")
    if not isinstance(endpoints, dict) or not isinstance(endpoints.get("api"), str):
        raise ConfigError("Constant Contact client_id requires endpoints.api")


def _validate_access_token(access_token: dict[str, Any]) -> None:
    """Ensure a token is a mapping containing a string ``access_token``."""
    if not isinstance(access_token, dict):
        raise ConfigError("Constant Contact access_token must be a mapping")
    if not isinstance(access_token.get("access_token"), str):
        raise ConfigError("Constant Contact access_token requires access_token")


def load_client_id(path: str | Path) -> dict[str, Any]:
    """Load the OAuth client-id JSON file into a dict."""
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def set_valid_from_to(start: dt.datetime, response: dict[str, Any]) -> None:
    """Annotate a token payload with its validity window.

    Records ``start`` as the token's "valid from" time and computes "valid to"
    by adding the OAuth ``expires_in`` (seconds) so later code can check
    expiry without re-parsing the raw token response.
    """
    response["valid from"] = start
    response["valid to"] = start + dt.timedelta(seconds=int(response["expires_in"]))


def save_access_token(path: str | Path, access_token: dict[str, Any]) -> None:
    """Persist a Constant Contact access token safely."""
    data = copy.deepcopy(access_token)
    for key in ("valid from", "valid to"):
        if isinstance(data.get(key), dt.datetime):
            data[key] = data[key].isoformat()
    token_path = Path(path).expanduser()
    atomic_write_text(
        token_path,
        json.dumps(data, sort_keys=True, indent=2),
    )


def load_access_token(path: str | Path) -> dict[str, Any]:
    """Load and validate a Constant Contact access token."""
    token_path = Path(path).expanduser()
    try:
        token = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError) as exc:
        raise ConfigError(
            f"invalid Constant Contact token file {token_path}: {exc}"
        ) from exc
    _validate_access_token(token)
    try:
        for key in ("valid from", "valid to"):
            token[key] = _parse_datetime(token[key])
    except KeyError as exc:
        raise ConfigError(
            f"Constant Contact token file {token_path} is missing {exc.args[0]!r}"
        ) from exc
    except ValueError as exc:
        raise ConfigError(
            f"Constant Contact token file {token_path} has invalid timestamp: {exc}"
        ) from exc
    return token


def token_is_valid(
    access_token: dict[str, Any], *, now: dt.datetime | None = None
) -> bool:
    """Report whether a token is currently within its validity window.

    Compares ``now`` (defaulting to the current UTC time) against the token's
    "valid from"/"valid to" timestamps set by :func:`set_valid_from_to`.
    """
    current = now or dt.datetime.now(dt.UTC)
    return access_token["valid from"] <= current <= access_token["valid to"]


def refresh_access_token(
    client_id: dict[str, Any],
    access_token: dict[str, Any],
    *,
    session: requests.Session | None = None,
    now: dt.datetime | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Exchange a refresh token for a new Constant Contact access token.

    Posts the OAuth ``refresh_token`` grant to the configured token endpoint
    and returns a validated token payload stamped with its validity window. The
    steps are kept explicit so operational behavior remains easy to audit and
    test. Raises :class:`ConfigError` if required config or token fields are
    missing, or if the response is non-2xx, not JSON, or malformed.
    """
    endpoints = client_id.get("endpoints", {})
    token_url = endpoints.get("token")
    cc_client_id = client_id.get("client id") or client_id.get("client_id")
    if not isinstance(token_url, str) or not isinstance(cc_client_id, str):
        raise ConfigError(
            "Constant Contact client_id requires endpoints.token and client id"
        )
    refresh_token = access_token.get("refresh_token")
    if not isinstance(refresh_token, str):
        raise ConfigError("Constant Contact refresh_token is required")
    http = session or requests.Session()
    start = now or dt.datetime.now(dt.UTC)
    response = http.post(
        token_url,
        data={
            "client_id": cc_client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=timeout,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConfigError(
            f"Constant Contact token refresh returned invalid JSON: {exc}"
        ) from exc
    if response.status_code < 200 or response.status_code > 299 or "error" in payload:
        detail = (
            payload.get("error_description") or payload.get("error") or response.text
        )
        raise ConfigError(f"Constant Contact token refresh failed: {detail}")
    # Constant Contact may omit the refresh token when it is unchanged; carry
    # the previous one forward so the saved token stays refreshable.
    if "refresh_token" not in payload and "refresh_token" in access_token:
        payload["refresh_token"] = access_token["refresh_token"]
    try:
        _validate_access_token(payload)
        set_valid_from_to(start, payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(
            f"Constant Contact token refresh returned malformed token: {exc}"
        ) from exc
    return payload


def run_device_oauth_flow(
    client_id: dict[str, Any],
    *,
    session: requests.Session | None = None,
    input_fn: Any = input,
    print_fn: Any = print,
    now: dt.datetime | None = None,
    timeout: float = 30.0,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    """Interactively obtain a token via the OAuth device authorization flow.

    Requests a device authorization, prints the verification URL for the user
    to visit, waits for confirmation, then polls the token endpoint until the
    user has authorized (or the request expires). The ``input_fn``, ``print_fn``,
    ``now``, and ``sleep_fn`` hooks exist so the flow can be driven and tested
    without real I/O. Returns a validated token payload stamped with its
    validity window, or raises :class:`ConfigError` on malformed/failed steps.

    OAuth behavior follows Constant Contact's device flow docs:
    https://developer.constantcontact.com/api_guide/device_flow.html
    """
    endpoints = client_id.get("endpoints", {})
    auth_url = endpoints.get("auth")
    token_url = endpoints.get("token")
    cc_client_id = client_id.get("client id") or client_id.get("client_id")
    if not all(isinstance(value, str) for value in (auth_url, token_url, cc_client_id)):
        raise ConfigError(
            "Constant Contact device flow requires client id, endpoints.auth, "
            "and endpoints.token"
        )
    http = session or requests.Session()
    auth_response = http.post(
        auth_url,
        data={
            "client_id": cc_client_id,
            "response_type": "code",
            "scope": "contact_data offline_access",
            "state": str(random.randrange(4294967296)),
        },
        timeout=timeout,
    )
    auth_payload = _json_payload(auth_response, "device authorization")
    if auth_response.status_code < 200 or auth_response.status_code > 299:
        raise ConfigError(
            f"Constant Contact device authorization failed: {auth_response.text}"
        )
    verification_url = auth_payload.get("verification_uri_complete")
    device_code = auth_payload.get("device_code")
    if not isinstance(verification_url, str) or not isinstance(device_code, str):
        raise ConfigError("Constant Contact device authorization response is malformed")
    print_fn("\nGo to this URL and authenticate:")
    print_fn(f"   {verification_url}\n")
    input_fn("Hit enter when authorization is complete: ")

    start = now or dt.datetime.now(dt.UTC)
    # The server tells us how often to poll (interval) and how long the device
    # code stays valid (expires_in); honor both to avoid hammering the API.
    interval = int(auth_payload.get("interval", 5))
    deadline = time.monotonic() + int(auth_payload.get("expires_in", 600))
    while True:
        token_response = http.post(
            token_url,
            data={
                "client_id": cc_client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=timeout,
        )
        token_payload = _json_payload(token_response, "device token")
        if token_response.status_code < 200 or token_response.status_code > 299:
            error = token_payload.get("error")
            # "authorization_pending" simply means the user has not finished
            # approving yet; keep polling at the server's interval until the
            # device code expires, then fall through to raise an error.
            if error == "authorization_pending" and time.monotonic() < deadline:
                sleep_fn(interval)
                continue
            detail = (
                token_payload.get("error_description") or error or token_response.text
            )
            raise ConfigError(f"Constant Contact device token failed: {detail}")
        try:
            _validate_access_token(token_payload)
            set_valid_from_to(start, token_payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(
                f"Constant Contact device token response is malformed: {exc}"
            ) from exc
        return token_payload


def _json_payload(response: requests.Response, context: str) -> dict[str, Any]:
    """Parse and validate a JSON HTTP response body."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConfigError(
            f"Constant Contact {context} returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Constant Contact {context} response must be a mapping")
    return payload


def get_access_token(
    token_file: str | Path,
    client_id: dict[str, Any],
    *,
    session: requests.Session | None = None,
    now: dt.datetime | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Return a valid Constant Contact access token, refreshing if needed.

    Loads the saved token from ``token_file``; if it is still within its
    validity window it is returned as-is, otherwise it is refreshed and the new
    token is written back to disk. Raises :class:`ConfigError` if the token file
    does not exist (the manual authorization flow must be run first).
    """
    path = Path(token_file).expanduser()
    if not path.exists():
        raise ConfigError(
            "Constant Contact access token file is missing; run the documented "
            "manual authorization flow and save the token before automation"
        )
    access_token = load_access_token(path)
    if token_is_valid(access_token, now=now):
        return access_token
    refreshed = refresh_access_token(
        client_id,
        access_token,
        session=session,
        now=now,
        timeout=timeout,
    )
    save_access_token(path, refreshed)
    return refreshed


def _parse_datetime(value: str) -> dt.datetime:
    """Parse an ISO datetime returned by Constant Contact."""
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def update_contact_body(contact: dict[str, Any]) -> dict[str, Any]:
    """Build the Constant Contact update-contact (PUT) payload.

    Copies only the supported, writable fields from ``contact`` and marks the
    update source as "Contact". Internal annotation keys (uppercase) are
    deliberately excluded so they are never sent to the API.
    """
    body: dict[str, Any] = {"update_source": "Contact"}
    for field in (
        "first_name",
        "last_name",
        "email_address",
        "job_title",
        "company_name",
        "birthday_month",
        "birthday_day",
        "anniversary",
        "street_addresses",
        "list_memberships",
    ):
        if field in contact:
            body[field] = contact[field]
    # Strip periods from the first name (e.g. abbreviated titles like "Fr.")
    # so Constant Contact stores a clean display name.
    if "first_name" in body:
        body["first_name"] = body["first_name"].replace(".", "")
    return body


def sign_up_form_body(contact: dict[str, Any]) -> dict[str, Any]:
    """Build the Constant Contact sign-up-form (create-contact) payload.

    Like :func:`update_contact_body`, but for creating a contact: the email is
    taken from the nested ``email_address.address`` value and the remaining
    supported fields are copied across when present.
    """
    body: dict[str, Any] = {"email_address": contact["email_address"]["address"]}
    for field in (
        "first_name",
        "last_name",
        "job_title",
        "company_name",
        "birthday_month",
        "birthday_day",
        "anniversary",
        "street_addresses",
        "list_memberships",
    ):
        if field in contact:
            body[field] = contact[field]
    # See update_contact_body: drop periods so abbreviated titles do not leak
    # into the stored first name.
    if "first_name" in body:
        body["first_name"] = body["first_name"].replace(".", "")
    return body


def create_contact_dict(email: str, ps_members: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an in-memory contact record linked to ParishSoft members.

    Derives a salutation (first/last name) from ``ps_members`` and returns a
    contact dict seeded with the lowercased email and empty membership lists.
    Each member is back-linked via its ``"CONTACT"`` key so the contact and its
    members can be traversed in either direction during reconciliation.
    """
    first_name, last_name = salutation_for_members(ps_members)
    contact = {
        "email_address": {"address": email.lower()},
        "first_name": first_name,
        "last_name": last_name,
        "list_memberships": [],
        "LIST MEMBERSHIPS": [],
        "PS MEMBERS": ps_members,
    }
    for member in ps_members:
        member["CONTACT"] = contact
    return contact


def link_cc_data(
    contacts: list[dict[str, Any]],
    custom_fields: list[dict[str, Any]],
    lists: list[dict[str, Any]],
) -> None:
    """Resolve list/custom-field IDs on contacts into named cross-references.

    Constant Contact returns custom fields and list memberships as opaque IDs.
    This builds ID->name lookups, then for each contact populates human-readable
    ``"CUSTOM FIELDS"`` and ``"LIST MEMBERSHIPS"`` annotations and, on each list,
    a ``"CONTACTS"`` index keyed by email. Mutates the input dicts in place.
    """
    custom_field_lookup = {
        item["custom_field_id"]: item["name"] for item in custom_fields
    }
    list_lookup = {item["list_id"]: item for item in lists}
    for item in list_lookup.values():
        item["CONTACTS"] = {}
    for contact in contacts:
        contact["CUSTOM FIELDS"] = {}
        for custom_field in contact.get("custom_fields", []):
            name = custom_field_lookup[custom_field["custom_field_id"]]
            custom_field["NAME"] = name
            contact["CUSTOM FIELDS"][name] = custom_field
        contact["LIST MEMBERSHIPS"] = []
        for list_id in contact.get("list_memberships", []):
            if list_id in list_lookup:
                cc_list = list_lookup[list_id]
                contact["LIST MEMBERSHIPS"].append(cc_list["name"])
                cc_list["CONTACTS"][contact["email_address"]["address"]] = contact


def link_contacts_to_ps_members(
    contacts: list[dict[str, Any]],
    ps_members: dict[int, dict[str, Any]],
) -> None:
    """Cross-link Constant Contact contacts with ParishSoft members by email.

    Builds an email->members index (a member can appear under several emails),
    then for each contact whose address matches attaches the matching members as
    ``"PS MEMBERS"`` and back-links each member to the contact via ``"CONTACT"``.
    Mutates the contacts and members in place; unmatched contacts are untouched.
    """
    members_by_email: dict[str, list[dict[str, Any]]] = {}
    for member in ps_members.values():
        # A single member may have multiple email addresses, so index the member
        # under each one to catch contacts reachable by any of them.
        for email in member.get("py emailAddresses", []):
            members_by_email.setdefault(email, []).append(member)
    for contact in contacts:
        email = contact["email_address"]["address"].lower()
        if email in members_by_email:
            contact["PS MEMBERS"] = members_by_email[email]
            for member in contact["PS MEMBERS"]:
                member["CONTACT"] = contact
