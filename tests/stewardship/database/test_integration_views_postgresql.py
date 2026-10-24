"""Real Admin HTTP flows over immutable YAML, sealed intake and restricted SQL roles."""

import json
import re
from datetime import timedelta
from html import unescape
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.handoff_discovery import publish_handoff
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.provider_models import ProviderValidationContext
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import (
    SealedCredentialStaging,
    SecretReplacementRequest,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_grants
from parishkit.stewardship.source.refresh_rules import stored_settings

from ..policy_factory import address
from ..schedule_rows import rows, shown
from .auth_builders import signed_in, stale_sign_in
from .campaign_builders import change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401
from .test_refresh_frequency_postgresql import stored_before_1147

pytestmark = pytest.mark.django_db(transaction=True)
INDEX = "/admin/system/integrations/"
URL = INDEX + "parishsoft/"
SECRET = "SYNTHETIC-PRIVATE-CANDIDATE"


def post(browser, url, values):
    """Every HTTP mutation goes through real Django CSRF enforcement."""
    return browser.post(
        url, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def hidden(response, name):
    """Use the signed token actually emitted in HTML, not a manufactured intent."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(rf'name="{name}" value="([^"]+)"', response.content.decode()).group(1)
    )


def save_key(browser, candidate, page=None, **fields):
    """Paste a key into the settings page's own form, as a browser would.

    ``page`` is the rendered settings page whose signed intent and base the
    save uses; by default a fresh one. Extra ``fields`` override the form.
    """
    page = page if page is not None else browser.get(URL)
    return post(
        browser,
        URL,
        {
            "action": "preview",
            "base_digest": hidden(page, "base_digest"),
            "intent": hidden(page, "intent"),
            "candidate": candidate,
        }
        | fields,
    )


# The settings page's passive status line, a key change's only status (#1568).
STATUS = URL + "status/"


def edit(store, **values):
    """The page's Save of a changed refresh schedule ("Full at 04:15").

    The known synthetic tenant profile starts with organization 12345,
    which has no field (#1554), so a ParishSoft settings change is a
    schedule change; ``values`` (such as ``shown(page)``) override it.
    """
    return (
        {"action": "preview", "base_digest": store.active().digest}
        | rows([{"kind": "full", "at": "04:15"}])
        | values
    )


@pytest.fixture
def handoff(auth_service, request):
    """Advertise encryption only after bootstrap, using the actual installer role."""
    request.getfixturevalue("isolated_roles")
    private = PrivateHandoff("parishsoft", Key("handoff", "active", b"h" * 32))
    with connection.cursor() as cursor:
        cursor.execute(
            "GRANT SELECT, INSERT ON stewardship_public_credential_handoff "
            "TO pk_stewardship_credential_parishsoft"
        )
        tables, columns = runtime_grants(ServiceRole.WEB)
        for table, permissions in tables.items():
            cursor.execute(
                f'GRANT {", ".join(sorted(permissions))} ON "{table}" '
                "TO pk_stewardship_web"
            )
        for table, permissions in columns.items():
            for permission, names in permissions.items():
                selected = ", ".join(f'"{name}"' for name in sorted(names))
                cursor.execute(
                    f'GRANT {permission} ({selected}) ON "{table}" '
                    "TO pk_stewardship_web"
                )
    with identity("pk_stewardship_credential_parishsoft"):
        publish_handoff(private)
    return private


def test_settings_preview_install_and_exact_retry(auth_service, google):
    """The web editor queues settings; the existing installer applies YAML and SQL."""
    browser, _ = signed_in()
    assert browser.get(INDEX).status_code == browser.get(URL).status_code == 200
    # The ParishSoft page states the data age and the connection (#510).
    page = browser.get(URL).content
    assert b"ParishSoft data: not yet loaded." in page and b"Connection:" in page
    # Save waits for a change to the saved settings (#1330).
    assert b'data-require-change="base_digest"' in page
    assert flow_steps(page) == (STEPS, "Make changes")
    old = auth_service.store.active()
    review = post(browser, URL, edit(auth_service.store))
    # The review is the review dialog's answer (#750), not a page of its own.
    assert b"data-review-answer" in review.content
    assert b"<html" not in review.content
    preview = hidden(review, "preview")
    response = post(browser, URL, {"action": "confirm", "preview": preview})
    assert response.status_code == 302
    # The change's status sits under the integration, by name (#196).
    status = browser.get(response["Location"]).content
    assert f'<li><a href="{URL}">ParishSoft</a></li>'.encode() in status
    assert f'<a href="{URL}">Return to ParishSoft</a>'.encode() in status
    assert auth_service.store.active() == old
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    assert (
        install_request(
            auth_service.store, request_id=row.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    record = auth_service.store.active().document()["sections"]["integrations"][0][
        "values"
    ]
    # The organization is kept (#1554); the schedule is the one saved.
    assert record["settings"]["organization_id"] == "12345"
    assert record["settings"]["nightly_time"] == "04:15"
    assert record["credential_fingerprint"] == "a" * 64
    assert (
        post(browser, URL, {"action": "confirm", "preview": preview})["Location"]
        == response["Location"]
    )


def full_at(value):
    """A "Full at" rule."""
    return {"kind": "full", "at": value}


def applied_settings(store):
    """The applied ParishSoft settings."""
    return store.active().document()["sections"]["integrations"][0]["values"][
        "settings"
    ]


def install(store, response):
    """Install the request a confirmation queued; return its applied version."""
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedConfigurationVersion,
    )

    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    assert (
        install_request(store, request_id=request.pk, correlation_id=uuid4()).state
        == "applied"
    )
    return request, AppliedConfigurationVersion.objects.get(
        pk=request.candidate_version_id
    )


def replace_settings(store, settings):
    """Store ``settings`` as the ParishSoft integration's settings."""
    base = store.active()
    integration = base.document()["sections"]["integrations"][0]
    change(
        store,
        base,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "integrations",
                "id": integration["id"],
                "values": {"settings": settings},
            }
        ],
    )


def test_a_saved_rule_reaches_yaml_and_the_real_scheduler(auth_service, google):
    """The editor's rows become rules and lists the scheduler runs (#632).

    The first writer of ``refresh_rules``: a full time typed as "3:15am" is
    stored with its derived list and no quick updates (#1147), and the
    scheduler's one tick names 03:15 at 03:15 parish time.
    """
    from zoneinfo import ZoneInfo

    from parishkit.stewardship.jobs.scheduler import scheduler_session
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
    from parishkit.stewardship.source.production import produce_refreshes
    from parishkit.stewardship.source.refresh_models import SourceRefreshTick

    SourceCurrent.objects.get_or_create(singleton=True)
    SourceMutationLease.objects.get_or_create(singleton=True)
    browser, _ = signed_in()
    posted = rows([full_at("03:15")]) | {
        "rules-0-at": "3:15am",
        "refresh_schedule_mode": "advanced",
    }
    review = post(browser, URL, edit(auth_service.store) | posted)
    assert review.status_code == 200, review.content
    page = unescape(review.content.decode())
    assert "Full refreshes added: 03:15." in page
    assert "Full refreshes removed: 02:00." in page
    response = post(
        browser, URL, {"action": "confirm", "preview": hidden(review, "preview")}
    )
    assert response.status_code == 302
    request, configuration = install(auth_service.store, response)
    assert request.request_schema == "source-cadence-patch-v8"
    assert configuration.validation_schema == "source-cadence-v8"
    settings = configuration.canonical_document["sections"]["integrations"][0][
        "values"
    ]["settings"]
    expected = stored_settings(
        {
            "rules": [full_at("03:15")],
            "skips": [],
            "skip_around_family_emails": False,
        }
    )
    # The editor mode it was saved in is stored beside the rules (#1122).
    assert settings == {"organization_id": "12345"} | expected | {
        "refresh_schedule_mode": "advanced"
    }
    assert settings["nightly_time"] == "03:15"
    assert settings["full_refresh_times"] == ["03:15"]
    assert settings["delta_refresh"] == "off"
    assert "quick_refresh_times" not in settings
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 1
    tick = SourceRefreshTick.objects.get()
    assert tick.command.cause == "nightly"
    assert tick.configuration_id == configuration.pk
    assert tick.nightly_time == "03:15"
    local = tick.due_at.astimezone(ZoneInfo(tick.timezone))
    assert (local.hour, local.minute) == (3, 15)
    # The page now shows the saved rules, and saving them as shown is no change.
    page = browser.get(URL).content.decode()
    assert 'name="rules-0-at" value="03:15"' in page
    unchanged = post(browser, URL, edit(auth_service.store) | shown(page))
    assert b"No settings have changed." in unchanged.content


def test_a_row_that_cannot_be_read_is_refused_at_its_row(auth_service, google):
    """A refused entry is named at its row and queues nothing (#631, #632)."""
    browser, _ = signed_in()
    requests = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        URL,
        edit(auth_service.store)
        | rows([full_at("02:00"), full_at("02:00")])
        | {"rules-0-at": "3:15 PM", "rules-1-at": "25:00"},
    )
    assert refused.status_code == 400
    page = unescape(refused.content.decode())
    assert "At: “25:00” has no hour 25" in page
    assert 'id="rules-1-messages"' in page
    # Drawn again with what was posted, Save does not wait for a change.
    assert "data-require-change" not in page
    assert "Fix 1 problem before saving:" in page
    assert ConfigurationChangeRequest.objects.count() == requests


