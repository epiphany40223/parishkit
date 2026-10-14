"""Accept this change once on System health, and ``pk-admin system
refresh-accept-preview`` with ``system refresh-accept`` (ADM-13 PR 5b, #530).

The control appears while the ParishSoft refresh panel shows a refused large
change. Preview (every recorded count, counts only), then the tick box and
confirm, answered with the page in place; a stale sign-in gets the "Confirm
with Google" step-up; a repeated confirmation returns the first acceptance;
a refusal that changed since the preview ("no longer the newest") is
answered as "This changed since you looked"; an empty load and a lapsed
acceptance grey the button with their reasons. Confirming records
``source_drop_accepted`` naming the acceptance and asks for a full refresh.
The commands do the same on the restricted web login, with the automation
session as the fresh sign-in.

The refusal itself is seeded as the schema owner with the guards off
(``unguarded``): the worker's side of recording one is 5a's
(test_source_drop_acceptance_postgresql.py); here only its counts matter.
"""

import re
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship import system_health
from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.audit.models import (
    AuditEvent,
    CriticalEventAcknowledgement,
)
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source.drop_models import (
    SourceDropAcceptance,
    SourceDropCount,
    SourceDropExample,
)
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshAttempt,
    SourceRefreshCommand,
)

from ..log_samples import sample as log_sample
from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in, unguarded
from .automation_builders import paired
from .campaign_builders import change
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_security_events_postgresql import home
from .test_source_families_postgresql import (
    publish,
    source_singletons,  # noqa: F401
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("source_singletons"),
]
PAGE = "/admin/system/health/"
# A refusal with two counts that fell too far and one within the limit.
FELL = (
    ("family", 1084, 612, True),
    ("member", 3000, 2900, False),
    ("email_eligible_families", 900, 500, True),
)


@pytest.fixture
def parish(auth_service):
    """A promoted ParishSoft source, so a full refresh can be requested."""
    publish(source())
    return auth_service


def refusal(counts=FELL):
    """Record a refused full refresh with ``counts``, as the schema owner.

    The attempt's own references are not needed for these tests, so the
    rows are written with the guards and foreign keys off.
    """
    configuration = AppliedConfigurationVersion.objects.values_list(
        "pk", flat=True
    ).first()
    with unguarded():
        attempt = SourceRefreshAttempt.objects.create(
            correlation_id=uuid4(),
            request_id=uuid4(),
            snapshot_id=uuid4(),
            task_id=uuid4(),
            task_fence=1,
            configuration_id=configuration,
            credential_fingerprint="0" * 64,
        )
        for measure, before, after, failed in counts:
            SourceDropCount.objects.create(
                correlation_id=uuid4(),
                attempt=attempt,
                measure=measure,
                before=before,
                after=after,
                limit_percent=25,
                failed=failed,
            )
    return attempt


def raised(attempt):
    """Record the CRITICAL entry the worker writes with ``attempt``'s
    refusal, naming its task (``source.failures``); return it."""
    with transaction.atomic():
        return operational(
            Event.SOURCE_DESTRUCTIVE_CHANGE,
            level="CRITICAL",
            schema=ContextKind.FAILURE,
            context={
                "failure": "destructive_change",
                "task_id": attempt.task_id,
                "version": 2,
                "attempt": 1,
                "outcome": Outcome.FAILED,
            },
        )


def unrelated():
    """Record an unrelated CRITICAL entry (a mail outage)."""
    event = Event.MAIL_PROVIDER_FAILED
    with transaction.atomic():
        return operational(event, level="CRITICAL", **log_sample(event))


def acknowledged():
    """The log entries acknowledged so far."""
    return set(CriticalEventAcknowledgement.objects.values_list("log_id", flat=True))


# The critical-problems banner and the refusal's line in it.
BANNER = "Critical problems in the past 24 hours"
DROP = "ParishSoft refresh would remove too much data"


