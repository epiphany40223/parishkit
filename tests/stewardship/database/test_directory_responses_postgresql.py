"""The directory's Response filters read the dashboard's own funnel rows (#933).

The campaign is the response lists' ``quality_funnel`` (five eligible
Families, Family 11 with a blank mailing name and 12 with envelope number 0),
taken through the real paths into Production: Family 14 submits before its
invitation is sent (so it is skipped), the other four are invited, Family 1
opens its form and gets past the first step, Family 11 submits twice, 12
only follows its link and 13 does nothing. Family 2 is the corpus' memberless
Family: a campaign Family that is never active and registered.

Checked here, as the restricted web login:

- ``stewardship_family_response_v1`` returns exactly what the inline
  statement it replaced returned, in Production and Testing and at earlier
  cutoffs (the parity the dashboard depends on);
- each Response choice lists the Families the matching response list chose
  from the same rows, so its count is the dashboard's;
- the data checks, the response columns, the response sort orders and the
  "no longer active" population;
- an export capture selects the same rows through v2, keeps the envelope for
  the code list and its response instants.
"""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import directory_exports
from parishkit.stewardship.reports.directories import (
    RESPONSES,
    DirectoryQuery,
    selection_parameters,
)
from parishkit.stewardship.reports.directory_exports import create_directory_export
from parishkit.stewardship.reports.directory_query import DIRECTORY
from parishkit.stewardship.reports.export_models import DirectoryExportSnapshot
from parishkit.stewardship.reports.response_lists import LISTS, ListedFamily
from parishkit.stewardship.reports.response_metrics import MODES
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.snapshots import promote_snapshot

from . import test_response_metrics_postgresql as metrics_tests
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_directories_postgresql import page
from .test_policy_postgresql import user
from .test_response_lists_postgresql import (  # noqa: F401
    BLANK_MAILING,
    ENVELOPE_ZERO,
    funnel,
    quality_funnel,
)
from .test_response_metrics_postgresql import (
    dispatch_all,
    live_login,
    open_form,
    prepare_all,
    progress_to,
    read,
    respond,
)
from .test_source_families_postgresql import prepare, promote
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

# The statement response_metrics ran inline before migration 0043, verbatim:
# the function must return exactly its rows.
LEGACY = """
WITH lifetime AS (
    SELECT e.created_at AS started_at,
        coalesce(e.invalidated_at, 'infinity'::timestamptz) AS ended_at
    FROM stewardship_rehearsal_epoch e WHERE e.id=%(epoch)s
    UNION ALL
    SELECT '-infinity'::timestamptz, 'infinity'::timestamptz
    WHERE %(epoch)s::uuid IS NULL
), invited AS (
    SELECT m.family_id, min(m.finished_at) AS at
    FROM stewardship_outbox_message m
    WHERE m.campaign_id=%(campaign)s AND m.purpose='initial'
      AND m.mode=%(mail_mode)s
      AND m.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
      AND m.state='delivered' AND m.finished_at<=%(as_of)s
    GROUP BY m.family_id
), skipped AS (
    SELECT DISTINCT o.target
    FROM stewardship_schedule_definition d
    JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id
    JOIN stewardship_occurrence_transition t ON t.occurrence_id=o.id
    JOIN lifetime l ON t.created_at>=l.started_at AND t.created_at<l.ended_at
    WHERE d.campaign_id=%(campaign)s AND d.kind='initial'
      AND o.mode=%(mail_mode)s
      AND t.after_state='skipped' AND t.reason='family_responded'
      AND t.created_at<=%(as_of)s
), responded AS (
    SELECT s.family_id, min(s.submitted_at) AS at, count(*) AS submissions,
        max(s.submitted_at) AS last_at
    FROM stewardship_submission s
    WHERE s.campaign_id=%(campaign)s AND s.mode=%(response_mode)s
      AND s.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
      AND s.submitted_at<=%(as_of)s
    GROUP BY s.family_id
)
SELECT f.id, f.family_duid, i.at, k.target IS NOT NULL,
    CASE WHEN e.first_link_at<=%(as_of)s THEN e.first_link_at END,
    CASE WHEN e.first_form_at<=%(as_of)s THEN e.first_form_at END,
    CASE WHEN e.first_progress_at<=%(as_of)s THEN e.first_progress_at END,
    r.at, coalesce(r.submissions, 0), r.last_at
FROM stewardship_family_campaign f
LEFT JOIN invited i ON i.family_id=f.id
LEFT JOIN skipped k ON k.target='family:'||f.id::text
LEFT JOIN stewardship_family_engagement e ON e.family_id=f.id
    AND e.mode=%(response_mode)s
    AND e.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
LEFT JOIN responded r ON r.family_id=f.id
WHERE f.campaign_id=%(campaign)s
ORDER BY f.family_duid, f.id
"""
FUNCTION = (
    "SELECT * FROM stewardship_family_response_v1(%(campaign)s, %(mode)s, "
    "%(epoch)s, %(as_of)s) ORDER BY family_duid, family_id"
)

