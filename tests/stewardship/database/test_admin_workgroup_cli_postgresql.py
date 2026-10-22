"""``workgroup show``, ``workgroup preview`` and ``workgroup confirm`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
go through the No-reminders WorkGroup page's own form, review and
confirmation, so a token from either channel confirms on the other.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import parish_views
from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.reminder_workgroup_views import SALT
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_SLUG
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.workgroups import excluded_duids

from .automation_builders import as_staff, paired
from .campaign_builders import change
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import post
from .test_parish_views_postgresql import digest
from .test_reminder_workgroup_postgresql import record
from .test_reminder_workgroup_views_postgresql import setup, token
from .test_source_snapshots_postgresql import prepared, publish

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_workgroup_confirm"
URL = f"/admin/campaign/{REMINDER_WORKGROUP_SLUG}/"
NAME = "Active: Stewardship 2027"


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate the source's idle seeds after the disposable test flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def current(admin):
    """The current draft campaign, with no WorkGroup yet."""
    return setup(admin.service.store)


def show(admin, secret):
    """Run ``workgroup show``."""
    return admin("workgroup", "show", secret=secret)


def quiet_show(admin, secret):
    """Run ``workgroup show`` successfully; it records no event at all."""
    before = AuditEvent.objects.count()
    code, document, _ = show(admin, secret)
    assert code == 0, document
    assert AuditEvent.objects.count() == before
    return document["result"]


def preview(admin, secret, name, version=None):
    """Run ``workgroup preview`` for ``name`` (None is ``--clear``)."""
    version = version or admin.service.store.active().digest
    choice = ["--clear"] if name is None else ["--name", name]
    return admin(
        "workgroup",
        "preview",
        *choice,
        "--expected-version",
        version,
        secret=secret,
    )


def confirm(admin, secret, signed):
    """Run ``workgroup confirm`` with ``signed``."""
    return admin("workgroup", "confirm", "--token", signed, secret=secret)


def signed_token(admin, secret, name=NAME):
    """A successful preview's token."""
    code, document, _ = preview(admin, secret, name)
    assert code == 0, document
    return document["result"]["preview"]["token"]


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def events():
    """How many ``workgroup confirm`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def install(admin, confirmed):
    """Install the request a successful ``workgroup confirm`` recorded."""
    assert confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def workgroup(row):
    """The campaign's applied WorkGroup name, or None."""
    row = Campaign.objects.get(pk=row.pk)
    return row.active_configuration.values.get("reminder_workgroup")


def set_name(admin, row, name):
    """Apply ``name`` as the campaign's WorkGroup directly."""
    store = admin.service.store
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(row.pk),
            "values": {"reminder_workgroup": name},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"


def test_show_follows_the_pages_now_panel(admin, current):
    """Off, then pending until a refresh reads the name, then missing or
    found with the number of Families; a read-only session reads, and
    nothing is recorded."""
    _, reader, _ = paired(admin.service, scope="read-only")
    assert quiet_show(admin, reader) == {
        "version": admin.service.store.active().digest,
        "campaign_id": str(current.pk),
        "name": None,
        "state": "off",
        "families": None,
        "excluding": 0,
        "excluding_from": None,
    }
    set_name(admin, current, NAME)
    result = quiet_show(admin, reader)
    assert (result["name"], result["state"]) == (NAME, "pending")
    assert (result["excluding"], result["excluding_from"]) == (0, None)
    assert result["version"] == admin.service.store.active().digest
    snapshot, claim = prepared()
    publish(snapshot, claim)
    release_source(claim)
    record(NAME, [], found=False)
    result = quiet_show(admin, reader)
    assert (result["state"], result["families"]) == ("missing", None)
    record(NAME, [11, 12, 13])
    result = quiet_show(admin, reader)
    assert (result["state"], result["families"]) == ("found", 3)
    assert (result["excluding"], result["excluding_from"]) == (3, None)
    # Counts only, never a Family's DUID.
    assert set(result) == {
        "version",
        "campaign_id",
        "name",
        "state",
        "families",
        "excluding",
        "excluding_from",
    }
    # Evidence for another name is not this name's (pending), but it is
    # what Reminders skip until a refresh reads this name (#1305).
    record("Something else", [11])
    result = quiet_show(admin, reader)
    assert (result["state"], result["families"]) == ("pending", None)
    assert (result["excluding"], result["excluding_from"]) == (1, "Something else")


