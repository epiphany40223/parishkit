"""``campaign create-preview`` and ``campaign create`` (#1899, ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
go through Create the campaign's own admission, form, Review and salt, so a
token from either channel confirms on the other, and the first campaign is
the only one either can add.
"""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.campaign_create_views import SALT
from parishkit.stewardship.accounts.campaign_forms import initial_fields
from parishkit.stewardship.accounts.campaign_views import SALT as SETTINGS_SALT
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.content_forms import (
    applicable_slots,
    matches_default,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..campaign_factory import campaign
from ..test_source_corpus import source
from .automation_builders import as_staff, paired
from .campaign_builders import add_draft, change
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_campaign_create_postgresql import CREATE, new_fields
from .test_campaign_views_postgresql import post, requested
from .test_parish_views_postgresql import token
from .test_source_families_postgresql import (  # noqa: F401
    publish,
    source_singletons,
)

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_campaign_create"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on, with a
    promoted ParishSoft snapshot and no campaign yet."""
    publish(source())
    return runner(auth_service, monkeypatch)


def document(**changes):
    """A whole new campaign as a change document, the factory's campaign.

    The page's form fields from the factory's values, with the default
    Ministry leader roles, as ``new_fields`` posts them on the page.
    """
    values = initial_fields(campaign()["values"], digest="")
    values.pop("base_digest")
    values["ministry_leader_roles"] = ["Chairperson", "Staff"]
    return values | changes


def preview(admin, secret, settings, version=None):
    """Run ``campaign create-preview`` for the ``settings`` document member."""
    version = version or admin.service.store.active().digest
    return admin(
        "campaign",
        "create-preview",
        "--changes",
        json.dumps({"campaign": settings}),
        "--expected-version",
        version,
        secret=secret,
    )


def create(admin, secret, signed):
    """Run ``campaign create`` with ``signed``."""
    return admin("campaign", "create", "--token", signed, secret=secret)


def reviewed(admin, secret, settings):
    """A successful preview's result."""
    code, result, _ = preview(admin, secret, settings)
    assert code == 0, result
    return result["result"]


def refused(result, code):
    """The error document's code is ``code``, with no result."""
    assert result["error"]["code"] == code, result
    assert "result" not in result


def events():
    """How many ``campaign create`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def install(admin, created):
    """Install the request a successful ``campaign create`` recorded."""
    assert created["result"]["created"], created
    request_id = UUID(created["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def test_the_first_campaign_is_created_with_default_content(admin):
    """The review prints the posted settings and the default content count
    and records nothing; the create records the request and the event;
    installed, the draft is current with the default text for each page and
    email; a repeat returns the same request, and a new review is refused."""
    _, secret, _ = paired(admin.service)
    version = admin.service.store.active().digest
    settings = document()
    result = reviewed(admin, secret, settings)
    signed = result.pop("preview")["token"]
    assert result["version"] == version
    assert result["content"] > 0
    # Every form field, the document's values over the starting ones.
    assert result["settings"]["name"] == settings["name"]
    assert result["settings"]["census"] is settings["census"]
    assert "base_digest" not in result["settings"]
    assert not Campaign.objects.exists()
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0
    code, created, _ = create(admin, secret, signed)
    assert code == 0, created
    request_id = created["result"]["request"]["request_id"]
    assert events() == 1
    event = AuditEvent.objects.get(event_type=EVENT)
    assert event.subject_id is not None
    install(admin, created)
    row = Campaign.objects.get()
    assert row.state == "draft"
    assert SystemConfiguration.objects.get().current_campaign_id == row.pk
    assert row.active_configuration.values["name"] == settings["name"]
    records = [
        record
        for record in admin.service.store.active().document()["sections"]["content"]
        if record["values"]["campaign_id"] == str(row.pk)
    ]
    assert len(records) == result["content"]
    assert sorted((r["values"]["kind"], r["values"]["slot"]) for r in records) == (
        sorted(applicable_slots(row.active_configuration.values))
    )
    assert all(matches_default(record["values"]) for record in records)
    # A repeat, once the campaign exists, still returns the same request.
    code, again, _ = create(admin, secret, signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1
    # A second campaign is not offered: the review is not available now.
    code, result, _ = preview(admin, secret, document(name="A second one"))
    assert code == 1
    refused(result, "not_available")
    assert ConfigurationChangeRequest.objects.count() == 1


def test_omitted_fields_take_the_forms_starting_values(admin):
    """A field the document leaves out is the page form's starting value:
    the parish zone, Census ticked, the default leader roles."""
    _, secret, _ = paired(admin.service)
    settings = document()
    for name in ("timezone", "census", "ministry_leader_roles"):
        settings.pop(name)
    result = reviewed(admin, secret, settings)
    parish = admin.service.store.active().document()["sections"]["parish"][0]
    assert result["settings"]["timezone"] == parish["values"]["timezone"]
    assert result["settings"]["census"] is True
    assert result["settings"]["ministry_leader_roles"] == ["Chairperson", "Staff"]


def test_a_command_token_confirms_on_the_page(admin):
    """A command review confirms in the page's dialog, recording no command
    event; installed, it is the first campaign."""
    browser, secret, _ = paired(admin.service)
    signed = reviewed(admin, secret, document())["preview"]["token"]
    with task_login(ServiceRole.WEB):
        response = post(browser, CREATE, {"action": "confirm", "preview": signed})
    install_request(
        admin.service.store, request_id=requested(response), correlation_id=uuid4()
    )
    assert Campaign.objects.count() == 1 and events() == 0


def test_a_page_token_creates_on_the_command_line(admin):
    """The page's own review token creates on the command line, which
    records its event."""
    browser, secret, _ = paired(admin.service)
    with task_login(ServiceRole.WEB):
        review = post(browser, CREATE, new_fields(admin.service.store))
    assert review.status_code == 200, review.content
    code, created, _ = create(admin, secret, token(review))
    assert code == 0, created
    assert events() == 1
    install(admin, created)
    assert Campaign.objects.count() == 1


def test_preview_refusals(admin):
    """Another version is stale; a field error, a field the page has not
    and a malformed document are invalid; nothing is requested."""
    _, secret, _ = paired(admin.service)
    code, result, _ = preview(admin, secret, document(), version="0" * 64)
    assert code == 1
    refused(result, "stale_version")
    code, result, _ = preview(admin, secret, document(name=""))
    assert code == 1
    refused(result, "invalid")
    assert [item["field"] for item in result["error"]["fields"]] == ["campaign.name"]
    code, result, _ = preview(admin, secret, {"name": "Only a name"})
    assert code == 1
    refused(result, "invalid")
    fields = {item["field"] for item in result["error"]["fields"]}
    assert {"campaign.start_date", "campaign.end_date"} <= fields
    code, result, _ = preview(admin, secret, {"colour": "red"})
    assert code == 1
    refused(result, "invalid")
    assert [item["field"] for item in result["error"]["fields"]] == ["campaign.colour"]
    code, result, _ = admin(
        "campaign",
        "create-preview",
        "--changes",
        "[]",
        "--expected-version",
        admin.service.store.active().digest,
        secret=secret,
    )
    assert code == 1
    refused(result, "invalid")
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_create_refuses_foreign_and_stale_tokens(admin):
    """An altered token or Campaign settings' is invalid; another
    Administrator's is denied; one reviewed before the settings changed is
    stale; nothing is requested by the command."""
    _, secret, _ = paired(admin.service)
    signed = reviewed(admin, secret, document())["preview"]["token"]
    code, result, _ = create(admin, secret, signed[:-2] + "xx")
    assert code == 1
    refused(result, "invalid")
    payload = signing.loads(signed, salt=SALT)
    code, result, _ = create(admin, secret, signing.dumps(payload, salt=SETTINGS_SALT))
    assert code == 1
    refused(result, "invalid")
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=SALT,
    )
    code, result, _ = create(admin, secret, other)
    assert code == 1
    refused(result, "denied")
    store = admin.service.store
    parish = store.active().document()["sections"]["parish"][0]
    patch = [
        {
            "operation": "update",
            "section": "parish",
            "id": parish["id"],
            "values": {"name": "Changed elsewhere"},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    code, result, _ = create(admin, secret, signed)
    assert code == 1
    refused(result, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == 1
    assert events() == 0


def test_once_a_campaign_exists_nothing_is_created(admin):
    """A campaign added since the review: the create is not available, as
    the page's Apply refuses it under the work lock; nothing is requested."""
    _, secret, _ = paired(admin.service)
    signed = reviewed(admin, secret, document())["preview"]["token"]
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    requests = ConfigurationChangeRequest.objects.count()
    code, result, _ = create(admin, secret, signed)
    assert code == 1
    refused(result, "not_available")
    code, result, _ = preview(admin, secret, document())
    assert code == 1
    refused(result, "not_available")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert Campaign.objects.count() == 1 and events() == 0


def test_refused_sessions(admin, monkeypatch):
    """A read-only session cannot create; Staff are denied; an ended
    session is exit 5."""
    _, reader, _ = paired(admin.service, scope="read-only")
    _, secret, _ = paired(admin.service)
    signed = reviewed(admin, secret, document())["preview"]["token"]
    for code, result, _ in (
        preview(admin, reader, document()),
        create(admin, reader, signed),
    ):
        assert code == 1
        refused(result, "denied")
    revoke(admin.service, secret)
    for code, result, _ in (
        preview(admin, secret, document()),
        create(admin, secret, signed),
    ):
        assert code == 5
        refused(result, "session_ended")
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, result, _ in (
        preview(admin, staff, document()),
        create(admin, staff, signed),
    ):
        assert code == 1
        refused(result, "denied")
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_no_parishsoft_data_is_not_available(auth_service, google, monkeypatch):
    """With no promoted ParishSoft snapshot neither command can create the
    campaign: the preview and a create with a token signed for the
    Administrator are both ``not_available``, and nothing is requested."""
    admin = runner(auth_service, monkeypatch)
    _, secret, row = paired(admin.service)
    code, result, _ = preview(admin, secret, document())
    assert code == 1
    refused(result, "not_available")
    signed = sign_preview(
        actor=SimpleNamespace(identity=row.principal_id),
        configuration=editable_configuration(admin.service),
        patch=[{"section": "campaigns", "operation": "add"}],
        salt=SALT,
    )
    code, result, _ = create(admin, secret, signed)
    assert code == 1
    refused(result, "not_available")
    assert ConfigurationChangeRequest.objects.count() == 0 and events() == 0