def with_examples(attempt):
    """Two example Families for the refusal's failing Family count (PR 5c),
    written as the schema owner, as the worker records them."""
    for position, (duid, name) in enumerate(EXAMPLES, start=1):
        SourceDropExample.objects.create(
            attempt=attempt,
            measure="family",
            position=position,
            family_duid=duid,
            family_name=name,
        )


EXAMPLES = ((4101, "Synthetic, Ann & Bob"), (4102, "Sample, Carol"))


def post(browser, **values):
    """A form submission with the Admin CSRF token, under the web login."""
    token = browser.cookies["pk_admin_csrf"].value
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            PAGE, {"csrfmiddlewaretoken": token, **values}, HTTP_ACCEPT="text/html"
        )


def page(browser):
    """The System health page under the web login, as text."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.get(PAGE).content.decode()


def token_of(text):
    """The signed binding the preview carries to its confirmation."""
    return re.search(r'name="token" value="([^"]+)"', text).group(1)


def count(event):
    """How many audit events of this type were recorded."""
    return AuditEvent.objects.filter(event_type=event).count()


def live():
    """The live acceptance's id, as the SQL lifetime check answers it."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.stewardship_source_drop_acceptance_active_v1()")
        return cursor.fetchone()[0]


def test_the_control_is_offered_only_while_a_refusal_is_shown(parish, google):
    """Always drawn in its tile (#1214): greyed with why without a refusal,
    the button once one is recorded."""
    browser, _ = signed_in()
    empty = page(browser)
    assert 'id="accept-change"' in empty
    assert 'data-accept-state="none"' in empty
    assert 'value="accept_preview"' not in empty
    refusal()
    body = page(browser)
    assert 'id="accept-change"' in body
    assert 'value="accept_preview"' in body


def test_preview_tick_and_confirm_record_one_acceptance_and_a_refresh(parish, google):
    """Counts only in the preview; the acceptance copies them as reviewed,
    its audit names it, a full refresh is requested, and a repeat returns
    the first acceptance."""
    attempt = refusal()
    browser, _ = signed_in()
    preview = post(browser, action="accept_preview")
    assert preview.status_code == 200
    body = preview.content.decode()
    region = body[body.index('id="accept-change"') :]
    assert region.count("data-accept-failed") == 2
    assert "1,084" in region and "44%" in region
    assert "I checked this in ParishSoft, and the change is real." in region
    # The panel and the preview name the refusal, so a poll that shows
    # another can mark the open preview changed.
    assert f'data-refused-attempt="{attempt.pk}"' in page(browser)
    assert f'data-live-keep-for="{attempt.pk}"' in region
    assert not SourceDropAcceptance.objects.exists()
    token = token_of(body)

    confirmed = post(browser, action="accept_confirm", token=token, checked="yes")
    assert confirmed.status_code == 200, confirmed.content.decode()
    text = confirmed.content.decode()
    assert "data-accept-done" in text and "A full refresh was requested" in text
    acceptance = SourceDropAcceptance.objects.get()
    assert acceptance.attempt_id == attempt.pk
    assert acceptance.counts["family"] == {"before": 1084, "after": 612, "failed": True}
    assert acceptance.expires_at - acceptance.created_at == timedelta(hours=24)
    assert live() == acceptance.pk
    event = AuditEvent.objects.get(event_type="source_drop_accepted")
    assert event.subject_id == acceptance.pk
    assert event.actor_id == acceptance.actor_id
    command = SourceRefreshCommand.objects.get(pk=acceptance.pk)
    assert (command.cause, command.kind) == ("manual", "full")
    assert command.actor_id == acceptance.actor_id
    # The greyed button says who accepted, when and until when.
    assert "Accepted by admin@example.org at" in text
    assert 'data-accept-state="accepted"' in text

    # The same confirmation again returns the first acceptance and joins the
    # same refresh: no second row, event or command.
    again = post(browser, action="accept_confirm", token=token, checked="yes")
    assert again.status_code == 200
    assert SourceDropAcceptance.objects.count() == 1
    assert count("source_drop_accepted") == 1
    assert SourceRefreshCommand.objects.count() == 1
    # Another preview now is "changed": this refusal is accepted already.
    refused = post(browser, action="accept_preview")
    assert refused.status_code == 409
    assert 'data-accept-refused="changed"' in refused.content.decode()
    assert 'data-accept-state="accepted"' in page(browser)


