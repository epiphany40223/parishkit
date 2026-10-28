"""The off-site uploader's password file against the cluster's verifier (#235).

Operations specification, "Uploader password": the stored SCRAM verifier is
parsed strictly and both keys are recomputed from the file and compared in
constant time; ``uploader-password-check`` prints only ``match`` or
``mismatch``; ``database-grants`` creates the login from the file when it is
absent and refuses a missing or mismatched file before any grant. Verifiers
here come from libpq itself, so the comparison is checked against
PostgreSQL's own computation, not against a copy of this code's.
"""

import base64
import io
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import database_provisioning as provisioning
from parishkit.stewardship import deployment as deployment_module
from parishkit.stewardship import uploader_password as checker
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.cli import main
from parishkit.stewardship.runtime_paths import RuntimeLayout

from .test_runtime_topology import configuration_at

PASSWORD = b"Zq3uYv0x-correct_horse_battery_staple_0123"
LOGIN = "pk_stewardship_backup_uploader"


def libpq_verifier(password, login=LOGIN, algorithm=b"scram-sha-256"):
    """The verifier libpq computes for ``password``, with no server involved."""
    connection = psycopg.pq.PGconn.connect_start(b"host=/nonexistent dbname=x")
    try:
        return connection.encrypt_password(password, login.encode(), algorithm).decode()
    finally:
        connection.finish()


def parts(verifier):
    """The verifier's four fields: iterations, salt, StoredKey, ServerKey."""
    head, keys = verifier.split("$", 1)[1].split("$")
    iterations, salt = head.split(":")
    stored, server = keys.split(":")
    return iterations, salt, stored, server


def rebuilt(iterations, salt, stored, server):
    """A verifier from its four fields."""
    return f"SCRAM-SHA-256${iterations}:{salt}${stored}:{server}"


def test_libpqs_verifier_matches_its_password_and_no_other():
    """The comparison agrees with PostgreSQL's own SCRAM computation."""
    verifier = libpq_verifier(PASSWORD)
    assert checker.parse_verifier(verifier) is not None
    assert checker.password_matches(PASSWORD, verifier) is True
    assert checker.password_matches(PASSWORD + b"x", verifier) is False
    assert checker.password_matches(PASSWORD[:-1], verifier) is False
    # A verifier for the same password with another salt still matches.
    assert checker.password_matches(PASSWORD, libpq_verifier(PASSWORD)) is True


def test_both_keys_must_match():
    """A verifier forged to match StoredKey alone, or ServerKey alone, is refused."""
    iterations, salt, stored, server = parts(libpq_verifier(PASSWORD))
    other = parts(libpq_verifier(b"another-password-entirely"))
    assert not checker.password_matches(
        PASSWORD, rebuilt(iterations, salt, stored, other[3])
    )
    assert not checker.password_matches(
        PASSWORD, rebuilt(iterations, salt, other[2], server)
    )


def malformed():
    """Every refused form, built from one real verifier."""
    iterations, salt, stored, server = parts(libpq_verifier(PASSWORD))
    short = base64.b64encode(base64.b64decode(stored)[:31]).decode()
    long = base64.b64encode(base64.b64decode(stored) + b"\0").decode()
    good = rebuilt(iterations, salt, stored, server)
    return {
        "md5": libpq_verifier(PASSWORD, algorithm=b"md5"),
        "null password": "",
        "not text": None,
        "wrong prefix": good.replace("SCRAM-SHA-256$", "SCRAM-SHA-1$"),
        "lowercase prefix": good.replace("SCRAM-SHA-256$", "scram-sha-256$"),
        "zero count": rebuilt("0", salt, stored, server),
        "leading zero": rebuilt("0" + iterations, salt, stored, server),
        "signed count": rebuilt("+" + iterations, salt, stored, server),
        "count over the bound": rebuilt("10000001", salt, stored, server),
        "hex count": rebuilt("0x1000", salt, stored, server),
        "empty salt": rebuilt(iterations, "", stored, server),
        "unpadded salt": rebuilt(iterations, salt.rstrip("="), stored, server),
        "urlsafe salt": rebuilt(
            iterations, salt.replace("+", "-").replace("/", "_"), stored, server
        )
        if "+" in salt or "/" in salt
        else rebuilt(iterations, salt + "-", stored, server),
        "space in a key": rebuilt(
            iterations, salt, stored[:4] + " " + stored[4:], server
        ),
        "short StoredKey": rebuilt(iterations, salt, short, server),
        "short ServerKey": rebuilt(iterations, salt, stored, short),
        "long ServerKey": rebuilt(iterations, salt, stored, long),
        "trailing text": good + ":extra",
        "trailing newline": good + "\n",
        "missing ServerKey": f"SCRAM-SHA-256${iterations}:{salt}${stored}",
        "oversized": good + "A" * checker.MAXIMUM_VERIFIER,
    }


