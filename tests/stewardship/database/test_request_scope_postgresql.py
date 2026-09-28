"""A request verifies an unchanged configuration corpus once, not per transaction."""

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
)
from parishkit.stewardship.request_scope import request_scope, verified_configurations

from .auth_builders import auth_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def _queries(store):
    """Run one coherence check in its own transaction and count its statements."""
    with CaptureQueriesContext(connection) as queries, transaction.atomic():
        runtime = coherent_configuration(store)
    statements = [
        query
        for query in queries.captured_queries
        if query["sql"] not in {"BEGIN", "COMMIT"}
    ]
    return runtime, len(statements)


def test_request_scope_verifies_corpus_once_but_selection_every_time(
    auth_service,  # noqa: F811
):
    """Later transactions of a request re-read only the live selection row."""
    store = auth_service.store
    _, outside = _queries(store)
    _, again = _queries(store)
    # Outside a request nothing is remembered: every call verifies in full.
    assert outside == again > 1
    assert verified_configurations() is None
    with request_scope():
        first, full = _queries(store)
        second, cheap = _queries(store)
        third, cheap_again = _queries(store)
    assert full == outside
    # A new transaction still re-reads SystemConfiguration (restore, mode and
    # campaign pointers stay live) but not the immutable projections.
    assert cheap == cheap_again == 1
    assert second.active_configuration is first.active_configuration
    assert third.pk == first.pk
    # The memory ended with the request; the next request verifies in full.
    assert verified_configurations() is None
    _, after = _queries(store)
    assert after == outside


def test_request_scope_never_masks_a_changed_selection(
    auth_service,  # noqa: F811
    monkeypatch,
):
    """A remembered corpus still fails when the manifest selection has moved."""
    store = auth_service.store
    real = store.manifest_reference
    with request_scope():
        _queries(store)
        reference = real()
        reads = []

        def moved_after_first_read():
            """store.active() sees the real selection; the final re-read moved."""
            reads.append(None)
            return reference if len(reads) == 1 else (reference[0], "0" * 64)

        # A concurrent activation between the two manifest reads of a call that
        # finds its corpus remembered: only the final re-read disagrees.
        monkeypatch.setattr(store, "manifest_reference", moved_after_first_read)
        with (
            CaptureQueriesContext(connection) as queries,
            pytest.raises(ConfigError),
            transaction.atomic(),
        ):
            coherent_configuration(store)
        assert len(reads) == 2
        # The remembered path was taken: no corpus (intake) query ran.
        assert not any(
            "stewardship_configuration_version" in query["sql"]
            for query in queries.captured_queries
        )