def test_an_older_document_is_shown_as_rules_and_saved_unchanged(auth_service, google):
    """A nightly time without a list shows as "Full at 03:00" (#465, #632).

    Saving the page as shown is no change, and writes no new key.
    """
    replace_settings(
        auth_service.store, {"organization_id": "12345", "nightly_time": "03:00"}
    )
    requests = ConfigurationChangeRequest.objects.count()
    browser, _ = signed_in()
    page = browser.get(URL).content.decode()
    assert 'name="rules-0-at" value="03:00"' in page
    assert "Your current schedule, shown as rules." in page
    unchanged = post(browser, URL, edit(auth_service.store) | shown(page))
    assert unchanged.status_code == 400
    assert b"No settings have changed." in unchanged.content
    assert ConfigurationChangeRequest.objects.count() == requests


# A schedule stored before the editor, with a kept full time at 23:50: it runs
# 23:45 and 00:00 quick updates, each less than 15 minutes from it.
CLOSE = {
    "organization_id": "12345",
    "nightly_time": "00:00",
    "full_refresh_times": ["00:00", "23:50"],
    "delta_refresh": "quarter_hour",
}


def test_a_posted_organization_is_refused_with_the_schedule_kept(auth_service, google):
    """The organization is chosen once, in setup (#1554): a Save that posts
    one, even with the shown schedule untouched, is refused as unexpected
    input, queues nothing and keeps the stored settings byte for byte."""
    replace_settings(auth_service.store, CLOSE)
    requests = ConfigurationChangeRequest.objects.count()
    browser, _ = signed_in()
    page = browser.get(URL).content.decode()
    assert 'name="organization_id"' not in page
    refused = post(
        browser,
        URL,
        edit(auth_service.store) | shown(page) | {"organization_id": "54321"},
    )
    assert refused.status_code == 400
    assert ConfigurationChangeRequest.objects.count() == requests
    assert applied_settings(auth_service.store) == CLOSE


