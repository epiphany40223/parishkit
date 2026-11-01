"""Show what the Google People API returns for your own account's email addresses.

A human-run smoke tool (#2352). It signs you in to Google with the OAuth 2.0
authorization-code flow and PKCE on a loopback redirect, asks for
``openid email https://www.googleapis.com/auth/user.emails.read``, then prints
the ID token's ``email``/``email_verified`` and, for each address the People
API returns, its value, ``metadata.verified``, ``metadata.primary`` and
``metadata.source.type``. It changes nothing in ParishKit or Google.

Two ways to finish the sign-in:

* Default: the tool listens on ``http://127.0.0.1:<port>/`` and opens your
  browser (``--no-browser`` only prints the URL). Google sends the browser
  back to that address and the tool reads the code itself.
* ``--manual``, for a headless host over ssh: the tool prints the URL and
  listens on nothing. Open the URL in your laptop's browser; after consent
  the browser goes to ``http://127.0.0.1:<port>/?...`` and fails to load,
  which is expected. Copy that whole address from the browser's address bar
  and paste it at the prompt.

The OAuth client comes from ``--client-file``: a Desktop-app client JSON
downloaded from Google Cloud (``{"installed": {...}}``), a Web client JSON
(``{"web": {...}}``), or ParishKit's compact ``{"client_id": ...,
"client_secret": ...}`` credential file. A Desktop client accepts any
loopback port; a Web client accepts only the exact redirect URIs registered
on it, so with one pass ``--port`` and register ``http://127.0.0.1:<port>/``.

Nothing is written to disk or logged: the client secret, the code, the
tokens and the addresses live only in this process's memory and on your
terminal. At exit the tool revokes the access token, so the grant ends too.

Before running it, in the Google Cloud project: enable the Google People API;
add the user.emails.read scope under Google Auth Platform -> Data Access; with
an External audience, keep it in Testing and add your account as a test user;
and create a Desktop-app client and download its JSON. The prerequisites,
the exact commands and what to report back are in
scripts/smoke-tests/README.md ("Google People API email addresses").

This file uses only the Python standard library and imports nothing from
ParishKit, so it can be copied to a host and run with ``python3`` alone.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PEOPLE_URL = (
    "https://people.googleapis.com/v1/people/me?personFields=emailAddresses,metadata"
)
SCOPES = ("openid", "email", "https://www.googleapis.com/auth/user.emails.read")
HTTP_TIMEOUT = 30
EXIT_FAILED = 2


class SmokeError(Exception):
    """A plain-language failure; its message never holds a secret or token."""


def load_client(path: Path) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` from one of the accepted shapes.

    Accepts Google's downloaded client JSON (``installed`` or ``web``) and
    ParishKit's compact credential file. Error messages name the file's
    problem only, never its content.
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SmokeError(
            f"Cannot read the OAuth client file: {error.strerror}."
        ) from None
    except (ValueError, UnicodeError):
        raise SmokeError("The OAuth client file is not valid JSON.") from None
    if isinstance(value, dict):
        for key in ("installed", "web"):
            if isinstance(value.get(key), dict):
                value = value[key]
                break
    if not isinstance(value, dict):
        raise SmokeError("The OAuth client file is not a JSON object.")
    client_id, secret = value.get("client_id"), value.get("client_secret")
    if not (isinstance(client_id, str) and client_id):
        raise SmokeError("The OAuth client file has no client_id.")
    if not (isinstance(secret, str) and secret):
        raise SmokeError("The OAuth client file has no client_secret.")
    return client_id, secret


def pkce_pair() -> tuple[str, str]:
    """Return a fresh ``(verifier, S256 challenge)`` pair (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri(port: int) -> str:
    """Return the loopback redirect URI for ``port``."""
    return f"http://127.0.0.1:{port}/"


def authorization_url(client_id: str, redirect: str, state: str, challenge: str):
    """Build Google's authorization URL for this flow."""
    query = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Show the consent screen every run, so the Administrator always
        # sees (and can report) what it says for user.emails.read.
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(query)}"


def parse_redirect(text: str, state: str) -> tuple[str, bool]:
    """Return ``(code, state_checked)`` from a pasted redirect URL or bare code.

    A full URL must carry the expected ``state``; a bare code cannot be
    state-checked, so ``state_checked`` is False (PKCE still binds the code to
    this run's verifier). Google's ``error`` parameter becomes a SmokeError.
    """
    text = text.strip()
    if not text:
        raise SmokeError("Nothing was pasted.")
    if "?" not in text and "=" not in text:
        return text, False
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query or text)
    return code_from_params(params, state), True