@pytest.mark.parametrize("name", sorted(malformed()))
def test_every_malformed_verifier_is_a_mismatch(name):
    """Strict parse: anything but the one canonical form never matches."""
    verifier = malformed()[name]
    assert checker.parse_verifier(verifier) is None
    assert checker.password_matches(PASSWORD, verifier) is False


def test_the_iteration_bound_is_inclusive():
    """1 and 10,000,000 parse; the parser never computes anything itself."""
    _, salt, stored, server = parts(libpq_verifier(PASSWORD))
    for count in ("1", "10000000"):
        parsed = checker.parse_verifier(rebuilt(count, salt, stored, server))
        assert parsed is not None and parsed[0] == int(count)


def test_a_noncanonical_base64_key_is_refused():
    """Trailing bits a decoder ignores make another spelling of the same bytes."""
    iterations, salt, stored, server = parts(libpq_verifier(PASSWORD))
    assert stored.endswith("=")
    # The last character before the padding carries two unused bits.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    last = alphabet.index(stored[-2])
    variant = stored[:-2] + alphabet[last ^ 1] + "="
    assert base64.b64decode(variant) == base64.b64decode(stored)
    assert checker.parse_verifier(rebuilt(iterations, salt, variant, server)) is None


@pytest.fixture
def uploader_deployment(tmp_path, monkeypatch):
    """A deployment whose uploader password file exists, loaded from 'YAML'."""
    configuration = configuration_at(tmp_path)
    path = RuntimeLayout(configuration).database_password("backup-uploader")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(path, PASSWORD + b"\n")
    monkeypatch.setattr(
        deployment_module, "load_deployment", lambda *args, **kwargs: configuration
    )
    return configuration, path


@pytest.mark.parametrize(
    "stdin, answer, code",
    [
        ("{good}\n", "match", 0),
        ("{good}", "match", 0),
        ("{other}\n", "mismatch", 1),
        ("{md5}\n", "mismatch", 1),
        ("\n", "mismatch", 1),
        ("", "mismatch", 1),
    ],
)
def test_the_command_prints_only_match_or_mismatch(
    uploader_deployment, capsys, stdin, answer, code
):
    """One word on stdout, nothing on stderr, never the file or the verifier."""
    good = libpq_verifier(PASSWORD)
    text = stdin.format(
        good=good,
        other=libpq_verifier(b"some-other-password"),
        md5=libpq_verifier(PASSWORD, algorithm=b"md5"),
    )
    args = SimpleNamespace(config="/run/operator.yaml")
    assert checker.execute_password_check(args, stdin=io.StringIO(text)) == code
    out, err = capsys.readouterr()
    assert out == answer + "\n"
    assert err == ""
    assert PASSWORD.decode() not in out + err
    assert good not in out + err


def test_a_missing_file_or_oversized_input_refuses_without_an_answer(
    uploader_deployment, capsys
):
    """Exit 2 with a generic line, so a caller can never read it as an answer."""
    _, path = uploader_deployment
    args = SimpleNamespace(config="/run/operator.yaml")
    oversized = io.StringIO("A" * (checker.MAXIMUM_VERIFIER + 2))
    assert checker.execute_password_check(args, stdin=oversized) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("ERROR: uploader password check refused")
    path.unlink()
    good = io.StringIO(libpq_verifier(PASSWORD) + "\n")
    assert checker.execute_password_check(args, stdin=good) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "match" not in out
    assert err.startswith("ERROR: uploader password check refused")


