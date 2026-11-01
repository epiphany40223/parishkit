"""Tests for the People API email-address smoke tool (#2352); no real Google."""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.google import people_emails as pe

ROOT = Path(__file__).parents[1]
MODULE = ROOT / "src/parishkit/google/people_emails.py"
WRAPPER = ROOT / "scripts/smoke-tests/google-people-emails.py"
CLIENT_ID = "client-123.apps.example"
SECRET = "very-secret-value"
ACCESS = "access-token-value"
CODE = "4/code-value"


def _jwt(claims):
    """Build an unsigned JWT-shaped string carrying ``claims``."""
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"e30.{body.decode()}.sig"


class _Response(io.BytesIO):
    """A urlopen response stand-in with a status and context-manager support."""

    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeGoogle:
    """Answer token, People and revoke requests; record every request."""

    def __init__(self, person=None, people_status=200, aud=CLIENT_ID):
        self.requests = []
        self.person = person if person is not None else _person()
        self.people_status = people_status
        self.aud = aud

    def __call__(self, request, timeout):
        """Route one request by URL, like ``urllib.request.urlopen``."""
        assert timeout == pe.HTTP_TIMEOUT
        self.requests.append(request)
        url = request.full_url
        if url == pe.TOKEN_URL:
            return _Response(
                {
                    "access_token": ACCESS,
                    "id_token": _jwt(
                        {
                            "aud": self.aud,
                            "email": "me@example.org",
                            "email_verified": True,
                        }
                    ),
                    "scope": " ".join(pe.SCOPES),
                }
            )
        if url == pe.PEOPLE_URL:
            if self.people_status != 200:
                raise urllib.error.HTTPError(
                    url,
                    self.people_status,
                    "x",
                    {},
                    io.BytesIO(
                        b'{"error": {"status": "PERMISSION_DENIED", '
                        b'"message": "People API has not been used"}}'
                    ),
                )
            return _Response(self.person)
        if url == pe.REVOKE_URL:
            return _Response({})
        raise AssertionError(url)

    def form(self, url):
        """Return the decoded form body of the request sent to ``url``."""
        (request,) = [r for r in self.requests if r.full_url == url]
        return dict(urllib.parse.parse_qsl(request.data.decode()))


def _person():
    """A People API answer with a primary, an alternate and an unverified address."""
    return {
        "resourceName": "people/1",
        "emailAddresses": [
            {
                "value": "me@example.org",
                "metadata": {
                    "primary": True,
                    "verified": True,
                    "source": {"type": "ACCOUNT"},
                },
            },
            {
                "value": "alt@other.example",
                "metadata": {"verified": True, "source": {"type": "ACCOUNT"}},
            },
            {"value": "maybe@third.example", "metadata": {"verified": False}},
        ],
    }


@pytest.fixture
def client_file(tmp_path):
    """A Desktop-app client download in an otherwise empty directory."""
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps({"installed": {"client_id": CLIENT_ID, "client_secret": SECRET}})
    )
    return path


def _args(client_file, **overrides):
    """Parsed arguments for ``run`` with the given overrides."""
    args = pe.parse_args(["--client-file", str(client_file)])
    vars(args).update(overrides)
    return args


@pytest.mark.parametrize(
    "document",
    [
        {"installed": {"client_id": CLIENT_ID, "client_secret": SECRET}},
        {"web": {"client_id": CLIENT_ID, "client_secret": SECRET}},
        {"client_id": CLIENT_ID, "client_secret": SECRET},
    ],
)
def test_load_client_accepts_desktop_web_and_compact_files(tmp_path, document):
    """All three client file shapes yield the same ID and secret."""
    path = tmp_path / "c.json"
    path.write_text(json.dumps(document))
    assert pe.load_client(path) == (CLIENT_ID, SECRET)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not json " + SECRET, "not valid JSON"),
        ('["x"]', "not a JSON object"),
        (json.dumps({"client_secret": SECRET}), "no client_id"),
        (json.dumps({"client_id": CLIENT_ID}), "no client_secret"),
    ],
)
def test_load_client_errors_never_show_the_content(tmp_path, content, message):
    """A bad file is named by its problem only, never by its content."""
    path = tmp_path / "c.json"
    path.write_text(content)
    with pytest.raises(pe.SmokeError, match=message) as error:
        pe.load_client(path)
    assert SECRET not in str(error.value)


def test_pkce_pair_is_s256_of_the_verifier():
    """The challenge is the unpadded base64url SHA-256 of a 43–128 char verifier."""
    verifier, challenge = pe.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.rstrip(b"=").decode()
    assert pe.pkce_pair()[0] != verifier


