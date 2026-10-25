"""A ParishSoft key that reaches several organizations (#1554), against PostgreSQL.

The refresh pipeline stores only the configured organization's records and
records the counts it left out in the snapshot's load evidence; the pages
show them. Only the provider exchange is replaced.
"""

import pytest
from test_parishsoft_source import member

from parishkit.stewardship.source import loading
from parishkit.stewardship.source.loading import (
    ORGANIZATIONS_KEY,
    snapshot_organizations,
)
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

from .auth_builders import signed_in
from .test_integration_views_postgresql import URL
from .test_source_attempts_postgresql import setup
from .test_source_refreshing_postgresql import fake_provider, pages, run

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only the disposable database's idle migration seeds."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def several_organizations():
    """The refresh fixture's pages, from a key that also reaches organization 999.

    The members search answers with the parish's Member and one of the other
    organization's, which the load must drop.
    """
    values = pages()
    values[0] = [{"organizationID": 999}, {"organizationID": 12345}]
    ours = values[4][0] | {"organizationID": 12345, "recordCount": 2}
    theirs = member(2, total=2, row=2) | {"organizationID": 999, "familyDUID": 77}
    values[4] = [ours, theirs]
    return values


def test_a_refresh_stores_only_the_configured_organization(tmp_path, monkeypatch):
    """Same records as a one-organization key; the left-out Member is counted."""
    credential, execution, lease, *_ = setup(tmp_path)
    remaining, _ = fake_provider(monkeypatch, several_organizations())
    result = run(credential, execution, lease)
    assert not remaining and result.state == "ready"
    assert result.counts["family"] == result.counts["member"] == 1
    assert result.cursor["load"][ORGANIZATIONS_KEY] == {
        "reached": 2,
        "skipped": {"members": 1},
        "families_without_members": 0,
    }
    # The loaded organization is named (#1554); this load recorded no name.
    assert snapshot_organizations(result.pk) == {
        "reached": 2,
        "skipped": 1,
        "organization": "ParishSoft organization 12345",
    }


def test_a_one_organization_refresh_records_nothing_new(tmp_path, monkeypatch):
    """Exactly as before: no evidence key, and nothing for the pages to show."""
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    result = run(credential, execution, lease)
    assert ORGANIZATIONS_KEY not in result.cursor["load"]
    assert snapshot_organizations(result.pk) is None
    # No current snapshot at all reads as nothing too.
    assert snapshot_organizations() is None


def test_the_parishsoft_settings_page_shows_what_the_key_reaches(
    auth_service, google, monkeypatch
):
    """The line is drawn from the current snapshot's counts, and only then,
    naming the loaded organization (#1554)."""
    browser, _ = signed_in()
    page = browser.get(URL)
    assert page.status_code == 200
    assert b"data-parishsoft-organizations" not in page.content
    counts = {"reached": 3, "skipped": 5, "organization": "St. Example Parish (12345)"}
    monkeypatch.setattr(loading, "snapshot_organizations", lambda: counts)
    page = browser.get(URL).content.decode()
    assert "At the last load from ParishSoft, this key reached 3 organizations." in page
    assert "Only the data of St. Example Parish (12345) is loaded;" in page
    assert "Records of other organizations left out by that load: 5." in page


def test_a_full_load_records_the_organization_name_for_the_pages(tmp_path, monkeypatch):
    """The configured organization's cleaned name is in the load evidence;
    once that snapshot is current it names the organization "Name (ID)",
    and only for that organization's ID."""
    from parishkit.stewardship.source.organization_names import (
        current_organization_label,
        current_organization_name,
        loaded_organization_label,
        organization_fixed,
    )
    from parishkit.stewardship.source.snapshots import promote_snapshot

    from .test_source_snapshots_postgresql import permit

    # No current snapshot yet: the ID alone.
    assert loaded_organization_label() is None
    assert current_organization_name(12345) is None
    assert current_organization_label(12345) == "ParishSoft organization 12345"
    credential, execution, lease, *_ = setup(tmp_path)
    values = pages()
    values[0][0]["organizationReportName"] = "  St. Example\tParish "
    fake_provider(monkeypatch, values)
    result = run(credential, execution, lease)
    assert result.state == "ready"
    assert result.cursor["load"][loading.ORGANIZATION_KEY] == {
        "name": "St. Example Parish"
    }
    assert loading.snapshot_organization(result.pk) == (12345, "St. Example Parish")
    with execution.effect():
        promote_snapshot(result.pk, lease, admit=permit, reconcile=permit)
    assert current_organization_name(12345) == "St. Example Parish"
    # The configuration may hold the ID as text.
    assert current_organization_label("12345") == "St. Example Parish (12345)"
    # Another organization's ID gets no name from this snapshot.
    assert current_organization_name(999) is None
    assert current_organization_label(999) == "ParishSoft organization 999"
    # The loaded organization, as the key mismatch notice, System health and
    # every refusal of another organization name it.
    assert loaded_organization_label() == "St. Example Parish (12345)"
    assert organization_fixed(999) == (
        "The ParishSoft organization is St. Example Parish (12345) and can't "
        "change after setup."
    )
