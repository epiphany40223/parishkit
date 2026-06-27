"""Google authentication and service-builder helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from parishkit.config import ConfigError
from parishkit.files import atomic_write_text
from parishkit.retry import RetryError, RetryPolicy, TransientRetryError, retry_call

TRANSIENT_GOOGLE_STATUSES = {429, 500, 502, 503, 504}


class GoogleAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Google API error: HTTP {status_code}: {message}")


class _TransientGoogleAPIError(TransientRetryError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _import_google_auth() -> tuple[Any, Any]:
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
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ConfigError(
            "Google API service building requires parishkit[google]"
        ) from exc
    return build


def _import_google_http_error() -> type[BaseException] | None:
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
    """Load service-account credentials, optionally with DWD subject."""

    service_account, _ = _import_google_auth()
    credentials = service_account.Credentials.from_service_account_file(
        str(Path(key_file).expanduser()),
        scopes=list(scopes),
    )
    if subject:
        credentials = credentials.with_subject(subject)
    return credentials


def load_user_credentials(
    token_file: str | Path,
    *,
    scopes: Sequence[str],
) -> Any:
    """Load OAuth user credentials from an authorized-user JSON file."""

    _, user_credentials = _import_google_auth()
    return user_credentials.Credentials.from_authorized_user_file(
        str(Path(token_file).expanduser()),
        scopes=list(scopes),
    )


def run_user_oauth_flow(
    client_secrets_file: str | Path,
    token_file: str | Path,
    *,
    scopes: Sequence[str],
    flow_factory: Any | None = None,
    port: int = 0,
) -> Any:
    """Run a human-driven installed-app OAuth flow and save the token."""

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
    atomic_write_text(path, text)


def build_service(
    service_name: str,
    version: str,
    *,
    credentials: Any,
    cache_discovery: bool = False,
    build_fn: Any | None = None,
) -> Any:
    """Build a Google API service using an injectable builder for tests."""

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
    """Execute one Google request with ParishKit retry semantics."""

    kwargs: dict[str, Any] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep

    def execute() -> Any:
        http_error = _import_google_http_error()
        try:
            return request.execute()
        except Exception as exc:
            if http_error is None or not isinstance(exc, http_error):
                raise
            status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
            if status in TRANSIENT_GOOGLE_STATUSES:
                raise _TransientGoogleAPIError(status, str(exc)) from exc
            raise GoogleAPIError(status, str(exc)) from exc

    try:
        return retry_call(execute, policy=policy, **kwargs)
    except RetryError as exc:
        if isinstance(exc.last_exception, _TransientGoogleAPIError):
            raise GoogleAPIError(
                exc.last_exception.status_code,
                exc.last_exception.message,
            ) from exc
        raise