def test_accepting_acknowledges_only_that_refusals_critical_problem(parish, google):
    """The banner stops asking about the refusal the Administrator accepted:
    its entry is acknowledged in the acceptance's transaction, by that
    Administrator, with the acceptance as the event's subject. An earlier
    refusal's entry and an unrelated problem stay; a repeat adds nothing."""
    earlier = raised(refusal())
    attempt = refusal()
    entry = raised(attempt)
    outage = unrelated()
    browser, _ = signed_in()
    before = home(browser)
    assert BANNER in before and f"{DROP} (2×)" in before
    assert "Email sending failed" in before
    token = token_of(post(browser, action="accept_preview").content.decode())
    # The preview acknowledges nothing.
    assert not acknowledged()
    answer = post(browser, action="accept_confirm", token=token, checked="yes")
    assert answer.status_code == 200, answer.content.decode()
    acceptance = SourceDropAcceptance.objects.get()
    assert acceptance.attempt_id == attempt.pk
    assert acknowledged() == {entry.pk}
    assert CriticalEventAcknowledgement.objects.get().actor_id == acceptance.actor_id
    event = AuditEvent.objects.get(event_type="critical_events_acknowledged")
    assert event.subject_id == acceptance.pk
    assert event.actor_id == acceptance.actor_id
    assert event.auditcontext.context == {"outcome": "succeeded", "count": 1}
    # The page the confirmation answers with no longer counts it either.
    assert f"{DROP} (2×)" not in answer.content.decode()
    after = home(browser)
    assert BANNER in after and DROP in after and f"{DROP} (2×)" not in after
    assert "Email sending failed" in after
    assert earlier.pk not in acknowledged() and outage.pk not in acknowledged()
    # The same confirmation again acknowledges nothing more.
    assert (
        post(browser, action="accept_confirm", token=token, checked="yes").status_code
        == 200
    )
    assert acknowledged() == {entry.pk}
    assert count("critical_events_acknowledged") == 1


@pytest.mark.parametrize("failure", ["configuration", "permission"])
def test_an_acknowledgement_that_fails_leaves_the_acceptance(
    parish, google, monkeypatch, caplog, failure
):
    """A restore under review or a missing grant refuses the banner's
    acknowledgement: it is logged, and the acceptance and its refresh
    stand; the banner keeps the entry."""
    from django.db import DatabaseError

    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts import critical_event_views

    class Refused(Exception):
        """A PostgreSQL permission refusal, as psycopg reports it."""

        sqlstate = "42501"

    def refuse(*args, **values):
        """The acknowledgement is refused."""
        if failure == "configuration":
            raise ConfigError("PRIVATE")
        raise DatabaseError("PRIVATE") from Refused()

    monkeypatch.setattr(critical_event_views, "acknowledge_now", refuse)
    raised(refusal())
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    answer = post(browser, action="accept_confirm", token=token, checked="yes")
    assert answer.status_code == 200, answer.content.decode()
    assert "data-accept-done" in answer.content.decode()
    assert SourceDropAcceptance.objects.count() == 1
    assert count("source_drop_accepted") == 1
    assert SourceRefreshCommand.objects.count() == 1
    assert not acknowledged()
    [logged] = [
        record for record in caplog.records if record.getMessage() == "task_failed"
    ]
    assert logged.levelname == "WARNING"
    assert "PRIVATE" not in caplog.text
    assert f"{DROP}" in home(browser)


