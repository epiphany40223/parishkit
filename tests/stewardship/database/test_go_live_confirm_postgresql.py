"""The Go live page's Confirm Production step over HTTP (#1585 slice 4).

Two harnesses. ``confirm_page`` is the Prepare step's: a real
Administrator's go-live with attempt 1 begun, its refresh promoted and its
Family links prepared by the scheduler's go-live producer; its campaign is
not configured to go live, so its step shows readiness problems, and it
drives the states the Prepare owners decide (links prepared again, a stop
by another Administrator, the hold's end). ``confirmable`` is a real setup
whose readiness passes (``test_confirmation_readiness_postgresql``), with
an attempt whose refresh is the current full snapshot, so its dialog runs
the real final owner and the real SQL confirmation guard.

Each page assertion is a plain GET, as a browser reload is; every POST
answers the dialog's fragment or a redirect.
"""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from datetime import timedelta
from time import time
from types import SimpleNamespace

import pytest
from django.core import signing
from django.db.models import F
from django.urls import reverse

from parishkit.stewardship.accounts import confirmation_commands, go_live_commands
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now, issue_admin
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.go_live_sequencing import (
    attempts,
    refresh_command_id,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.refresh_models import SourceRefreshCommand
from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from .auth_builders import signed_in, unguarded
from .leader_builders import grant_role
from .test_confirmation_readiness_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    prepare,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_go_live_prepare_postgresql import (  # noqa: F401
    CHECK,
    PREPARE,
    prepare_page,
    ready,
)
from .test_go_live_sequencing_postgresql import refreshed, start, step
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

CONFIRM = reverse("admin:go_live_confirm")
ACTIVATION = reverse("admin:production_progress")


def shown(browser, **headers):
    """GET the Confirm Production step; return its response and its state."""
    with web_login():
        page = browser.get(CONFIRM, **headers)
    assert page.status_code == 200, page.content
    return page, page.context["confirm"]


def review(browser):
    """The dialog's Review; return the answer."""
    with web_login():
        return post(browser, CONFIRM, {"action": "review"})


def binding(answer):
    """The signed Review binding the dialog's Apply posts."""
    text = answer.content.decode()
    return text.split('name="review" value="', 1)[1].split('"', 1)[0]


def apply(browser, token, typed="Production"):
    """The dialog's Apply with the typed word; return the answer."""
    with web_login():
        return post(
            browser, CONFIRM, {"action": "confirm", "review": token, "typed": typed}
        )


def backdate(request, number, instant):
    """Move attempt ``number``'s start to ``instant`` (its refresh command's time).

    Raw SQL as only a superuser could: the command is a historical record,
    which its model and its guards never update.
    """
    from django.db import connection

    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {SourceRefreshCommand._meta.db_table} SET created_at=%s"
            " WHERE id=%s",
            [instant, refresh_command_id(request.pk, number)],
        )


@pytest.fixture
def confirm_page(prepare_page):
    """Prepare's harness with the Family links ready on current data."""
    harness, browser, admin, request = prepare_page
    preparation = ready(harness, request, admin)
    return harness, browser, admin, request, preparation


@pytest.fixture
def confirmable(ready_links):
    """A setup whose readiness passes, its links prepared, ready to confirm.

    Attempt 1 is begun after the links were prepared, then moved to just
    before the current full snapshot started, so that snapshot is the
    attempt's refresh and its hold runs from now. The Administrator signs in
    again after cleanup; a sign-in of any age confirms (#1547), as
    test_confirmation_readiness_postgresql.py shows.
    """
    browser = ready_links[0]
    preparation, arguments = prepare(ready_links)
    login, service = arguments[:2]
    row = preparation.transition
    start(row, login.portal_session.principal_id)
    full = (
        SourceSnapshot.objects.filter(kind="full", promoted_at__isnull=False)
        .order_by("-started_at")
        .first()
    )
    backdate(row, 1, full.started_at - timedelta(seconds=1))
    issue_admin(
        login,
        login.portal_session.principal_id,
        store=service.store,
        authenticated_at=database_now(),
    )
    browser.cookies["pk_admin"] = login.session.session_key
    # Open the step, as the dialog's page is open (its CSRF cookie too).
    assert shown(browser)[1].state == "ready"
    return browser, login, arguments, preparation


