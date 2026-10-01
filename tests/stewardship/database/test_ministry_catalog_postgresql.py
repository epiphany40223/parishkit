"""ParishSoft Ministry catalog changes are recorded at staging and shown (#342)."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source import ministry_catalog
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.ministry_catalog import catalog_notice
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .source_builders import source_corpus
from .test_background_grants_postgresql import task_login
from .test_source_attempts_postgresql import setup
from .test_source_compaction_postgresql import cleanup
from .test_source_refreshing_postgresql import (
    fake_provider,
    next_delta,
    run,
    seed_full,
)
from .test_source_snapshots_postgresql import prepared, publish

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000342")

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only idle/empty migration seeds after disposable test flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def corpus(ministries):
    """The synthetic corpus with this Ministry catalog (DUID 3 has a roster)."""
    return source_corpus() | {"ministry": ministries}


def promote_pair(before, after):
    """Promote two consecutive full snapshots with these Ministry catalogs."""
    first, claim = prepared(corpus(before))
    publish(first, claim)
    release_source(claim)
    second, claim = prepared(corpus(after))
    promoted = publish(second, claim)
    release_source(claim)
    return first, promoted


BEFORE = {"3": {"name": "Choir"}, "8": {"name": "Greeters"}}
AFTER = {"3": {"name": "X-Choir"}, "9": {"name": "Lectors"}}


def test_staging_records_added_removed_and_renamed_ministries():
    """The cursor names each change by DUID; a first load records nothing."""
    first, second = promote_pair(BEFORE, AFTER)
    assert "ministry_catalog" not in first.cursor
    assert second.cursor["ministry_catalog"] == {
        "counts": {"added": 1, "removed": 1, "renamed": 1},
        "added": [{"duid": 9, "name": "Lectors"}],
        "removed": [{"duid": 8, "name": "Greeters"}],
        "renamed": [{"duid": 3, "before": "Choir", "name": "X-Choir"}],
    }


def test_an_unchanged_catalog_records_nothing():
    """A refresh whose Ministries did not change adds no notice."""
    _, second = promote_pair(BEFORE, BEFORE)
    assert "ministry_catalog" not in second.cursor


def test_a_failed_catalog_comparison_never_fails_the_refresh(monkeypatch, caplog):
    """The comparison is omitted, with a WARNING, on a real database error.

    The savepoint keeps the refresh's own transaction usable for the ready
    transition and the promotion that follow.
    """
    first, claim = prepared(corpus(BEFORE))
    publish(first, claim)
    release_source(claim)
    names = ministry_catalog.catalog_names

    def broken(snapshot_id):
        """Fail only the base read, with a genuine SQL error."""
        if snapshot_id == first.pk:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1/0")
        return names(snapshot_id)

    monkeypatch.setattr(ministry_catalog, "catalog_names", broken)
    with caplog.at_level("WARNING", logger="parishkit.stewardship"):
        second, claim = prepared(corpus(AFTER))
    assert "ministry_catalog" not in second.cursor
    # The changed-record counts still come from their own comparison.
    assert second.cursor["changes"]["ministry"] == 3
    assert publish(second, claim).state == "promoted"
    (warning,) = [
        r for r in caplog.records if r.getMessage() == "report_shaping_failed"
    ]
    assert warning.levelname == "WARNING" and not warning.exc_info
    # Told apart from the change counts' failure without a new event name.
    assert warning.extra["shaping"] == "ministry_catalog"


def open_campaign(duids, state="active"):
    """A configuration and current campaign carrying only what the notice reads."""
    configuration = SimpleNamespace(
        current_campaign_id=CAMPAIGN,
        active_configuration=SimpleNamespace(
            canonical_document={
                "sections": {
                    "campaigns": [
                        {"id": str(CAMPAIGN), "values": {"ministry_duids": duids}}
                    ]
                }
            }
        ),
    )
    return configuration, SimpleNamespace(pk=CAMPAIGN, state=state)


def test_an_open_campaigns_ministries_are_marked_in_one_query():
    """Missing and "X-" campaign Ministries cost no second query (#417 review).

    Missing DUIDs use a recent removal's name, else the DUID fallback.
    """
    _, second = promote_pair(BEFORE, AFTER)
    configuration, campaign = open_campaign([3, 8, 9, 77])
    with CaptureQueriesContext(connection) as queries:
        notice = catalog_notice(
            configuration, campaign, second.promoted_at, promoted=True
        )
    assert len(queries) == 1
    assert notice["missing"] == [
        {"duid": 8, "name": "Greeters"},
        {"duid": 77, "name": "Ministry 77"},
    ]
    assert notice["retired"] == [{"duid": 3, "name": "X-Choir"}]
    assert notice["campaign_id"] == CAMPAIGN
    (refresh,) = notice["refreshes"]
    assert [row["in_campaign"] for row in refresh["added"]] == [True]
    assert [row["in_campaign"] for row in refresh["removed"]] == [True]
    # A closed campaign marks nothing and needs no attention list.
    configuration, campaign = open_campaign([3, 8, 9, 77], state="closed")
    with CaptureQueriesContext(connection) as queries:
        notice = catalog_notice(
            configuration, campaign, second.promoted_at, promoted=True
        )
    assert len(queries) == 1
    assert notice["missing"] == notice["retired"] == []
    assert notice["campaign_id"] is None
    (refresh,) = notice["refreshes"]
    assert not any(row["in_campaign"] for row in refresh["renamed"])


def test_the_notice_shows_only_recent_refreshes():
    """Changes older than the window drop off; no source means no notice."""
    _, second = promote_pair(BEFORE, AFTER)
    now = second.promoted_at
    assert catalog_notice(None, None, now, promoted=False) is None
    notice = catalog_notice(None, None, now, promoted=True)
    (refresh,) = notice["refreshes"]
    assert [row["duid"] for row in refresh["renamed"]] == [3]
    assert refresh["renamed"][0]["retired"] is True
    assert refresh["more"] == {"added": 0, "removed": 0, "renamed": 0}
    assert notice["missing"] == notice["retired"] == []
    later = now + ministry_catalog.WINDOW + timedelta(minutes=1)
    assert catalog_notice(None, None, later, promoted=True) is None


def test_the_admin_home_page_shows_catalog_changes(auth_service, google):
    """The web role reads the notice under its real grants."""
    promote_pair(BEFORE, AFTER)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        response = browser.get("/admin/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "ParishSoft Ministry changes" in body
    assert "Choir → X-Choir (DUID 3) — possibly retired" in body
    assert "Greeters (DUID 8)" in body and "Lectors (DUID 9)" in body


def test_a_real_delta_refresh_never_compares_the_catalog(tmp_path, monkeypatch):
    """A 15-minute update copies its base catalog, so it is not even read."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed_full(credential, execution, lease)
    execution, lease = next_delta()
    fake_provider(
        monkeypatch,
        [[{"organizationID": 12345}], [], [{"famGroupID": 7, "famGroup": "Active"}]],
    )
    read = []
    names = ministry_catalog.catalog_names
    monkeypatch.setattr(
        ministry_catalog,
        "catalog_names",
        lambda snapshot_id: read.append(snapshot_id) or names(snapshot_id),
    )
    result = run(credential, execution, lease)
    assert result.kind == "delta" and result.state == "ready"
    assert "ministry_catalog" not in result.cursor and read == []


def test_the_notice_survives_compaction_of_its_snapshot():
    """Manifests (and their cursors) outlive their compacted memberships."""
    _, second = promote_pair(BEFORE, AFTER)
    third, claim = prepared(corpus(AFTER))
    third = publish(third, claim)
    release_source(claim)
    assert "ministry_catalog" not in third.cursor
    # The recording snapshot's content equals the current one, so it is
    # redundant and compacted at once.
    while cleanup().snapshot_count:
        pass
    assert SourceSnapshot.objects.get(pk=second.pk).compacted_at is not None
    notice = catalog_notice(None, None, third.promoted_at, promoted=True)
    (refresh,) = notice["refreshes"]
    assert refresh["promoted_at"] == second.promoted_at
    assert [row["duid"] for row in refresh["removed"]] == [8]


def test_staff_do_not_see_the_panel(auth_service, google):
    """Only those who may change Ministry activity see (or pay for) it."""
    promote_pair(BEFORE, AFTER)
    rule = address("staff@example.org", ("staff",))
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        response = browser.get("/admin/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "ParishSoft Ministry changes" not in body and "Lectors" not in body


def test_ministry_names_are_escaped_on_the_page(auth_service, google):
    """ParishSoft names render as text, never markup."""
    evil = '<script>alert("x")</script>'
    promote_pair(
        {"3": {"name": "Choir"}, "8": {"name": evil}},
        {"3": {"name": "X-<b>bold</b>"}, "9": {"name": "<img src=x onerror=1>"}},
    )
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        body = browser.get("/admin/").content.decode()
    assert "ParishSoft Ministry changes" in body
    for raw in ("<script>alert", "<img src=x", "<b>bold</b>"):
        assert raw not in body
    assert "&lt;script&gt;" in body and "&lt;img src=x onerror=1&gt;" in body
