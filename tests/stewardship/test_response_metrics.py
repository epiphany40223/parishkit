"""Response funnel arithmetic (#477), without a database.

The per-Family rows come from one statement (tested against PostgreSQL);
here the stage totals, the two separate figures, the activity buckets (local
hours and days, including a repeated autumn hour) and the scope and input
checks are covered on synthetic rows.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.reports.response_metrics import (
    GRAINS,
    LINK_FOLLOWED_NOTE,
    STAGES,
    ActivityBucket,
    FamilyResponse,
    ResponseScope,
    activity_series,
    bucket_start,
    response_metrics,
    stage_counts,
)

NEW_YORK = ZoneInfo("America/New_York")
# 2026-10-03 10:00 New York (EDT), the launch morning.
START = datetime(2026, 10, 3, 14, tzinfo=UTC)


def family(duid, *stages, invited=True, skipped=False, submissions=None):
    """A Family that reached ``stages`` (prefixes of the funnel after invited).

    Each later stage is one minute after the one before it; ``submissions``
    defaults to one when the Family submitted.
    """
    reached = {}
    at = START + timedelta(minutes=duid)
    for stage in ("link_at", "form_at", "progress_at", "submitted_at"):
        reached[stage] = at if stage[:-3] in stages else None
        at += timedelta(minutes=1)
    submitted = reached["submitted_at"] is not None
    return FamilyResponse(
        family_id=uuid4(),
        family_duid=duid,
        invited_at=START if invited else None,
        skipped_responded=skipped,
        submissions=(1 if submitted else 0) if submissions is None else submissions,
        **reached,
    )


ROWS = (
    family(1, "link", "form", "progress", "submitted", submissions=2),
    family(2, "link", "form", "progress", "submitted"),
    family(3, "link"),
    family(4, "link", "form"),
    family(5, "link", "form", "submitted", invited=False, skipped=True),
    family(6, invited=False),
)


def test_stage_counts_are_distinct_families_in_funnel_order():
    """Each row is one Family; a stage counts the rows that reached it."""
    stages = stage_counts(ROWS)
    assert tuple(stage.key for stage in stages) == STAGES
    # Family 5 submitted with no progress recorded: it counts as progressed.
    assert tuple(stage.count for stage in stages) == (4, 5, 4, 3, 3)
    assert [stage.note for stage in stages] == ["", LINK_FOLLOWED_NOTE, "", "", ""]
    # A Family's own stages are the prefix of the funnel it reached, except
    # that a Family may submit without a delivered invitation.
    assert ROWS[0].stages == STAGES
    assert ROWS[2].stages == ("invited", "link_followed")
    assert ROWS[4].stages == (
        "link_followed",
        "form_opened",
        "progressed",
        "submitted",
    )
    assert ROWS[5].stages == ()


def test_a_submission_implies_the_form_was_opened_and_progressed():
    """Submitting counts as opening the form and progressing, not as a link;
    progressing counts as opening the form.

    Progress was not recorded before the engagement record's release and a
    form open can go unrecorded, so a Family that submitted counts in Form
    opened and Progressed at the earlier of the recorded instant and its
    first submission. A Family can sign in with its code instead of its
    link, so Link followed stays as recorded.
    """
    bare = family(8, "submitted")
    assert (bare.link_at, bare.form_at, bare.progress_at) == (None, None, None)
    assert bare.form_opened_at == bare.progressed_at == bare.submitted_at
    assert bare.stages == ("invited", "form_opened", "progressed", "submitted")
    # A recorded instant earlier than the submission is kept.
    early = family(9, "form", "progress", "submitted")
    assert early.form_opened_at == early.form_at < early.submitted_at
    assert early.progressed_at == early.progress_at
    # A recorded instant later than the first submission (a later edit) is not.
    late = replace(early, progress_at=early.submitted_at + timedelta(hours=1))
    assert late.progressed_at == late.submitted_at
    # Without a submission nothing is implied about progress.
    assert family(10, "link", "form").progressed_at is None
    # A heartbeat can record progress with no form open recorded: progress
    # happens on the form, so it counts as Form opened too.
    stepped = family(11, "progress")
    assert stepped.form_at is None and stepped.submitted_at is None
    assert stepped.form_opened_at == stepped.progressed_at == stepped.progress_at
    assert stepped.stages == ("invited", "form_opened", "progressed")
    # From Form opened on, the funnel can only narrow.
    for rows in (ROWS, (*ROWS, bare, early, late, stepped)):
        counts = {stage.key: stage.count for stage in stage_counts(rows)}
        assert counts["form_opened"] >= counts["progressed"] >= counts["submitted"]
    totals = {stage.key: stage.count for stage in stage_counts((bare,))}
    assert totals == {
        "invited": 1,
        "link_followed": 0,
        "form_opened": 1,
        "progressed": 1,
        "submitted": 1,
    }
    # The forms series uses the same instant, so it still adds up.
    (bucket,) = activity_series((bare,), NEW_YORK, "hour")
    assert (bucket.links, bucket.forms, bucket.submissions) == (0, 1, 1)
    (bucket,) = activity_series((stepped,), NEW_YORK, "hour")
    assert (bucket.links, bucket.forms, bucket.submissions) == (0, 1, 0)


def test_series_counts_first_instants_and_adds_up_to_the_totals():
    """Every link, form and submission lands in exactly one local bucket."""
    hourly = activity_series(ROWS, NEW_YORK, "hour")
    assert all(isinstance(bucket, ActivityBucket) for bucket in hourly)
    assert [bucket.start.isoformat() for bucket in hourly] == [
        "2026-10-03T10:00:00-04:00"
    ]
    assert (hourly[0].links, hourly[0].forms, hourly[0].submissions) == (5, 4, 3)
    # Spread over two local days (a submission just after local midnight).
    late = family(7, "link", "form", "submitted")
    late = replace(
        late,
        link_at=datetime(2026, 10, 4, 3, 50, tzinfo=UTC),
        form_at=datetime(2026, 10, 4, 3, 55, tzinfo=UTC),
        submitted_at=datetime(2026, 10, 4, 4, 5, tzinfo=UTC),
    )
    daily = activity_series((*ROWS, late), NEW_YORK, "day")
    assert [bucket.start.isoformat() for bucket in daily] == [
        "2026-10-03T00:00:00-04:00",
        "2026-10-04T00:00:00-04:00",
    ]
    assert (daily[0].links, daily[0].forms, daily[0].submissions) == (6, 5, 3)
    assert (daily[1].links, daily[1].forms, daily[1].submissions) == (0, 0, 1)
    totals = {stage.key: stage.count for stage in stage_counts((*ROWS, late))}
    assert sum(bucket.links for bucket in daily) == totals["link_followed"]
    assert sum(bucket.forms for bucket in daily) == totals["form_opened"]
    assert sum(bucket.submissions for bucket in daily) == totals["submitted"]
    assert activity_series((), NEW_YORK) == ()


def test_a_repeated_autumn_hour_is_two_buckets_in_real_time_order():
    """01:30 EDT and 01:30 EST on 2026-11-01 are an hour apart, not one bucket."""
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT
    second = first + timedelta(hours=1)  # 01:30 EST
    rows = (
        FamilyResponse(uuid4(), 1, None, False, first, None, None, None, 0),
        FamilyResponse(uuid4(), 2, None, False, second, None, None, None, 0),
    )
    starts = [bucket.start for bucket in activity_series(rows, NEW_YORK)]
    assert [start.fold for start in starts] == [0, 1]
    assert [start.astimezone(UTC).hour for start in starts] == [5, 6]
    # The day is one bucket whichever side of the fold its instants fall.
    (day,) = activity_series(rows, NEW_YORK, "day")
    assert (day.start.isoformat(), day.start.fold, day.links) == (
        "2026-11-01T00:00:00-04:00",
        0,
        2,
    )
    assert bucket_start(second, NEW_YORK, "day") == bucket_start(first, NEW_YORK, "day")
    with pytest.raises(ValueError, match="hour or day"):
        bucket_start(first, NEW_YORK, "week")
    assert GRAINS == ("hour", "day")


def test_scope_names_production_or_one_rehearsal():
    """Production has no epoch; Testing needs one; nothing else is a scope."""
    campaign, epoch = uuid4(), uuid4()
    production = ResponseScope(campaign)
    assert (production.mode, production.response_mode, production.mail_mode) == (
        "production",
        "live",
        "production",
    )
    testing = ResponseScope(campaign, "testing", epoch)
    assert (testing.response_mode, testing.mail_mode) == ("test", "testing")
    with pytest.raises(ValueError, match="rehearsal epoch"):
        ResponseScope(campaign, "testing")
    with pytest.raises(ValueError, match="rehearsal epoch"):
        ResponseScope(campaign, "production", epoch)
    with pytest.raises(ValueError, match="production or testing"):
        ResponseScope(campaign, "live")
    with pytest.raises(TypeError):
        ResponseScope(str(campaign))


def test_inputs_are_checked_before_any_database_read():
    """A naive cutoff, an unknown grain or a bare UUID never reach PostgreSQL."""
    scope = ResponseScope(uuid4())
    with pytest.raises(ValueError, match="timezone-aware"):
        response_metrics(scope, datetime(2026, 10, 3, 14))
    with pytest.raises(ValueError, match="hour or day"):
        response_metrics(scope, START, grain="week")
    with pytest.raises(TypeError):
        response_metrics(scope.campaign_id, START)
