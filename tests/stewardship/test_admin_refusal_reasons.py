"""Specific reasons and closed details of refused Admin input (#1403, PR 5).

The four refusals from the Administrator's 2026-10-15 review (a campaign
zone that is not the parish's, a logo step's unexpected fields, a refused
logo upload and the Off-site backups page's Test access), the pages that
answer with a body of their own, and the refusals drawn in place with 200.
No database: ``record_refusal`` is replaced by a list, as in
``test_admin_refusal_log.py``; durable entries are tested in
``database/test_admin_refusal_reasons_postgresql.py``.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse, JsonResponse, QueryDict
from django.test import RequestFactory

from parishkit.stewardship.accounts import clone_views, integration_views
from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.accounts.branding_views import LogoForm, image_reason
from parishkit.stewardship.audit.refusal_context import (
    REASONS,
    SETTING_REASONS,
    WARNING_REASONS,
)
from parishkit.stewardship.audit.refusals import level_of
from parishkit.stewardship.reports import export_ui
from parishkit.stewardship.web import refusal_log
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES
from parishkit.stewardship.web.refusals import (
    STEP_REASONS,
    UserFacingError,
    refused_in_place,
    refused_step,
    unexpected_fields,
    with_codes,
    with_reason,
)
from parishkit.stewardship.web.security import private_response

from .test_admin_refusal_log import answer

ZONE = ("America/New_York", "America/Kentucky/Louisville")


@pytest.fixture
def recorded(monkeypatch):
    """Every RefusalRecord the middleware hands to the writer, in order."""
    records = []
    monkeypatch.setattr(refusal_log, "record_refusal", records.append)
    monkeypatch.setattr(refusal_log, "current_mode", lambda: "testing")
    return records


def posted(text):
    """A submitted QueryDict, as ``request.POST`` holds it."""
    return QueryDict(text)


def test_unexpected_fields_counts_unknown_names_and_names_its_own_duplicates(
    recorded,
):
    """Unknown names are only counted; an own field sent twice is named."""
    data = posted("version=1&version=2&zz_secret_name=hunter2&other=1")
    answer(error_response(unexpected_fields(data, allowed={"version", "logo"})))
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "unexpected_fields"
    assert context["unexpected_count"] == 2
    assert context["fields"] == ["version"]
    assert context["codes"] == ["invalid", "duplicate"]
    text = json.dumps(context)
    assert "zz_secret_name" not in text and "hunter2" not in text


def test_unexpected_fields_without_its_sources_records_no_count(recorded):
    """A pre-check that does not pass what was sent records neither."""
    answer(error_response(unexpected_fields()))
    (record,) = recorded
    assert "unexpected_count" not in record.context()
    assert record.context()["codes"] == ["invalid"]


def test_the_csrf_token_and_multiple_fields_are_never_duplicates():
    """Only one-value fields of the form's own count as sent twice."""
    data = posted("csrfmiddlewaretoken=a&csrfmiddlewaretoken=b&email=x&email=y")
    error = unexpected_fields(
        data, allowed={"csrfmiddlewaretoken", "email"}, multiple={"email"}
    )
    assert error.unexpected_count == 0 and error.duplicates == ()


def test_a_parish_zone_refusal_records_both_zones_at_warning(recorded):
    """The campaign zone case: a setting reason, with expected and actual."""
    answer(
        with_reason(
            HttpResponse(status=400),
            "campaign_zone_not_parish",
            expected=ZONE[0],
            actual=ZONE[1],
        )
    )
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "campaign_zone_not_parish"
    assert (context["expected"], context["actual"]) == ZONE
    assert record.level == "WARNING"


