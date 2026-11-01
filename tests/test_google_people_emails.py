"""Tests for the People API email-address smoke tool (#2352); no real Google."""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
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


def test_running_the_file_directly_ignores_its_sibling_modules(tmp_path):
    """``python3 people_emails.py`` works although a sibling shadows the stdlib.

    The planted ``calendar.py`` stands in for parishkit/google/calendar.py,
    which shadowed the stdlib module http.server needs (on older Pythons,
    through ``email``). Newer Pythons import ``calendar`` lazily, so a planted
    ``base64.py``, which this module imports directly, makes the check bite
    on every version.
    """
    shutil.copy(MODULE, tmp_path / "people_emails.py")
    for name in ("calendar", "base64"):
        (tmp_path / f"{name}.py").write_text("raise ImportError('shadowed')\n")
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(tmp_path / "people_emails.py"), "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--client-file" in result.stdout


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


SUB = "google-sub-1234567890"
STEP1, STEP2 = "access-step-1", "access-step-2"
IDENTITY = (
    "openid https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)


class TwoStepGoogle:
    """Answer --two-step's two exchanges, tokeninfo, People and revoke requests.

    Revoking the step 2 token ends the whole incremental grant, as at Google,
    so a later revoke of the step 1 token answers 400.
    """

    def __init__(self, scope=f"{IDENTITY} {pe.EMAILS_SCOPE}", sub=SUB, id_token=True):
        self.requests = []
        self.scope, self.sub, self.id_token = scope, sub, id_token
        self.revoked = []

    def __call__(self, request, timeout):
        """Route one request by URL, like ``urllib.request.urlopen``."""
        assert timeout == pe.HTTP_TIMEOUT
        self.requests.append(request)
        url = request.full_url
        if url == pe.TOKEN_URL:
            if self.exchanges() == 1:
                claims = {"aud": CLIENT_ID, "email": "me@example.org", "sub": SUB}
                answer = {"access_token": STEP1, "scope": IDENTITY}
                answer["id_token"] = _jwt({**claims, "email_verified": True})
                return _Response(answer)
            answer = {"access_token": STEP2, "scope": self.scope}
            if self.id_token:
                answer["id_token"] = _jwt({"aud": CLIENT_ID, "sub": self.sub})
            return _Response(answer)
        if url.startswith(pe.TOKENINFO_URL + "?"):
            return _Response({"aud": CLIENT_ID, "sub": self.sub})
        if url == pe.PEOPLE_URL:
            return _Response(_person())
        if url == pe.REVOKE_URL:
            token = dict(urllib.parse.parse_qsl(request.data.decode()))["token"]
            self.revoked.append(token)
            if token == STEP1 and STEP2 in self.revoked[:-1]:
                raise urllib.error.HTTPError(url, 400, "x", {}, io.BytesIO(b"{}"))
            return _Response({})
        raise AssertionError(url)

    def exchanges(self):
        """Return how many token exchanges have been requested so far."""
        return sum(r.full_url == pe.TOKEN_URL for r in self.requests)

    def people_calls(self):
        """Return the People API requests sent so far."""
        return [r for r in self.requests if r.full_url == pe.PEOPLE_URL]


def _two_step(client_file, google, cancel=False):
    """Run --two-step in manual mode, pasting Google's answer to each URL.

    Returns the exit status, the printed text and each step's URL query.
    With ``cancel`` the step 2 answer is Google's access_denied redirect.
    """
    shown, queries = [], []

    def paste(_prompt):
        """Answer the prompt with Google's redirect for the latest printed URL."""
        url = [line for line in shown if line.startswith(pe.AUTH_URL)][-1]
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        queries.append(query)
        base = f"{query['redirect_uri']}?state={query['state']}"
        if cancel and len(queries) == 2:
            return f"{base}&error=access_denied"
        return f"{base}&code=code-{len(queries)}"

    def show(*items):
        """Collect printed text line by line."""
        for item in items:
            shown.extend(str(item).splitlines())

    status = pe.run(
        _args(client_file, manual=True, two_step=True),
        opener=google,
        input_fn=paste,
        print_fn=show,
    )
    return status, "\n".join(shown), queries


def test_two_step_both_steps_ok_lists_the_addresses(client_file):
    """Identity first, then user.emails.read alone; same account → the table."""
    assert pe.parse_args(["--client-file", "c.json", "--two-step"]).two_step
    google = TwoStepGoogle()
    status, text, (first, second) = _two_step(client_file, google)
    assert status == 0
    assert first["scope"] == "openid email profile"
    assert "include_granted_scopes" not in first
    assert second["scope"] == pe.EMAILS_SCOPE
    assert second["include_granted_scopes"] == "true"
    assert second["login_hint"] == "me@example.org"
    assert second["prompt"] == "consent"
    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]
    (people,) = google.people_calls()
    assert people.get_header("Authorization") == f"Bearer {STEP2}"
    assert f"(account {pe.account_hash(SUB)})" in text
    assert SUB not in text
    assert ["alt@other.example", "true", "-", "ACCOUNT"] in [
        row.split() for row in text.splitlines()
    ]
    assert google.revoked == [STEP2, STEP1]
    assert "Step 2 access revoked at Google." in text
    assert "Step 1 access had already ended at Google." in text
    for secret in (SECRET, STEP1, STEP2, "code-1", "code-2"):
        assert secret not in text