def test_authorization_url_asks_for_the_scopes_with_pkce():
    """The URL carries the loopback redirect, scopes, state and S256 challenge."""
    url = pe.authorization_url(CLIENT_ID, pe.redirect_uri(5555), "st", "ch")
    parts = urllib.parse.urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == pe.AUTH_URL
    query = dict(urllib.parse.parse_qsl(parts.query))
    assert query == {
        "client_id": CLIENT_ID,
        "redirect_uri": "http://127.0.0.1:5555/",
        "response_type": "code",
        "scope": "openid email profile https://www.googleapis.com/auth/user.emails.read",
        "state": "st",
        "code_challenge": "ch",
        "code_challenge_method": "S256",
        "prompt": "consent",
    }


def test_parse_redirect_accepts_the_pasted_url_or_a_bare_code():
    """A full URL is state-checked; a bare code is accepted but flagged unchecked."""
    url = f"http://127.0.0.1:5555/?state=st&code={urllib.parse.quote(CODE)}&scope=x"
    assert pe.parse_redirect(url, "st") == (CODE, True)
    assert pe.parse_redirect(f"  {CODE}\n", "st") == (CODE, False)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("http://127.0.0.1:5555/?state=other&code=c", "state does not match"),
        ("http://127.0.0.1:5555/?error=access_denied&state=st", "access_denied"),
        ("http://127.0.0.1:5555/?state=st", "no authorization code"),
        ("   ", "Nothing was pasted"),
    ],
)
def test_parse_redirect_refuses_bad_answers(text, message):
    """Wrong state, Google's refusal, a missing code and empty input all fail."""
    with pytest.raises(pe.SmokeError, match=message):
        pe.parse_redirect(text, "st")


def test_state_is_checked_before_googles_error():
    """A foreign request's ``error`` never reaches the terminal: state fails first."""
    params = {"error": ["\x1b]0;evil\x07owned"], "state": ["other"]}
    with pytest.raises(pe.StateMismatch) as error:
        pe.code_from_params(params, "st")
    assert "owned" not in str(error.value)
    with pytest.raises(pe.StateMismatch):
        pe.code_from_params({"state": ["é"], "code": ["c"]}, "st")


def test_googles_error_is_printed_without_control_characters():
    """With the right state, Google's error shows with non-printables stripped."""
    params = {"error": ["access_denied\x1b[2J\n"], "state": ["st"]}
    with pytest.raises(pe.SmokeError) as error:
        pe.code_from_params(params, "st")
    assert str(error.value) == "Google refused the sign-in: access_denied[2J."


def test_redirect_handler_has_a_socket_timeout():
    """An idle accepted connection cannot block handle_request() forever."""
    assert pe._RedirectHandler.timeout == 10


def test_email_rows_and_render_show_each_flag_and_the_id_token():
    """The table lists every address; an omitted flag prints as '-', not false."""
    rows = pe.email_rows(_person())
    assert rows == [
        ("me@example.org", "true", "true", "ACCOUNT"),
        ("alt@other.example", "true", "-", "ACCOUNT"),
        ("maybe@third.example", "false", "-", "-"),
    ]
    text = pe.render(
        {"email": "me@example.org", "email_verified": True},
        rows,
        "openid email",
        redact_addresses=False,
    )
    assert text.splitlines()[:3] == [
        "ID token email: me@example.org",
        "ID token email_verified: true",
        "Granted scopes: openid email",
    ]
    assert text.splitlines()[4].split() == ["address", "verified", "primary", "source"]
    assert ["alt@other.example", "true", "-", "ACCOUNT"] in [
        row.split() for row in text.splitlines()
    ]
    assert "no email addresses" in pe.render({}, [], "", redact_addresses=False)


def test_redact_keeps_only_the_domain():
    """--redact hides every address's local part, in the claims and the table."""
    text = pe.render(
        {"email": "me@example.org"},
        pe.email_rows(_person()),
        "",
        redact_addresses=True,
    )
    assert "me@" not in text and "alt@" not in text and "maybe@" not in text
    assert "…@other.example" in text
    assert pe.redact("no-at-sign") == "…"