def test_a_readiness_problem_is_listed_with_no_primary(confirm_page):
    """Not configured to go live: the problems with their pages, no primary.

    Step 3 of 4 in the Go live step bar, the impact preview, Stop go-live as
    the one secondary; the step re-reads itself, and a Review is refused
    inside the dialog. The GET confirms nothing and leaves Testing mode.
    """
    _, browser, _, _, _ = confirm_page
    page, confirm = shown(browser)
    assert confirm.state == "problems"
    assert b"Something needs attention before going live." in page.content
    assert b"Step 3 of 4:" in page.content
    assert b"2 of 4 steps completed" in page.content
    assert b"data-go-live-primary" not in page.content
    assert b"data-go-live-stop" in page.content
    assert b"Family emails due at once" in page.content
    assert b"data-live-pending" in page.content
    assert b'data-live-interval="30000"' in page.content
    assert page["Cache-Control"] == "no-store"
    answer = review(browser)
    assert answer.status_code == 409
    assert b'data-go-live-confirm-refused="problems"' in answer.content
    assert not ProductionConfirmation.objects.exists()
    assert SystemConfiguration.objects.get().mode == "testing"


def test_stale_links_move_the_step_back_to_prepare(confirm_page):
    """ParishSoft data changed: the links are prepared again, so Prepare is not
    done; the step says so, settles and leads to Prepare by itself."""
    harness, browser, _, _, _ = confirm_page
    refreshed(harness)
    (discarded,) = step()
    assert discarded.action == "discard"
    page, confirm = shown(browser)
    assert confirm.state == "moved"
    assert b"ParishSoft data changed, so new links are being prepared." in (
        page.content
    )
    assert b"data-live-pending" not in page.content
    assert page.content.count(b"data-live-follow") == 1
    assert b"Open Prepare" in page.content
    answer = review(browser)
    assert answer.status_code == 409
    assert b'data-go-live-confirm-refused="moved"' in answer.content


def test_steps_before_the_links_are_ready_and_after_a_stop(prepare_page, google):
    """Not reachable yet: the step bar and the reason; then another
    Administrator's stop, which a watching page follows to Check."""
    harness, browser, admin, request = prepare_page
    page, confirm = shown(browser)
    assert confirm.state == "moved"
    assert b"Not available yet: the Family links are not prepared yet." in (
        page.content
    )
    start(request, admin)
    grant_role(harness.service.store, "other@example.org", "administrator")
    google[0].update(email="other@example.org", sub="another-google-subject")
    other, login = signed_in()
    assert login.status_code == 302
    with web_login():
        control = other.get(PREPARE).context["stage"].controls["stop"]
        answer = post(other, PREPARE, {"action": "stop", "control": control})
    assert answer.status_code == 303 and answer["Location"] == CHECK
    page, confirm = shown(browser)
    assert confirm.state == "stopped" and confirm.prepare.stopped_by_other
    assert b"Another Administrator stopped go-live." in page.content
    assert b"data-live-pending" not in page.content
    assert b"Open Check" in page.content
    refused = review(browser)
    assert refused.status_code == 409
    assert b'data-go-live-confirm-refused="stopped"' in refused.content
    assert b"Another Administrator stopped go-live." in refused.content


def test_hold_ended_offers_refresh_and_prepare_again(confirm_page):
    """The hold ended before confirming: Prepare's own Refresh and prepare
    again, posted from here, begins attempt 2 and opens Prepare."""
    _, browser, _, request, _ = confirm_page
    backdate(request, 1, database_now() - timedelta(hours=4))
    page, confirm = shown(browser)
    assert confirm.state == "hold_ended"
    assert b"ParishSoft refreshes resumed while this waited." in page.content
    assert page.content.count(b"data-go-live-primary") == 1
    control = confirm.prepare.controls["again"]
    assert f'value="{control}"'.encode() in page.content
    refused = review(browser)
    assert b'data-go-live-confirm-refused="hold_ended"' in refused.content
    with web_login():
        answer = post(browser, PREPARE, {"action": "again", "control": control})
    assert answer.status_code == 303 and answer["Location"] == PREPARE
    assert len(attempts(request)) == 2
    assert shown(browser)[1].state == "moved"


