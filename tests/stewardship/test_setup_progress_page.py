"""The setup load progress page names each situation in plain language."""

import pytest

from parishkit.stewardship.accounts.setup_progress_views import SUMMARIES, summary


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        (
            {"setup_state": "loading", "task_state": "running", "phase": "fetching"},
            "fetching",
        ),
        (
            {"setup_state": "loading", "task_state": "running", "phase": "odd"},
            "working",
        ),
        (
            {"setup_state": "loading", "task_state": "queued", "phase": "unspecified"},
            "queued",
        ),
        (
            {
                "setup_state": "collecting",
                "task_state": "succeeded",
                "phase": "validating",
            },
            "done",
        ),
        (
            {"setup_state": "expired", "task_state": "running", "phase": "fetching"},
            "failed",
        ),
        (
            {"setup_state": "loading", "task_state": "failed", "phase": "staging"},
            "failed",
        ),
    ],
)
def test_progress_summary_names_each_situation(progress, expected):
    """The server and the polling script share one plain-language vocabulary."""
    assert summary(progress) == expected
    assert expected in SUMMARIES
