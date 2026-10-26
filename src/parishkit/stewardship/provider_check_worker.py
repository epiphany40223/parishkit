"""Private, bounded connectivity helper; never send mail or Slack notifications.

Input is one parent-validated request over stdin. Output is one closed outcome,
never a token, provider body or exception. The parent owns the wall-clock limit
and forced drainage, including DNS and trickling-response stalls.
"""

import base64
import json
import logging
import smtplib
import ssl
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import requests
from google.auth.exceptions import RefreshError

from parishkit.config import ConfigError
from parishkit.email.google_workspace import xoauth2_string
from parishkit.parishsoft import ParishSoftAPIError, ParishSoftClient, ParishSoftConfig
from parishkit.parishsoft_pagination import IncompleteSourceCollection
from parishkit.parishsoft_source import (
    CoherentParishSoftClient,
    SourceOrganizationMismatch,
    organization_name,
    organization_records,
)
from parishkit.parishsoft_transport import ExactSourceResponse
from parishkit.retry import RetryError, RetryPolicy

from .accounts.credential_errors import CredentialValidationUnavailable
from .accounts.cryptography import CryptographicError
from .accounts.integration_candidates import (
    GOOGLE_TOKEN_URI,
    slack_candidate,
    workspace_candidate,
)
from .accounts.key_files import MAX_FILE_BYTES
from .accounts.provider_context import validated_context
from .deployment import DeploymentProfile
from .local import source_base_url
from .mail_catcher import (
    MailTransport,
    open_mail_catcher,
    request_redirect,
    workspace_transport,
)
from .source.credentials import SourceCredential

MAX_INPUT = MAX_FILE_BYTES * 2 + 4096
MAX_RESPONSE = 65536
# The ParishSoft organization search may list every organization a key
# reaches (#1554), so a diocesan key's answer is not refused as oversized; the
# client also caps the record count (parishsoft_source.MAXIMUM_ORGANIZATIONS).
MAX_ORGANIZATION_RESPONSE = 1024 * 1024
# "mismatch" (#1492): a ParishSoft key that authenticates but does not reach
# the organization configured (it may reach others, #1554). Like "invalid" it
# is permanent, so it is never retried, but it has its own word so the smoke
# check names it.
OUTCOMES = frozenset({"valid", "invalid", "mismatch", "unavailable"})
REQUEST_FIELDS = frozenset({"target", "settings", "candidate", "profile", "base_url"})
FIXED_ENDPOINTS = frozenset(
    {
        ("POST", GOOGLE_TOKEN_URI),
        ("POST", "https://slack.com/api/auth.test"),
    }
)


def endpoints(profile=None, base_url=None):
    """The exact endpoints a check may contact under one deployment profile.

    Without a profile (the mail deliverers' token exchanges) only the Google
    and Slack endpoints are reachable and no ParishSoft URL at all. With one,
    the ParishSoft organization search is reachable at exactly one base URL:
    the deployment's ``parishsoft_base_url`` choice (``base_url``; None is the
    profile's default), which may be the fake only for a ``local`` request
    (#1399). The helper has no environment and learns both only from its
    request.
    """
    if profile is None:
        return FIXED_ENDPOINTS
    return FIXED_ENDPOINTS | {
        ("POST", source_base_url(profile, base_url) + "/organizations/search")
    }


