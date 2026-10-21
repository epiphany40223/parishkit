"""``content preview --edit`` and ``content confirm --edit`` (#1873).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
are the content editor's Preview changes and its review dialog's Apply:
the fields on standard input are bound to the page's own ``ContentForm``,
the change is built and signed by the page's own
``content_views.review_change`` with the editor's salt, and the Apply goes
through the page's own ``confirm_intent`` and scope, so the page and the
command line answer alike.
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
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.content_views import preview_salt
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate

from ..content_factory import content
from .auth_builders import signed_in, unguarded
from .automation_builders import as_staff, paired
from .test_admin_content_cli_postgresql import draft
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_artwork_views_postgresql import change_parish
from .test_campaign_views_postgresql import post
from .test_content_views_postgresql import added, saved_emails

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_content_confirm"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def emails(admin):
    """A current draft whose first schedule sends one invitation, and an
    unused reminder. Returns ``(campaign, schedule, sent, spare)``."""
    store = admin.service.store
    campaign, schedule = draft(store)
    owner = str(campaign.pk)
    sent = content(owner, kind="email", slot="initial", subject="Sent")
    spare = content(owner, kind="email", slot="reminder", subject="Spare")
    added(store, campaign, sent, spare, schedule=schedule, sent=sent)
    return campaign, schedule, sent, spare


def target(kind, slot, revision=None):
    """``--edit KIND SLOT`` and, for one email, ``--revision ID``."""
    return ["--edit", kind, slot, *(["--revision", revision] if revision else [])]


def preview(admin, secret, edit, fields, version=None):
    """Run ``content preview --edit`` with ``fields`` on standard input."""
    version = version or admin.service.store.active().digest
    source = fields if isinstance(fields, bytes) else json.dumps(fields).encode()
    return admin(
        "content",
        "preview",
        *edit,
        "--expected-version",
        version,
        "-",
        secret=secret,
        answer=source,
    )


def confirm(admin, secret, edit, signed):
    """Run ``content confirm`` (``--edit`` unless ``edit`` is empty)."""
    return admin("content", "confirm", *edit, "--token", signed, secret=secret)


def token(admin, secret, edit, fields):
    """A successful preview's token."""
    code, document, _ = preview(admin, secret, edit, fields)
    assert code == 0, document
    return document["result"]["preview"]["token"]


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def events():
    """How many ``content confirm`` events are recorded."""
    return AuditEvent.objects.filter(event_type=EVENT).count()


def install(admin, confirmed):
    """Install the request a successful ``content confirm`` recorded."""
    assert confirmed["result"]["created"], confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def saved(kind, slot):
    """The applied revisions of one slot: each ``subject``, ``html``, ``text``."""
    rows = SystemConfiguration.objects.get().active_configuration.content_versions
    return list(rows.filter(kind=kind, slot=slot).values("subject", "html", "text"))


def test_a_page_changes_as_the_editors_review_and_apply(admin):
    """The preview prints the fictional samples and changes nothing, as the
    editor's review; the confirm records one request and the event; once
    installed the page holds the new text; a repeat returns the request."""
    store = admin.service.store
    campaign, _ = draft(store)
    _, secret, _ = paired(admin.service)
    version = store.active().digest
    requests = ConfigurationChangeRequest.objects.count()
    edit = target("page", "welcome")
    code, document, _ = preview(admin, secret, edit, {"html": "<p>Welcome in</p>"})
    assert code == 0, document
    result = document["result"]
    signed = result.pop("preview")["token"]
    change = result.pop("edit")
    assert result == {
        "action": "edit",
        "version": version,
        "campaign_id": str(campaign.pk),
        "deleting": None,
        "plain_text": None,
    }
    assert {key: change[key] for key in ("kind", "slot", "revision_id", "name")} == {
        "kind": "page",
        "slot": "welcome",
        "revision_id": None,
        "name": "Family welcome",
    }
    assert "Welcome in" in change["after"]["html"] and change["schedules"] == []
    assert set(change["after"]) == {"subject", "html", "text"}
    # The page's own review of the same change shows the same sample.
    browser, _ = signed_in()
    page = post(
        browser,
        "/admin/campaign/content/page/welcome/",
        {"base_digest": version, "action": "preview", "html": "<p>Welcome in</p>"},
    )
    assert page.status_code == 200 and b"Welcome in" in page.content
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0

    code, confirmed, _ = confirm(admin, secret, edit, signed)
    assert code == 0 and confirmed["result"]["created"], confirmed
    request_id = confirmed["result"]["request"]["request_id"]
    assert events() == 1
    install(admin, confirmed)
    [values] = saved("page", "welcome")
    assert values["html"] == "<p>Welcome in</p>"
    code, again, _ = confirm(admin, secret, edit, signed)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == request_id
    assert events() == 1
    assert ConfigurationChangeRequest.objects.count() == requests + 1


