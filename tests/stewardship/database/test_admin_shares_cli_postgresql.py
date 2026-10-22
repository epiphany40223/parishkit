"""``campaign shares-preview`` and ``campaign shares-confirm`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
go through Share options' own formset, review and confirmation, so a token
from either channel confirms on the other.
"""

import json
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
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.share_views import EDITOR
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate
from parishkit.stewardship.deployment import ServiceRole

from ..test_share_forms import data_for
from .auth_builders import unguarded
from .automation_builders import as_staff, paired
from .campaign_builders import add_draft, change, command
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_settings_cli_postgresql import shares_draft
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import post
from .test_parish_views_postgresql import token

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_campaign_shares_confirm"
URL = "/admin/campaign/share-options/"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def draft(admin):
    """The current Testing-mode draft, with the financial module and options."""
    return shares_draft(admin.service.store)


def kept(previous):
    """The change document's options keeping every saved one as it is."""
    return [
        {"id": row["id"], "name": row["label"], "free_text": row["free_text"]}
        for row in previous
    ]


def preview(admin, secret, document, version=None, *more, answer=b""):
    """Run ``campaign shares-preview`` for ``document`` (a dict, or a string
    given as is: ``-`` reads ``answer`` from standard input)."""
    version = version or admin.service.store.active().digest
    text = document if isinstance(document, str) else json.dumps(document)
    return admin(
        "campaign",
        "shares-preview",
        "--changes",
        text,
        "--expected-version",
        version,
        *more,
        secret=secret,
        answer=answer,
    )


def confirm(admin, secret, signed, *more):
    """Run ``campaign shares-confirm`` with ``signed``."""
    return admin("campaign", "shares-confirm", "--token", signed, *more, secret=secret)


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def fields(document):
    """The fields an ``invalid`` document names."""
    return [problem["field"] for problem in document["error"]["fields"]]


def events():
    """How many ``campaign shares-confirm`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def install(admin, confirmed):
    """Install the request a successful ``campaign shares-confirm`` recorded."""
    assert confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def saved(row):
    """The campaign's applied Share options."""
    return EDITOR.current(Campaign.objects.get(pk=row.pk))


def signed_token(admin, secret, previous):
    """A successful preview's token for a relabel of the first option."""
    options = kept(previous)
    options[0]["name"] = "Relabelled"
    code, document, _ = preview(admin, secret, {"options": options})
    assert code == 0, document
    return document["result"]["preview"]["token"]