def test_a_changed_schedule_with_problems_is_refused(auth_service, google):
    """Once the schedule changes, its problems must be fixed before saving."""
    replace_settings(auth_service.store, CLOSE)
    requests = ConfigurationChangeRequest.objects.count()
    browser, _ = signed_in()
    posted = shown(browser.get(URL).content.decode())
    count = int(posted["skips-TOTAL_FORMS"])
    posted |= {
        "skips-TOTAL_FORMS": str(count + 1),
        f"skips-{count}-shape": "at",
        f"skips-{count}-at": "12:00",
    }
    refused = post(browser, URL, edit(auth_service.store) | posted)
    assert refused.status_code == 400
    assert b"Fix " in refused.content
    assert ConfigurationChangeRequest.objects.count() == requests
    assert applied_settings(auth_service.store) == CLOSE


def test_the_live_check_saves_nothing_and_needs_the_page_permission(
    auth_service, google
):
    """The read-only check: no request, no audit entry, never cached (#632)."""
    browser, _ = signed_in()
    page = browser.get(URL).content.decode()
    requests = ConfigurationChangeRequest.objects.count()
    audits = AuditEvent.objects.count()
    check = INDEX + "parishsoft/schedule-check/"
    answer = post(browser, check, shown(page))
    assert answer.status_code == 200, answer.content
    assert answer["Cache-Control"] == "no-store"
    assert b'data-blocking="false"' in answer.content
    # The page and the check both draw the seven-day grid (slice 4): seven
    # day rows, from the real preview reader, with the current quarter hour
    # marked once (and named in the legend).
    for drawn in (page, answer.content.decode()):
        assert drawn.count('<div class="schedule-grid-row">') == 7
        assert drawn.count(" grid-now") == 2
    changed = post(browser, check, shown(page) | {"rules-0-at": "02:10"})
    assert b'data-blocking="true"' in changed.content
    assert "At: Use :00, :15, :30 or :45." in unescape(changed.content.decode())
    assert ConfigurationChangeRequest.objects.count() == requests
    assert AuditEvent.objects.count() == audits
    # Only the editor's fields: never a pasted key or another setting.
    for extra in ({"candidate": SECRET}, {"organization_id": "1"}):
        refused = post(browser, check, shown(page) | extra)
        assert refused.status_code == 400
        assert SECRET.encode() not in refused.content
    assert (
        post(browser, INDEX + "slack/schedule-check/", shown(page)).status_code == 404
    )
    assert browser.get(check).status_code == 405


# A schedule saved with its rules (#632) before quick updates were removed
# (#1147): 12 full times every hour from 00:00 to 12:00 less 06:00, and a
# quick update at 18:00.
FULL_RULE = {"kind": "full", "every": 60, "from": "00:00", "to": "12:00"}
QUICK_RULE = {"kind": "quick", "at": "18:00"}


def rules_schedule():
    """The stored schedule's keys, as a schedule saved before #1147 has them."""
    full = stored_settings(
        {
            "rules": [FULL_RULE],
            "skips": [{"at": "06:00"}],
            "skip_around_family_emails": False,
        }
    )
    return full | {
        "refresh_rules": full["refresh_rules"] | {"rules": [FULL_RULE, QUICK_RULE]},
        "delta_refresh": "times",
        "quick_refresh_times": ["18:00"],
    }


def apply_rules_schedule(store):
    """Store ``rules_schedule`` beside the integration's other settings.

    A new schedule may no longer hold a quick rule, so it is applied as a
    version stored before #1147 (``stored_before_1147``).
    """
    base = store.active()
    integration = base.document()["sections"]["integrations"][0]
    with stored_before_1147():
        applied = change(
            store,
            base,
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "integrations",
                    "id": integration["id"],
                    "values": {
                        "settings": integration["values"]["settings"] | rules_schedule()
                    },
                }
            ],
        )
    assert applied.state == "applied"


def stored_schedule(store):
    """The active document's schedule keys, serialized byte for byte."""
    settings = store.active().document()["sections"]["integrations"][0]["values"][
        "settings"
    ]
    return json.dumps(
        {name: settings[name] for name in rules_schedule()}, sort_keys=True
    )