def skipped(row):
    """How many Families planning skips now (``excluded_duids``)."""
    row = Campaign.objects.get(pk=row.pk)
    return len(excluded_duids(row.active_configuration.values))


def test_show_reports_the_applied_set_through_a_rename(admin, current):
    """``excluding`` follows what Reminders skip (#1313): a rename keeps the
    previous WorkGroup's Families until a refresh reads the new name, a
    missing name keeps the earlier set, and a clear stops at once."""
    _, reader, _ = paired(admin.service, scope="read-only")
    snapshot, claim = prepared()
    publish(snapshot, claim)
    release_source(claim)
    set_name(admin, current, NAME)
    record(NAME, [11, 12])
    assert quiet_show(admin, reader)["excluding"] == 2
    set_name(admin, current, "Active: Stewardship 2028")
    result = quiet_show(admin, reader)
    assert (result["state"], result["families"]) == ("pending", None)
    assert (result["excluding"], result["excluding_from"]) == (2, NAME)
    assert result["excluding"] == skipped(current)
    # The refresh found no such WorkGroup: the earlier set is kept, unnamed.
    record("Active: Stewardship 2028", [11, 12], found=False)
    result = quiet_show(admin, reader)
    assert (result["state"], result["families"]) == ("missing", None)
    assert (result["excluding"], result["excluding_from"]) == (2, None)
    assert result["excluding"] == skipped(current)
    # A refresh that finds it applies only its own Families.
    record("Active: Stewardship 2028", [13])
    result = quiet_show(admin, reader)
    assert (result["state"], result["families"]) == ("found", 1)
    assert (result["excluding"], result["excluding_from"]) == (1, None)
    set_name(admin, current, None)
    result = quiet_show(admin, reader)
    assert (result["state"], result["excluding"]) == ("off", 0)
    assert result["excluding_from"] is None


