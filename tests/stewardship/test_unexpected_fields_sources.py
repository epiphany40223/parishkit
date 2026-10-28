"""Every field check passes what was sent to ``unexpected_fields`` (#1444).

A field check that refuses with ``unexpected_fields(request.POST,
allowed=...)`` lets the refusal log count the submitted names the form does
not have (never naming them) and name the form's own fields sent twice. A
bare ``unexpected_fields()`` records neither, so each one says why it passes
nothing, in a ``# No sources:`` comment on the line above: an unknown action
value, a malformed value, a missing editor field or an unreadable body. A
call that passes sources without ``allowed=`` records nothing either. The
guard below enforces both; the other tests check what a few of the field
checks now record.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from django.test import RequestFactory

from parishkit.stewardship.accounts import (
    campaign_family_test_views,
    content_delete_views,
    integration_views,
    leader_import_views,
    restore_review_views,
    schedule_entry_views,
    user_views,
)
from parishkit.stewardship.accounts.admin_editing import form_action
from parishkit.stewardship.jobs import delivery_views
from parishkit.stewardship.reports import past_campaign_views
from parishkit.stewardship.web.refusals import UserFacingError

SOURCE = Path(__file__).resolve().parents[2] / "src/parishkit/stewardship"
MARK = "# No sources:"


def bare_calls_without_reason(source, name="<sample>"):
    """``name:line`` of each ``unexpected_fields`` call in ``source`` that
    records no count: a bare call whose line above is not a ``# No sources:``
    comment, or a call that passes sources but no ``allowed=``."""
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source, name)):
        if not (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            == "unexpected_fields"
        ):
            continue
        if node.args or node.keywords:
            if not any(item.arg == "allowed" for item in node.keywords):
                found.append(f"{name}:{node.lineno}")
            continue
        above = lines[node.lineno - 2].strip() if node.lineno > 1 else ""
        if not above.startswith(MARK):
            found.append(f"{name}:{node.lineno}")
    return found


def test_every_bare_unexpected_fields_says_why_it_passes_nothing():
    """A new field check passes its sources, or says why it cannot."""
    missing = [
        item
        for path in sorted(SOURCE.rglob("*.py"))
        for item in bare_calls_without_reason(
            path.read_text(), str(path.relative_to(SOURCE))
        )
    ]
    assert missing == [], (
        "Pass the submitted sources and allowed= to unexpected_fields, or "
        f"say why not in a '{MARK}' comment above the call (#1444): {missing}"
    )


def test_the_guard_catches_a_bare_call_with_no_reason():
    """A deliberately broken sample: the guard names exactly its bare call."""
    sample = (
        "def check(request):\n"
        "    if set(request.POST) - {'name'}:\n"
        "        raise unexpected_fields()\n"
        "    if request.FILES:\n"
        f"        {MARK} no file is allowed here.\n"
        "        raise unexpected_fields()\n"
        "    if request.GET:\n"
        "        raise unexpected_fields(request.GET)\n"
        "    raise refusals.unexpected_fields(request.POST, allowed={'name'})\n"
    )
    assert bare_calls_without_reason(sample) == ["<sample>:3", "<sample>:8"]


def posted(query):
    """A submitted form's fields, as Django parses them."""
    return QueryDict(query)


def form_post(query):
    """A POST of the urlencoded ``query``, repeated names kept."""
    return RequestFactory().post(
        "/", query, content_type="application/x-www-form-urlencoded"
    )


def refusal(check, *args):
    """The ``unexpected_fields`` refusal that ``check(*args)`` raises."""
    with pytest.raises(UserFacingError) as raised:
        check(*args)
    assert raised.value.reason == "unexpected_fields"
    return raised.value


def counted(error):
    """``(unexpected_count, duplicates)`` of a refusal."""
    return error.unexpected_count, error.duplicates


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("action=preview&name=a&zz=1&yy=2", (2, ())),
        ("action=preview&name=a&name=b", (0, ("name",))),
        ("action=preview&tags=a&tags=b&zz=1", (1, ())),
        # An unknown action value is not an unexpected field: no count.
        ("action=nope&name=a&zz=1", (None, ())),
        ("action=confirm&preview=p&name=a", (1, ())),
    ],
)
def test_the_shared_form_action_check_counts(query, expected):
    """``form_action``, which most Admin review forms use."""
    error = refusal(
        lambda: form_action(
            posted(query), preview_fields={"name", "tags"}, multiple_fields={"tags"}
        )
    )
    assert counted(error) == expected


