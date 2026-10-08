"""``restore-check``: a backup set compared with this image before a restore.

pg_restore is not installed on the test host, so a stand-in on PATH plays
it: it accepts only the exact arguments the check passes (anything else
exits 64) and prints the ``COPY`` block a real data-only restore of
``django_migrations`` prints. The comparison, the parsing, the refusals and
the timeout line are the real code.
"""

import json
import logging
import os
import shutil
import stat

import pytest

from parishkit.stewardship import observability, restore_check
from parishkit.stewardship.cli import main
from parishkit.stewardship.upgrade_check import disk_migrations

IMAGE = "ghcr.io/example/parishkit/stewardship@sha256:" + "a" * 64
SET = [["auth", "0001_initial"], ["stewardship_jobs", "0001_initial"]]

# The check gives pg_restore only PATH in its environment, so the stand-in's
# paths are written into it, and its behaviour is chosen by files: SLEEP
# holds seconds to sleep, FAIL makes it fail, COPY is what it prints.
STAND_IN = """#!/bin/sh
# Stand-in pg_restore: only the check's exact arguments are accepted.
[ "$#" -eq 4 ] && [ "$1" = --data-only ] && [ "$2" = --table=django_migrations ] \\
    && [ "$3" = --file=- ] && [ "$4" = '{dump}' ] || exit 64
[ ! -e '{state}/SLEEP' ] || exec sleep "$(cat '{state}/SLEEP')"
[ ! -e '{state}/FAIL' ] || exit 1
cat '{state}/COPY'
"""


def copy_block(pairs, *, columns="id, app, name, applied", end=True):
    """What pg_restore prints for these rows (with the SET preamble)."""
    lines = [
        "SET statement_timeout = 0;",
        "SET client_encoding = 'UTF8';",
        "",
        f"COPY public.django_migrations ({columns}) FROM stdin;",
    ]
    lines += [
        f"{number}\t{app}\t{name}\t2026-10-01 00:00:00+00"
        for number, (app, name) in enumerate(pairs, 1)
    ]
    if end:
        lines.append("\\.")
    return "\n".join(lines) + "\n"


@pytest.fixture
def files(tmp_path, monkeypatch):
    """A manifest writer and a dump read by the stand-in pg_restore."""
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    dump = tmp_path / "database.pgdump"
    dump.write_bytes(b"PGDMP")
    stand_in = bin_directory / "pg_restore"
    stand_in.write_text(STAND_IN.format(dump=dump, state=tmp_path))
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_directory}{os.pathsep}{os.environ['PATH']}")
    # The Compose test container mounts /tmp noexec: the stand-in is not
    # executable there, so PATH would find the image's real pg_restore and
    # every case would read as an unreadable dump. The other CI jobs still
    # run these tests.
    if shutil.which("pg_restore") != str(stand_in):
        pytest.skip("the temporary directory cannot run the stand-in pg_restore")

    class Files:
        """The test's inputs."""

        def __init__(self):
            self.dump = dump
            self.state = tmp_path

        @staticmethod
        def manifest(**fields):
            """Write a manifest with these fields over a current one."""
            document = {
                "version": 1,
                "application_version": "1.6.1",
                "image": IMAGE,
                "migrations": SET,
                "started_at": "2026-10-07T04:00:00+00:00",
            }
            document.update(fields)
            document = {k: v for k, v in document.items() if v is not ...}
            path = tmp_path / "manifest.json"
            path.write_text(json.dumps(document))
            return path

        @staticmethod
        def copied(text):
            """What the stand-in pg_restore prints."""
            (tmp_path / "COPY").write_text(text)

    return Files()


def test_an_exact_match_passes(files):
    """The set's migrations are the image's: match, exit 0."""
    code, report = restore_check.check(files.manifest(), image_migrations=SET)
    assert code == restore_check.MATCH
    assert report["result"] == "match"
    assert report["not_in_backup"] == report["unknown_to_image"] == []
    assert report["backup"] == {
        "application_version": "1.6.1",
        "image": IMAGE,
        "migrations_from": "manifest",
    }
    assert "use_image" not in report


def test_a_mismatch_names_both_directions_and_the_image_to_use(files):
    """A migration the set lacks, and one the image does not know."""
    image = [SET[0], ["stewardship_jobs", "0002_more"]]
    code, report = restore_check.check(files.manifest(), image_migrations=image)
    assert code == restore_check.MISMATCH
    assert report["result"] == "mismatch"
    assert report["not_in_backup"] == [["stewardship_jobs", "0002_more"]]
    assert report["unknown_to_image"] == [["stewardship_jobs", "0001_initial"]]
    assert report["use_image"] == IMAGE