def test_an_email_lists_the_schedules_it_switches(admin, emails):
    """A sent email's preview lists its schedule, as the review dialog does;
    absent fields keep the editor's values, so only the subject changes."""
    _, schedule, sent, _ = emails
    _, secret, _ = paired(admin.service)
    edit = target("email", "initial", sent["id"])
    code, document, _ = preview(admin, secret, edit, {"subject": "Come and see"})
    assert code == 0, document
    change = document["result"]["edit"]
    assert change["before"]["subject"] != change["after"]["subject"]
    [row] = change["schedules"]
    assert row["id"] == schedule["id"] and row["kind"] == "initial"
    assert row["name"] == "Initial invitation" and row["due_at"]
    assert row["sent"] is False
    code, confirmed, _ = confirm(
        admin, secret, edit, document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    [kept] = saved("email", "initial")
    assert kept["subject"] == "Come and see"
    assert kept["html"] == sent["values"]["html"]


def test_the_preview_refuses_what_the_editor_refuses(admin, emails):
    """The form's refusals are at their fields with the page's words; no
    change, and removing a sent email, are the page's refusals; another
    version is stale; an unknown slot or revision is not available."""
    _, _, sent, _ = emails
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    page = target("page", "welcome")
    invitation = target("email", "initial", sent["id"])
    for edit, fields, field, words in (
        (page, {"html": "<p>{{ nope }}</p>"}, "content.html", "placeholders"),
        (invitation, {"subject": ""}, "content.subject", "required"),
        (invitation, {}, "content", "No content has changed."),
        (invitation, {"clear": True}, "content.clear", "used by a mail schedule"),
        (page, {"subject": "x"}, "content.subject", "no such field"),
        # Typed text that differs while the text is generated (text_conflict).
        (invitation, {"text": "Different"}, "content.text", "differs"),
        (page, b"[1]", "content", "one JSON object"),
    ):
        code, document, _ = preview(admin, secret, edit, fields)
        assert code == 1, document
        refused(document, "invalid")
        problem = document["error"]["fields"][0]
        assert problem["field"] == field and words in problem["message"], problem
    code, document, _ = preview(
        admin, secret, page, {"html": "<p>x</p>"}, version="0" * 64
    )
    assert code == 1
    refused(document, "stale_version")
    for edit in (
        target("page", "nope"),
        target("email", "initial", str(uuid4())),
        target("page", "welcome", sent["id"]),
    ):
        code, document, _ = preview(admin, secret, edit, {"html": "<p>x</p>"})
        assert code == 1
        refused(document, "not_available")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_a_removal_needs_no_subject(admin, emails):
    """Remove alone removes an unused email: the greyed subject is not
    needed, as on the page (#563 item 6)."""
    _, _, _, spare = emails
    _, secret, _ = paired(admin.service)
    edit = target("email", "reminder", spare["id"])
    code, document, _ = preview(admin, secret, edit, {"clear": True})
    assert code == 0, document
    assert document["result"]["edit"]["after"] is None
    code, confirmed, _ = confirm(
        admin, secret, edit, document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    assert spare["id"] not in saved_emails()


def test_confirm_takes_only_this_editors_token(admin, emails):
    """A token is bound to its page or email: another slot's editor, the
    Delete and an altered token are invalid; nothing is requested."""
    _, _, _, spare = emails
    _, secret, _ = paired(admin.service)
    requests = ConfigurationChangeRequest.objects.count()
    signed = token(admin, secret, target("page", "welcome"), {"html": "<p>Hi</p>"})
    deletion = admin(
        "content",
        "preview",
        "--delete",
        spare["id"],
        "--expected-version",
        admin.service.store.active().digest,
        secret=secret,
    )[1]["result"]["preview"]["token"]
    for edit, value in (
        (target("email", "reminder", spare["id"]), signed),
        ([], signed),
        (target("page", "welcome"), deletion),
        (target("page", "welcome"), signed[:-2] + "xx"),
    ):
        code, document, _ = confirm(admin, secret, edit, value)
        assert code == 1
        refused(document, "invalid")
    assert ConfigurationChangeRequest.objects.count() == requests and events() == 0


def test_a_held_campaign_is_stale(admin, emails):
    """Background work holding the campaign is the page's "can't be edited
    right now"."""
    campaign, _, _, _ = emails
    with unguarded():
        CampaignWorkGate.objects.create(
            campaign=campaign, request_id=uuid4(), initiated_by_id=uuid4()
        )
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(
        admin, secret, target("page", "welcome"), {"html": "<p>x</p>"}
    )
    assert code == 1
    refused(document, "stale_version")


def test_refused_sessions(admin, emails, monkeypatch):
    """A read-only session and Staff are denied, Staff before standard input
    is read; an ended session is exit 5."""
    edit = target("page", "welcome")
    fields = {"html": "<p>x</p>"}
    _, reader, _ = paired(admin.service, scope="read-only")
    for code, document, _ in (
        preview(admin, reader, edit, fields),
        confirm(admin, reader, edit, "x"),
    ):
        assert code == 1
        refused(document, "denied")
    _, secret, _ = paired(admin.service)
    signed = token(admin, secret, edit, fields)
    revoke(admin.service, secret)
    for code, document, _ in (
        preview(admin, secret, edit, fields),
        confirm(admin, secret, edit, signed),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        preview(admin, staff, edit, fields),
        # The role first (#1341): input that is not UTF-8 is never read.
        preview(admin, staff, edit, b"\xff"),
        confirm(admin, staff, edit, signed),
    ):
        assert code == 1
        refused(document, "denied")
    assert events() == 0


def test_markup_the_page_drops_is_dropped(admin):
    """The HTML goes through the page's own sanitizer: a script and an
    event handler reach neither the sample nor the saved page."""
    draft(admin.service.store)
    _, secret, _ = paired(admin.service)
    edit = target("page", "welcome")
    source = '<p onclick="x()">Hi</p><script>alert(1)</script>'
    code, document, _ = preview(admin, secret, edit, {"html": source})
    assert code == 0, document
    after = document["result"]["edit"]["after"]["html"]
    assert "onclick" not in after and "<script" not in after and "Hi" in after
    code, confirmed, _ = confirm(
        admin, secret, edit, document["result"]["preview"]["token"]
    )
    install(admin, confirmed)
    [values] = saved("page", "welcome")
    assert values["html"] == "<p>Hi</p>"


def test_the_banner_choice_changes_as_on_the_page(admin, emails):
    """Turning a Family email's campaign banner off hides it for that email
    once installed, as the editor's box does (#248)."""
    _, _, _, spare = emails
    _, secret, _ = paired(admin.service)
    edit = target("email", "reminder", spare["id"])
    code, document, _ = preview(admin, secret, edit, {"show_banner": False})
    assert code == 0, document
    code, confirmed, _ = confirm(
        admin, secret, edit, document["result"]["preview"]["token"]
    )
    install(admin, confirmed)
    values = Campaign.objects.get().active_configuration.values
    assert "reminder" in values["artwork"]["hide_banner"]


def test_confirm_refuses_a_stale_or_another_administrators_token(admin):
    """A token built before the settings changed is stale; another
    Administrator's build of the same change is denied."""
    store = admin.service.store
    campaign, _ = draft(store)
    _, secret, _ = paired(admin.service)
    edit = target("page", "welcome")
    signed = token(admin, secret, edit, {"html": "<p>Hi</p>"})
    salt = preview_salt(campaign.pk, "page", "welcome", None)
    payload = signing.loads(signed, salt=salt)
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(admin.service),
        patch=payload["patch"],
        salt=salt,
        snapshot=payload["snapshot"],
    )
    code, document, _ = confirm(admin, secret, edit, other)
    assert code == 1
    refused(document, "denied")
    change_parish(store)
    code, document, _ = confirm(admin, secret, edit, signed)
    assert code == 1
    refused(document, "stale_version")
    assert events() == 0


def test_archived_or_no_campaign_is_not_available(admin):
    """No current campaign, or an archived one, has no editor to change."""
    from django.db import connection

    _, secret, _ = paired(admin.service)
    edit = target("page", "welcome")
    code, document, _ = preview(admin, secret, edit, {"html": "<p>x</p>"})
    assert code == 1
    refused(document, "not_available")
    draft(admin.service.store)
    with unguarded(), connection.cursor() as cursor:
        cursor.execute("UPDATE public.stewardship_campaign SET state='archived'")
    code, document, _ = preview(admin, secret, edit, {"html": "<p>x</p>"})
    assert code == 1
    refused(document, "not_available")
    assert events() == 0
