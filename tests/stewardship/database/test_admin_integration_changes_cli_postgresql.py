"""``integration preview`` and ``integration confirm`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
review and apply an integration settings page's non-secret Save and Remove
through the page's own form, review and confirmation, so a token from
either channel confirms on the other.
"""

import json
import re
from html import unescape
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.integration_views import SALT
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent

from ..schedule_rows import rows, shown
from . import campaign_builders
from .automation_builders import as_staff, paired
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_integration_views_postgresql import (
    apply_rules_schedule,
    replace_settings,
    stored_schedule,
)

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_integration_confirm"
FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"
LINK = f"https://drive.google.com/drive/folders/{FOLDER}"
CHANNEL = "C0123ABC"
MAIL = {"sender": "sender@example.org", "reply_to": "reply@example.org"}


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on (no mail)."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def mail(monkeypatch, request):
    """An applied configuration with mail, Workspace and Slack set up."""
    original = campaign_builders.configuration_document

    def document():
        """The synthetic configuration, plus three integration records."""
        value = original()
        value["sections"]["integrations"] += [
            {
                "id": str(uuid4()),
                "values": {
                    "kind": kind,
                    "settings": settings,
                    "credential_fingerprint": fingerprint,
                },
            }
            for kind, settings, fingerprint in (
                ("email", MAIL, None),
                ("google_workspace", {"delegated_email": "mail@example.org"}, "b" * 64),
                ("slack", {"channel_id": CHANNEL}, "c" * 64),
            )
        ]
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    return request.getfixturevalue("admin")


def records(admin):
    """The applied integration records' values, by kind."""
    document = admin.service.store.active().document()
    return {
        record["values"]["kind"]: record["values"]
        for record in document["sections"]["integrations"]
    }


def preview(admin, secret, target, changes=None, *, version=None, remove=False):
    """Run ``integration preview`` with ``changes`` (the ``settings`` member)."""
    version = version or admin.service.store.active().digest
    change = (
        ("--remove",) if remove else ("--changes", json.dumps({"settings": changes}))
    )
    return admin(
        "integration",
        "preview",
        target,
        "--expected-version",
        version,
        *change,
        secret=secret,
    )


def confirm(admin, secret, target, token, *, answer=None):
    """Run ``integration confirm``; ``--yes`` unless an ``answer`` is typed."""
    extra = () if answer is not None else ("--yes",)
    return admin(
        "integration",
        "confirm",
        target,
        "--token",
        token,
        *extra,
        secret=secret,
        answer=answer or b"",
    )


def install(admin, document):
    """Install the request a confirmation recorded."""
    request_id = UUID(document["result"]["request"]["request_id"])
    receipt = install_request(
        admin.service.store, request_id=request_id, correlation_id=uuid4()
    )
    assert receipt.state == "applied"


def trail():
    """The command events, fresh-gate events and notices by kind so far."""
    return (
        AuditEvent.objects.filter(event_type=EVENT).count(),
        AuditEvent.objects.filter(event_type="automation_fresh_gate").count(),
        AutomationNotice.objects.filter(kind="fresh_gated").count(),
        AutomationNotice.objects.filter(kind="policy_change").count(),
    )


def refused(document, code):
    """The error document's code is ``code``; return its field names."""
    assert document["error"]["code"] == code, document
    assert "result" not in document
    return [problem["field"] for problem in document["error"].get("fields", [])]


