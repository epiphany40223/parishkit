"""The codes and reasons pages with a refusal body of their own record (#1444).

The strict test settings refuse an Admin refusal the log can say nothing
about; these tests pin what each page fixed for it records. No database:
``record_refusal`` is replaced by a list, as in ``test_admin_refusal_log.py``,
and a page's drawing is stubbed where it would read the database. The
hosted-file rename's ``in_use`` code is checked against PostgreSQL in
``database/test_hosted_files_postgresql.py``.
"""

import pytest
from django.http import HttpResponse, QueryDict

from parishkit.stewardship.accounts import setup_views
from parishkit.stewardship.audit import log_views
from parishkit.stewardship.jobs import delivery_views
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import ErrorCode
from parishkit.stewardship.web.refusals import unexpected_fields, with_default_code

from .test_admin_refusal_log import answer, recorded, request_for  # noqa: F401


def counted_refusal():
    """An unexpected-fields refusal that counts one extra submitted name."""
    return unexpected_fields(QueryDict("note=a&zz=1"), allowed={"note"})


def test_the_mail_error_page_records_its_code_and_refusal(recorded):  # noqa: F811
    """Sent mail's error page: its code, and the refusal it shows."""
    answer(delivery_views._error(ErrorCode.INVALID, 400, counted_refusal()))
    answer(delivery_views._error(ErrorCode.DENIED, 403))
    first, second = (record.context() for record in recorded)
    assert first["codes"] == ["invalid"]
    assert first["reason"] == "unexpected_fields"
    assert first["unexpected_count"] == 1
    assert second["codes"] == ["denied"] and second["reason"] == "unclassified"


@pytest.mark.parametrize("own_error", [False, True])
def test_a_refused_bulk_action_records_its_refusal(
    recorded,  # noqa: F811
    monkeypatch,
    own_error,
):
    """The bulk region's refusal is recorded, unless the page answered with
    a refusal of its own (its 400 error page), which keeps its own reason."""
    page = HttpResponse(status=400)
    if own_error:
        page = delivery_views._error(ErrorCode.INVALID, 400, StaleRecordError())
    monkeypatch.setattr(
        delivery_views, "_bulk", lambda _: ({"_refusal": counted_refusal()}, 400)
    )
    monkeypatch.setattr(delivery_views, "_page", lambda *_, **__: page)
    answer(delivery_views.delivery_list(request_for("/admin/mail/sent/")))
    (record,) = recorded
    context = record.context()
    if own_error:
        assert context["reason"] == "unclassified"
        assert context["exception_class"] == "stale_record_error"
    else:
        assert context["reason"] == "unexpected_fields"
        assert context["unexpected_count"] == 1


def test_the_logs_error_page_records_its_code(recorded):  # noqa: F811
    """System logs' own error page."""
    answer(log_views._error(ErrorCode.INVALID, 400, query_string=True))
    (record,) = recorded
    assert record.context()["codes"] == ["invalid"]


def test_setups_error_page_keeps_the_refusals_reason(recorded, monkeypatch):  # noqa: F811
    """The wizard page drawn from the JSON answer keeps its reason and class."""
    monkeypatch.setattr(
        setup_views, "runtime", lambda: (_ for _ in ()).throw(PermissionError())
    )
    monkeypatch.setattr(
        setup_views,
        "render",
        lambda request, template, context, status: HttpResponse(status=status),
    )
    request = request_for()
    request.method = "GET"
    page = setup_views.page_error(
        request, StaleRecordError(), fallback=lambda _: HttpResponse(status=409)
    )
    answer(page, request)
    (record,) = recorded
    context = record.context()
    assert context["status"] == 409
    assert context["exception_class"] == "stale_record_error"


def test_a_default_code_must_be_a_closed_identifier():
    """As for ``with_codes``: a page names closed codes only."""
    with pytest.raises(TypeError):
        with_default_code(HttpResponse(status=400), "Not a code")