def test_review_then_apply_mints_the_token_at_apply(confirmable, monkeypatch):
    """Review holds no confirmation token; Apply mints it and confirms.

    Without the typed word Apply is refused (400) and nothing is
    confirmed. The Review binding has no age: an Apply long after the
    Review still confirms, because the token's five minutes start at Apply.
    A resend of the same Apply, or another tab's Apply, opens Activation
    and confirms nothing more.
    """
    browser = confirmable[0]
    minted = []
    original = confirmation_commands.verify_preview

    def counted(*args, **kwargs):
        """Count every token mint (the old page's Verify did it per click)."""
        minted.append(kwargs.get("key"))
        return original(*args, **kwargs)

    monkeypatch.setattr(confirmation_commands, "verify_preview", counted)
    page, confirm = shown(browser)
    assert confirm.state == "ready", confirm.preview.problems
    assert page.content.count(b"data-go-live-primary") == 1
    assert b"Confirm Production</button>" in page.content

    first, second = review(browser), review(browser)
    assert first.status_code == 200, first.content
    assert b"data-review-answer" in first.content
    assert b'data-review-title="' in first.content
    assert b'form="go-live-confirm-review-apply"' in first.content
    assert b"Type Production to confirm" in first.content
    assert minted == [] and not ProductionConfirmation.objects.exists()
    token = binding(first)
    for typed in ("", "production"):
        refused = apply(browser, token, typed)
        assert refused.status_code == 400
        assert b'data-go-live-confirm-refused="typed"' in refused.content
    assert minted == [] and not ProductionConfirmation.objects.exists()
    # A forged binding, or another sign-in's, is refused outright.
    assert apply(browser, token + "x").status_code == 400
    # An hour after the Review (as signatures tell time): the binding has
    # no age, and the token minted at Apply is new, so its five minutes
    # never reach the Administrator.
    with monkeypatch.context() as patch:
        patch.setattr(signing, "time", SimpleNamespace(time=lambda: time() + 3600))
        answer = apply(browser, token, "  Production ")
    assert answer.status_code == 303, answer.content
    assert answer["Location"] == ACTIVATION
    assert len(minted) == 1
    assert ProductionConfirmation.objects.count() == 1
    # The same Apply again, and the other tab's Apply: Activation, no more.
    assert apply(browser, token)["Location"] == ACTIVATION
    assert apply(browser, binding(second))["Location"] == ACTIVATION
    assert ProductionConfirmation.objects.count() == 1 and len(minted) == 1
    assert SystemConfiguration.objects.get().mode == "production"
    assert ProductionTransitionRequest.objects.get().state == "activated"
    page, confirm = shown(browser)
    assert confirm.state == "confirmed" and not confirm.confirmed_by_other
    assert b"Production is confirmed." in page.content
    assert b"Open Production activation" in page.content


def test_apply_runs_the_public_web_address_check_again(confirmable, monkeypatch):
    """Apply re-runs the DNS and origin check before anything reaches Families."""
    browser = confirmable[0]
    token = binding(review(browser))
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *a: False)
    refused = apply(browser, token)
    assert refused.status_code == 409
    assert b'data-go-live-confirm-refused="origin_unverified"' in refused.content
    assert not ProductionConfirmation.objects.exists()
    assert SystemConfiguration.objects.get().mode == "testing"


def test_apply_refuses_a_preview_whose_inputs_changed(confirmable):
    """Mail impact changed since the Review: refused in the dialog, naming it.

    A new Review shows the updated preview and its Apply confirms.
    """
    browser = confirmable[0]
    token = binding(review(browser))
    with work_transaction():
        FamilyCampaign.objects.update(email_deliverable=False, version=F("version") + 1)
    refused = apply(browser, token)
    assert refused.status_code == 409
    assert b'data-go-live-confirm-refused="changed"' in refused.content
    assert b"ParishSoft data changed since this preview" in refused.content
    assert not ProductionConfirmation.objects.exists()
    assert apply(browser, binding(review(browser))).status_code == 303
    assert ProductionConfirmation.objects.count() == 1


def test_a_stop_after_the_review_refuses_apply(confirmable):
    """Stop go-live (here, another tab) after the Review: nothing confirmed."""
    browser = confirmable[0]
    token = binding(review(browser))
    with web_login():
        control = browser.get(PREPARE).context["stage"].controls["stop"]
        assert post(browser, PREPARE, {"action": "stop", "control": control})[
            "Location"
        ] == (CHECK)
    refused = apply(browser, token)
    assert refused.status_code == 409
    assert b'data-go-live-confirm-refused="stopped"' in refused.content
    assert not ProductionConfirmation.objects.exists()
    assert SystemConfiguration.objects.get().mode == "testing"