def test_a_schedule_saved_with_its_rules_is_shown_as_saved(auth_service, google):
    """The page shows a rules schedule's own full rows; saved as shown, no change.

    Its stored quick rule is not shown at all (#1147). The old frequency and
    time-list fields are gone (#632): posting them is refused as an unknown
    field.
    """
    apply_rules_schedule(auth_service.store)
    requests = ConfigurationChangeRequest.objects.count()
    browser, _ = signed_in()
    page = browser.get(URL).content.decode()
    for name in ("full_refresh", "full_refresh_times", "delta_refresh"):
        assert f'name="{name}"' not in page
    assert 'name="rules-0-every" ' in page and 'name="skips-0-at" value="06:00"' in page
    assert shown(page)["rules-TOTAL_FORMS"] == "1"
    assert 'name="rules-1-' not in page
    assert "18:00" not in shown(page).values()
    assert "Your current schedule, shown as rules." not in page
    unchanged = post(browser, URL, edit(auth_service.store) | shown(page))
    assert unchanged.status_code == 400
    assert b"No settings have changed." in unchanged.content
    stale = post(
        browser,
        URL,
        edit(auth_service.store, full_refresh_times="02:00"),
    )
    assert stale.status_code == 400
    assert ConfigurationChangeRequest.objects.count() == requests


def test_a_new_key_on_a_rules_schedule_leaves_the_schedule_alone(
    auth_service, google, handoff
):
    """The key-rotation save path queues only the key, never a schedule change."""
    apply_rules_schedule(auth_service.store)
    requests = set(ConfigurationChangeRequest.objects.values_list("pk", flat=True))
    browser, _ = signed_in()
    with identity("pk_stewardship_web"):
        page = browser.get(URL)
        rows_shown = shown(page.content.decode())
        # A key and a schedule change are not saved together.
        refused = save_key(
            browser, SECRET, page, **(rows_shown | {"skips-0-at": "07:00"})
        )
        assert refused.status_code == 400
        assert b"a new key and a refresh schedule change" in refused.content
        page = browser.get(URL)
        response = save_key(browser, SECRET, page, **shown(page.content.decode()))
        assert response.status_code == 302, response.content
    (queued,) = ConfigurationChangeRequest.objects.exclude(pk__in=requests)
    for item in queued.patch:
        assert "settings" not in item["values"]


def confirm_and_install(browser, store, review):
    """Apply a reviewed settings change; return the applied settings."""
    assert review.status_code == 200, review.content
    response = post(
        browser, URL, {"action": "confirm", "preview": hidden(review, "preview")}
    )
    assert response.status_code == 302
    install(store, response)
    return store.active().document()["sections"]["integrations"][0]["values"][
        "settings"
    ]


def test_a_changed_schedule_drops_the_stored_quick_rule(auth_service, google):
    """A stored quick rule is not shown and offers no action (#1147); changing
    the schedule saves only its full rules, with quick updates off."""
    apply_rules_schedule(auth_service.store)
    browser, _ = signed_in()
    page = unescape(browser.get(URL).content.decode())
    assert "Remove quick updates" not in page and "Quick update" not in page
    assert 'value="advanced" data-editor-mode-choice checked' in page
    posted = shown(page) | {"skips-0-at": "07:00", "refresh_schedule_mode": "advanced"}
    review = post(browser, URL, edit(auth_service.store) | posted)
    reviewed = unescape(review.content.decode())
    assert "Quick updates removed" not in reviewed
    assert "Full refreshes added: 06:00." in reviewed
    assert "Full refreshes removed: 07:00." in reviewed
    settings = confirm_and_install(browser, auth_service.store, review)
    assert settings["delta_refresh"] == "off" and "quick_refresh_times" not in settings
    assert settings["refresh_rules"] == {
        "rules": [FULL_RULE],
        "skips": [{"at": "07:00"}],
        "skip_around_family_emails": False,
    }
    assert "06:00" in settings["full_refresh_times"]
    assert "07:00" not in settings["full_refresh_times"]
    assert settings["refresh_schedule_mode"] == "advanced"


def test_a_basic_choice_is_saved_with_its_mode(auth_service, google):
    """Basic's controls are posted while Basic is shown; the server reads the
    rows they were written into and stores the mode beside them (#1122)."""
    browser, _ = signed_in()
    posted = rows([{"kind": "full", "every": 240, "from": "00:00", "to": "20:00"}]) | {
        "refresh_schedule_mode": "basic",
        "refresh_basic": "every",
        "refresh_basic_hours": "4",
    }
    review = post(browser, URL, edit(auth_service.store) | posted)
    settings = confirm_and_install(browser, auth_service.store, review)
    assert settings["refresh_schedule_mode"] == "basic"
    assert settings["full_refresh_times"] == ["00:00", "04:00", "08:00"] + [
        "12:00",
        "16:00",
        "20:00",
    ]
    page = browser.get(URL).content.decode()
    assert 'value="basic" data-editor-mode-choice checked' in page
    assert '<option value="4" selected>' in page