class CheckSession(requests.Session):
    """Restrict private HTTP calls to fixed provider endpoints and bounded bodies.

    Delivery modules construct it with no arguments as their default session
    factory for Google token exchanges; only the provider-check and smoke
    paths, which may reach ParishSoft, bind a deployment profile. A check
    also carries the request's mail ``redirect`` (#1399), which the
    Workspace check needs before it may reach Google from LOCAL.
    """

    def __init__(
        self,
        profile=None,
        base_url=None,
        redirect=None,
        *,
        families=False,
        members=False,
    ):
        """Bind the session to the request's profile, base URL and allowlist.

        ``families`` also admits the one-row Family search the setup
        wizard's organization lookup and parish data check make (#1554,
        #2201), and ``members`` the check's one-row Member search; no other
        check may send them.
        """
        super().__init__()
        if profile is not None and not isinstance(profile, DeploymentProfile):
            raise ValueError("A provider check requires a deployment profile.")
        if (families or members) and profile is None:
            raise ValueError("A Family search requires a deployment profile.")
        self.trust_env = False
        self.profile = profile
        self.redirect = redirect
        self.base_url = None if profile is None else source_base_url(profile, base_url)
        self.endpoints = endpoints(profile, self.base_url)
        if families:
            self.endpoints |= {("POST", self.base_url + "/families/search")}
        if members:
            self.endpoints |= {("POST", self.base_url + "/members/search")}

    def request(self, method, url, **kwargs):
        """Forbid redirects/proxies; the subprocess parent supplies a hard timeout."""
        if (method, url) not in self.endpoints or set(kwargs) - {
            "headers",
            "data",
            "json",
            "timeout",
        }:
            raise ValueError("Unsupported provider check operation.")
        # At most 10 seconds; a lookup near its overall limit asks for less.
        kwargs["timeout"] = min(10, kwargs.get("timeout") or 10)
        with super().request(
            method, url, stream=True, allow_redirects=False, **kwargs
        ) as upstream:
            if upstream.status_code == 429 or upstream.status_code >= 500:
                raise CredentialValidationUnavailable()
            if 300 <= upstream.status_code < 400:
                raise ValueError("Provider check redirect is forbidden.")
            limit = (
                MAX_ORGANIZATION_RESPONSE
                if url.endswith("/organizations/search")
                else MAX_RESPONSE
            )
            content = bytearray()
            for chunk in upstream.iter_content(chunk_size=8192):
                if len(content) + len(chunk) > limit:
                    raise ValueError("Provider check response exceeds its bound.")
                content.extend(chunk)
            result = ExactSourceResponse()
            result.status_code = upstream.status_code
            result._content = bytes(content)
            result._content_consumed = True
            result.headers = upstream.headers.copy()
            return result


def _parishsoft(value, settings, session):
    """Reuse ParishKit's uncached exact-tenant validation, with no disk cache.

    A key that reaches the configured organization is valid even when it
    also reaches others (#1554); one that does not reach it is "mismatch".
    """
    if session.profile is None:
        raise ValueError("A ParishSoft check requires a deployment profile.")
    credential = SourceCredential(value)
    client = CoherentParishSoftClient(
        ParishSoftConfig(
            credential.api_key,
            Path("."),
            cache_enabled=False,
            api_base_url=session.base_url,
            timeout=10,
        ),
        organization_id=settings["organization_id"],
        session=session,
        retry_policy=RetryPolicy(attempts=1),
        maximum_requests=1,
        maximum_bytes=MAX_ORGANIZATION_RESPONSE,
        maximum_seconds=30,
    )
    return client.validate_organization_uncached() == settings["organization_id"]