def test_preview_and_confirm_as_the_page(admin, draft):
    """The review names each change; nothing changes until it is confirmed,
    which records the request and the command's event; once installed the
    list is the document's, saved ids kept; a repeat returns the request."""
    row, previous = draft
    _, secret, _ = paired(admin.service)
    version = admin.service.store.active().digest
    requests = ConfigurationChangeRequest.objects.count()
    first, second, third, *rest = kept(previous)
    document = {
        "options": [
            second,
            first | {"name": "Online giving, renamed"},
            {"name": "A matching gift", "free_text": True},
            *rest,
        ],
        "delete": [third["id"]],
    }
    code, previewed, _ = preview(admin, secret, document)
    assert code == 0, previewed
    result = previewed["result"]
    signed = result.pop("preview")["token"]
    assert (result["version"], result["campaign_id"]) == (version, str(row.pk))
    # The page's compact review: the relabel, the swap's one moved option,
    # the added and the removed one.
    assert [line["label"] for line in result["changes"]] == [
        "Label",
        "Position of “Online giving, renamed”",
        "Added",
        "Removed",
    ]
    assert result["changes"][0] == {
        "label": "Label",
        "before": previous[0]["label"],
        "after": "Online giving, renamed",
    }
    names = [option["name"] for option in result["options"]]
    assert names == [
        second["name"],
        "Online giving, renamed",
        "A matching gift",
        *(option["name"] for option in rest),
    ]
    new_id = result["options"][2]["id"]
    assert new_id not in {option["id"] for option in previous}
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0
    code, confirmed, _ = confirm(admin, secret, signed)
    assert code == 0, confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events() == 1
    install(admin, confirmed)
    assert saved(row) == [
        {
            "id": option["id"],
            "label": option["name"],
            "free_text": option["free_text"],
        }
        for option in result["options"]
    ]
    code, again, _ = confirm(admin, secret, signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1
    assert ConfigurationChangeRequest.objects.count() == requests + 1


def test_the_document_from_standard_input_with_its_campaign(admin, draft):
    """``--changes -`` reads the document; ``--campaign`` names the draft."""
    row, previous = draft
    _, secret, _ = paired(admin.service)
    options = kept(previous)
    options[0]["free_text"] = not options[0]["free_text"]
    code, document, _ = preview(
        admin,
        secret,
        "-",
        None,
        "--campaign",
        str(row.pk),
        answer=json.dumps({"options": options}).encode(),
    )
    assert code == 0, document
    (line,) = document["result"]["changes"]
    assert line["label"].startswith("Free-text response for")
    code, confirmed, _ = confirm(
        admin, secret, document["result"]["preview"]["token"], "--campaign", str(row.pk)
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    assert saved(row)[0]["free_text"] == options[0]["free_text"]


def test_a_command_preview_confirms_on_the_page(admin, draft):
    """Tokens are bound to the Administrator, not the channel."""
    row, previous = draft
    browser, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    signed = signed_token(admin, secret, previous)
    with task_login(ServiceRole.WEB):
        response = post(browser, URL, {"action": "confirm", "preview": signed})
    assert response.status_code == 302, response.content
    assert response["Location"].startswith("/admin/changes/")
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    assert events() == 0


def test_a_page_preview_confirms_on_the_command_line(admin, draft):
    """The page's own review token, confirmed by ``campaign shares-confirm``,
    records the request and the command's event."""
    row, previous = draft
    browser, secret, _ = paired(admin.service)
    data = data_for(previous) | {
        "base_digest": admin.service.store.active().digest,
        "options-0-label": "From the page",
    }
    with task_login(ServiceRole.WEB):
        signed = token(post(browser, URL, data))
    code, confirmed, _ = confirm(admin, secret, signed)
    assert code == 0, confirmed
    install(admin, confirmed)
    assert saved(row)[0]["label"] == "From the page"
    assert events() == 1


def test_preview_refusals(admin, draft):
    """Another version is stale; no change, the page's identity rules and a
    label it refuses are invalid at their fields; nothing is requested."""
    row, previous = draft
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    options = kept(previous)
    code, document, _ = preview(admin, secret, {"options": options}, "0" * 64)
    assert code == 1
    refused(document, "stale_version")
    for edit, field in (
        # The page's unchanged safeguard.
        ({"options": options}, "options"),
        # A saved option left out: the page's retained-or-deleted rule.
        ({"options": options[1:]}, "options"),
        # An id the campaign never had.
        ({"options": [*options, {"id": str(uuid4()), "name": "Gift"}]}, "options"),
        # The page's label check.
        (
            {"options": [options[0] | {"name": "{{ family_code }}"}, *options[1:]]},
            "options.0.name",
        ),
        # The document's own shape.
        ({"options": [{"name": "Gift", "colour": "red"}]}, "options.0.colour"),
    ):
        code, document, _ = preview(admin, secret, edit)
        assert code == 1, document
        refused(document, "invalid")
        assert field in fields(document), document
    code, document, _ = preview(admin, secret, "not json")
    assert code == 1
    refused(document, "invalid")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_a_campaign_the_page_does_not_edit_is_refused(admin):
    """A census-only draft and a live campaign are the page's own refusal
    (stale_version, as its 409); an unknown campaign is not available."""
    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, {"options": []})
    assert code == 1
    refused(document, "stale_version")
    code, document, _ = preview(
        admin, secret, {"options": []}, None, "--campaign", str(uuid4())
    )
    assert code == 1
    refused(document, "not_available")


def test_a_live_campaign_is_refused_and_its_token_goes_stale(admin, draft):
    """Once the campaign goes live, its preview is refused as the page's is
    and an earlier token is stale; nothing is requested."""
    row, previous = draft
    _, secret, _ = paired(admin.service)
    signed = signed_token(admin, secret, previous)
    requests = ConfigurationChangeRequest.objects.count()
    command(row, uuid4(), Action.ACTIVATE)
    code, document, _ = preview(admin, secret, {"options": kept(previous)})
    assert code == 1
    refused(document, "stale_version")
    code, document, _ = confirm(admin, secret, signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_confirm_refuses_foreign_and_stale_tokens(admin, draft):
    """An altered token or another page's is invalid; another
    Administrator's is denied; one reviewed before the settings changed is
    stale; nothing is requested by the command."""
    row, previous = draft
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    signed = signed_token(admin, secret, previous)
    salt = f"{EDITOR.salt}:{row.pk}"
    code, document, _ = confirm(admin, secret, signed[:-2] + "xx")
    assert code == 1
    refused(document, "invalid")
    payload = signing.loads(signed, salt=salt)
    code, document, _ = confirm(
        admin, secret, signing.dumps(payload, salt=parish_views.SALT)
    )
    assert code == 1
    refused(document, "invalid")
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=salt,
    )
    code, document, _ = confirm(admin, secret, other)
    assert code == 1
    refused(document, "denied")
    store = admin.service.store
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(row.pk),
            "values": {"share_options": previous[1:]},
        }
    ]
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    requests += 1
    code, document, _ = confirm(admin, secret, signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0


def test_a_locked_or_held_draft_is_refused_as_the_page_does(admin, draft):
    """A structurally locked draft, and one that background work holds, are
    the page's refusal (stale_version, its 409); a token reviewed before the
    work began is stale too; nothing is requested."""
    row, previous = draft
    _, secret, _ = paired(admin.service)
    signed = signed_token(admin, secret, previous)
    requests = ConfigurationChangeRequest.objects.count()
    # Seeded past the guards, as the page's own tests do: a real lock or gate
    # needs a lifecycle command or the exclusive maintenance window.
    with unguarded():
        Campaign.objects.filter(pk=row.pk).update(structural_locked=True)
    code, document, _ = preview(admin, secret, {"options": kept(previous)})
    assert code == 1
    refused(document, "stale_version")
    with unguarded():
        Campaign.objects.filter(pk=row.pk).update(structural_locked=False)
        CampaignWorkGate.objects.create(
            campaign=row, request_id=uuid4(), initiated_by_id=uuid4()
        )
    code, document, _ = preview(admin, secret, {"options": kept(previous)})
    assert code == 1
    refused(document, "stale_version")
    code, document, _ = confirm(admin, secret, signed)
    assert code == 1
    refused(document, "stale_version")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_a_token_for_another_campaign_is_invalid(admin, draft):
    """The token's salt is bound to its campaign: confirming against another
    or unknown campaign fails its signature (invalid, as the page's
    Apply), never not_available; nothing is requested."""
    row, previous = draft
    _, secret, _ = paired(admin.service)
    signed = signed_token(admin, secret, previous)
    requests = ConfigurationChangeRequest.objects.count()
    # Another campaign's id builds another salt, as an unknown one does.
    code, document, _ = confirm(admin, secret, signed, "--campaign", str(uuid4()))
    assert code == 1
    refused(document, "invalid")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_without_a_campaign_nothing_is_available(admin):
    """No current campaign: both commands are not_available."""
    _, secret, _ = paired(admin.service)
    for code, document, _ in (
        preview(admin, secret, {"options": []}),
        confirm(admin, secret, "x"),
    ):
        assert code == 1
        refused(document, "not_available")
    assert not ConfigurationChangeRequest.objects.exists() and events() == 0


def test_refused_sessions(admin, draft, monkeypatch):
    """A read-only session cannot change it; an ended session is exit 5;
    Staff are denied before any input is read (standard input that is not
    UTF-8 is never reached)."""
    row, previous = draft
    requests = ConfigurationChangeRequest.objects.count()
    _, reader, _ = paired(admin.service, scope="read-only")
    _, secret, _ = paired(admin.service)
    signed = signed_token(admin, secret, previous)
    document = {"options": kept(previous)}
    for code, result, _ in (
        preview(admin, reader, document),
        confirm(admin, reader, signed),
    ):
        assert code == 1
        refused(result, "denied")
    _, staff, _ = paired(admin.service)
    revoke(admin.service, secret)
    for code, result, _ in (
        preview(admin, secret, document),
        confirm(admin, secret, signed),
    ):
        assert code == 5
        refused(result, "session_ended")
    as_staff(monkeypatch)
    for code, result, _ in (
        preview(admin, staff, "-", None, answer=b"\xff\xfe"),
        admin(
            "campaign",
            "shares-confirm",
            "--token",
            "-",
            secret=staff,
            answer=b"\xff\xfe",
        ),
    ):
        assert code == 1
        refused(result, "denied")
    assert ConfigurationChangeRequest.objects.count() == requests
    assert events() == 0