@pytest.mark.parametrize("direct_insert", [False, True])
def test_cadence_upgrade_does_not_bypass_manual_grant_provenance(
    auth_service, direct_insert
):
    """A structurally valid new rule still must identify this precise request."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.accounts.request_patch import build_candidate

    base = auth_service.store.active()
    integration = base.document()["sections"]["integrations"][0]
    rule = address("new-admin@example.org")
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": integration["id"],
            "values": {
                "settings": integration["values"]["settings"]
                | {"nightly_time": "03:15"}
            },
        },
        {"operation": "add", "section": "login_rules", **rule},
    ]
    if direct_insert:
        # A valid digest/patch cannot hide wrong provenance from the installer,
        # even if a producer bypasses the ordinary intake helper entirely.
        intent = build_candidate(
            base,
            patch,
            candidate_id=uuid4(),
            request_schema="source-cadence-patch-v8",
        )
        request = ConfigurationChangeRequest.objects.create(
            actor_id=uuid4(),
            request_key=uuid4(),
            request_schema="source-cadence-patch-v8",
            base_id=base.version_id,
            patch=intent.patch(),
            payload_fingerprint=intent.payload_fingerprint,
            candidate_version_id=intent.candidate.version_id,
            candidate_digest=intent.candidate.digest,
        )
        result = install_request(
            auth_service.store, request_id=request.pk, correlation_id=uuid4()
        )
        assert result.state == "failed" and result.failure_code == "invalid_candidate"
        assert auth_service.store.active() == base
    else:
        with pytest.raises(ConfigError):
            record_request(
                base_digest=base.digest,
                patch=patch,
                actor_id=uuid4(),
                request_key=uuid4(),
                correlation_id=uuid4(),
            )
        assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_id": "0"},
        {"organization_id": "12345"},
        {"candidate": SECRET},
        {"credential_fingerprint": "b" * 64},
        {"base_digest": "malformed"},
        {"organization_id": ["123", "456"]},
    ],
)
def test_invalid_and_hidden_settings_do_not_queue(auth_service, google, changes):
    """Private credentials and unknown fields are never part of public YAML input."""
    browser, _ = signed_in()
    response = post(browser, URL, edit(auth_service.store, **changes))
    assert response.status_code == 400
    assert SECRET.encode() not in response.content
    assert not ConfigurationChangeRequest.objects.exists()


def test_replacement_seals_once_and_real_web_cannot_read_ciphertext(
    auth_service, google, handoff
):
    """The target's private key opens the exact request; HTML/audit contain no value."""
    browser, _ = signed_in()
    with identity("pk_stewardship_web"):
        page = browser.get(URL)
        response = save_key(browser, SECRET, page)
        assert response.status_code == 302, response.content
        progress = browser.get(STATUS)
        assert progress.status_code == 200
        assert progress["Cache-Control"] == "no-store"
        assert SECRET.encode() not in progress.content
        # An identical browser retry reuses the original request.
        assert save_key(browser, SECRET, page).status_code == 302
        changed = save_key(browser, "DIFFERENT-PRIVATE", page)
        assert changed.status_code == 400
        assert b"DIFFERENT-PRIVATE" not in changed.content
    row = SecretReplacementRequest.objects.get()
    staged = SealedCredentialStaging.objects.get()
    assert row.state == "staged" and row.required_consumers == ["worker"]
    assert timedelta(minutes=59) < row.expires_at - row.created_at <= timedelta(hours=1)
    assert handoff.open(row.pk, staged.ciphertext) == SECRET.encode()
    context = ProviderValidationContext.objects.get()
    assert context.settings == {"organization_id": 12345}
    assert (
        context.actor_id
        == row.requested_by_id
        == PortalSession.objects.get().principal_id
    )
    assert SECRET not in json.dumps(list(AuditEvent.objects.values()), default=str)
    assert (
        auth_service.store.active().document()["sections"]["integrations"][0]["values"][
            "credential_fingerprint"
        ]
        == "a" * 64
    )


def test_private_form_errors_and_csrf_never_stage_or_redisplay(
    auth_service, google, handoff
):
    """Invalid controls, CSRF and bad signatures fail before sealed persistence."""
    browser, _ = signed_in()
    page = browser.get(URL)
    token = hidden(page, "intent")
    for fields in [
        {"intent": ""},
        {"intent": "forged"},
        {"target": "slack"},
        {"intent": [token, token]},
    ]:
        response = save_key(browser, SECRET, page, **fields)
        assert response.status_code == 400
        assert SECRET.encode() not in response.content
    fields = {
        "action": "preview",
        "base_digest": hidden(page, "base_digest"),
        "organization_id": "12345",
        "candidate": SECRET,
        "intent": token,
    }
    assert browser.post(URL, fields).status_code == 403
    assert not SecretReplacementRequest.objects.exists()


def test_stale_configuration_denies_secret_intake_and_an_old_sign_in_saves(
    auth_service, google, handoff, monkeypatch
):
    """Old forms do not bind credentials to changed settings (#1547).

    A sign-in older than five minutes still saves the key from a current page.
    """
    browser, _ = signed_in()
    page = browser.get(URL)
    version = auth_service.store.active()
    record = version.document()["sections"]["parish"][0]
    change(
        auth_service.store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": record["id"],
                "values": {"name": "Changed Parish"},
            }
        ],
    )
    assert save_key(browser, SECRET, page).status_code == 409
    assert not SecretReplacementRequest.objects.exists()
    stale_sign_in()
    response = save_key(browser, SECRET)
    assert (response.status_code, response["Location"]) == (302, URL)
    assert SecretReplacementRequest.objects.count() == 1


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_integrations_and_secrets_are_admin_only(auth_service, google, role):
    """Direct endpoints and submitted targets cannot promote a read-only role."""
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    for url in (INDEX, URL, STATUS):
        assert browser.get(url).status_code == 403
    # The refresh schedule's live check needs the page's own permission.
    assert post(browser, URL + "schedule-check/", {}).status_code == 403
    response = post(
        browser,
        URL,
        {"action": "preview", "candidate": SECRET, "intent": "forged"},
    )
    assert response.status_code == 403
    assert not SecretReplacementRequest.objects.exists()