def _workspace(value, settings, session):
    """Authenticate the mailbox; delivery and sender acceptance are separate.

    The transport follows the document and the request's profile (#476,
    ``mail_catcher.workspace_transport``): the mail-catcher document is valid
    only for a LOCAL request and is checked by reaching Mailpit; a Google
    service account is checked against Google and Gmail as before, and in a
    LOCAL request only beside a mail redirect on the normal clock (#1399),
    else it is invalid before Google is contacted. The session carries the
    request's profile and redirect; a check with no profile is a defect, not
    a verdict.
    """
    if session.profile is None:
        raise ValueError("A Workspace check requires a deployment profile.")
    try:
        transport = workspace_transport(
            value, session.profile, redirect=session.redirect
        )
    except ConfigError:
        return False
    if transport is MailTransport.MAIL_CATCHER:
        with open_mail_catcher() as smtp:
            code, _ = smtp.ehlo()
            if code != 250:
                raise CredentialValidationUnavailable()
        return True
    credentials = workspace_candidate(
        value, delegated_email=settings["delegated_email"]
    )
    rejected = False

    def token_request(url, method="GET", body=None, headers=None, **kwargs):
        """Adapt the Google transport protocol without accepting alternate origins."""
        nonlocal rejected
        if url != GOOGLE_TOKEN_URI or method != "POST":
            raise ValueError("Unsupported token exchange.")
        response = session.request(method, url, data=body, headers=headers)
        if response.status_code in {400, 401}:
            data = response.json()
            rejected = type(data) is dict and data.get("error") in {
                "invalid_grant",
                "unauthorized_client",
                "invalid_client",
            }
        return SimpleNamespace(
            status=response.status_code, data=response.content, headers=response.headers
        )

    try:
        credentials.refresh(token_request)
    except RefreshError:
        if rejected:
            return False
        raise CredentialValidationUnavailable() from None
    with smtplib.SMTP_SSL(
        "smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()
    ) as smtp:
        code, _ = smtp.ehlo()
        if code != 250:
            raise CredentialValidationUnavailable()
        code, _ = smtp.docmd(
            "AUTH",
            "XOAUTH2 " + xoauth2_string(settings["delegated_email"], credentials.token),
        )
        if code == 334:
            # Gmail's SASL error challenge requires an empty continuation before
            # its definitive 535 reply. Never decode or log the private challenge.
            code, _ = smtp.docmd("")
        if code in {235, 535}:
            return code == 235
        raise CredentialValidationUnavailable()


def _slack(value, settings, session):
    """Check token authentication only; no channel-read scope or message side effect.

    The same check runs in every profile (#1399), so pk-local proves a test
    token against Slack exactly as Production proves its own.
    """
    if session.profile is None:
        raise ValueError("A Slack check requires a deployment profile.")
    token = slack_candidate(value)
    response = session.request(
        "POST",
        "https://slack.com/api/auth.test",
        headers={"Authorization": "Bearer " + token},
        data={},
    )
    if response.status_code in {401, 403}:
        return False
    if response.status_code != 200:
        raise CredentialValidationUnavailable()
    body = response.json()
    if type(body) is not dict or type(body.get("ok")) is not bool:
        raise CredentialValidationUnavailable()
    if body["ok"]:
        return True
    if body.get("error") in {
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "account_inactive",
        "token_expired",
    }:
        return False
    raise CredentialValidationUnavailable()