def test_a_set_from_before_the_check_needs_its_dump(files):
    """No migration list in the manifest: refused without the dump, and
    read from the dump's django_migrations rows with it."""
    old = files.manifest(image=..., migrations=...)
    with pytest.raises(restore_check.RestoreCheckRefused, match="decrypted dump"):
        restore_check.check(old, image_migrations=SET)
    files.copied(copy_block(SET))
    code, report = restore_check.check(old, dump=files.dump, image_migrations=SET)
    assert code == restore_check.MATCH
    assert report["backup"]["migrations_from"] == "dump"
    assert report["backup"]["image"] is None
    code, report = restore_check.check(
        old, dump=files.dump, image_migrations=[*SET, ["auth", "0002_x"]]
    )
    assert code == restore_check.MISMATCH and report["use_image"] is None


# pg_restore 18.6's own output for a dump of a database holding these two
# rows and another table (captured from postgres:18.6-trixie, the image the
# deployment pins; the restrict key is a fixed stand-in for its random one).
REAL_OUTPUT = """--
-- PostgreSQL database dump
--

\\restrict ba6eSlqMOw6KVAsohFljvhk8K6soc1bgeRD9ECKMIJ9LK54CzjjZW6bqAg0QxUg

-- Dumped from database version 18.6 (Debian 18.6-1.pgdg13+2)
-- Dumped by pg_dump version 18.6 (Debian 18.6-1.pgdg13+2)

SET statement_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET row_security = off;

--
-- Data for Name: django_migrations; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.django_migrations (id, app, name, applied) FROM stdin;
1\tauth\t0001_initial\t2026-10-07 21:46:59.531291+00
2\tstewardship_jobs\t0012_log_events\t2026-10-07 21:46:59.531291+00
\\.


--
-- PostgreSQL database dump complete
--

\\unrestrict ba6eSlqMOw6KVAsohFljvhk8K6soc1bgeRD9ECKMIJ9LK54CzjjZW6bqAg0QxUg

"""


def test_real_pg_restore_output_is_read(files):
    """The block exactly as pg_restore 18.6 prints it."""
    files.copied(REAL_OUTPUT)
    assert restore_check.dump_migrations(files.dump) == {
        ("auth", "0001_initial"),
        ("stewardship_jobs", "0012_log_events"),
    }


def test_the_dump_columns_are_found_by_name(files):
    """A COPY header in another column order still reads app and name."""
    files.copied(
        copy_block([], columns="app, name, id, applied").replace(
            "\\.", "auth\t0001_initial\t1\tx\nstewardship_jobs\t0001_initial\t2\tx\n\\."
        )
    )
    old = files.manifest(migrations=...)
    assert restore_check.check(old, dump=files.dump, image_migrations=SET)[0] == 0


def test_a_manifest_that_does_not_describe_the_dump_is_refused(files):
    """Both given, and they differ: the manifest is not this dump's."""
    files.copied(copy_block(SET[:1]))
    with pytest.raises(restore_check.RestoreCheckRefused, match="does not describe"):
        restore_check.check(files.manifest(), dump=files.dump, image_migrations=SET)
    files.copied(copy_block(SET))
    code, report = restore_check.check(
        files.manifest(), dump=files.dump, image_migrations=SET
    )
    assert code == 0 and report["backup"]["migrations_from"] == "manifest"


@pytest.mark.parametrize(
    "fields",
    [
        {"version": 2},
        {"application_version": "has space"},
        {"application_version": ...},
        {"image": "two words"},
        {"image": 7},
        {"migrations": []},
        {"migrations": [["auth"]]},
        {"migrations": [["Auth", "0001_initial"]]},
        {"migrations": [["auth", "0001-initial"]]},
        {"migrations": [SET[0], SET[0]]},
        {"migrations": {"auth": "0001_initial"}},
    ],
)
def test_a_malformed_manifest_is_refused(files, fields):
    """Every field the check reads is checked before it is trusted."""
    with pytest.raises(restore_check.RestoreCheckRefused):
        restore_check.check(files.manifest(**fields), image_migrations=SET)


def test_a_manifest_that_is_not_json_or_too_large_is_refused(files, tmp_path):
    """Not JSON, a JSON list, and a file past the size bound."""
    path = tmp_path / "manifest.json"
    for content in (b"{", b"[]", b" " * (restore_check.MAX_MANIFEST_BYTES + 1)):
        path.write_bytes(content)
        with pytest.raises(restore_check.RestoreCheckRefused):
            restore_check.check(path, image_migrations=SET)