def test_missing_handoff_and_unknown_targets_fail_closed(auth_service, google):
    """Web cannot invent a key, create an unknown integration or claim readiness."""
    browser, _ = signed_in()
    page = browser.get(URL)
    assert page.status_code == 200 and page.context["credential_unavailable"]
    assert b'name="candidate"' not in page.content
    # Say what is missing and who can fix it, not "your system administrator".
    assert b"stores new keys for this integration has not started" in page.content
    assert b"ask whoever manages the parish" in page.content
    assert b"system administrator" not in page.content
    assert browser.get(INDEX + "unknown/").status_code == 404
    # The unlinked stand-alone "replace credential" page is gone: it staged a
    # key with nothing to switch to it, which stopped mail (#307 M1).
    for target in ("parishsoft", "google_workspace", "slack", "email"):
        assert browser.get(f"{INDEX}{target}/credential/").status_code == 404
    # The key change pages are gone, with no redirect (#1568).
    for page in ("", "selection/"):
        address = f"/admin/system/key-changes/{uuid4()}/{page}"
        assert browser.get(address).status_code == 404


def test_a_key_change_is_followed_live_and_never_renews_idle(
    auth_service, google, handoff
):
    """The settings page's status line is a passive read: polling it cannot
    keep an otherwise idle login alive, and it stays pending until the key
    is installed. It links no separate status page (#1568)."""
    browser, _ = signed_in()
    assert save_key(browser, SECRET).status_code == 302
    page = browser.get(URL).content
    assert b"This page updates by itself." in page and b'name="candidate"' not in page
    session = PortalSession.objects.get(revoked_at__isnull=True)
    activity = session.last_activity_at
    for _ in range(3):
        progress = browser.get(STATUS)
        assert progress.status_code == 200
        assert b'data-live-status="integration"' in progress.content
        assert b"data-live-pending" in progress.content
        assert b"Installing the new key" in progress.content
        assert b"key-changes" not in progress.content
        assert SECRET.encode() not in progress.content
    session.refresh_from_db()
    assert session.last_activity_at == activity


def test_an_old_sign_in_reviews_and_confirms_settings(auth_service, google):
    """A sign-in older than five minutes reviews and confirms a change (#1547).

    The reviewed form confirms exactly one request, and posting it again
    returns that same request.
    """
    browser, _ = signed_in()
    stale_sign_in()
    review = post(browser, URL, edit(auth_service.store))
    assert review.status_code == 200
    preview = hidden(review, "preview")
    confirmed = post(browser, URL, {"action": "confirm", "preview": preview})
    assert confirmed.status_code == 302
    again = post(browser, URL, {"action": "confirm", "preview": preview})
    assert again["Location"] == confirmed["Location"]
    assert ConfigurationChangeRequest.objects.count() == 1


def fetch(browser, url, values):
    """Post ``values`` as the review dialog's script does (#750)."""
    return browser.post(
        url,
        values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
        HTTP_X_REQUESTED_WITH="fetch",
    )


def test_the_review_dialog_shows_public_settings_only(auth_service, google, handoff):
    """Save without a key answers the review dialog with its review (#750):
    ParishSoft's one editable setting is its refresh schedule (#1554), so
    the schedule's changes in words, and the signed preview as its only
    field. Neither the key field, its signed
    request nor the key in use is ever in it, even with spaces posted as the
    key; Apply's answer is the change's status, which the dialog follows."""
    browser, _ = signed_in()
    page = browser.get(URL)
    intent = hidden(page, "intent")
    review = fetch(
        browser, URL, edit(auth_service.store) | {"intent": intent, "candidate": "  "}
    )
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    assert 'data-review-done="ParishSoft settings changed."' in text
    assert re.findall(r'<input type="hidden" name="(\w+)"', text) == ["preview"]
    # ParishSoft's one editable setting is its schedule (#1554), whose
    # review lists its changes in words.
    assert "Full refreshes added: 04:15." in text
    for secret in (intent, "a" * 64, 'name="candidate"', 'name="intent"'):
        assert secret not in text
    confirmed = fetch(
        browser, URL, {"action": "confirm", "preview": hidden(review, "preview")}
    )
    assert confirmed.status_code == 302
    assert not SecretReplacementRequest.objects.exists()


def test_an_old_sign_in_reviews_and_applies_in_the_dialog(auth_service, google):
    """The review dialog's Save and Apply admit a sign-in older than five
    minutes (#1547): Save answers with the review, Apply queues one request."""
    browser, _ = signed_in()
    stale_sign_in()
    review = fetch(browser, URL, edit(auth_service.store))
    assert review.status_code == 200 and b"data-review-answer" in review.content
    confirmed = fetch(
        browser, URL, {"action": "confirm", "preview": hidden(review, "preview")}
    )
    assert confirmed.status_code == 302
    assert ConfigurationChangeRequest.objects.count() == 1