def test_a_changed_or_expired_confirmation_acknowledges_nothing(
    parish, google, monkeypatch
):
    """A refusal superseded since the preview, or an expired binding, is
    "This changed since you looked": nothing is accepted or acknowledged."""
    entry = raised(refusal())
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    with monkeypatch.context() as patch:
        patch.setattr(system_health, "ACCEPT_MAX_AGE", -1)
        expired = post(browser, action="accept_confirm", token=token, checked="yes")
    assert expired.status_code == 409
    assert 'data-accept-refused="changed"' in expired.content.decode()
    newer = raised(refusal())
    changed = post(browser, action="accept_confirm", token=token, checked="yes")
    assert changed.status_code == 409
    assert 'data-accept-refused="changed"' in changed.content.decode()
    assert not SourceDropAcceptance.objects.exists()
    assert not acknowledged()
    assert not count("critical_events_acknowledged")
    assert f"{DROP} (2×)" in home(browser)
    # Accepting the newer refusal acknowledges its entry only.
    fresh = token_of(post(browser, action="accept_preview").content.decode())
    post(browser, action="accept_confirm", token=fresh, checked="yes")
    assert acknowledged() == {newer.pk}
    assert entry.pk not in acknowledged()


def test_a_new_refusal_since_the_preview_is_changed_not_an_error(parish, google):
    """The guard's "no longer the newest" reads as "This changed since you
    looked"; nothing is accepted, and the new refusal can be reviewed."""
    refusal()
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    refusal()
    answer = post(browser, action="accept_confirm", token=token, checked="yes")
    assert answer.status_code == 409
    text = answer.content.decode()
    assert 'data-accept-refused="changed"' in text
    assert "This changed since you looked; review it again." in text
    assert "no longer the newest" not in text
    assert not SourceDropAcceptance.objects.exists()
    assert not count("source_drop_accepted")
    # The newer refusal can be previewed and accepted.
    fresh = token_of(post(browser, action="accept_preview").content.decode())
    assert (
        post(browser, action="accept_confirm", token=fresh, checked="yes").status_code
        == 200
    )
    assert SourceDropAcceptance.objects.count() == 1


def test_the_tick_box_and_the_binding_are_checked_by_the_server(parish, google):
    """No tick, an altered binding or a stray field: refused in the region."""
    refusal()
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    for values in (
        {"token": token},
        {"token": token + "x", "checked": "yes"},
        {"token": token, "checked": "yes", "key": str(uuid4())},
    ):
        answer = post(browser, action="accept_confirm", **values)
        assert answer.status_code == 400, values
        assert 'data-accept-refused="invalid"' in answer.content.decode()
    assert not SourceDropAcceptance.objects.exists()
    cancelled = post(browser, action="accept_cancel")
    assert cancelled.status_code == 200
    assert 'value="accept_preview"' in cancelled.content.decode()


def test_a_stale_sign_in_gets_the_google_step_up(parish, google, monkeypatch):
    """Answered in the region: the step-up form, returning to this page."""
    refusal()
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    monkeypatch.setattr(sessions, "FRESH_SECONDS", -1)
    for values in (
        {"action": "accept_preview"},
        {"action": "accept_confirm", "token": token, "checked": "yes"},
    ):
        response = post(browser, **values)
        assert response.status_code == 403
        body = response.content.decode()
        assert 'data-accept-refused="reauthenticate"' in body
        assert 'name="next" value="/admin/system/health/"' in body
    assert not SourceDropAcceptance.objects.exists()


def test_only_an_administrator_may_accept(parish, google):
    """Staff can neither preview nor confirm, even with a valid binding."""
    refusal()
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    store = parish.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    reader, _ = signed_in()
    assert post(reader, action="accept_preview").status_code == 403
    answer = post(reader, action="accept_confirm", token=token, checked="yes")
    assert answer.status_code == 403
    assert not SourceDropAcceptance.objects.exists()


def test_another_administrators_binding_is_refused(parish, google):
    """A preview's binding is its reviewer's own: another Administrator
    posting it is refused in the region, and nothing is accepted."""
    refusal()
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    store = parish.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("other@example.org"),
            }
        ],
    )
    google[0]["email"] = "other@example.org"
    google[0]["sub"] = "synthetic-other-subject"
    other, _ = signed_in()
    answer = post(other, action="accept_confirm", token=token, checked="yes")
    assert answer.status_code == 400
    assert 'data-accept-refused="invalid"' in answer.content.decode()
    assert not SourceDropAcceptance.objects.exists()
    assert not count("source_drop_accepted")
    # Their own preview and confirmation work.
    own = token_of(post(other, action="accept_preview").content.decode())
    assert (
        post(other, action="accept_confirm", token=own, checked="yes").status_code
        == 200
    )
    assert SourceDropAcceptance.objects.count() == 1