def test_clone_settings_records_the_parish_zone_and_the_zone_chosen(
    recorded, monkeypatch
):
    """Clone settings' own refusal, with a deep parish zone name.

    The page is retired (410) until the single-campaign change (#145), so
    its preview step is driven directly, with the page drawing stubbed.
    """
    parish, chosen = "America/Argentina/Buenos_Aires", "America/Kentucky/Louisville"
    monkeypatch.setattr(clone_views, "document_parish", lambda _: {"timezone": parish})
    monkeypatch.setattr(
        clone_views, "_page", lambda *args, **kwargs: HttpResponse(status=400)
    )
    errors = []
    form = SimpleNamespace(
        is_valid=lambda: True,
        values=lambda: {"timezone": chosen},
        cleaned_data={"base_digest": "digest"},
        add_error=lambda field, error: errors.append((field, error.code)),
    )
    schedules = SimpleNamespace(is_valid=lambda: True)
    configuration = SimpleNamespace(
        active_configuration=SimpleNamespace(digest="digest", canonical_document={})
    )
    response = clone_views._preview(
        None,
        None,
        None,
        (configuration, "f"),
        None,
        None,
        form,
        schedules,
        (),
        None,
        "salt",
    )
    answer(response)
    assert errors == [("timezone", "not_parish_zone")]
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "campaign_zone_not_parish"
    assert (context["expected"], context["actual"]) == (parish, chosen)
    assert record.level == "WARNING"


def test_only_a_setting_reason_carries_values():
    """Values with any other reason are refused where they are given."""
    with pytest.raises(TypeError):
        with_reason(HttpResponse(status=400), "stale_page", expected="x")
    with pytest.raises(TypeError):
        UserFacingError("No.", reason="denied", actual="x")
    with pytest.raises(TypeError):
        with_reason(HttpResponse(status=400), "not a reason")


def test_a_setting_value_of_the_wrong_shape_is_dropped(recorded):
    """The shape check still applies: an address never fits it."""
    answer(
        with_reason(
            HttpResponse(status=400),
            "campaign_zone_not_parish",
            expected="someone@example.org",
            actual=ZONE[1],
        )
    )
    (record,) = recorded
    context = record.context()
    assert "expected" not in context and context["actual"] == ZONE[1]


def test_each_setting_reason_is_a_reason():
    """The closed sets agree with each other."""
    assert SETTING_REASONS <= REASONS and WARNING_REASONS <= REASONS
    assert set(STEP_REASONS.values()) <= REASONS


def _logo(size):
    """A bound logo form with an upload of ``size`` bytes, validated."""
    upload = SimpleUploadedFile("logo.png", b"x" * size, content_type="image/png")
    form = LogoForm({"base_digest": "0" * 64}, {"logo": upload})
    form.is_valid()
    return form


def test_a_logo_over_the_limit_records_the_limit_and_its_size(recorded):
    """The logo upload case: ``image_too_large`` with both sizes, in bytes."""
    form = _logo(MAX_IMAGE_BYTES + 1)
    answer(image_reason(HttpResponse(status=400), form, "logo"))
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "image_too_large"
    assert context["expected"] == str(MAX_IMAGE_BYTES)
    assert context["actual"] == str(MAX_IMAGE_BYTES + 1)
    assert record.level == "INFO"
    assert "logo.png" not in json.dumps(context)


def test_an_unsupported_logo_is_image_unsupported(recorded):
    """A file the image check refuses names that reason, with no values."""
    form = _logo(10)
    form.add_error("logo", forms.ValidationError("Choose one.", code="required"))
    response = image_reason(HttpResponse(status=400), form, "logo")
    assert getattr(response, "stewardship_reason", None) is None
    form.add_error("logo", forms.ValidationError("No.", code="unsupported_image"))
    answer(image_reason(HttpResponse(status=400), form, "logo"))
    (record,) = recorded
    assert record.context()["reason"] == "image_unsupported"
    assert "expected" not in record.context()


@pytest.mark.parametrize(
    ("records", "local", "entered", "reason", "level"),
    [
        ({}, True, "", "drive_not_in_local", "WARNING"),
        ({}, False, "", "workspace_mail_missing", "WARNING"),
        ({"google_workspace": {}}, False, "", "drive_folder_missing", "INFO"),
        (
            {"google_workspace": {}},
            False,
            "https://example.org/x",
            "drive_folder_link_invalid",
            "INFO",
        ),
    ],
)
def test_each_test_access_refusal_has_its_reason(
    recorded, monkeypatch, records, local, entered, reason, level
):
    """The Test access case: each of its refusals says which one it was."""
    monkeypatch.setattr(integration_views, "_drive_in_local", lambda: local)
    monkeypatch.setattr(integration_views, "_records", lambda _: records)
    with pytest.raises(UserFacingError) as refused:
        integration_views.queue_probe(None, None, entered)
    answer(error_response(refused.value))
    (record,) = recorded
    assert record.context()["reason"] == reason
    assert record.level == level
    assert entered not in json.dumps(record.context()) or not entered