def test_an_unchanged_save_is_refused_to_the_dialog(auth_service, google):
    """A Save with nothing changed, which the page's change gate keeps out
    of reach, is refused to the review dialog's script as the server's own
    words (400, with its fix) and queues nothing (#750)."""
    browser, _ = signed_in()
    shown_page = shown(browser.get(URL).content.decode())
    refused = fetch(browser, URL, edit(auth_service.store) | shown_page)
    assert refused.status_code == 400
    assert refused.json()["refusal"]["message"] == "No settings have changed."
    assert refused.json()["refusal"]["fix"] == "Nothing was changed."
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize("pasted", [SECRET, "\ufeff"])
def test_a_key_from_a_script_still_takes_the_key_save_path(
    auth_service, google, handoff, pasted
):
    """A key posted by a script (as the dialog's would be) is still the
    key-save path, never a review: the sealed intake and its redirect back
    to the page, with the key never echoed. A value str.strip() keeps, such
    as U+FEFF, is a key there, as the page script reads it (#750), and the
    key rule refuses it at the field (#1577), still never as a review."""
    browser, _ = signed_in()
    page = browser.get(URL)
    response = fetch(
        browser,
        URL,
        edit(auth_service.store)
        | shown(page.content.decode())
        | {"intent": hidden(page, "intent"), "candidate": pasted},
    )
    assert b"data-review-answer" not in response.content
    if pasted == SECRET:
        assert (response.status_code, response["Location"]) == (302, URL)
        assert SecretReplacementRequest.objects.count() == 1
    else:
        assert response.status_code == 400
        assert b"This does not look like a ParishSoft API key." in response.content
        assert nothing_staged()


def key_review(browser, page, **fields):
    """Save with a key pasted, as the review dialog's script reviews it
    (#1568): the page's form without the key, marked key_review."""
    return fetch(
        browser,
        URL,
        {
            "action": "preview",
            "base_digest": hidden(page, "base_digest"),
            "intent": hidden(page, "intent"),
            "key_review": "yes",
        }
        | fields,
    )


def nothing_staged():
    """No key request, sealed key or configuration request exists."""
    return not (
        SecretReplacementRequest.objects.exists()
        or SealedCredentialStaging.objects.exists()
        or ConfigurationChangeRequest.objects.exists()
    )


def test_a_key_is_confirmed_in_the_dialog_and_staged_by_apply(
    auth_service, google, handoff
):
    """Save with a key pasted is a confirmation in the review dialog (#1568).

    Its Review is posted without the key and stages, seals and records
    nothing: the answer names the integration, lists no other setting (the
    organization has no field, #1554), says what happens next, and carries
    no field at all, so
    no key, signed request or preview can reach the dialog. Apply posts the
    page's own form, key included, to the unchanged key save: exactly one
    sealed request and one selection request, and an answer (the page,
    pending) that never echoes the key.
    """
    from parishkit.stewardship.accounts.integration_credentials import selection_key

    browser, _ = signed_in()
    with identity("pk_stewardship_web"):
        page = browser.get(URL)
        intent = hidden(page, "intent")
        review = key_review(browser, page)
        assert review.status_code == 200, review.content
        text = unescape(review.content.decode())
        assert "data-review-key " in text
        assert 'data-review-title="Replace the ParishSoft key?"' in text
        assert 'data-review-describedby="integration-review-summary"' in text
        assert "ending in {tail}" in text
        assert "The new key is checked with ParishSoft in the background." in text
        assert "compact-review" not in text
        assert re.findall(r"<input\b", text) == []
        for absent in (intent, 'name="candidate"', "a" * 64):
            assert absent not in text
        assert nothing_staged()
        applied = fetch(
            browser,
            URL,
            {
                "action": "preview",
                "base_digest": hidden(page, "base_digest"),
                "intent": intent,
                "candidate": SECRET,
            },
        )
        assert (applied.status_code, applied["Location"]) == (302, URL)
        answer = browser.get(URL).content
    row = SecretReplacementRequest.objects.get()
    assert SealedCredentialStaging.objects.count() == 1
    (selection,) = ConfigurationChangeRequest.objects.all()
    assert selection.request_key == selection_key(row.pk)
    assert b"Installing the new key" in answer
    for content in (review.content, applied.content, answer):
        assert SECRET.encode() not in content
    assert SECRET not in json.dumps(list(AuditEvent.objects.values()), default=str)


@pytest.mark.parametrize(
    "fields", [{"candidate": SECRET}, {"key_review": "no"}, {"candidate": " x "}]
)
def test_a_key_review_never_takes_a_key(auth_service, google, handoff, fields):
    """A Review marked key_review that carries a key (or is marked otherwise)
    is refused as unexpected input, never staged, and the key never echoed."""
    browser, _ = signed_in()
    refused = key_review(browser, browser.get(URL), **fields)
    assert refused.status_code == 400
    assert SECRET.encode() not in refused.content
    assert nothing_staged()