# Each Response choice and the response list (and its Show choice) that chose
# the same Families from the same funnel rows (reports.response_lists).
LIST_OF = {
    "submitted": ("submitted", "all"),
    "more-than-once": ("more-than-once", "all"),
    "started": ("started", "all"),
    "progressed": ("started", "progressed"),
    "opened-only": ("started", "opened"),
    "never-opened": ("not-opened", "all"),
    "link-followed": ("not-opened", "followed"),
    "link-not-followed": ("not-opened", "unfollowed"),
}


def parity(harness, as_of, mode="production", epoch=None):
    """The function's rows and the legacy statement's, read as the web login."""
    values = {
        "campaign": harness.campaign.pk,
        "mode": mode,
        "epoch": epoch,
        "as_of": as_of,
        "response_mode": MODES[mode][0],
        "mail_mode": MODES[mode][1],
    }
    with (
        task_login(ServiceRole.WEB, exact=True),
        read_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(LEGACY, values)
        legacy = cursor.fetchall()
        cursor.execute(FUNCTION, values)
        installed = cursor.fetchall()
    return legacy, installed


def listed(spec_key, show, families):
    """The DUIDs a response list chose from the funnel rows (its old rule)."""
    spec = LISTS[spec_key]
    keeps = spec.choice(show).keeps
    return {
        family.family_duid
        for family in families
        if spec.selects(family) and keeps(ListedFamily(family))
    }


def duids(report):
    """The listed Family DUIDs, in the page's order."""
    return [row["family_duid"] for row in report["rows"]]


def legacy_parameters(response):
    """Parameters as the release before v2 built them, with its yes or no.

    No data check, response columns, mode or epoch: an application rolled
    back to that release still captures exports through v2 (#933 M2).
    """
    return {
        "filters": {
            "search": "",
            "reason": "any",
            "phone": "any",
            "response": response,
            "sort": "duid",
            "reach": "any",
        },
        "postal": False,
        "exact": False,
        "family_id": None,
    }


def legacy_duids(harness, response):
    """The DUIDs v2 lists for the earlier release's yes or no, as the web login."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            DIRECTORY,
            (harness.campaign.pk, json.dumps(legacy_parameters(response)), None),
        )
        return duids(json.loads(cursor.fetchone()[0]))


def test_directory_response_filters_follow_the_dashboard_rows(
    quality_funnel,  # noqa: F811
    monkeypatch,
):
    """Every Response choice lists the dashboard's Families, as the lists did."""
    harness, epoch = quality_funnel
    families = {
        duid: pk
        for duid, pk in FamilyCampaign.objects.values_list("family_duid", "pk")
        if duid in metrics_tests.EVERYONE
    }
    initial = ScheduleDefinition.objects.get(kind="initial")
    due = initial.current_revision.due_at

    # Testing: the rehearsal Family opens its form and submits; the function
    # reads it for that epoch only, exactly as the inline statement did.
    open_form(harness)
    respond(harness)
    testing = parity(harness, database_now(), "testing", epoch)
    assert testing[0] == testing[1] and any(row[7] for row in testing[1])
    # Another epoch sees none of it, in both.
    legacy, installed = parity(harness, database_now(), "testing", uuid4())
    assert legacy == installed and installed
    assert all(
        row[2:] == (None, False, None, None, None, None, 0, None) for row in installed
    )
    # Production saw nothing yet, in both.
    legacy, installed = parity(harness, database_now())
    assert legacy == installed and not any(row[7] for row in installed)

    # Production, through the real paths (see the module docstring).
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(due):
        messages = prepare_all(harness, "production")
    early = live_login(harness, 14)
    open_form(early)
    respond(early)
    before_dispatch = database_now()
    dispatch_all(harness, messages, families, due)
    open_form(harness)
    progress_to(harness, "ministry")
    second = live_login(harness, 11)
    open_form(second)
    respond(second)
    respond(live_login(harness, 11))
    live_login(harness, 12)
    settled = database_now()

    # The function is the inline statement, now and at earlier cutoffs.
    for as_of in (before_dispatch, settled):
        legacy, installed = parity(harness, as_of)
        assert legacy == installed and len(installed) == 6
    metrics = read(harness, settled)
    rows = metrics.families
    assert metrics.stage("submitted") == 2 and metrics.submitted_again == 1

    # Each Response choice lists what its response list listed, so its count
    # equals the dashboard's figure; nothing else changes the rows.
    expected = {response: listed(*LIST_OF[response], rows) for response in LIST_OF}
    assert expected == {
        "submitted": {11, 14},
        "more-than-once": {11},
        "started": {1},
        "progressed": {1},
        "opened-only": set(),
        "never-opened": {12, 13},
        "link-followed": {12},
        "link-not-followed": {13},
    }
    for response in RESPONSES:
        report = page(harness, response=response)
        if response in expected:
            assert set(duids(report)) == expected[response], response
            assert report["total"] == len(expected[response])
        assert report["metadata"]["counted_at"] is not None
    # No invitation delivered: active with a campaign record, no delivered invitation,
    # form never opened. Family 14 submitted (so opened) and Family 2 is not active,
    # so none here. Not submitted is every active Family without a submission, and
    # Started, Invited never opened and No invitation delivered split it with no
    # overlap.
    assert page(harness, response="not-invited")["total"] == 0
    assert set(duids(page(harness, response="not-submitted"))) == {1, 12, 13}
    assert expected["started"] | expected["never-opened"] == {1, 12, 13}
    assert not expected["started"] & expected["never-opened"]
    # The earlier release's yes and no still select (M2), as Submitted and
    # Not submitted over the active Families.
    assert legacy_duids(harness, "yes") == [11, 14]
    assert legacy_duids(harness, "no") == [1, 12, 13]
    assert page(harness)["metadata"]["counted_at"] is None
    assert page(harness)["total"] == 5

    # The response columns carry the funnel instants and sort both ways,
    # missing values last; the default order is still by name.
    report = page(harness, response="submitted", responses="yes", sort="submitted")
    assert duids(report) == [14, 11]
    first = {row["family_duid"]: row for row in report["rows"]}
    assert first[11]["submissions"] == 2 and first[14]["submissions"] == 1
    assert first[11]["submitted_at"] < first[11]["last_submitted_at"]
    assert first[14]["invited_at"] is None and first[11]["invited_at"]
    assert duids(
        page(harness, response="submitted", responses="yes", sort="submitted_desc")
    ) == [11, 14]
    assert duids(
        page(harness, response="submitted", responses="yes", sort="submissions_desc")
    ) == [11, 14]
    everyone = page(harness, responses="yes", sort="invited")
    assert duids(everyone)[-1] == 14  # never invited: last either way
    assert duids(page(harness, responses="yes", sort="invited_desc"))[-1] == 14
    assert everyone["metadata"]["counted_at"] is not None
    # A response column orders the rows only while it is shown: with the
    # columns off, or a column the choice does not show, it is by name.
    assert page(harness, sort="invited_desc")["metadata"]["counted_at"] is None
    by_name = duids(page(harness))
    assert duids(page(harness, sort="invited_desc")) == by_name
    hidden = page(harness, response="submitted", responses="yes", sort="invited_desc")
    assert duids(hidden) == duids(page(harness, response="submitted"))

    # ParishSoft data to check: the launch-day problems, active Families only.
    assert duids(page(harness, check="mailing-name")) == [BLANK_MAILING]
    assert duids(page(harness, check="envelope")) == [ENVELOPE_ZERO]
    assert set(duids(page(harness, check="anything"))) == {
        BLANK_MAILING,
        ENVELOPE_ZERO,
    }
    combined = page(harness, check="anything", response="never-opened")
    assert duids(combined) == [ENVELOPE_ZERO]

    # An export captures the same selection through v2, with the response
    # instants, and keeps the envelope number for the code list.
    actor = user("admin@example.org").pk
    query = DirectoryQuery(response="never-opened", responses="yes", sort="duid")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_directory_export(
            harness.service.store,
            actor,
            campaign_id=harness.campaign.pk,
            query=query,
            postal=False,
            mac=harness.rings.mac,
            format="csv",
            browser_timezone="UTC",
            request_key=uuid4(),
        )
        captured = DirectoryExportSnapshot.objects.get(pk=request.directory_snapshot_id)
    assert captured.row_count == 2 and captured.parameters["responses"] is True
    assert [row["family_duid"] for row in captured.document["rows"]] == [12, 13]
    assert captured.document["metadata"]["counted_at"]
    by_duid = {row["family_duid"]: row for row in captured.document["rows"]}
    assert by_duid[12]["link_at"] and by_duid[13]["link_at"] is None
    assert by_duid[12]["envelope"] == "0" and by_duid[12]["address"] == {}

    # The previous release's application, restored after this migration,
    # still captures its yes and no through the replaced trigger (M2).
    for response, expected_rows in (("yes", [11, 14]), ("no", [1, 12, 13])):
        monkeypatch.setattr(
            directory_exports,
            "selection_parameters",
            lambda *args, response=response, **kwargs: legacy_parameters(response),
        )
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            request = create_directory_export(
                harness.service.store,
                actor,
                campaign_id=harness.campaign.pk,
                query=DirectoryQuery(),
                postal=False,
                mac=harness.rings.mac,
                format="csv",
                browser_timezone="UTC",
                request_key=uuid4(),
            )
            legacy = DirectoryExportSnapshot.objects.get(
                pk=request.directory_snapshot_id
            )
        rows = [row["family_duid"] for row in legacy.document["rows"]]
        assert rows == expected_rows, response

    # A Family the current data no longer has: a Response list still lists
    # it (marked), so the count stays the dashboard's; Any does not.
    data = metrics_tests.funnel_source()
    del data.families[11]
    del data.members[111]
    snapshot, claim = prepare(data)
    # The activation dated eligibility by the pinned campaign clock; the
    # refresh comes after it, as it would in Production.
    with campaign_clock(database_now() + timedelta(days=1)):
        promote(snapshot, claim, harness.campaign, harness.rings)
    submitted = page(harness, response="submitted", responses="yes")
    assert set(duids(submitted)) == {11, 14} and submitted["total"] == 2
    gone = next(row for row in submitted["rows"] if row["family_duid"] == 11)
    assert gone["active"] is False and gone["reason"] == "inactive"
    assert gone["family_name"] is None and gone["display_name"]
    assert not gone["email_deliverable"] and not gone["mailable"]
    assert gone["submissions"] == 2
    assert 11 not in duids(page(harness))
    assert page(harness)["active_total"] == 4
    # The active-only choices leave it out: the earlier yes, Not submitted.
    assert legacy_duids(harness, "yes") == [14]
    assert 11 not in duids(page(harness, response="not-submitted"))
    # Campaign mail reaches an inactive Family neither way.
    assert duids(page(harness, response="submitted", reach="neither")) == [11]
    assert page(harness, response="submitted", reach="email")["total"] <= 1

    # An active Family with no campaign record: ParishSoft lists it, but the
    # source refresh has not given it a Family code for this campaign (the
    # promotion here skips the campaign's reconcile), so no invitation could
    # be sent. Not submitted lists it, as the old Not yet responded did. No
    # invitation delivered is a response status, not a postal list, and
    # leaves it out (#951).
    data.families[30] = dict(
        data.families[1], familyDUID=30, familyID=130, lastName="Family30"
    )
    data.members[130] = dict(
        data.members[3], memberDUID=130, familyDUID=30, firstName="Head30"
    )
    snapshot, claim = prepare(data)
    with work_transaction():
        promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=lambda _: True)
    release_source(claim)
    assert not FamilyCampaign.objects.filter(
        campaign_id=harness.campaign.pk, family_duid=30
    ).exists()
    pending = page(harness, response="not-submitted")
    unrecorded = next(row for row in pending["rows"] if row["family_duid"] == 30)
    assert unrecorded["family_id"] is None and unrecorded["active"] is True
    assert 30 not in duids(page(harness, response="not-invited"))
    assert page(harness, response="not-invited")["total"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"filters": {"response": "Submitted"}},
        {"filters": {"response": "maybe"}},
        {"filters": {"check": "blank"}},
        {"filters": {"check": 1}},
        {"filters": {"sort": "-submitted"}},
        {"responses": "yes"},
        {"mode": "live"},
        {"mode": None},
        {"mode": "testing"},
        {"epoch": str(uuid4())},
        {"mode": "testing", "epoch": "not-a-uuid"},
        {"extra": True},
    ],
)
def test_the_selection_refuses_unknown_choices(change):
    """v2's closed vocabulary: unknown choices fail before anything is read."""
    parameters = {
        "filters": {
            "search": "",
            "reason": "any",
            "phone": "any",
            "response": "any",
            "sort": "name",
        },
        "postal": False,
        "exact": False,
        "family_id": None,
    }
    for key, value in change.items():
        if key == "filters":
            parameters["filters"] |= value
        else:
            parameters[key] = value
    with (
        task_login(ServiceRole.WEB, exact=True),
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="Invalid directory report parameters"),
        transaction.atomic(),
    ):
        cursor.execute(DIRECTORY, (uuid4(), json.dumps(parameters), 1))