def test_the_command_is_wired_with_only_its_config_option(
    uploader_deployment, capsys, monkeypatch
):
    """``pk-stewardship uploader-password-check --config`` reads stdin."""
    monkeypatch.setattr("sys.stdin", io.StringIO(libpq_verifier(PASSWORD) + "\n"))
    assert main(["uploader-password-check", "--config", "/run/operator.yaml"]) == 0
    assert capsys.readouterr().out == "match\n"
    with pytest.raises(SystemExit) as exit_:
        main(
            [
                "uploader-password-check",
                "--config",
                "/run/operator.yaml",
                "--confirm-deployment",
                str(uuid4()),
            ]
        )
    assert exit_.value.code == 2


class Cursor:
    """Answers the uploader's role lookup and verifier read, records the rest."""

    def __init__(self, exists, verifier=None):
        self.answers = [(exists,)] + ([(verifier,)] if exists else [])
        self.statements = []

    def execute(self, statement, parameters=None):
        """Keep every statement as text for the assertions."""
        self.statements.append(
            statement if isinstance(statement, str) else statement.as_string()
        )

    def fetchone(self):
        """The next scripted answer; an unexpected read fails the test."""
        return self.answers.pop(0)


def database():
    """A stand-in connection whose libpq computes real verifiers."""
    return SimpleNamespace(
        pgconn=psycopg.pq.PGconn.connect_start(b"host=/nonexistent dbname=x")
    )


def test_grants_create_a_missing_uploader_login_from_its_file(uploader_deployment):
    """The expand upgrade's database-grants creates the login, as roles would."""
    configuration, _ = uploader_deployment
    cursor = Cursor(exists=False)
    provisioning._ensure_uploader(database(), cursor, configuration, "marker")
    sql = "\n".join(cursor.statements)
    assert 'CREATE ROLE "pk_stewardship_backup_uploader" LOGIN NOINHERIT' in sql
    assert " NOSUPERUSER NOBYPASSRLS NOCREATEDB " in sql
    limit = configuration.runtime_budget.operator_connections
    assert f"CONNECTION LIMIT {limit} PASSWORD 'SCRAM-SHA-256$" in sql
    assert PASSWORD.decode() not in sql
    assert """COMMENT ON ROLE "pk_stewardship_backup_uploader" IS 'marker'""" in sql
    assert 'GRANT CONNECT ON DATABASE "stewardship"' in sql
    assert 'GRANT USAGE ON SCHEMA public TO "pk_stewardship_backup_uploader"' in sql
    assert "pg_read_all_data" not in sql
    # The verifier written is one this password matches.
    written = sql.split("PASSWORD '", 1)[1].split("'", 1)[0]
    assert checker.password_matches(PASSWORD, written)


def test_grants_check_an_existing_uploader_against_its_verifier(uploader_deployment):
    """A matching file changes nothing; a mismatch refuses before any grant."""
    configuration, _ = uploader_deployment
    cursor = Cursor(exists=True, verifier=libpq_verifier(PASSWORD))
    provisioning._ensure_uploader(database(), cursor, configuration, "marker")
    assert not any("CREATE" in s or "GRANT" in s for s in cursor.statements)
    for verifier in (libpq_verifier(b"not-the-file"), "", "md5abc"):
        cursor = Cursor(exists=True, verifier=verifier)
        with pytest.raises(ConfigError, match="lost-file recovery"):
            provisioning._ensure_uploader(database(), cursor, configuration, "marker")
        assert not any("CREATE" in s or "GRANT" in s for s in cursor.statements)


def test_grants_refuse_a_missing_uploader_file_before_any_change(uploader_deployment):
    """The file is read first, so its absence refuses with nothing executed."""
    from parishkit.stewardship.accounts.key_files import CryptographicError

    configuration, path = uploader_deployment
    path.unlink()
    cursor = Cursor(exists=False)
    with pytest.raises(CryptographicError):
        provisioning._ensure_uploader(database(), cursor, configuration, "marker")
    assert cursor.statements == []
