"""Google authentication and service-builder helpers."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from parishkit.config import ConfigError
from parishkit.files import atomic_write_text
from parishkit.retry import RetryError, RetryPolicy, TransientRetryError, retry_call

# HTTP statuses that indicate a transient Google API condition worth retrying:
# 429 (rate limited) plus the 5xx server/gateway errors.
TRANSIENT_GOOGLE_STATUSES = {429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


class GoogleAPIError(RuntimeError):
    """A non-retryable Google API failure, carrying the HTTP status code."""

    def __init__(self, status_code: int, message: str):
        """Store the HTTP status code and build a human-readable message."""
        self.status_code = status_code
        super().__init__(f"Google API error: HTTP {status_code}: {message}")


class _TransientGoogleAPIError(TransientRetryError):
    """A retryable Google API failure (e.g. rate-limit or 5xx server error).

    Subclasses :class:`TransientRetryError` so the shared retry helper will keep
    retrying it; the status code and message are retained for re-raising as a
    terminal :class:`GoogleAPIError` once retries are exhausted.
    """

    def __init__(self, status_code: int, message: str):
        """Store the status code and message alongside the retry marker."""
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _import_google_auth() -> tuple[Any, Any]:
    """Import the optional google-auth credential modules lazily.

    The Google client libraries are an optional install, so the import is
    deferred to call time and a missing dependency is surfaced as a friendly
    :class:`ConfigError` pointing at the ``parishkit[google]`` extra. Returns
    the ``(service_account, user_credentials)`` modules.
    """
    try:
        from google.oauth2 import credentials as user_credentials
        from google.oauth2 import service_account
    except ImportError as exc:
        raise ConfigError(
            "Google support requires the optional google dependencies; "
            "install parishkit[google]"
        ) from exc
    return service_account, user_credentials


def _import_google_build() -> Any:
    """Import the googleapiclient ``build`` factory lazily.

    Deferred so importing this module does not require the optional Google
    dependencies; raises :class:`ConfigError` if they are not installed.
    """
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ConfigError(
            "Google API service building requires parishkit[google]"
        ) from exc
    return build


def _import_google_http_error() -> type[BaseException] | None:
    """Return googleapiclient's ``HttpError`` type, or ``None`` if unavailable.

    Unlike the other import helpers this never raises: callers use the returned
    type only to classify exceptions, and absence simply means no Google-
    specific error handling can apply.
    """
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        return None
    return HttpError


def load_service_account_credentials(
    key_file: str | Path,
    *,
    scopes: Sequence[str],
    subject: str | None = None,
) -> Any:
    """Load service-account credentials from a key file for the given scopes.

    When ``subject`` is provided the credentials impersonate that user via
    domain-wide delegation (DWD); this requires the service account to be
    authorized for the requested scopes in the Workspace admin console. ``~`` in
    ``key_file`` is expanded. Returns a google-auth credentials object.
    """

    key_path = Path(key_file).expanduser()
    service_account, _ = _import_google_auth()
    try:
        credentials = service_account.Credentials.from_service_account_file(
            str(key_path),
            scopes=list(scopes),
        )
    except Exception as exc:
        raise ConfigError(
            f"could not load Google service-account credential file {key_path}: {exc}"
        ) from exc
    # with_subject() turns plain service-account creds into domain-wide
    # delegation creds that act on behalf of the named user.
    if subject:
        credentials = credentials.with_subject(subject)
    return credentials


def load_user_credentials(
    token_file: str | Path,
    *,
    scopes: Sequence[str],
) -> Any:
    """Load OAuth user credentials from an authorized-user JSON file.

    The file is the token previously produced by :func:`run_user_oauth_flow`.
    ``~`` in ``token_file`` is expanded; ``scopes`` should match those the token
    was granted. Returns a google-auth user credentials object that the client
    libraries can refresh as needed.
    """

    token_path = Path(token_file).expanduser()
    _, user_credentials = _import_google_auth()
    try:
        return user_credentials.Credentials.from_authorized_user_file(
            str(token_path),
            scopes=list(scopes),
        )
    except Exception as exc:
        raise ConfigError(
            f"could not load Google user credential file {token_path}: {exc}"
        ) from exc


def run_user_oauth_flow(
    client_secrets_file: str | Path,
    token_file: str | Path,
    *,
    scopes: Sequence[str],
    flow_factory: Any | None = None,
    port: int = 0,
) -> Any:
    """Run a human-driven installed-app OAuth flow and persist the token.

    Opens a local browser-based consent flow for the given client secrets and
    scopes, then writes the resulting credentials to ``token_file`` for later
    reuse by :func:`load_user_credentials`. ``flow_factory`` is injectable for
    testing; ``port=0`` lets the OS pick a free local callback port. Intended
    for interactive bootstrap, not normal automated runs. Returns the
    credentials.
    """

    if flow_factory is None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise ConfigError(
                "Google user OAuth bootstrap requires parishkit[google]"
            ) from exc
        flow_factory = InstalledAppFlow.from_client_secrets_file
    flow = flow_factory(
        str(Path(client_secrets_file).expanduser()), scopes=list(scopes)
    )
    credentials = flow.run_local_server(port=port)
    _write_credential_file(Path(token_file).expanduser(), credentials.to_json())
    return credentials


def _write_credential_file(path: Path, text: str) -> None:
    """Write credential JSON atomically to avoid leaving a partial token file."""
    atomic_write_text(path, text)


def build_service(
    service_name: str,
    version: str,
    *,
    credentials: Any,
    cache_discovery: bool = False,
    build_fn: Any | None = None,
) -> Any:
    """Build a Google API service client for ``service_name``/``version``.

    ``build_fn`` is injectable so tests can supply a fake builder; otherwise the
    real googleapiclient ``build`` is imported lazily. ``cache_discovery``
    defaults to off to avoid the noisy file-cache warnings (and stale cache
    issues) the client library emits under modern setups.
    """

    builder = build_fn or _import_google_build()
    return builder(
        serviceName=service_name,
        version=version,
        credentials=credentials,
        cache_discovery=cache_discovery,
    )


def execute_google_request(
    request: Any,
    *,
    policy: RetryPolicy | None = None,
    sleep: Any = None,
) -> Any:
    """Execute one Google API request under the shared ParishKit retry policy.

    Maps Google ``HttpError`` responses onto ParishKit's retry model: statuses
    in :data:`TRANSIENT_GOOGLE_STATUSES` (rate limits and 5xx) become retryable
    errors, anything else becomes a terminal :class:`GoogleAPIError`. ``policy``
    and ``sleep`` are forwarded to :func:`retry_call` (``sleep`` is injectable so
    tests need not actually wait). Returns the request's parsed response.
    """

    kwargs: dict[str, Any] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep

    def execute() -> Any:
        """Execute the request once, translating HttpError into typed errors.

        Non-HttpError exceptions propagate unchanged. The status is read
        defensively (``resp`` and ``status`` may be missing) and classified as
        transient or terminal so the retry loop can decide whether to retry.
        """
        http_error = _import_google_http_error()
        try:
            return request.execute()
        except Exception as exc:
            endpoint = str(getattr(request, "uri", request.__class__.__name__))
            if http_error is None or not isinstance(exc, http_error):
                LOGGER.warning("Google API request failed for %s: %s", endpoint, exc)
                raise
            status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
            LOGGER.warning(
                "Google API request failed for %s with HTTP %s: %s",
                endpoint,
                status,
                exc,
            )
            if status in TRANSIENT_GOOGLE_STATUSES:
                raise _TransientGoogleAPIError(status, str(exc)) from exc
            raise GoogleAPIError(status, str(exc)) from exc

    try:
        return retry_call(execute, policy=policy, **kwargs)
    except RetryError as exc:
        # Retries were exhausted on a transient error: re-raise it as a terminal
        # GoogleAPIError so callers see one consistent, status-bearing exception
        # type regardless of whether the failure was retryable.
        if isinstance(exc.last_exception, _TransientGoogleAPIError):
            raise GoogleAPIError(
                exc.last_exception.status_code,
                exc.last_exception.message,
            ) from exc
        raise