@pytest.mark.parametrize(
    "mode, epoch",
    [("live", None), ("production", uuid4()), ("testing", None), (None, None)],
)
def test_the_funnel_refuses_a_mode_that_does_not_fit(mode, epoch):
    """A Testing read names its epoch; Production has none; nothing else."""
    with (
        task_login(ServiceRole.WEB, exact=True),
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="Invalid response funnel parameters"),
        transaction.atomic(),
    ):
        cursor.execute(
            "SELECT * FROM stewardship_family_response_v1(%s, %s, %s, now())",
            (uuid4(), mode, epoch),
        )


@pytest.mark.parametrize("role", [ServiceRole.WEB, ServiceRole.WORKER])
def test_web_and_worker_logins_may_read_the_funnel(role):
    """The pages (web) and the daily digest (worker) both read the function.

    Both are invoker functions, so each login also needs the tables the
    funnel reads; an unknown campaign reads no rows rather than failing.
    """
    with task_login(role, exact=True), connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege(current_user, "
            "'stewardship_family_response_v1(uuid,text,uuid,timestamptz)', "
            "'EXECUTE'), has_function_privilege(current_user, "
            "'stewardship_directory_report_v2(uuid,jsonb,integer)', 'EXECUTE')"
        )
        assert cursor.fetchone() == (True, True)
        cursor.execute(
            "SELECT count(*) FROM stewardship_family_response_v1"
            "(%s, 'production', NULL, now())",
            (uuid4(),),
        )
        assert cursor.fetchone() == (0,)