def test_a_refresh_that_cannot_be_requested_leaves_the_acceptance(
    parish, google, monkeypatch, caplog
):
    """Once the acceptance committed, a failed refresh request is logged and
    the page says the acceptance stands; never "Nothing was accepted"."""
    from parishkit.stewardship.accounts import refresh_views

    def broken(*args, **values):
        """The refresh request fails after the acceptance committed."""
        raise RuntimeError("PRIVATE")

    monkeypatch.setattr(refresh_views, "request_manual_refresh", broken)
    refusal()
    browser, _ = signed_in()
    token = token_of(post(browser, action="accept_preview").content.decode())
    answer = post(browser, action="accept_confirm", token=token, checked="yes")
    assert answer.status_code == 200
    text = answer.content.decode()
    assert "data-accept-done" in text
    assert "A full refresh could not be requested right now" in text
    assert "the acceptance stands" in text
    assert "Nothing was accepted" not in text
    acceptance = SourceDropAcceptance.objects.get()
    event = AuditEvent.objects.get(event_type="source_drop_accepted")
    assert event.subject_id == acceptance.pk
    assert not SourceRefreshCommand.objects.exists()
    [failure] = [
        record for record in caplog.records if record.getMessage() == "task_failed"
    ]
    assert failure.levelname == "WARNING"
    assert "PRIVATE" not in caplog.text


def test_an_empty_load_is_never_offered(parish, google):
    """Greyed with why; a preview posted anyway is refused."""
    refusal((("family", 1084, 0, True), ("member", 3000, 0, True)))
    browser, _ = signed_in()
    body = page(browser)
    assert 'data-accept-state="empty"' in body
    assert 'value="accept_preview"' not in body
    assert post(browser, action="accept_preview").status_code == 409


def test_a_lapsed_acceptance_offers_refresh_now(parish, google):
    """An acceptance that ended unused greys the button: Refresh now gets a
    new refusal, since a refusal is accepted only once."""
    attempt = refusal()
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        (now,) = cursor.fetchone()
    # Written as the schema owner, whose insert the guard does not check.
    SourceDropAcceptance.objects.create(
        actor_id=uuid4(),
        session_id=uuid4(),
        authenticated_at=now,
        attempt_id=attempt.pk,
        counts={},
        expires_at=now - timedelta(minutes=1),
    )
    browser, _ = signed_in()
    body = page(browser)
    assert 'data-accept-state="lapsed"' in body
    assert "the acceptance ended before a full refresh used it" in body
    assert post(browser, action="accept_preview").status_code == 409


