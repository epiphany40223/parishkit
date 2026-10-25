"""Finishing setup says why the final parish data load failed (#2201).

Since the quick parish data check replaced the setup load, the final load is
setup's only full load, so problems a first load used to find show there:
the failed step names the kind from the load's closed failure word and gives
the way back to the ParishSoft connection.
"""

import pytest


@pytest.mark.parametrize(
    "source,first",
    [
        ({"failure": "organization_mismatch"}, "ParishSoft did not accept"),
        (
            {"failure": "provider_status", "failure_status": 401},
            "ParishSoft did not accept",
        ),
        (
            {"failure": "provider_status", "failure_status": 503},
            "ParishSoft did not answer",
        ),
        (
            {"failure": "provider_status", "failure_status": 429},
            "ParishSoft did not answer",
        ),
        # Another error answer: ParishSoft did answer, so no "in time".
        (
            {"failure": "provider_status", "failure_status": 404},
            "The final parish data load did not finish.",
        ),
        (
            {"failure": "destructive_change"},
            "ParishSoft's parish data could not be used",
        ),
        ({"failure": "invalid_payload"}, "ParishSoft's parish data could not be used"),
        ({"failure": "shifted_scan"}, "ParishSoft's parish data could not be used"),
        ({"failure": "provider_timeout"}, "ParishSoft did not answer in time."),
        (
            {"failure": "lease_unavailable"},
            "The final parish data load did not finish.",
        ),
        ({}, "The final parish data load did not finish."),
        (None, "The final parish data load did not finish."),
    ],
)
def test_a_failed_final_load_says_what_went_wrong_and_the_way_back(source, first):
    """Each failure word names its kind, then the way to ParishSoft connection."""
    from parishkit.stewardship.accounts.setup_finishing import source_failed

    text = str(source_failed(source))
    assert text.startswith(first)
    assert "Cancel this setup, then start a new setup. At its ParishSoft" in text
    assert "check the parish data again before you finish" in text