def test_a_test_access_post_with_extra_fields_is_counted(recorded):
    """The Test access body: the extra fields are counted, never named."""
    request = RequestFactory().post(
        "/admin/", {"action": "probe", "target": "x", "zz_extra": "1"}
    )
    with pytest.raises(UserFacingError) as refused:
        integration_views._test_access(request, None, None)
    answer(error_response(refused.value))
    (record,) = recorded
    assert record.context()["unexpected_count"] == 1
    assert "zz_extra" not in json.dumps(record.context())


def test_a_refusal_drawn_in_place_is_recorded_without_a_status(recorded):
    """A 200 step-up or pairing answer is a refusal; a plain 200 is not."""
    answer(HttpResponse(status=200))
    assert recorded == []
    answer(refused_in_place(HttpResponse(), "fresh_authentication_required"))
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "fresh_authentication_required"
    assert "status" not in context
    assert record.level == "INFO"


def test_a_pairing_refusal_drawn_in_place_is_info(recorded):
    """An unknown code is the input's own fault (spec level table)."""
    answer(refused_in_place(HttpResponse(), "pairing_code_refused"))
    (record,) = recorded
    assert record.level == "INFO"


@pytest.mark.parametrize(
    ("refused", "status", "reason"),
    [
        ("reauthenticate", 403, "fresh_authentication_required"),
        ("busy", 409, "stale_page"),
        ("changed", 409, "stale_page"),
        ("production", 403, "production_only"),
        ("needs_holds", 409, "release_refused"),
        ("held_elsewhere", 409, "resend_refused"),
        ("something_new", 200, "unclassified"),
    ],
)
def test_an_in_place_steps_refusal_has_its_reason_and_key(
    recorded, refused, status, reason
):
    """Restore review, Held emails and System health: the key is the code."""
    answer(refused_step(HttpResponse(status=status), refused))
    (record,) = recorded
    context = record.context()
    assert context["reason"] == reason and context["codes"] == [refused]
    assert context.get("status") == (status if status != 200 else None)


def test_an_in_place_steps_invalid_form_keeps_its_errors_reason(recorded):
    """A stale form's unexpected fields keep that reason and its count."""
    error = unexpected_fields(posted("zz=1"), allowed={"action"})
    answer(refused_step(HttpResponse(status=400), "invalid", error))
    (record,) = recorded
    context = record.context()
    assert context["reason"] == "unexpected_fields"
    assert context["unexpected_count"] == 1
    assert context["codes"] == ["invalid"]


def test_a_json_answer_of_its_own_carries_its_codes(recorded):
    """Scheduled email's Send test answers its refused text with codes."""
    answer(with_codes(JsonResponse({"errors": []}, status=400), "invalid"))
    (record,) = recorded
    assert record.context()["codes"] == ["invalid"]
    with pytest.raises(TypeError):
        with_codes(HttpResponse(status=400), "Not a code")


def test_a_report_error_page_carries_its_status_code_and_flags(recorded):
    """A report's own error page says why through its closed codes."""
    request = RequestFactory().get("/admin/")
    request.session = {}
    answer(export_ui._error(request, campaign_id=uuid4(), status=409, bound=True))
    (record,) = recorded
    assert record.context()["codes"] == ["stale_version", "form_reused"]


def test_a_plain_text_refusal_carries_its_status_code(recorded):
    """``private_response`` refusals record the code their status stands for."""
    answer(private_response("Invalid report filters.\n", status=400))
    (record,) = recorded
    assert record.context()["codes"] == ["invalid"]
    assert "Invalid" not in json.dumps(record.context())


def test_level_of_each_new_reason():
    """Server-state reasons are WARNING; the input's own are INFO."""
    for reason in (
        "campaign_zone_not_parish",
        "workspace_mail_missing",
        "test_result_uncertain",
    ):
        assert level_of(reason) == "WARNING"
    for reason in (
        "image_too_large",
        "test_pending",
        "drive_folder_missing",
        "pairing_code_refused",
    ):
        assert level_of(reason) == "INFO"


def test_a_refusal_never_records_its_message(recorded):
    """The reviewed text stays on the page; only the reason is kept."""
    error = UserFacingError("A secret message.", reason="drive_folder_missing")
    answer(error_response(error))
    (record,) = recorded
    assert "secret" not in json.dumps(record.context())