def decode_request(raw):
    """Reject ambiguous/oversized private IPC before constructing provider clients."""
    from .accounts.integration_candidates import _object

    if type(raw) is not bytes or len(raw) > MAX_INPUT:
        raise ValueError("Invalid provider check request.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    # ``mail_redirect`` rides beside the profile only when a LOCAL deployment
    # sets one (#1399), so every other request is exactly as before.
    if type(request) is not dict or set(request) - {"mail_redirect"} != REQUEST_FIELDS:
        raise ValueError("Invalid provider check request.")
    settings = validated_context(request["target"], request["settings"])
    value = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(value) <= MAX_FILE_BYTES:
        raise ValueError("Invalid provider check candidate.")
    if type(request["profile"]) is not str or type(request["base_url"]) is not str:
        raise ValueError("Invalid provider check profile.")
    profile = DeploymentProfile(request["profile"])
    # Refused here, as "invalid", when the profile may not reach the base URL.
    base_url = source_base_url(profile, request["base_url"])
    # Refused here too beside any profile but ``local``.
    redirect = request_redirect(request, profile)
    return request["target"], settings, value, profile, base_url, redirect


def check_request(raw):
    """Return a fixed classification; transport outages are not rejected credentials."""
    try:
        target, settings, value, profile, base_url, redirect = decode_request(raw)
    except (ValueError, TypeError, ConfigError, RecursionError):
        return "invalid"
    return classify(
        target, value, settings, profile=profile, base_url=base_url, redirect=redirect
    )


def classify(target, value, settings, *, profile, base_url=None, redirect=None):
    """Run one provider check in the restricted session and name its outcome.

    The installer's helper and the human-run smoke command share this
    ladder, so "valid", "invalid", "mismatch" and "unavailable" mean the
    same in both. ``profile`` and ``base_url`` (the deployment's ParishSoft
    choice; None is the profile's default) select the endpoints the check
    may reach, and ``redirect`` (the deployment's mail redirect, #1399)
    whether a real Workspace key may be checked from LOCAL. "mismatch" is a
    ParishSoft key that does not reach the configured organization (#1554:
    reaching others too is fine): permanent, never a temporary outage (#1492).
    """
    try:
        with CheckSession(profile, base_url, redirect) as session:
            check = {
                "parishsoft": _parishsoft,
                "google_workspace": _workspace,
                "slack": _slack,
            }[target]
            valid = check(value, settings, session)
        return "valid" if valid is True else "invalid"
    except (ConfigError, CryptographicError):
        # Closed candidate parsers reject malformed private bytes locally.
        return "invalid"
    except SourceOrganizationMismatch:
        # Checked before the generic failures below: it is a kind of
        # incomplete source collection, but no retry can ever pass it.
        return "mismatch"
    except ParishSoftAPIError as error:
        return "invalid" if error.status_code in {401, 403} else "unavailable"
    except RetryError as error:
        # One attempt only, but the shared client may still wrap its failure.
        if isinstance(error.last_exception, SourceOrganizationMismatch):
            return "mismatch"
        return "unavailable"
    except (
        CredentialValidationUnavailable,
        requests.ConnectionError,
        requests.Timeout,
        OSError,
        smtplib.SMTPServerDisconnected,
    ):
        return "unavailable"
    except Exception:
        # Unknown adapter/DTO/library failures are not evidence of bad credentials.
        # Suppress all private exception text without manufacturing a rejection.
        return "unavailable"


# The setup wizard's organization lookup (#1554): one request names the
# operation instead of a target and settings, and the answer is one JSON
# line (``lookup_request``), never a key, provider body or exception.
LOOKUP_FIELDS = frozenset({"operation", "candidate", "profile", "base_url", "seconds"})
# The longest a lookup may run, which the parent's own limit matches.
MAX_LOOKUP_SECONDS = 120
# The lookup stops this long before its limit, so it can still answer with
# the organizations checked so far before the parent stops the helper.
LOOKUP_MARGIN_SECONDS = 5


def _lookup_client(credential, session, timeout):
    """An uncached, single-attempt ParishSoft client on the restricted session."""
    return ParishSoftClient(
        ParishSoftConfig(
            credential.api_key,
            Path("."),
            cache_enabled=False,
            api_base_url=session.base_url,
            timeout=timeout,
        ),
        session=session,
        retry_policy=RetryPolicy(attempts=1),
    )


def _holds_families(client, organization):
    """Whether one organization holds Families: True, False, or raise.

    One ``families/search`` for at most one Family of that organization
    (#1554). An empty array is no Families; one row numbered 1 with a
    positive integer total is some. Its content is never read further.
    Anything else raises, which the caller records as unknown.
    """
    rows = client.post_uncached(
        "families/search",
        {"organizationIDs": [organization], "pageSize": 1, "pageNumber": 1},
    )
    if rows == []:
        return False
    if (
        type(rows) is list
        and len(rows) == 1
        and type(rows[0]) is dict
        and rows[0].get("rowNumber") == 1
        and type(rows[0].get("totalResults")) is int
        and rows[0]["totalResults"] >= 1
    ):
        return True
    raise ValueError("Unrecognized Family search answer.")


def lookup_organizations(value, *, profile, base_url, seconds):
    """List the organizations a ParishSoft key reaches, and which hold Families.

    The answer is a dict with ``outcome``: ``refused`` (ParishSoft refused
    the key, HTTP 401 or 403, on any request), ``unavailable`` (ParishSoft
    could not be reached or the organization list timed out),
    ``unreadable`` (the list is malformed or over its bound), or ``ok``
    with ``organizations`` (each ``{"id", "name", "families"}``, in
    ParishSoft's order, ``families`` None when unknown, possibly empty),
    ``unchecked`` (how many were not checked when the time limit came) and
    ``elapsed`` (seconds, when the limit stopped the checks). The Family
    checks run one at a time; an error other than a refused key leaves
    that organization unknown.
    """
    started = time.monotonic()
    deadline = started + max(1, seconds - LOOKUP_MARGIN_SECONDS)
    try:
        credential = SourceCredential(value)
        with CheckSession(profile, base_url, families=True) as session:
            client = _lookup_client(credential, session, 10)
            try:
                rows = client.post_uncached("organizations/search", {})
                records = organization_records(rows, allow_empty=True)
            except IncompleteSourceCollection:
                return {"outcome": "unreadable"}
            organizations = [
                {
                    "id": identity,
                    "name": organization_name(row.get("organizationReportName")),
                    "families": None,
                }
                for identity, row in records
            ]
            unchecked, elapsed = 0, None
            for index, organization in enumerate(organizations):
                remaining = deadline - time.monotonic()
                if remaining < 1:
                    unchecked = len(organizations) - index
                    elapsed = time.monotonic() - started
                    break
                try:
                    organization["families"] = _holds_families(
                        _lookup_client(credential, session, min(10, remaining)),
                        organization["id"],
                    )
                except ParishSoftAPIError as error:
                    if error.status_code in {401, 403}:
                        return {"outcome": "refused"}
                except Exception:
                    # Unknown: the first load's empty-data check still
                    # refuses an organization with no Families.
                    pass
            return {
                "outcome": "ok",
                "organizations": organizations,
                "unchecked": unchecked,
                "elapsed": elapsed,
            }
    except ParishSoftAPIError as error:
        if error.status_code in {401, 403}:
            return {"outcome": "refused"}
        # A success status whose body is not JSON is an unreadable answer.
        if 200 <= error.status_code < 300:
            return {"outcome": "unreadable"}
        return {"outcome": "unavailable"}
    except (ConfigError, CryptographicError):
        # The staged key's own format was checked when it was sealed.
        return {"outcome": "refused"}
    except ValueError:
        # An over-bound or redirected answer: not readable as a list.
        return {"outcome": "unreadable"}
    except Exception:
        # Unreachable, timed out or any other transport failure.
        return {"outcome": "unavailable"}


# The largest total the parish data check accepts for Families or Members
# (#2201): far above any parish, and inside the check row's integer column.
MAX_CHECK_TOTAL = 2**31 - 1


def _total(rows, *, total_field, ordinal_field):
    """ParishSoft's collection total from a one-row search answer, or raise.

    An empty array is none; one row whose ordinal is 1 carries the total as
    a positive integer. The row's content is never read further. Anything
    else raises ``ValueError``, which the check reports as unreadable.
    """
    if rows == []:
        return 0
    if (
        type(rows) is list
        and len(rows) == 1
        and type(rows[0]) is dict
        and rows[0].get(ordinal_field) == 1
        and type(rows[0].get(total_field)) is int
        and 1 <= rows[0][total_field] <= MAX_CHECK_TOTAL
    ):
        return rows[0][total_field]
    raise ValueError("Unrecognized search answer.")


def check_source(value, *, organization, profile, base_url, seconds):
    """The setup wizard's quick parish data check (#2201).

    Three uncached requests with the staged key, each bounded by what is
    left of ``seconds``: the organization list (``organization`` must be
    on it; its cleaned name is kept), then one-row Family and Member
    searches of that organization, whose rows carry ParishSoft's totals.
    The answer is a dict with ``outcome``: ``refused`` (HTTP 401 or 403 on
    any request), ``not_reached`` (the key does not reach the
    organization), ``unreadable`` (a malformed or over-bound answer),
    ``unavailable`` (unreachable, an error status or out of time), or
    ``ok`` with ``name`` (None when ParishSoft names nothing),
    ``families`` and ``members`` (the totals). No Family or Member content
    leaves this function.
    """
    deadline = time.monotonic() + max(1, seconds - LOOKUP_MARGIN_SECONDS)

    def client():
        """A single-attempt client whose timeout fits the time still left."""
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise TimeoutError("The parish data check ran out of time.")
        return _lookup_client(credential, session, min(10, remaining))

    scope = {"organizationIDs": [organization]}
    try:
        credential = SourceCredential(value)
        with CheckSession(profile, base_url, families=True, members=True) as session:
            try:
                records = organization_records(
                    client().post_uncached("organizations/search", {}),
                    allow_empty=True,
                )
            except IncompleteSourceCollection:
                return {"outcome": "unreadable"}
            row = dict(records).get(organization)
            if row is None:
                return {"outcome": "not_reached"}
            families = _total(
                client().post_uncached(
                    "families/search", scope | {"pageSize": 1, "pageNumber": 1}
                ),
                total_field="totalResults",
                ordinal_field="rowNumber",
            )
            members = _total(
                client().post_uncached(
                    "members/search",
                    scope | {"maximumRows": 1, "startRowIndex": 0},
                ),
                total_field="recordCount",
                ordinal_field="rowNum",
            )
            return {
                "outcome": "ok",
                "name": organization_name(row.get("organizationReportName")),
                "families": families,
                "members": members,
            }
    except ParishSoftAPIError as error:
        if error.status_code in {401, 403}:
            return {"outcome": "refused"}
        # A success status whose body is not JSON is an unreadable answer.
        if 200 <= error.status_code < 300:
            return {"outcome": "unreadable"}
        return {"outcome": "unavailable"}
    except (ConfigError, CryptographicError):
        # The staged key's own format was checked when it was sealed.
        return {"outcome": "refused"}
    except ValueError:
        # An over-bound, redirected or unrecognized answer.
        return {"outcome": "unreadable"}
    except Exception:
        # Unreachable, timed out or any other transport failure.
        return {"outcome": "unavailable"}


def lookup_request(request):
    """Answer one decoded lookup or check request as a JSON line's dict.

    ``organizations`` is the organization lookup (#1554); ``source_check``
    is the parish data check (#2201), which also names the ``organization``
    to check.
    """
    try:
        operation = request.get("operation")
        fields = LOOKUP_FIELDS | (
            {"organization"} if operation == "source_check" else set()
        )
        if set(request) != fields or operation not in {"organizations", "source_check"}:
            raise ValueError("Invalid lookup request.")
        value = base64.b64decode(request["candidate"], validate=True)
        seconds = request["seconds"]
        if (
            not 0 < len(value) <= MAX_FILE_BYTES
            or type(seconds) is not int
            or not 1 <= seconds <= MAX_LOOKUP_SECONDS
            or type(request["profile"]) is not str
            or type(request["base_url"]) is not str
            or (
                operation == "source_check"
                and (
                    type(request["organization"]) is not int
                    or not 1 <= request["organization"] < 2**31
                )
            )
        ):
            raise ValueError("Invalid lookup request.")
        profile = DeploymentProfile(request["profile"])
        base_url = source_base_url(profile, request["base_url"])
    except (ValueError, TypeError, ConfigError):
        return {"outcome": "unavailable"}
    if operation == "source_check":
        return check_source(
            value,
            organization=request["organization"],
            profile=profile,
            base_url=base_url,
            seconds=seconds,
        )
    return lookup_organizations(
        value, profile=profile, base_url=base_url, seconds=seconds
    )


def main():
    """Keep every third-party diagnostic off stdout/stderr in the isolated helper.

    A request naming an ``operation`` is the organization lookup (#1554),
    which answers with one JSON line; every other request is a provider
    check, which answers with one closed word.
    """
    from .accounts.integration_candidates import _object

    logging.disable(logging.CRITICAL)
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    try:
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except (ValueError, TypeError, RecursionError):
        request = None
    if type(request) is dict and "operation" in request and len(raw) <= MAX_INPUT:
        sys.stdout.write(json.dumps(lookup_request(request)) + "\n")
        return 0
    result = check_request(raw)
    sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