def code_from_params(params: dict[str, list[str]], state: str) -> str:
    """Check Google's redirect parameters and return the authorization code."""
    if "error" in params:
        raise SmokeError(f"Google refused the sign-in: {params['error'][0]}.")
    if params.get("state", [""])[0] != state:
        raise SmokeError(
            "The redirect's state does not match this run; start the tool again."
        )
    code = params.get("code", [""])[0]
    if not code:
        raise SmokeError("The redirect has no authorization code.")
    return code


def wait_for_code(server, state: str, timeout: float, clock=time.monotonic) -> str:
    """Serve the loopback redirect until one request carries Google's answer.

    Other requests (a browser's favicon fetch) are answered and ignored.
    ``server`` is an ``http.server.HTTPServer`` whose handler stores the
    request's query parameters in ``server.params``.
    """
    deadline = clock() + timeout
    server.timeout = 1
    server.params = None
    while server.params is None:
        if clock() > deadline:
            raise SmokeError(f"No sign-in arrived within {int(timeout)} seconds.")
        server.handle_request()
    return code_from_params(server.params, state)


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """Record Google's redirect parameters; never log the request line."""

    def do_GET(self):  # noqa: N802 - http.server's method name
        """Store the query if it is Google's answer and reply with plain text."""
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        answered = "code" in params or "error" in params
        if answered:
            self.server.params = params
        body = (
            b"Done. You can close this tab and return to the terminal.\n"
            if answered
            else b"Not found.\n"
        )
        self.send_response(200 if answered else 404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - base class signature
        """Suppress access logging: the request line holds the code."""


def http_json(request: urllib.request.Request, opener=urllib.request.urlopen):
    """Send ``request`` and return its JSON body, or raise a plain SmokeError.

    Google's error bodies carry an error name and description, never the
    caller's secret or token, so they are safe to show to the operator.
    """
    try:
        with opener(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise SmokeError(
            f"{request.full_url.split('?')[0]} answered HTTP {error.code}: "
            f"{_google_error(error)}"
        ) from None
    except (urllib.error.URLError, OSError) as error:
        raise SmokeError(f"Cannot reach Google: {error}.") from None
    except ValueError:
        raise SmokeError("Google answered with something that is not JSON.") from None


def _google_error(error: urllib.error.HTTPError) -> str:
    """Summarize a Google error body (token endpoint or API shape)."""
    try:
        body = json.loads(error.read().decode("utf-8"))
    except (ValueError, UnicodeError, OSError):
        return "no details."
    detail = body.get("error") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return f"{detail.get('status', '')} {detail.get('message', '')}".strip()
    description = body.get("error_description", "") if isinstance(body, dict) else ""
    return f"{detail} {description}".strip() or "no details."


def exchange_code(client, code, verifier, redirect, opener=urllib.request.urlopen):
    """Exchange the authorization code (with the PKCE verifier) for tokens."""
    client_id, secret = client
    form = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    ).encode("ascii")
    return http_json(urllib.request.Request(TOKEN_URL, data=form), opener)


def fetch_people(access_token: str, opener=urllib.request.urlopen):
    """Call ``people/me`` with the access token and return its JSON."""
    request = urllib.request.Request(
        PEOPLE_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    return http_json(request, opener)


def revoke(token: str, opener=urllib.request.urlopen) -> bool:
    """Revoke ``token`` at Google; return whether Google confirmed it."""
    form = urllib.parse.urlencode({"token": token}).encode("ascii")
    try:
        with opener(
            urllib.request.Request(REVOKE_URL, data=form), timeout=HTTP_TIMEOUT
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def id_token_claims(id_token: str) -> dict:
    """Decode the ID token's payload without checking its signature.

    The token came straight from Google's token endpoint over TLS, which
    OpenID Connect Core 3.1.3.7 accepts in place of a signature check.
    """
    try:
        payload = id_token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError, UnicodeError):
        raise SmokeError("Google's ID token could not be read.") from None
    if not isinstance(claims, dict):
        raise SmokeError("Google's ID token could not be read.")
    return claims


def email_rows(person: dict) -> list[tuple[str, str, str, str]]:
    """Return ``(value, verified, primary, source type)`` for each address.

    Missing metadata prints as ``-`` so an absent flag is never mistaken for
    ``False``.
    """
    rows = []
    for entry in person.get("emailAddresses") or []:
        metadata = entry.get("metadata") or {}
        rows.append(
            (
                str(entry.get("value", "-")),
                _flag(metadata, "verified"),
                _flag(metadata, "primary"),
                str((metadata.get("source") or {}).get("type", "-")),
            )
        )
    return rows


def _flag(metadata: dict, name: str) -> str:
    """Render a metadata flag as true/false, or ``-`` when Google omitted it."""
    return "-" if name not in metadata else str(bool(metadata[name])).lower()


def redact(address: str) -> str:
    """Hide an address's local part: ``someone@example.org`` → ``…@example.org``."""
    _, at, domain = address.rpartition("@")
    return f"…@{domain}" if at else "…"


def render(claims: dict, rows, granted: str, *, redact_addresses: bool) -> str:
    """Return the report: ID token claims, granted scopes and the address table."""
    show = redact if redact_addresses else str
    lines = [
        f"ID token email: {show(str(claims.get('email', '-')))}",
        f"ID token email_verified: {str(claims.get('email_verified', '-')).lower()}",
        f"Granted scopes: {granted or '-'}",
        "",
    ]
    table = [("address", "verified", "primary", "source")]
    table += [(show(row[0]), *row[1:]) for row in rows]
    widths = [max(len(row[i]) for row in table) for i in range(4)]
    for row in table:
        lines.append(
            "  ".join(
                cell.ljust(w) for cell, w in zip(row, widths, strict=True)
            ).rstrip()
        )
    if not rows:
        lines.append("(the People API returned no email addresses)")
    return "\n".join(lines)


def parse_args(argv):
    """Parse the command line; the help is the operator's instructions."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--client-file",
        type=Path,
        required=True,
        help="OAuth client JSON (Desktop or Web download, or ParishKit's "
        "compact client_id/client_secret file); read only, never printed",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="headless: print the URL and read the pasted redirect address",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the URL instead of opening a browser (still listens)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="loopback port (default: random; a Web client needs the exact "
        "registered port)",
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        help="print each address's domain only",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="seconds to wait for the browser's redirect (default 300)",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be 0..65535")
    return args


def run(
    args,
    *,
    opener=urllib.request.urlopen,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    open_browser: Callable[[str], bool] = webbrowser.open,
    server_factory=http.server.HTTPServer,
) -> int:
    """Run the sign-in, the People API call and the report; return the exit code."""
    client = load_client(args.client_file)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    server = None
    if args.manual:
        # Nothing listens; the port only has to match the client's rules.
        port = args.port or 49152 + secrets.randbelow(16383)
    else:
        try:
            server = server_factory(("127.0.0.1", args.port), _RedirectHandler)
        except OSError as error:
            raise SmokeError(f"Cannot listen on 127.0.0.1: {error.strerror}.") from None
        port = server.server_address[1]
    redirect = redirect_uri(port)
    url = authorization_url(client[0], redirect, state, challenge)
    try:
        if args.manual:
            print_fn("Open this URL in a browser and sign in:\n\n" + url + "\n")
            print_fn(
                f"Google then sends the browser to {redirect}?... and the page "
                "fails to load. That is expected: copy the whole address from "
                "the address bar."
            )
            code, checked = parse_redirect(input_fn("Paste it here: "), state)
            if not checked:
                print_fn("Note: a bare code cannot be state-checked; PKCE still is.")
        else:
            if args.no_browser or not open_browser(url):
                print_fn("Open this URL in a browser and sign in:\n\n" + url + "\n")
            else:
                print_fn("Opened your browser; finish signing in there.")
            code = wait_for_code(server, state, args.timeout)
    finally:
        if server is not None:
            server.server_close()
    tokens = exchange_code(client, code, verifier, redirect, opener)
    access = tokens.get("access_token")
    if not access:
        raise SmokeError("Google's token answer has no access token.")
    try:
        claims = id_token_claims(tokens.get("id_token", ""))
        if claims.get("aud") != client[0]:
            raise SmokeError("The ID token was issued for a different client.")
        person = fetch_people(access, opener)
        print_fn(
            render(
                claims,
                email_rows(person),
                tokens.get("scope", ""),
                redact_addresses=args.redact,
            )
        )
    finally:
        revoked = revoke(access, opener)
        tokens.clear()
        print_fn(
            "Access revoked at Google."
            if revoked
            else "Could not revoke the access; remove it at "
            "https://myaccount.google.com/permissions if you want."
        )
    return 0


def main(argv=None) -> int:
    """Command-line entry point: exit 0 on success, 2 with one plain error line."""
    args = parse_args(argv)
    try:
        return run(args)
    except SmokeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("ERROR: stopped before finishing.", file=sys.stderr)
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