def test_mail_settings_are_reviewed_prompted_and_applied(mail):
    """The review lists stored values; Apply prompts and records the request,
    the command's event and the fresh gate's trail; a repeat returns the
    same request without a new command event."""
    _, secret, _ = paired(mail.service)
    version = mail.service.store.active().digest
    code, document, _ = preview(
        mail, secret, "email", {"sender_name": "Parish Office", "reply_to": None}
    )
    # Only the From name may be null.
    assert code == 1 and refused(document, "invalid") == ["settings.reply_to"]
    code, document, _ = preview(
        mail,
        secret,
        "email",
        {"sender_name": "Parish Office", "reply_to": "OFFICE@example.org"},
    )
    assert code == 0, document
    result = document["result"]
    assert (result["version"], result["target"]) == (version, "email")
    assert result["name"] == "Outgoing email addresses" and not result["removing"]
    assert result["changes"] == [
        {"field": "sender_name", "before": None, "after": "Parish Office"},
        {
            "field": "reply_to",
            "before": "reply@example.org",
            "after": "office@example.org",
        },
    ]
    assert not ConfigurationChangeRequest.objects.exists()
    assert trail() == (0, 0, 0, 0)

    token = result["preview"]["token"]
    # A declined or unanswered prompt changes nothing.
    for answer in (b"\n", b"no\n"):
        code, document, errors = confirm(mail, secret, "email", token, answer=answer)
        assert code == 4, document
        assert document["error"]["code"] == "confirmation_required"
        assert "sender_name" in errors and "Parish Office" not in errors
    assert not ConfigurationChangeRequest.objects.exists()
    code, confirmed, _ = confirm(mail, secret, "email", token, answer=b"yes\n")
    assert code == 0 and confirmed["result"]["created"], confirmed
    assert trail() == (1, 1, 1, 0)
    install(mail, confirmed)
    assert records(mail)["email"]["settings"] == {
        "sender": "sender@example.org",
        "sender_name": "Parish Office",
        "reply_to": "office@example.org",
    }
    code, again, _ = confirm(mail, secret, "email", token)
    assert code == 0 and not again["result"]["created"], again
    request_id = confirmed["result"]["request"]["request_id"]
    assert again["result"]["request"]["request_id"] == request_id
    assert ConfigurationChangeRequest.objects.count() == 1
    assert trail() == (1, 1, 1, 0)

    # Clearing the From name omits it again, as the page's empty field.
    code, document, _ = preview(mail, secret, "email", {"sender_name": ""})
    assert code == 0, document
    assert document["result"]["changes"] == [
        {"field": "sender_name", "before": "Parish Office", "after": None}
    ]