@pytest.fixture
def admin(parish, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(parish, monkeypatch)


def test_a_change_is_accepted_from_the_command_line(admin):
    """The preview prints counts and the binding; the confirmation prompts
    with the page's tick box, records both events and asks for a refresh."""
    attempt = refusal()
    entry = raised(attempt)
    _, secret, row = paired(admin.service)
    code, document, _ = admin("system", "refresh-accept-preview", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["attempt_id"] == str(attempt.pk)
    assert {count["measure"] for count in result["counts"]} == {
        "family",
        "member",
        "email_eligible_families",
    }
    token = result["preview"]["token"]
    assert not count("admin_cmd_system_refresh_accept")

    # Without a "yes", nothing is accepted; the prompt shows the tick box.
    code, refused, errors = admin(
        "system", "refresh-accept", "--token", token, secret=secret, answer=b"no\n"
    )
    assert code == 4 and refused["error"]["code"] == "confirmation_required"
    assert "I checked this in ParishSoft, and the change is real." in errors
    assert "family: 1084 before, 612 after" in errors
    assert not SourceDropAcceptance.objects.exists()

    code, document, _ = admin(
        "system", "refresh-accept", "--token", token, secret=secret, answer=b"yes\n"
    )
    assert code == 0, document
    result = document["result"]
    acceptance = SourceDropAcceptance.objects.get()
    assert result["acceptance_id"] == str(acceptance.pk)
    assert result["created"] is True and result["live"] is True
    assert UUID(result["task_root_id"])
    assert acceptance.actor_id == row.principal_id
    assert (
        count("source_drop_accepted") == count("admin_cmd_system_refresh_accept") == 1
    )
    [event] = AuditEvent.objects.filter(event_type="admin_cmd_system_refresh_accept")
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    # The refusal's critical problem is acknowledged, as the page does.
    assert acknowledged() == {entry.pk}
    [event] = AuditEvent.objects.filter(event_type="critical_events_acknowledged")
    assert event.actor_id == row.principal_id and event.subject_id == acceptance.pk

    # The same token again returns the first acceptance.
    code, document, _ = admin(
        "system", "refresh-accept", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and document["result"]["created"] is False
    assert SourceDropAcceptance.objects.count() == 1
    # A preview now is refused: this refusal is accepted already.
    code, refused, _ = admin("system", "refresh-accept-preview", secret=secret)
    assert code == 1 and refused["error"]["code"] == "stale_version"


def test_a_command_line_refresh_failure_still_reports_the_acceptance(
    admin, monkeypatch
):
    """Exit 0 with the acceptance and a null refresh, not an error."""
    from parishkit.stewardship.accounts import refresh_views

    def broken(*args, **values):
        """The refresh request fails after the acceptance committed."""
        raise RuntimeError("PRIVATE")

    monkeypatch.setattr(refresh_views, "request_manual_refresh", broken)
    refusal()
    _, secret, _ = paired(admin.service)
    _, document, _ = admin("system", "refresh-accept-preview", secret=secret)
    token = document["result"]["preview"]["token"]
    code, document, _ = admin(
        "system", "refresh-accept", "--token", token, "--yes", secret=secret
    )
    assert code == 0, document
    assert document["result"]["created"] is True
    assert document["result"]["task_root_id"] is None
    assert SourceDropAcceptance.objects.count() == 1


def test_a_read_only_session_cannot_accept(admin):
    """Accepting needs a full-scope session, as every change does."""
    refusal()
    _, reader, _ = paired(admin.service, scope="read-only")
    code, refused, _ = admin("system", "refresh-accept-preview", secret=reader)
    assert code != 0 and refused["error"]["code"] in {"denied", "scope_required"}
    assert not SourceDropAcceptance.objects.exists()


def test_the_preview_lists_example_families_and_the_command_only_their_duids(
    admin,
):
    """PR 5c: the page's preview names the examples under the failing count;
    the command prints their DUIDs without names; once the change is
    accepted, neither shows them any more."""
    attempt = refusal()
    with_examples(attempt)
    browser, _ = signed_in()
    body = post(browser, action="accept_preview").content.decode()
    region = body[body.index('data-accept-examples="family"') :]
    region = region[: region.index("</table>")]
    assert '<td>Synthetic, Ann &amp; Bob</td><td class="numeric">4101</td>' in region
    assert '<td>Sample, Carol</td><td class="numeric">4102</td>' in region
    assert "No example Families are available for this count." in body

    _, secret, _ = paired(admin.service)
    code, document, errors = admin("system", "refresh-accept-preview", secret=secret)
    assert code == 0, document
    assert document["result"]["examples"] == {"family": [4101, 4102]}
    for name in ("Synthetic", "Sample"):
        assert name not in str(document) and name not in errors
    token = document["result"]["preview"]["token"]
    code, document, errors = admin(
        "system", "refresh-accept", "--token", token, secret=secret, answer=b"yes\n"
    )
    assert code == 0, document
    assert "Synthetic" not in str(document) and "Synthetic" not in errors
    # Decided: the examples are no longer shown, and housekeeping may go.
    from parishkit.stewardship.source.drop_examples import shown_examples

    assert shown_examples(attempt.pk) == {}
    assert SourceDropExample.objects.count() == len(EXAMPLES)