def test_a_key_review_never_takes_an_uploaded_key(auth_service, google, handoff):
    """A key's Review posted multipart with the key as a ``candidate`` file
    part is refused like a pasted one (400), and the key never echoed."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    browser, _ = signed_in()
    upload = SimpleUploadedFile("key.txt", SECRET.encode())
    refused = key_review(browser, browser.get(URL), candidate=upload)
    assert refused.status_code == 400
    assert b"this page sent something the server did not expect" in refused.content
    assert SECRET.encode() not in refused.content
    assert nothing_staged()


def test_a_key_review_waits_for_a_key_change_installing(auth_service, google, handoff):
    """A second tab's key Review while the first tab's key is still
    installing is refused in plain words (409), before its Apply could meet
    the intake guard; only the first key is staged and nothing is echoed."""
    browser, _ = signed_in()
    with identity("pk_stewardship_web"):
        first, second = browser.get(URL), browser.get(URL)
        applied = fetch(
            browser,
            URL,
            {
                "action": "preview",
                "base_digest": hidden(first, "base_digest"),
                "intent": hidden(first, "intent"),
                "candidate": SECRET,
            },
        )
        assert applied.status_code == 302, applied.content
        refused = key_review(browser, second)
    assert refused.status_code == 409, refused.content
    assert b"A new key is already being installed." in refused.content
    assert SECRET.encode() not in refused.content
    assert SecretReplacementRequest.objects.count() == 1


@pytest.mark.parametrize("refusal", ["schedule", "locked", "forged", "stale"])
def test_a_key_review_refuses_before_apply(
    auth_service, google, handoff, monkeypatch, refusal
):
    """A key's Review refuses what its save would (#1568). A key with a
    refresh schedule change is refused at its field, the form drawn again
    (400) so the dialog stays shut; a posted organization, which has no
    field (#1554), is unexpected input (400), and a forged key request
    (400) and an out-of-date page (409) are the dialog's refusals. Nothing
    is staged."""
    if refusal == "schedule":
        apply_rules_schedule(auth_service.store)
    browser, _ = signed_in()
    requests = set(ConfigurationChangeRequest.objects.values_list("pk", flat=True))
    with identity("pk_stewardship_web"):
        page = browser.get(URL)
        fields = {
            "schedule": shown(page.content.decode()) | {"skips-0-at": "07:00"},
            "locked": {"organization_id": "54321"},
            "forged": {"intent": "forged"},
            "stale": {"base_digest": "b" * 64},
        }[refusal]
        response = key_review(browser, page, **fields)
    assert response.status_code == {"stale": 409}.get(refusal, 400), response.content
    form = b'id="integration-form"' in response.content
    assert form is (refusal == "schedule")
    if refusal == "schedule":
        assert b"a new key and a refresh schedule change" in response.content
    assert not SecretReplacementRequest.objects.exists()
    assert not SealedCredentialStaging.objects.exists()
    assert set(ConfigurationChangeRequest.objects.values_list("pk", flat=True)) == (
        requests
    )


def test_the_parishsoft_key_sits_between_the_organization_and_the_schedule(
    auth_service, google, handoff
):
    """ParishSoft's form draws its organization line (text naming it, never
    a field, #1554), the new key, then the refresh schedule editor, Save,
    and the schedule's outlook after the form, so the key never splits the
    schedule's entry from its display (#1568). The form reviews a pasted key
    without it (data-review-key)."""
    browser, _ = signed_in()
    page = browser.get(URL).content.decode()
    places = [
        page.index(marker)
        for marker in (
            "ParishSoft organization: ParishSoft organization 12345",
            'name="candidate"',
            'name="rules-TOTAL_FORMS"',
            'id="refresh-schedule-status"',
            'id="integration-outcome"',
            'id="refresh-schedule-outlook"',
        )
    ]
    assert places == sorted(places)
    assert 'data-review-key="candidate"' in page
    assert "data-review-native" not in page


def test_spaces_the_server_strips_are_no_key(auth_service, google, handoff):
    """A key field holding only what str.strip() removes (U+0085 included)
    is no key: Save is reviewed in the dialog and nothing is sealed (#750)."""
    browser, _ = signed_in()
    page = browser.get(URL)
    review = fetch(
        browser,
        URL,
        edit(auth_service.store)
        | {"intent": hidden(page, "intent"), "candidate": "\x85 \u3000"},
    )
    assert review.status_code == 200 and b"data-review-answer" in review.content
    assert not SecretReplacementRequest.objects.exists()


def test_a_malformed_new_key_is_refused_at_its_field(auth_service, google, handoff):
    """Save with a new key its rule refuses (a space inside, #1577) draws the
    page again (400) with the rule's words at the key field, the key never
    echoed and nothing staged."""
    from parishkit.stewardship.accounts.integration_forms import KEY_RULES

    browser, _ = signed_in()
    page = browser.get(URL)
    refused = fetch(
        browser,
        URL,
        edit(auth_service.store)
        | shown(page.content.decode())
        | {"intent": hidden(page, "intent"), "candidate": "SYNTHETIC KEY"},
    )
    assert refused.status_code == 400
    text = unescape(refused.content.decode())
    assert str(KEY_RULES["parishsoft"].message) in text
    assert "SYNTHETIC KEY" not in text
    assert nothing_staged()


@pytest.mark.parametrize("role", [None, "staff"])
def test_a_settled_status_re_reads_the_settings_page_passively(
    auth_service, google, role
):
    """Once a key change or a backup check settles, the settings page
    re-reads itself in place with X-ParishKit-Read: passive, which never
    extends the sign-in (#1985); the reader's own open still does. The
    header admits nobody the page would refuse: a Staff reader sending it
    is still denied the page and its status."""
    if role:
        change(
            auth_service.store,
            auth_service.store.active(),
            uuid4(),
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("reader@example.org", roles=(role,)),
                }
            ],
        )
        google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    if role:
        for url in (INDEX, URL, STATUS):
            assert browser.get(url, HTTP_X_PARISHKIT_READ="passive").status_code == 403
        return
    session = PortalSession.objects.get(revoked_at__isnull=True)
    before = session.last_activity_at
    for url in (URL, INDEX + "backup/"):
        assert browser.get(url, HTTP_X_PARISHKIT_READ="passive").status_code == 200
    session.refresh_from_db()
    assert session.last_activity_at == before
    assert browser.get(URL).status_code == 200
    session.refresh_from_db()
    assert session.last_activity_at > before