def test_a_slack_change_and_removal_leave_the_policy_notice(mail):
    """Slack is a notification setting: its change and its removal each
    leave a policy_change notice, besides the fresh gate's."""
    _, secret, _ = paired(mail.service)
    code, document, _ = preview(mail, secret, "slack", {"channel_id": "G0NEW1"})
    assert code == 0, document
    assert document["result"]["changes"] == [
        {"field": "channel_id", "before": CHANNEL, "after": "G0NEW1"}
    ]
    code, confirmed, _ = confirm(
        mail, secret, "slack", document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    assert trail() == (1, 1, 1, 1)
    install(mail, confirmed)
    assert records(mail)["slack"]["settings"] == {"channel_id": "G0NEW1"}
    # The key stays as it was: settings never touch it.
    assert records(mail)["slack"]["credential_fingerprint"] == "c" * 64

    code, document, _ = preview(mail, secret, "slack", remove=True)
    assert code == 0, document
    assert document["result"]["removing"] and document["result"]["changes"] == []
    code, confirmed, errors = confirm(
        mail, secret, "slack", document["result"]["preview"]["token"], answer=b"yes\n"
    )
    assert code == 0, confirmed
    assert "This removes Slack notifications." in errors
    assert trail() == (2, 2, 2, 2)
    install(mail, confirmed)
    assert "slack" not in records(mail)


def test_the_offsite_folder_is_added_and_removed_by_settings(mail):
    """The off-site folder has no key: settings add it (any folder link is
    stored canonically) and Remove takes it away."""
    _, secret, _ = paired(mail.service)
    code, document, _ = preview(
        mail, secret, "backup", {"target": f"{LINK}?usp=sharing"}
    )
    assert code == 0, document
    assert document["result"]["changes"] == [
        {"field": "target", "before": None, "after": LINK}
    ]
    code, confirmed, _ = confirm(
        mail, secret, "backup", document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    install(mail, confirmed)
    assert records(mail)["backup"]["settings"] == {"target": LINK}
    code, document, _ = preview(mail, secret, "backup", remove=True)
    assert code == 0, document
    code, confirmed, _ = confirm(
        mail, secret, "backup", document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    install(mail, confirmed)
    assert "backup" not in records(mail)
    # Slack and the folder have no policy notice beyond Slack's.
    assert trail()[3] == 0


def page_preview(browser, store, target, values):
    """The settings page's own review of ``values``; its signed token."""
    response = browser.post(
        f"/admin/system/integrations/{target}/",
        {
            "action": "preview",
            "base_digest": store.active().digest,
            "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        }
        | values,
    )
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def test_tokens_confirm_on_either_channel(mail):
    """Tokens are bound to the Administrator, not the channel."""
    browser, secret, _ = paired(mail.service)
    code, document, _ = preview(mail, secret, "parishsoft", {"organization_id": 54321})
    assert code == 0, document
    assert document["result"]["changes"] == [
        {"field": "organization_id", "before": "12345", "after": "54321"}
    ]
    response = browser.post(
        "/admin/system/integrations/parishsoft/",
        {
            "action": "confirm",
            "preview": document["result"]["preview"]["token"],
            "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        },
    )
    assert response.status_code == 302, response.content
    assert ConfigurationChangeRequest.objects.count() == 1 and trail()[0] == 0

    token = page_preview(
        browser, mail.service.store, "email", {**MAIL, "sender": "new@example.org"}
    )
    code, confirmed, _ = confirm(mail, secret, "email", token)
    assert code == 0 and confirmed["result"]["created"], confirmed
    assert ConfigurationChangeRequest.objects.count() == 2 and trail()[0] == 1


def test_a_preview_that_went_out_of_date_is_stale(mail):
    """Another change installed between the review and Apply: the old token
    is ``stale_version``, with nothing requested or recorded."""
    browser, secret, _ = paired(mail.service)
    code, document, _ = preview(mail, secret, "email", {"sender": "q@example.org"})
    assert code == 0, document
    token = document["result"]["preview"]["token"]
    response = browser.post(
        "/admin/system/integrations/slack/",
        {
            "action": "confirm",
            "preview": page_preview(
                browser, mail.service.store, "slack", {"channel_id": "G0OTHER"}
            ),
            "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        },
    )
    assert response.status_code == 302, response.content
    [other] = ConfigurationChangeRequest.objects.all()
    assert (
        install_request(
            mail.service.store, request_id=other.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    code, document, _ = confirm(mail, secret, "email", token)
    assert code == 1 and refused(document, "stale_version") == []
    assert ConfigurationChangeRequest.objects.count() == 1
    assert trail() == (0, 0, 0, 0)


def test_a_parishsoft_change_keeps_the_stored_schedule_exactly(admin):
    """Saving the organization ID from the command line stores the refresh
    schedule's keys byte for byte, as the page's save does (#632); the
    prompt names only the organization ID."""
    apply_rules_schedule(admin.service.store)
    before = stored_schedule(admin.service.store)
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, "parishsoft", {"organization_id": "777"})
    assert code == 0, document
    assert document["result"]["changes"] == [
        {"field": "organization_id", "before": "12345", "after": "777"}
    ]
    code, confirmed, errors = confirm(
        admin,
        secret,
        "parishsoft",
        document["result"]["preview"]["token"],
        answer=b"yes\n",
    )
    assert code == 0, confirmed
    assert "This changes the ParishSoft settings: organization_id." in errors
    install(admin, confirmed)
    assert records(admin)["parishsoft"]["settings"]["organization_id"] == "777"
    assert stored_schedule(admin.service.store) == before


def test_the_offsite_folder_is_refused_in_the_local_environment(mail, monkeypatch):
    """The local environment never reaches Google Drive, as the page says."""
    from parishkit.stewardship import deployment

    monkeypatch.setattr(
        deployment, "recorded_profile", lambda: deployment.DeploymentProfile.LOCAL
    )
    _, secret, _ = paired(mail.service)
    code, document, _ = preview(mail, secret, "backup", {"target": LINK})
    # Refused before the form is read: no field problem, unlike a bad link.
    assert code == 1 and refused(document, "invalid") == []
    assert not ConfigurationChangeRequest.objects.exists()


def test_preview_refusals(mail):
    """The page's refusals, each with nothing requested."""
    _, secret, _ = paired(mail.service)
    code, document, _ = preview(
        mail, secret, "email", {"sender": "a@example.org"}, version="0" * 64
    )
    assert code == 1 and refused(document, "stale_version") == []
    code, document, _ = preview(mail, secret, "email", {"sender": MAIL["sender"]})
    assert code == 1 and refused(document, "invalid") == ["settings"]
    assert "No settings have changed." in json.dumps(document["error"])
    code, document, _ = preview(mail, secret, "email", {"sender": "x", "reply_to": ""})
    assert code == 1
    assert sorted(refused(document, "invalid")) == [
        "settings.reply_to",
        "settings.sender",
    ]
    code, document, _ = preview(mail, secret, "slack", {"channel_id": "#general"})
    assert code == 1 and refused(document, "invalid") == ["settings.channel_id"]
    code, document, _ = preview(mail, secret, "backup", {"target": "not a link"})
    assert code == 1 and refused(document, "invalid") == ["settings.target"]
    # Only Slack and the off-site folder can be removed, once set up.
    for target in ("email", "backup"):
        code, document, _ = preview(mail, secret, target, remove=True)
        assert code == 1 and refused(document, "not_available") == []
    assert not ConfigurationChangeRequest.objects.exists() and trail() == (0, 0, 0, 0)


def test_integrations_not_set_up(admin):
    """Slack is set up by its key; a required integration with no record is
    the page's 404."""
    _, secret, _ = paired(admin.service)
    code, document, _ = preview(admin, secret, "slack", {"channel_id": CHANNEL})
    assert code == 1 and refused(document, "invalid") == ["key"]
    code, document, _ = preview(admin, secret, "slack", remove=True)
    assert code == 1 and refused(document, "not_available") == []
    code, document, _ = preview(admin, secret, "email", {"sender": "a@example.org"})
    assert code == 1 and refused(document, "not_available") == []
    assert not ConfigurationChangeRequest.objects.exists()


def test_confirm_refuses_foreign_tokens(mail):
    """An altered token, another integration's, another page's or another
    Administrator's, each before or at the prompt, with nothing requested."""
    _, secret, _ = paired(mail.service)
    code, document, _ = preview(mail, secret, "email", {"sender": "z@example.org"})
    token = document["result"]["preview"]["token"]
    code, document, _ = confirm(mail, secret, "email", token[:-2] + "xx")
    assert code == 1 and refused(document, "invalid") == []
    code, document, _ = confirm(mail, secret, "slack", token)
    assert code == 1 and refused(document, "invalid") == []
    payload = signing.loads(token, salt=SALT + "email")
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(mail.service),
        patch=payload["patch"],
        salt=SALT + "email",
    )
    code, document, _ = confirm(mail, secret, "email", other)
    assert code == 1 and refused(document, "denied") == []
    assert not ConfigurationChangeRequest.objects.exists() and trail()[0] == 0


def test_refused_sessions(mail, monkeypatch):
    """A read-only session and Staff are denied; an ended session is exit 5."""
    _, reader, _ = paired(mail.service, scope="read-only")
    change = {"sender": "y@example.org"}
    code, document, _ = preview(mail, reader, "email", change)
    assert code == 1 and refused(document, "denied") == []
    _, secret, _ = paired(mail.service)
    code, document, _ = preview(mail, secret, "email", change)
    token = document["result"]["preview"]["token"]
    code, document, _ = confirm(mail, reader, "email", token)
    assert code == 1 and refused(document, "denied") == []
    revoke(mail.service, secret)
    for code, document, _ in (
        preview(mail, secret, "email", change),
        confirm(mail, secret, "email", token),
    ):
        assert code == 5 and refused(document, "session_ended") == []
    _, staff, _ = paired(mail.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        preview(mail, staff, "email", change),
        confirm(mail, staff, "email", token),
    ):
        assert code == 1 and refused(document, "denied") == []
    # The role comes before any lookup or prompt (#1339): a typed answer, an
    # altered token or an integration with no change is denied, unprompted.
    for code, document, stderr in (
        confirm(mail, staff, "email", token, answer=b"yes\n"),
        confirm(mail, staff, "email", "altered"),
        confirm(mail, staff, "parishsoft", token, answer=b"yes\n"),
    ):
        assert code == 1 and refused(document, "denied") == []
        assert "This " not in stderr and "Type yes" not in stderr, stderr
    assert not ConfigurationChangeRequest.objects.exists() and trail()[0] == 0


SCHEDULE = {
    "rules": [
        {"kind": "full", "at": "02:00"},
        {"kind": "full", "every": 120, "from": "08:00", "to": "18:00"},
    ],
    "skips": [{"at": "12:00"}],
    "skip_around_family_emails": True,
}


def schedule_preview(admin, secret, schedule, *, changes=None, target="parishsoft"):
    """Run ``integration preview --refresh-schedule`` (``-``: standard input)."""
    text = schedule if isinstance(schedule, str) else json.dumps(schedule)
    extra = () if changes is None else ("--changes", json.dumps({"settings": changes}))
    return admin(
        "integration",
        "preview",
        target,
        "--expected-version",
        admin.service.store.active().digest,
        "--refresh-schedule",
        "-",
        *extra,
        secret=secret,
        answer=text.encode(),
    )


def show_schedule(admin, secret):
    """``integration show parishsoft``'s schedule as the page shows it."""
    code, document, _ = admin("integration", "show", "parishsoft", secret=secret)
    assert code == 0, document
    return document["result"]["refresh_schedule"]


def test_a_refresh_schedule_is_reviewed_and_applied(admin):
    """The review shows the schedule before and after and records nothing;
    Apply installs exactly the settings the page's editor derives (#632)."""
    from parishkit.stewardship.source.refresh_rules import stored_settings

    _, secret, _ = paired(admin.service)
    shown = show_schedule(admin, secret)
    code, document, _ = schedule_preview(admin, secret, SCHEDULE)
    assert code == 0, document
    result = document["result"]
    assert result["changes"] == []
    schedule = result["schedule"]
    assert (schedule["before"], schedule["after"]) == (shown, SCHEDULE)
    assert schedule["timezone"]
    assert (
        "Full refreshes added: 08:00, 10:00, 14:00, 16:00 and 18:00."
        in (schedule["differences"])
    )
    assert not ConfigurationChangeRequest.objects.exists()
    assert trail() == (0, 0, 0, 0)

    code, confirmed, errors = confirm(
        admin, secret, "parishsoft", result["preview"]["token"], answer=b"yes\n"
    )
    assert code == 0, confirmed
    assert "refresh_rules" in errors and "08:00" not in errors
    assert trail() == (1, 1, 1, 0)
    install(admin, confirmed)
    settings = records(admin)["parishsoft"]["settings"]
    assert settings == {"organization_id": "12345"} | stored_settings(SCHEDULE) | {
        "refresh_schedule_mode": "advanced"
    }
    assert show_schedule(admin, secret) == SCHEDULE


# An older schedule, stored before the editor: two listed full times, one
# kept off the quarter hour, and no saved mode, as Production's is.
OLDER = {
    "organization_id": "12345",
    "nightly_time": "02:07",
    "full_refresh_times": ["02:07", "13:00"],
    "delta_refresh": "off",
}


def page_rows(browser, rules):
    """The settings page's own post of ``rules``: its drawn mode toggle and
    the switch as drawn, with the rule rows as its script writes them."""
    page = browser.get("/admin/system/integrations/parishsoft/").content.decode()
    drawn = shown(page)
    [mode] = re.findall(
        r'name="refresh_schedule_mode" value="(\w+)" data-editor-mode-choice checked',
        page,
    )
    switch = drawn.get("skip_around_family_emails") == "on"
    return rows(rules, switch=switch) | {"refresh_schedule_mode": mode}


def test_the_command_and_the_page_build_the_same_change(admin):
    """For an older schedule with no saved mode, the command's change equals
    the page's own post of the same rules (built from the page as drawn, not
    the command's rows): the same patch, mode included; the organization ID
    goes with it."""
    replace_settings(admin.service.store, OLDER)
    browser, secret, _ = paired(admin.service)
    nightly = [{"kind": "full", "at": "02:00"}]
    code, document, _ = schedule_preview(
        admin, secret, {"rules": nightly}, changes={"organization_id": "777"}
    )
    assert code == 0, document
    assert document["result"]["changes"] == [
        {"field": "organization_id", "before": "12345", "after": "777"}
    ]
    page = page_preview(
        browser,
        admin.service.store,
        "parishsoft",
        {"organization_id": "777"} | page_rows(browser, nightly),
    )
    salt = SALT + "parishsoft"
    command = signing.loads(document["result"]["preview"]["token"], salt=salt)
    assert command["patch"] == signing.loads(page, salt=salt)["patch"]
    settings = command["patch"][0]["values"]["settings"]
    # The page opened in Advanced (Basic can't show the older schedule).
    assert settings["refresh_schedule_mode"] == "advanced"


def test_an_older_schedules_kept_times_stay_allowed(admin):
    """The kept 02:07 posts back beside an edit and is saved; a new time off
    the quarter hour is refused at its row."""
    replace_settings(admin.service.store, OLDER)
    _, secret, _ = paired(admin.service)
    shown_schedule = show_schedule(admin, secret)
    assert {"kind": "full", "at": "02:07"} in shown_schedule["rules"]
    edited = dict(shown_schedule)
    edited["rules"] = [*shown_schedule["rules"], {"kind": "full", "at": "18:00"}]
    code, document, _ = schedule_preview(admin, secret, edited)
    assert code == 0, document
    code, confirmed, _ = confirm(
        admin, secret, "parishsoft", document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    times = records(admin)["parishsoft"]["settings"]["full_refresh_times"]
    assert "02:07" in times and "18:00" in times
    added = dict(shown_schedule)
    added["rules"] = [*shown_schedule["rules"], {"kind": "full", "at": "03:10"}]
    code, document, _ = schedule_preview(admin, secret, added)
    index = len(shown_schedule["rules"])
    assert code == 1
    assert refused(document, "invalid") == [f"refresh_schedule.rules[{index}]"]


def test_refusals_never_echo_the_input(mail):
    """A kind, time or interval the page's form would quote back, and a new
    quick rule, are refused in fixed words; the value never appears in the
    output or the error stream."""
    _, secret, _ = paired(mail.service)
    for row in (
        {"kind": "SECRETXYZ", "at": "02:00"},
        {"kind": "full", "at": "SECRETXYZ"},
        {"kind": "full", "every": 7777, "from": "00:00", "to": "01:00"},
    ):
        code, document, errors = schedule_preview(mail, secret, {"rules": [row]})
        assert code == 1 and refused(document, "invalid") == [
            "refresh_schedule.rules[0]"
        ]
        for value in ("SECRETXYZ", "7777"):
            assert value not in json.dumps(document) and value not in errors
    quick = {
        "rules": [{"kind": "full", "at": "02:00"}, {"kind": "quick", "at": "12:00"}]
    }
    code, document, _ = schedule_preview(mail, secret, quick)
    assert code == 1 and refused(document, "invalid") == ["refresh_schedule.rules[1]"]
    assert not ConfigurationChangeRequest.objects.exists() and trail() == (0, 0, 0, 0)


def test_an_unchanged_refresh_schedule_is_no_change(admin):
    """``integration show``'s schedule posted back, in any row order, is the
    page's "No settings have changed."; the stored keys stay exactly."""
    apply_rules_schedule(admin.service.store)
    before = stored_schedule(admin.service.store)
    _, secret, _ = paired(admin.service)
    shown = show_schedule(admin, secret)
    shown["rules"].reverse()
    code, document, _ = schedule_preview(admin, secret, shown)
    assert code == 1 and refused(document, "invalid") == ["settings"]
    assert "No settings have changed." in json.dumps(document["error"])
    # With another change, the schedule is kept exactly as stored.
    code, document, _ = schedule_preview(
        admin, secret, shown, changes={"organization_id": "777"}
    )
    assert code == 0 and document["result"]["schedule"] is None, document
    code, confirmed, _ = confirm(
        admin, secret, "parishsoft", document["result"]["preview"]["token"]
    )
    assert code == 0, confirmed
    install(admin, confirmed)
    assert stored_schedule(admin.service.store) == before


def test_refresh_schedule_refusals(mail):
    """The page editor's problems at each row, a malformed document, and
    another integration; nothing is requested or recorded."""
    _, secret, _ = paired(mail.service)
    off = {"rules": [{"kind": "full", "at": "02:00"}, {"kind": "full", "at": "03:10"}]}
    code, document, _ = schedule_preview(mail, secret, off)
    assert code == 1 and refused(document, "invalid") == ["refresh_schedule.rules[1]"]
    assert "Use :00, :15, :30 or :45." in json.dumps(document["error"])
    skip_all = {"rules": [{"kind": "full", "at": "02:00"}], "skips": [{"at": "02:00"}]}
    code, document, _ = schedule_preview(mail, secret, skip_all)
    assert code == 1 and refused(document, "invalid") == ["refresh_schedule.skips[0]"]
    many = {"rules": [{"kind": "full", "at": "02:00"}] * 65}
    code, document, _ = schedule_preview(mail, secret, many)
    assert code == 1 and "refresh_schedule" in refused(document, "invalid")
    assert "A schedule has at most 64 rules." in json.dumps(document["error"])
    code, document, _ = schedule_preview(mail, secret, "rules: [")
    assert code == 1 and refused(document, "invalid") == []
    code, document, _ = schedule_preview(mail, secret, SCHEDULE, target="email")
    assert code == 1 and refused(document, "invalid") == ["refresh_schedule"]
    assert not ConfigurationChangeRequest.objects.exists() and trail() == (0, 0, 0, 0)