def test_manual_run_prints_the_table_revokes_and_writes_nothing(
    client_file, tmp_path, monkeypatch, capsys
):
    """Manual mode: paste the redirect, exchange with the verifier, revoke at exit.

    Nothing is written to disk, and no secret, code or token reaches stdout.
    """
    monkeypatch.chdir(tmp_path)
    google = FakeGoogle()
    shown = []

    def paste(_prompt):
        """Answer the prompt with Google's redirect for the printed URL."""
        url = next(line for line in shown if line.startswith(pe.AUTH_URL))
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        shown.append(query)
        return f"{query['redirect_uri']}?state={query['state']}&code={CODE}"

    def show(*items):
        """Collect printed text line by line."""
        for item in items:
            shown.extend(str(item).splitlines())

    status = pe.run(
        _args(client_file, manual=True), opener=google, input_fn=paste, print_fn=show
    )
    assert status == 0
    query = next(item for item in shown if isinstance(item, dict))
    form = google.form(pe.TOKEN_URL)
    assert form["code"] == CODE
    assert form["redirect_uri"] == query["redirect_uri"]
    digest = hashlib.sha256(form["code_verifier"].encode()).digest()
    assert (
        base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        == (query["code_challenge"])
    )
    (people,) = [r for r in google.requests if r.full_url == pe.PEOPLE_URL]
    assert people.get_header("Authorization") == f"Bearer {ACCESS}"
    assert google.form(pe.REVOKE_URL) == {"token": ACCESS}
    text = "\n".join(line for line in shown if isinstance(line, str))
    assert ["alt@other.example", "true", "-", "ACCOUNT"] in [
        row.split() for row in text.splitlines()
    ]
    assert "Access revoked at Google." in text
    for secret in (SECRET, ACCESS, CODE, form["code_verifier"]):
        assert secret not in text
    assert capsys.readouterr().out == ""
    assert sorted(os.listdir(tmp_path)) == ["client.json"]


def test_loopback_run_reads_the_redirect_itself(client_file):
    """Default mode: listen on 127.0.0.1, ignore stray requests, take the code."""
    google = FakeGoogle()
    out = []

    def browser(url):
        """Play the browser: a favicon fetch, then Google's redirect."""
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))

        def visit():
            """Fetch the loopback URLs the way a browser would."""
            base = query["redirect_uri"]
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "favicon.ico", timeout=5)
            # A request with Google's shape but a foreign state is ignored.
            stray = f"{base}?state=guess&error=owned"
            urllib.request.urlopen(stray, timeout=5).close()
            target = f"{base}?state={query['state']}&code={CODE}"
            with urllib.request.urlopen(target, timeout=5) as response:
                assert b"close this tab" in response.read()

        threading.Thread(target=visit, daemon=True).start()
        return True

    status = pe.run(
        _args(client_file, timeout=20),
        opener=google,
        print_fn=lambda *a: out.extend(a),
        open_browser=browser,
    )
    assert status == 0
    assert google.form(pe.TOKEN_URL)["code"] == CODE
    assert any("me@example.org" in line for line in out)


def test_wait_for_code_gives_up_after_the_timeout():
    """No redirect within the timeout is a plain failure."""
    ticks = iter([0, 0, 5, 11])
    server = SimpleNamespace(handle_request=lambda: None)
    with pytest.raises(pe.SmokeError, match="within 10 seconds"):
        pe.wait_for_code(server, "st", 10, clock=lambda: next(ticks))


@pytest.mark.parametrize(
    ("google", "message"),
    [
        (FakeGoogle(people_status=403), "HTTP 403: PERMISSION_DENIED People API"),
        (FakeGoogle(aud="someone-else"), "different client"),
    ],
)
def test_failures_after_the_exchange_still_revoke(client_file, google, message):
    """A People API error or a foreign ID token fails, and the token is revoked."""
    state = {}

    def paste(_prompt):
        """Return a redirect carrying the run's state."""
        return f"http://127.0.0.1:1/?state={state['value']}&code={CODE}"

    def show(*items):
        """Capture the state from the printed URL."""
        for item in items:
            for line in str(item).splitlines():
                if line.startswith(pe.AUTH_URL):
                    query = urllib.parse.urlsplit(line).query
                    state["value"] = dict(urllib.parse.parse_qsl(query))["state"]

    with pytest.raises(pe.SmokeError, match=message):
        pe.run(
            _args(client_file, manual=True),
            opener=google,
            input_fn=paste,
            print_fn=show,
        )
    assert google.form(pe.REVOKE_URL) == {"token": ACCESS}


def test_main_exits_2_with_one_plain_line(tmp_path, capsys):
    """A missing client file exits 2 with an ERROR line and no traceback."""
    assert pe.main(["--client-file", str(tmp_path / "missing.json")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR: Cannot read the OAuth client file")
    assert len(captured.err.splitlines()) == 1


def test_module_is_standalone_stdlib_only():
    """The module imports only the standard library, so it runs when copied alone."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert names - {"__future__"} <= set(sys.stdlib_module_names)


def test_redirect_handler_does_not_log_requests():
    """The handler's access log is silenced: the request line holds the code."""
    assert pe._RedirectHandler.log_message(None, "%s", CODE) is None


def test_wrapper_delegates_to_the_package():
    """The smoke-test wrapper is an executable python3 script that only delegates."""
    text = WRAPPER.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3\n")
    assert os.access(WRAPPER, os.X_OK)
    assert "from parishkit.google.people_emails import main" in text
    assert "raise SystemExit(main())" in text