@pytest.mark.parametrize(
    "text",
    [
        "SET client_encoding = 'UTF8';\n",  # no COPY block
        copy_block(SET, end=False),  # unterminated
        copy_block([]),  # no rows
        copy_block(SET).replace("\tauth\t", "\tAuth\t"),  # not a label
        copy_block(SET).replace("0001_initial\t2026", "0001_initial\textra\t2026"),
        copy_block(SET, columns="id, label, applied"),  # no app column
    ],
)
def test_dump_records_that_cannot_be_trusted_are_refused(files, text):
    """The dump's block is parsed strictly, never guessed at."""
    files.copied(text)
    old = files.manifest(migrations=...)
    with pytest.raises(restore_check.RestoreCheckRefused):
        restore_check.check(old, dump=files.dump, image_migrations=SET)


def test_a_failed_or_missing_pg_restore_is_refused(files, monkeypatch, tmp_path):
    """pg_restore failing, or given other arguments (the stand-in's 64)."""
    old = files.manifest(migrations=...)
    files.copied(copy_block(SET))
    (files.state / "FAIL").touch()
    with pytest.raises(restore_check.RestoreCheckRefused, match="could not be read"):
        restore_check.check(old, dump=files.dump, image_migrations=SET)
    (files.state / "FAIL").unlink()
    other = tmp_path / "other.pgdump"
    other.write_bytes(b"PGDMP")
    with pytest.raises(restore_check.RestoreCheckRefused, match="could not be read"):
        restore_check.check(old, dump=other, image_migrations=SET)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(restore_check.RestoreCheckRefused, match="not installed"):
        restore_check.check(old, dump=files.dump, image_migrations=SET)


def test_a_pg_restore_past_its_limit_is_killed_and_logged(files, monkeypatch, caplog):
    """The kill is logged with what was stopped, the limit and the elapsed
    time (the timeout rule), and the check is refused."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    (files.state / "SLEEP").write_text("30")
    with pytest.raises(restore_check.RestoreCheckRefused, match="too long"):
        restore_check.dump_migrations(files.dump, seconds=1)
    lines = [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]
    [line] = [line for line in lines if "timeout" in line.get("extra", {})]
    assert line["level"] == "WARNING"
    assert line["extra"]["timeout"] == "restore_check_dump"
    assert line["extra"]["limit_seconds"] == 1
    assert line["extra"]["elapsed_seconds"] in (1, 2)


def test_the_command_compares_with_this_images_own_migrations(
    files, monkeypatch, capsys
):
    """End to end through pk-stewardship: this image's real migration files;
    exit 0, 3, and 2 with the fixed reason on standard error."""
    monkeypatch.setattr(restore_check, "configure_logging", lambda: None)
    current = [list(pair) for pair in disk_migrations()]
    manifest = files.manifest(migrations=current)
    assert main(["restore-check", "--manifest", str(manifest)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["result"] == "match"
    manifest = files.manifest(migrations=current[:-1])
    assert main(["restore-check", "--manifest", str(manifest)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["not_in_backup"] == [current[-1]]
    manifest = files.manifest(migrations=...)
    assert main(["restore-check", "--manifest", str(manifest)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: restore check refused: This set's manifest lists no "
        "migrations; pass its decrypted dump; the set is from before this "
        "check: add --dump with its decrypted database.pgdump\n"
    )
    files.copied(copy_block(SET[:1]))
    disagreeing = files.manifest(migrations=current)
    arguments = ["--manifest", str(disagreeing), "--dump", str(files.dump)]
    assert main(["restore-check", *arguments]) == 2
    assert "pass the two files from the same set directory" in capsys.readouterr().err


def test_every_refusal_has_its_own_advice():
    """Each fixed reason the module raises has an entry in ADVICE."""
    import ast
    import inspect

    reasons = set()
    for node in ast.walk(ast.parse(inspect.getsource(restore_check))):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "RestoreCheckRefused"
        ):
            reasons.add(ast.literal_eval(node.args[0]))
    assert reasons and reasons == set(restore_check.ADVICE)


def test_a_generic_refusal_names_no_path(files, monkeypatch, capsys, tmp_path):
    """A missing manifest (an OSError, whose text names the path) gets the
    generic line, which names neither the path nor the error."""
    monkeypatch.setattr(restore_check, "configure_logging", lambda: None)
    missing = tmp_path / "secret-dir-name" / "manifest.json"
    assert main(["restore-check", "--manifest", str(missing)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"ERROR: restore check refused: {restore_check.GENERIC_ADVICE}\n"
    )
    assert "secret-dir-name" not in captured.err


def test_the_command_takes_only_its_own_options(capsys):
    """Another command's option is a usage error before anything is read."""
    with pytest.raises(SystemExit):
        main(["restore-check", "--manifest", "m", "--key", "k"])
    assert "--key" in capsys.readouterr().err
