"""Compare a backup set with this image before anything is restored (#608).

Every service refuses to start unless the database's applied migrations are
exactly the image's (``runtime_database.require_current_schema``), so a set
restored under the wrong image fails closed, but only after the restore,
when the services refuse to start. ``pk-stewardship restore-check`` asks the
same question first, in the target image, with no database and no secret:

* the set's applied migrations come from its plaintext manifest (sets taken
  since #608 record them) or from its decrypted dump's ``django_migrations``
  rows, read with ``pg_restore``; when both are given they must agree, or
  the manifest does not describe that dump;
* the image's are its own migration files (``upgrade_check.disk_migrations``).

The report is one JSON document: ``match`` when the two sets are equal,
otherwise ``mismatch`` with the migrations the set lacks (it would need
forward migrations) and the ones this image does not know (the wrong or an
older image), and the image the set names, which is the one to restore
with. The command reads only the files it is given and changes nothing.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from parishkit import __version__
from parishkit.config import ConfigError

from .observability import Event, configure_logging, emit, emit_failure

# A manifest is a few kilobytes; the migration list adds a few more.
MAX_MANIFEST_BYTES = 1024 * 1024
# How long pg_restore may take to read the migration rows from a dump. It
# reads the dump's table of contents and seeks to that one table, so even a
# large dump takes seconds; the limit only bounds a damaged or hostile one.
DUMP_READ_SECONDS = 600
# The reviewed name of that limit in the timeout line (observability).
DUMP_READ = "restore_check_dump"
# Exit codes: the sets match; they differ; the check was refused.
MATCH, REFUSED, MISMATCH = 0, 2, 3
# Django app labels and migration names, as this code base writes them.
APP = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
NAME = re.compile(r"^[0-9A-Za-z_]{1,255}$")
# A release version, as the backup record's own constraint admits it.
VERSION = re.compile(r"^[0-9A-Za-z.+-]{1,40}$")
# An image reference: printable, no whitespace, bounded.
IMAGE = re.compile(r"^[!-~]{1,512}$")
COPY = re.compile(r"^COPY public\.django_migrations \(([a-z_, ]+)\) FROM stdin;$")


# What to do about each refusal, by its fixed reason. A manifest problem, a
# dump problem and a mix-up between them each need a different fix, so one
# generic hint would send the operator to the wrong file.
_MANIFEST = "pass --manifest the set's own manifest.json, copied whole"
_DUMP = (
    "pass --dump the set's database.pgdump as backup-open decrypted it, copied whole"
)
ADVICE = {
    "An explicit manifest is required.": "pass --manifest the set's manifest.json",
    "The manifest is too large.": _MANIFEST,
    "The manifest is not JSON.": _MANIFEST,
    "The manifest has an unknown shape.": _MANIFEST,
    "The manifest's image is malformed.": _MANIFEST,
    "The manifest's migration list is malformed.": _MANIFEST,
    "This set's manifest lists no migrations; pass its decrypted dump.": (
        "the set is from before this check: add --dump with its decrypted "
        "database.pgdump"
    ),
    "pg_restore is not installed in this image.": (
        "run the check in the release image, which ships pg_restore"
    ),
    "Reading the dump took too long.": _DUMP,
    "The dump could not be read.": _DUMP,
    "The dump holds no migration records.": _DUMP,
    "The dump's migration records are malformed.": _DUMP,
    "The manifest does not describe this dump.": (
        "the manifest and the dump come from different sets: pass the two "
        "files from the same set directory"
    ),
}
# Any other failure (a missing or unreadable file, say) is reported without
# its text, which could name a path.
GENERIC_ADVICE = (
    "check that the files exist and that this user can read them; pass the "
    "set's manifest.json and, for a set from before this check, its "
    "decrypted database.pgdump"
)


class RestoreCheckRefused(ConfigError):
    """The inputs cannot be compared; the reason is a fixed sentence in
    ``ADVICE``, which says what to do about it."""


def _migration_set(value):
    """A manifest's ``migrations`` list as a set of ``(app, name)`` pairs."""
    if type(value) is not list or not value:
        raise RestoreCheckRefused("The manifest's migration list is malformed.")
    pairs = set()
    for item in value:
        if (
            type(item) is not list
            or len(item) != 2
            or not all(type(part) is str for part in item)
            or not APP.match(item[0])
            or not NAME.match(item[1])
        ):
            raise RestoreCheckRefused("The manifest's migration list is malformed.")
        pairs.add((item[0], item[1]))
    if len(pairs) != len(value):
        raise RestoreCheckRefused("The manifest's migration list is malformed.")
    return frozenset(pairs)


def read_manifest(path):
    """The facts a manifest records about what its set needs.

    Returns ``application_version``, ``image`` (None for a set taken before
    #608) and ``migrations`` (a set of pairs, or None likewise). Anything
    else in the manifest is the backup's own business and is not checked.
    """
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise RestoreCheckRefused("The manifest is too large.")
    try:
        manifest = json.loads(raw)
    except ValueError:
        raise RestoreCheckRefused("The manifest is not JSON.") from None
    if type(manifest) is not dict or manifest.get("version") != 1:
        raise RestoreCheckRefused("The manifest has an unknown shape.")
    version = manifest.get("application_version")
    if type(version) is not str or not VERSION.match(version):
        raise RestoreCheckRefused("The manifest has an unknown shape.")
    image = manifest.get("image")
    if image is not None and (type(image) is not str or not IMAGE.match(image)):
        raise RestoreCheckRefused("The manifest's image is malformed.")
    migrations = manifest.get("migrations")
    return {
        "application_version": version,
        "image": image,
        "migrations": None if migrations is None else _migration_set(migrations),
    }