def test_confirmation_readiness_follows_the_attempt_hold(confirmable, monkeypatch):
    """After Start, the source is ready until the attempt's hold end, however
    old its full refresh (``source_stale_seconds`` governs only before
    Start); once the hold has ended, readiness says so and Apply refuses."""
    from parishkit.stewardship.accounts.confirmation_readiness import (
        collect_readiness,
    )
    from parishkit.stewardship.source import readiness as source_readiness

    browser, _, arguments, preparation = confirmable
    row = preparation.transition
    token = binding(review(browser))
    monkeypatch.setattr(
        source_readiness,
        "configured_policy",
        lambda: SimpleNamespace(source_stale_seconds=1),
    )
    with web_login(), work_transaction():
        state = collect_readiness(*arguments)
    (attempt,) = attempts(row)
    assert state.source.reason == "ready" and not state.problems
    assert state.source.expires_at == attempt.hold_end
    backdate(row, 1, database_now() - timedelta(hours=4))
    with web_login(), work_transaction():
        state = collect_readiness(*arguments)
    assert state.source.reason == "go_live_hold_ended"
    assert "go_live_hold_ended" in state.problems
    refused = apply(browser, token)
    assert refused.status_code == 409
    assert b'data-go-live-confirm-refused="hold_ended"' in refused.content
    assert not ProductionConfirmation.objects.exists()


def test_apply_while_another_installer_runs_is_a_409(confirmable):
    """Another installer owns the deployment: a recoverable 409, nothing done."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection

    from parishkit.stewardship.accounts.installation_lock import installation_lock

    browser = confirmable[0]
    token = binding(review(browser))

    def submit():
        """Own the HTTP database handle solely within this competing thread."""
        connection.close()
        try:
            return apply(browser, token).status_code
        finally:
            connection.close()

    with installation_lock(), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(submit).result(timeout=30) == 409
    assert not ProductionConfirmation.objects.exists()


def waiting(pid):
    """Observe backend ``pid`` waiting on a lock the winner holds (any kind:
    the shared sign-in's row or the work order), never infer it from a sleep."""
    from threading import Event
    from time import monotonic

    from django.db import connection

    deadline = monotonic() + 10
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted)",
                [pid],
            )
            if cursor.fetchone()[0]:
                return
        Event().wait(0.01)
    pytest.fail("The second Apply never waited for the first")


def test_an_apply_waiting_on_another_confirmation_opens_activation(confirmable):
    """Two tabs apply at once: the first wins under the final locks; the
    second waits for it, finds Production confirmed and opens Activation,
    confirming nothing more."""
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from threading import Event

    from django.db import connection

    browser = confirmable[0]
    first, second = binding(review(browser)), binding(review(browser))
    entered, release, started = Event(), Event(), Queue()

    def after_effect(execute, sql, params, many, context):
        """Pause the winner after its SQL effect, before its commit."""
        result = execute(sql, params, many, context)
        if isinstance(sql, str) and sql.startswith(
            'INSERT INTO "stewardship_production_confirmation"'
        ):
            entered.set()
            assert release.wait(10), "The competing Apply never waited"
        return result

    def tab(token, wrapper=None):
        """One tab's Apply as the web login, on this thread's own connection."""
        connection.close()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_web")
                cursor.execute("SELECT pg_backend_pid()")
                if wrapper is None:
                    started.put(cursor.fetchone()[0])
            values = {"action": "confirm", "review": token, "typed": "Production"}
            if wrapper is None:
                return post(browser, CONFIRM, values)
            with connection.execute_wrapper(wrapper):
                return post(browser, CONFIRM, values)
        finally:
            connection.close()

    with web_login(), ThreadPoolExecutor(max_workers=2) as pool:
        won = pool.submit(tab, first, after_effect)
        if not entered.wait(30):
            release.set()
            pytest.fail(f"The first Apply never reached its SQL effect: {won.result()}")
        lost = pool.submit(tab, second)
        try:
            waiting(started.get(timeout=30))
        finally:
            release.set()
        assert won.result(timeout=30)["Location"] == ACTIVATION
        answer = lost.result(timeout=30)
    assert answer.status_code == 303 and answer["Location"] == ACTIVATION
    assert ProductionConfirmation.objects.count() == 1