def test_preview_and_confirm_as_the_page(admin, current):
    """The review names the change; nothing changes until it is confirmed,
    which records the request and the command's event; once installed the
    name applies; a repeat returns the same request; clearing works too."""
    _, secret, _ = paired(admin.service)
    version = admin.service.store.active().digest
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(admin, secret, f"  {NAME}  ")
    assert code == 0, document
    result = document["result"]
    signed = result.pop("preview")["token"]
    assert result == {
        "version": version,
        "campaign_id": str(current.pk),
        "before": None,
        "after": NAME,
    }
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0
    code, confirmed, _ = confirm(admin, secret, signed)
    assert code == 0, confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events() == 1
    install(admin, confirmed)
    assert workgroup(current) == NAME
    code, again, _ = confirm(admin, secret, signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # Turning it off again.
    code, document, _ = preview(admin, secret, None)
    assert code == 0, document
    assert (document["result"]["before"], document["result"]["after"]) == (NAME, None)
    code, confirmed, _ = confirm(admin, secret, document["result"]["preview"]["token"])
    assert code == 0, confirmed
    install(admin, confirmed)
    assert workgroup(current) is None
    assert events() == 2


def test_a_command_preview_confirms_on_the_page(admin, current):
    """Tokens are bound to the Administrator, not the channel."""
    browser, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    signed = signed_token(admin, secret)
    with task_login(ServiceRole.WEB):
        response = post(browser, URL, {"action": "confirm", "preview": signed})
    assert response.status_code == 302, response.content
    # The review dialog's Apply follows the change's own status page (#1356).
    assert response["Location"].startswith("/admin/changes/")
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    assert events() == 0


def test_a_page_preview_confirms_on_the_command_line(admin, current):
    """The page's own review token, confirmed by ``workgroup confirm``,
    records the request and the command's event."""
    browser, secret, _ = paired(admin.service)
    with task_login(ServiceRole.WEB):
        loaded = digest(browser.get(URL))
        signed = token(
            post(
                browser,
                URL,
                {"action": "preview", "name": NAME, "base_digest": loaded},
            )
        )
    code, confirmed, _ = confirm(admin, secret, signed)
    assert code == 0, confirmed
    install(admin, confirmed)
    assert workgroup(current) == NAME
    assert events() == 1


def test_preview_refusals(admin, current):
    """Another version is stale; no change and a name the page's form
    refuses are invalid; nothing is requested."""
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(admin, secret, NAME, version="0" * 64)
    assert code == 1
    refused(document, "stale_version")
    code, document, _ = preview(admin, secret, None)
    assert code == 1
    refused(document, "invalid")
    assert document["error"]["fields"][0]["field"] == "name"
    code, document, _ = preview(admin, secret, "   ")
    assert code == 1
    refused(document, "invalid")
    code, document, _ = preview(admin, secret, "x" * 500)
    assert code == 1
    refused(document, "invalid")
    assert document["error"]["fields"][0]["field"] == "name"
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_a_blank_name_never_clears_a_set_workgroup(admin, current):
    """With a WorkGroup set, an empty or whitespace-only --name is invalid
    and points to --clear: nothing is requested and the name stays."""
    set_name(admin, current, NAME)
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    for blank in ("", "   ", "\t"):
        code, document, _ = preview(admin, secret, blank)
        assert code == 1, document
        refused(document, "invalid")
        (problem,) = document["error"]["fields"]
        assert problem["field"] == "name" and "--clear" in problem["message"]
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0
    assert workgroup(current) == NAME


def test_confirm_refuses_foreign_and_stale_tokens(admin, current):
    """An altered token or another page's is invalid; another
    Administrator's is denied; one reviewed before the settings changed is
    stale; nothing is requested by the command."""
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    signed = signed_token(admin, secret)
    code, document, _ = confirm(admin, secret, signed[:-2] + "xx")
    assert code == 1
    refused(document, "invalid")
    payload = signing.loads(signed, salt=SALT + str(current.pk))
    code, document, _ = confirm(
        admin, secret, signing.dumps(payload, salt=parish_views.SALT)
    )
    assert code == 1
    refused(document, "invalid")
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=SALT + str(current.pk),
    )
    code, document, _ = confirm(admin, secret, other)
    assert code == 1
    refused(document, "denied")
    set_name(admin, current, "Changed elsewhere")
    requests += 1
    code, document, _ = confirm(admin, secret, signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0


def test_without_a_campaign_nothing_is_available(admin):
    """No current campaign: every command is not_available."""
    _, secret, _ = paired(admin.service)
    for code, document, _ in (
        show(admin, secret),
        preview(admin, secret, NAME),
        confirm(admin, secret, "x"),
    ):
        assert code == 1
        refused(document, "not_available")
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_refused_sessions(admin, current, monkeypatch):
    """A read-only session cannot change it; Staff are denied; an ended
    session is exit 5."""
    requests = ConfigurationChangeRequest.objects.count()
    _, reader, _ = paired(admin.service, scope="read-only")
    _, secret, _ = paired(admin.service)
    signed = signed_token(admin, secret)
    for code, document, _ in (
        preview(admin, reader, NAME),
        confirm(admin, reader, signed),
    ):
        assert code == 1
        refused(document, "denied")
    revoke(admin.service, secret)
    for code, document, _ in (
        show(admin, secret),
        preview(admin, secret, NAME),
        confirm(admin, secret, signed),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        show(admin, staff),
        preview(admin, staff, NAME),
        confirm(admin, staff, signed),
    ):
        assert code == 1
        refused(document, "denied")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0