@pytest.mark.parametrize(
    ("check", "query", "expected"),
    [
        (
            leader_import_views._choice,
            "scope=all&scope=selected&email=a@x&email=b@x&zz=1",
            (1, ("scope",)),
        ),
        (
            schedule_entry_views._chosen,
            "base_digest=a&base_digest=b&schedule_id=1&schedule_id=2&zz=1",
            (1, ("base_digest",)),
        ),
        (user_views._chosen, "base_digest=a&user_id=1&zz=1", (1, ())),
        # Missing a field: nothing extra was sent.
        (user_views._chosen, "base_digest=a", (0, ())),
        (
            content_delete_views._chosen,
            "base_digest=a&revision_id=1&revision_id=2&zz=1&yy=2",
            (2, ()),
        ),
    ],
)
def test_the_chosen_rows_checks_count(check, query, expected):
    """The delete and import dialogs' field checks."""
    assert counted(refusal(check, posted(query))) == expected


def test_a_value_refusal_stays_bare():
    """A malformed value, not an unexpected field, records no count."""
    error = refusal(
        user_views._chosen, posted("csrfmiddlewaretoken=t&base_digest=a&user_id=x")
    )
    assert counted(error) == (None, ())


@pytest.mark.parametrize(
    ("step", "query", "expected"),
    [
        (restore_review_views._step, "action=release-confirm&key=k&zz=1", (1, ())),
        (restore_review_views._step, "action=nope&key=k", (None, ())),
        (restore_review_views._held_step, "action=cancel&zz=1&yy=2", (2, ())),
    ],
)
def test_the_in_place_steps_count(step, query, expected):
    """Restore review and Held emails: an action's own fields only."""
    request = form_post(query)
    assert counted(refusal(step, request, None, {})) == expected


def test_removing_an_integration_counts():
    """Remove an optional integration posts only its action."""
    request = form_post("action=remove&zz=1")
    error = refusal(
        integration_views._remove, request, None, None, SimpleNamespace(), "slack"
    )
    assert counted(error) == (1, ())


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("action=preview&kind=a&scope=page&zz=1", (1, ())),
        ("action=preview&kind=a&kind=b&scope=page", (0, ("kind",))),
        # The ticked rows repeat ``delivery``; only the unknown name counts.
        ("action=preview&kind=a&scope=page&delivery=x&delivery=y&zz=1", (1, ())),
        # Only Apply after a review posts the records check.
        ("action=continue&preview=p&note=c&checked=yes", (1, ())),
        ("action=confirm&preview=p&note=c&checked=yes&zz=1", (1, ())),
        ("action=nope&kind=a", (None, ())),
    ],
)
def test_the_bulk_mail_actions_count(monkeypatch, query, expected):
    """Sent mail's bulk actions: each action's own fields, from one table.
    The refusal is the review dialog's fragment (#2296), which carries the
    count and the duplicated names for the refusal log."""
    monkeypatch.setattr(delivery_views, "runtime", lambda: SimpleNamespace(store=None))
    monkeypatch.setattr(delivery_views, "_principal", lambda *_, **__: None)
    response = delivery_views._bulk(form_post(query))
    assert response.status_code == 400
    assert response.stewardship_reason == "unexpected_fields"
    assert (
        response.stewardship_unexpected_count,
        response.stewardship_duplicates,
    ) == expected


def test_a_submitted_file_is_counted():
    """A file field the form does not have counts, as any other field does."""
    request = RequestFactory().post(
        "/", {"action": "preview", "upload": SimpleUploadedFile("x.txt", b"x")}
    )
    error = refusal(campaign_family_test_views._action, request)
    assert counted(error) == (1, ())


def test_an_archive_sent_twice_is_named(monkeypatch):
    """Past campaigns Remove takes exactly one archive."""
    monkeypatch.setattr(past_campaign_views, "_administrator", lambda _: None)
    response = past_campaign_views.remove(
        form_post("csrfmiddlewaretoken=t&archive=2020:1&archive=2021:1")
    )
    assert response.status_code == 400
    assert response.stewardship_unexpected_count == 0
    assert response.stewardship_duplicates == ("archive",)
