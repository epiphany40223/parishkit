"""The comparison read logs a database error every retry repeats (#1286)."""

import logging

import pytest
from django.db import DataError, OperationalError

from parishkit.stewardship.observability import Event
from parishkit.stewardship.reports.comparison_reads import log_read_failure


@pytest.mark.parametrize(
    ("error", "logged"),
    [(DataError("time zone not recognized"), True), (OperationalError("gone"), False)],
)
def test_a_persistent_comparison_database_error_is_logged(caplog, error, logged):
    """A refusal every retry repeats is logged by class; a lost connection is not."""
    with caplog.at_level(logging.ERROR, logger="parishkit.stewardship"):
        log_read_failure(error)
    events = [
        record for record in caplog.records if record.msg is Event.REPORT_SHAPING_FAILED
    ]
    assert bool(events) is logged
    if logged:
        assert events[0].extra["error_class"] == "django.db.utils.DataError"
        assert "not recognized" not in caplog.text