def test_the_funnel_runs_only_when_a_choice_needs_it(response_service):
    """No Response filter, column or order: the directory never reads it.

    Counted by PostgreSQL's per-transaction function statistics, so the
    one-time filter inside the installed selection is what is measured.
    """
    harness = response_service

    def calls(query):
        """How many times one directory read ran the funnel function."""
        parameters = selection_parameters(
            harness.campaign.pk, query, postal=False, mac=None
        )
        counted = (
            "SELECT coalesce(sum(calls), 0) FROM pg_stat_xact_user_functions "
            "WHERE funcname='stewardship_family_response_v1'"
        )
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL track_functions = 'all'")
            # Pending statistics can outlive a transaction until they are
            # flushed, so count the difference this read makes.
            cursor.execute(counted)
            before = cursor.fetchone()[0]
            cursor.execute(DIRECTORY, (harness.campaign.pk, json.dumps(parameters), 1))
            assert json.loads(cursor.fetchone()[0])["active_total"] >= 1
            cursor.execute(counted)
            return cursor.fetchone()[0] - before

    assert calls(DirectoryQuery()) == 0
    assert calls(DirectoryQuery(check="anything", reach="email")) == 0
    assert calls(DirectoryQuery(responses="yes")) == 1
    # A hidden response column's order falls back to the name order.
    assert calls(DirectoryQuery(sort="submitted_desc")) == 0
    assert calls(DirectoryQuery(responses="yes", sort="submitted_desc")) == 1
    assert calls(DirectoryQuery(response="never-opened")) == 1