def dump_migrations(path, *, seconds=DUMP_READ_SECONDS):
    """The ``(app, name)`` rows of a decrypted dump's ``django_migrations``.

    ``pg_restore`` writes that one table's data as SQL to its standard
    output, with no database; the rows are read from its ``COPY`` block.
    It is killed at ``seconds``, and the kill is logged with that limit and
    the time it ran (the timeout rule). Its own messages are discarded:
    they can quote the dump.
    """
    binary = shutil.which("pg_restore")
    if binary is None:
        raise RestoreCheckRefused("pg_restore is not installed in this image.")
    command = [
        binary,
        "--data-only",
        "--table=django_migrations",
        "--file=-",
        str(Path(path)),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=seconds,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run has killed and reaped pg_restore by now.
        emit(
            Event.TASK_TIMED_OUT,
            level=logging.WARNING,
            timeout=DUMP_READ,
            limit_seconds=int(seconds),
            elapsed_seconds=int(time.monotonic() - started),
        )
        raise RestoreCheckRefused("Reading the dump took too long.") from None
    if completed.returncode != 0:
        raise RestoreCheckRefused("The dump could not be read.")
    return _copied_rows(completed.stdout.decode("utf-8", "replace").splitlines())


def _copied_rows(lines):
    """Parse the one ``COPY public.django_migrations`` block into pairs.

    The columns are found by name in the block's header, so a column order
    other than Django's own still reads correctly. A dump with no such block,
    an unterminated one, or a row this code base could not have written is
    refused rather than guessed at.
    """
    found = [
        (index, header)
        for index, line in enumerate(lines)
        if (header := COPY.match(line))
    ]
    if len(found) != 1:
        raise RestoreCheckRefused("The dump holds no migration records.")
    [(index, header)] = found
    columns = [column.strip() for column in header[1].split(",")]
    if "app" not in columns or "name" not in columns:
        raise RestoreCheckRefused("The dump's migration records are malformed.")
    app, name = columns.index("app"), columns.index("name")
    pairs = set()
    for line in lines[index + 1 :]:
        if line == "\\.":
            break
        fields = line.split("\t")
        if (
            len(fields) != len(columns)
            or not APP.match(fields[app])
            or not NAME.match(fields[name])
        ):
            raise RestoreCheckRefused("The dump's migration records are malformed.")
        pairs.add((fields[app], fields[name]))
    else:
        raise RestoreCheckRefused("The dump's migration records are malformed.")
    if not pairs:
        raise RestoreCheckRefused("The dump holds no migration records.")
    return frozenset(pairs)


def _pairs(values):
    """Sorted ``[app, name]`` lists, as the report prints them."""
    return [list(pair) for pair in sorted(values)]


def check(manifest, *, dump=None, image_migrations=None):
    """Compare one set with this image; return (exit code, report).

    ``image_migrations`` stands in for the image's own set in tests; by
    default it is read from this image's migration files.
    """
    facts = read_manifest(manifest)
    recorded = facts["migrations"]
    dumped = None if dump is None else dump_migrations(dump)
    if recorded is not None and dumped is not None and recorded != dumped:
        raise RestoreCheckRefused("The manifest does not describe this dump.")
    applied = recorded if recorded is not None else dumped
    if applied is None:
        raise RestoreCheckRefused(
            "This set's manifest lists no migrations; pass its decrypted dump."
        )
    if image_migrations is None:
        from .upgrade_check import code_settings, disk_migrations

        code_settings()
        image_migrations = disk_migrations()
    image = frozenset(tuple(pair) for pair in image_migrations)
    not_in_backup = image - applied
    unknown_to_image = applied - image
    matched = not not_in_backup and not unknown_to_image
    report = {
        "result": "match" if matched else "mismatch",
        "backup": {
            "application_version": facts["application_version"],
            "image": facts["image"],
            "migrations_from": "dump" if recorded is None else "manifest",
        },
        "this_image": {"application_version": __version__},
        # Migrations this image has and the set lacks: restored here, the
        # set would need forward migrations, which a restore never runs.
        "not_in_backup": _pairs(not_in_backup),
        # Migrations the set has and this image does not know: this is the
        # wrong image, usually an older release than the set's.
        "unknown_to_image": _pairs(unknown_to_image),
    }
    if not matched:
        # The set's own image is the one that matches it. A set from before
        # #608 does not name it; the operators' notes record it.
        report["use_image"] = facts["image"]
    return (MATCH if matched else MISMATCH), report


def execute_restore_check(args):
    """``restore-check --manifest PATH [--dump PATH]``: print the report.

    Exits 0 on a match, 3 on a mismatch, and 2 with one fixed line when the
    inputs cannot be compared: this module's own reason and its advice from
    ``ADVICE``, or ``GENERIC_ADVICE`` (the process log names only the
    reviewed failure category, never a path or file content).
    """
    configure_logging()
    try:
        if args.manifest is None:
            raise RestoreCheckRefused("An explicit manifest is required.")
        code, report = check(args.manifest, dump=args.dump)
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        # A refusal of this module's own is one of its fixed sentences, which
        # name no path and quote no file, with its own advice; anything else
        # stays generic.
        if type(error) is RestoreCheckRefused and str(error) in ADVICE:
            line = f"{str(error).rstrip('.')}; {ADVICE[str(error)]}"
        else:
            line = GENERIC_ADVICE
        print(f"ERROR: restore check refused: {line}", file=sys.stderr)
        return REFUSED
    print(json.dumps(report, sort_keys=True))
    return code
