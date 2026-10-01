"""Reproduce the configuration activation window (#429) against real storage."""

import logging
from contextlib import contextmanager
from threading import Timer
from uuid import uuid4

from parishkit.stewardship.accounts.authority import parse_version


@contextmanager
def activation_window(store, *, closes_after=None):
    """Select a successor YAML one activation ahead of SQL, as apply_version does.

    The successor is written and selected but never activated, which is the
    state between ``store.select`` and the database activation's commit. After
    ``closes_after`` seconds a timer selects the base again: to every reader
    that ends the window just as the activation's commit would, since YAML and
    SQL agree once more. With ``None`` the window stays open for the block.
    """
    base = store.active()
    document = base.document()
    document["version_id"] = str(uuid4())
    document["predecessor_digest"] = base.digest
    candidate = parse_version(document, validate_sections=store.validate_sections)
    store.write_version(candidate)
    store.select(candidate)
    timer = None
    if closes_after is not None:
        timer = Timer(closes_after, store.select, args=(base,))
        timer.start()
    try:
        yield candidate
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        store.select(base)


def errors(caplog):
    """The ERROR (or worse) lines logged by the application or Django."""
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR
        and record.name.startswith(("parishkit", "django"))
    ]


def recorded_holds(monkeypatch):
    """Count the background admissions that met the activation window.

    Proves a test's window was really observed by the worker, rather than
    opening and closing between two admissions.
    """
    from parishkit.stewardship import runtime_background
    from parishkit.stewardship.accounts.authority import AuthorityChanging

    original, holds = runtime_background.matching_authority, []

    def matching(store):
        """Delegate, noting each AuthorityChanging on its way out."""
        try:
            return original(store)
        except AuthorityChanging:
            holds.append(1)
            raise

    monkeypatch.setattr(runtime_background, "matching_authority", matching)
    return holds