def test_two_step_unchecked_box_is_not_granted(client_file):
    """Without user.emails.read in the grant, no People call: the no-access page."""
    google = TwoStepGoogle(scope=IDENTITY)
    status, text, _ = _two_step(client_file, google)
    assert status == 0
    assert "Not granted: ParishKit would show the normal no-access page." in text
    assert google.people_calls() == []
    assert google.revoked == [STEP2, STEP1]


def test_two_step_different_account_is_refused(client_file):
    """Consent from another Google account in step 2 is refused, unread."""
    google = TwoStepGoogle(sub="someone-else")
    status, text, _ = _two_step(client_file, google)
    assert status == 0
    assert (
        "Refused: a different Google account consented; ParishKit would refuse." in text
    )
    assert f"consent from account {pe.account_hash('someone-else')}" in text
    assert google.people_calls() == []
    assert google.revoked == [STEP2, STEP1]


def test_two_step_cancelled_step_2_is_the_no_access_page(client_file):
    """access_denied in step 2: no second exchange, and step 1 is revoked."""
    google = TwoStepGoogle()
    status, text, _ = _two_step(client_file, google, cancel=True)
    assert status == 0
    assert (
        "Step 2 cancelled (Google answered access_denied): ParishKit would show "
        "the normal no-access page." in text
    )
    assert google.exchanges() == 1
    assert google.people_calls() == []
    assert google.revoked == [STEP1]
    assert "Step 1 access revoked at Google." in text


def test_two_step_without_a_step_2_id_token_asks_tokeninfo(client_file):
    """No ID token in step 2: the account comes from tokeninfo, never printed."""
    google = TwoStepGoogle(id_token=False)
    status, text, _ = _two_step(client_file, google)
    assert status == 0
    (info,) = [r for r in google.requests if r.full_url.startswith(pe.TOKENINFO_URL)]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(info.full_url).query))
    assert query == {"access_token": STEP2}
    assert len(google.people_calls()) == 1
    assert STEP2 not in text


def test_two_step_works_with_the_loopback_redirect(client_file):
    """Default mode: each step listens anew and reads its own redirect."""
    google = TwoStepGoogle()
    out = []

    def browser(url):
        """Play the browser: follow Google's redirect for this step's URL."""
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))

        def visit():
            """Fetch the loopback redirect the way a browser would."""
            target = f"{query['redirect_uri']}?state={query['state']}&code=c"
            urllib.request.urlopen(target, timeout=5).close()

        threading.Thread(target=visit, daemon=True).start()
        return True

    status = pe.run(
        _args(client_file, two_step=True, timeout=20),
        opener=google,
        print_fn=lambda *a: out.extend(a),
        open_browser=browser,
    )
    assert status == 0
    assert google.exchanges() == 2
    assert len(google.people_calls()) == 1
    assert google.revoked == [STEP2, STEP1]
