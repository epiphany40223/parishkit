"""The shared Admin form base and what the refusal log reads from it (#1403).

Every form and formset class under ``parishkit.stewardship`` derives from
``web.admin_forms``' bases, so the refusal log middleware can name a refused
form's own fields and Django error codes with no logging call in any of the
views that draw a refused form again. No database: ``record_refusal`` is
replaced by a list, as in ``test_admin_refusal_log.py``; the durable entries
of real refused pages are tested in
``database/test_admin_form_refusal_postgresql.py``.
"""

import ast
import importlib
import json
import pkgutil
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from django import forms
from django.forms import BaseFormSet, formset_factory
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import resolve

import parishkit.stewardship
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.web import admin_forms, refusal_log
from parishkit.stewardship.web.admin_forms import AdminForm, AdminFormSet, collecting
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.refusal_log import AdminRefusalMiddleware

PACKAGE = Path(__file__).resolve().parents[2] / "src/parishkit/stewardship"
BASES = PACKAGE / "web/admin_forms.py"
ROUTE = "/admin/users/automation/sessions/"
# Django's own form and formset bases. The text guard matches the last
# part of a base's dotted name (``forms.Form``, ``djforms.Form``,
# ``django.forms.formsets.BaseFormSet``); the runtime guard below also
# catches an imported alias.
DJANGO_BASES = frozenset(
    {
        "Form",
        "BaseForm",
        "ModelForm",
        "BaseModelForm",
        "BaseFormSet",
        "BaseModelFormSet",
        "BaseInlineFormSet",
    }
)
# Each factory and the keyword that must name an Admin base for it.
FACTORIES = {
    "formset_factory": "formset",
    "modelformset_factory": "formset",
    "inlineformset_factory": "formset",
    "modelform_factory": "form",
}
# A value and a message no entry may ever hold.
SECRET = "zz-secret-value@example.org"


def _django(node):
    """Whether an expression names one of Django's own bases."""
    return ast.unparse(node).rsplit(".", 1)[-1] in DJANGO_BASES


def bypasses(source):
    """``(class or call, line)`` for each form that skips the Admin bases.

    A class whose base is one of Django's own form or formset bases, and a
    form or formset factory call whose ``formset=`` (``form=`` for
    ``modelform_factory``) is missing or names one of Django's bases, so
    the class it builds would derive from Django's directly.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef):
            if any(_django(base) for base in node.bases):
                found.append((node.name, node.lineno))
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func).rsplit(".", 1)[-1]
            keyword = FACTORIES.get(name)
            if keyword is None:
                continue
            given = [item.value for item in node.keywords if item.arg == keyword]
            if not given or _django(given[0]):
                found.append((name, node.lineno))
    return found


def stewardship_classes():
    """Every form and formset class the package defines or builds.

    Imports every ``parishkit.stewardship`` module, then takes each
    subclass of Django's form and formset bases defined in the package,
    and each such class a module holds at its top level (a factory's
    result, whose ``__module__`` is Django's), leaving out Django's own
    named classes.
    """
    # Settings modules configure a process (one refuses to load on its own)
    # and define no forms.
    modules = [
        importlib.import_module(info.name)
        for info in pkgutil.walk_packages(
            parishkit.stewardship.__path__, "parishkit.stewardship."
        )
        if not info.name.startswith("parishkit.stewardship.settings")
    ]
    found, pending = set(), [forms.BaseForm, BaseFormSet]
    while pending:
        for child in pending.pop().__subclasses__():
            if child not in found:
                found.add(child)
                pending.append(child)
    held = {
        value
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, (forms.BaseForm, BaseFormSet))
    }

    def django_own(cls):
        """Whether ``cls`` is a class Django itself defines and names."""
        module = sys.modules.get(cls.__module__)
        return cls.__module__.startswith("django.") and (
            getattr(module, cls.__name__, None) is cls
        )

    return {
        cls
        for cls in found | held
        if not django_own(cls)
        and (cls.__module__.startswith("parishkit.stewardship") or cls in held)
        and cls not in {AdminForm, AdminFormSet}
    }


def test_every_form_and_formset_class_derives_from_the_admin_bases():
    """At run time, aliases and factories included, every form and formset
    class of the package is an ``AdminForm`` or ``AdminFormSet``."""
    classes = stewardship_classes()
    assert len(classes) > 40
    wrong = sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in classes
        if not issubclass(cls, (AdminForm, AdminFormSet))
    )
    assert not wrong, wrong


def test_every_form_and_formset_uses_the_admin_bases():
    """A new form that derives from Django's bases directly is refused here,
    so the refusal log can never silently miss its errors."""
    found = {
        f"{path.relative_to(PACKAGE)}:{line} {name}"
        for path in PACKAGE.rglob("*.py")
        if path != BASES
        for name, line in bypasses(path.read_text(encoding="utf-8"))
    }
    assert not found, sorted(found)


@pytest.mark.parametrize(
    "source",
    [
        "class A(forms.Form): pass",
        "from django.forms import Form\nclass A(Form): pass",
        "class A(django.forms.ModelForm): pass",
        "class A(BaseFormSet): pass",
        "class A(djforms.Form): pass",
        "class A(django.forms.formsets.BaseFormSet): pass",
        "A = formset_factory(F)",
        "A = forms.formset_factory(F, extra=1)",
        "A = formset_factory(F, formset=BaseFormSet)",
        "A = formset_factory(F, formset=forms.BaseFormSet)",
        "A = modelform_factory(M)",
        "A = modelform_factory(M, form=forms.ModelForm)",
    ],
)
def test_the_guard_refuses_each_way_around_the_bases(source):
    """The guard is not vacuous: each spelling of Django's bases is caught."""
    assert bypasses(source)


def test_the_guard_admits_the_admin_bases():
    """Forms on the bases, and formsets built on one, pass."""
    assert not bypasses(
        "class A(AdminForm): pass\nclass B(AdminFormSet): pass\n"
        "C = formset_factory(A, formset=B)\nD = modelform_factory(M, form=A)"
    )


@pytest.fixture
def recorded(monkeypatch):
    """Every RefusalRecord the middleware hands to the writer, in order."""
    records = []
    monkeypatch.setattr(refusal_log, "record_refusal", records.append)
    monkeypatch.setattr(refusal_log, "_mode", lambda: "testing")
    return records


class EntryForm(AdminForm):
    """A form with Django's usual codes: required, invalid_choice, max_length."""

    name = forms.CharField(max_length=5)
    kind = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])
    tags = forms.MultipleChoiceField(choices=[("x", "X"), ("y", "Y")], required=False)
    note = forms.CharField(required=False)


class SlotForm(AdminForm):
    """One formset row."""

    time = forms.TimeField()


class SlotRows(AdminFormSet):
    """A formset with a rule across its rows."""

    def clean(self):
        """At most one row may be on the hour."""
        if sum(1 for form in self.forms if form.cleaned_data.get("time")) > 1:
            raise forms.ValidationError("One slot only.", code="too_many_slots")


Slots = formset_factory(SlotForm, formset=SlotRows, extra=0)


def refuse(data, build, *, status=400, response=None):
    """POST ``data``; ``build`` validates forms from it in the view, which
    then answers ``status`` (or ``response``). Returns the request."""
    request = RequestFactory().post(ROUTE, data)
    request.resolver_match = resolve(ROUTE)
    request.principal = Principal(uuid4(), roles=frozenset({"administrator"}))

    def view(request):
        """A view that draws its refused form again."""
        build(request)
        return response or HttpResponse(status=status)

    AdminRefusalMiddleware(view)(request)
    return request


def test_a_form_drawn_again_names_its_fields_and_django_codes(recorded):
    """No logging call in the view: the 400 re-render alone is recorded."""
    refuse(
        {"name": "toolong", "kind": "zz", "note": SECRET},
        lambda request: EntryForm(request.POST).is_valid(),
    )
    (record,) = recorded
    context = record.context()
    assert context["form"] == "entry_form"
    assert context["fields"] == ["name", "kind"]
    assert context["codes"] == ["max_length", "invalid_choice"]
    assert context["reason"] == "unclassified" and "exception_class" not in context
    assert record.level == "INFO"


def test_no_message_or_value_is_ever_recorded(recorded, settings):
    """A view's own error message and every submitted value stay out.

    A code-less error fails a test under the strict test settings (#1437);
    this test turns that off to show what production records for one.
    """
    settings.STEWARDSHIP_ERROR_CODES_STRICT = False

    def build(request):
        form = EntryForm(request.POST)
        form.is_valid()
        form.add_error("note", f"Not {SECRET}.")
        form.add_error(None, forms.ValidationError(SECRET, code="clash"))

    refuse({"name": SECRET, "kind": SECRET, "note": SECRET}, build)
    (record,) = recorded
    context = record.context()
    assert SECRET not in json.dumps(context) and "Not" not in json.dumps(context)
    # A code-less error names its field with no code; a form-wide error
    # gives its code with no field.
    assert context["fields"] == ["name", "kind", "note"]
    assert context["codes"] == ["max_length", "invalid_choice", "clash"]


def test_a_submitted_extra_key_never_appears(recorded):
    """Fields come only from the form's own fields, never from the request."""
    extra = "zz_submitted_extra_key"
    refuse(
        {extra: "1", "kind": "a"}, lambda request: EntryForm(request.POST).is_valid()
    )
    (record,) = recorded
    assert extra not in json.dumps(record.context())
    assert record.context()["fields"] == ["name"]


def test_a_field_of_a_refused_forms_own_sent_twice_is_a_duplicate(recorded):
    """Named in ``fields`` with the code ``duplicate``; a multiple-choice
    field's several values and an unknown repeated key are not."""
    refuse(
        {
            "name": ["a", "b"],
            "kind": "zz",
            "tags": ["x", "y"],
            "zz_unknown": ["1", "2"],
        },
        lambda request: EntryForm(request.POST).is_valid(),
    )
    (record,) = recorded
    context = record.context()
    assert context["fields"] == ["kind", "name"]
    assert context["codes"] == ["invalid_choice", "duplicate"]
    assert context["form"] == "entry_form"


def test_a_clean_form_sent_twice_is_not_the_refused_one(recorded):
    """A form that validated cleanly adds no ``duplicate`` and does not
    claim ``form`` ahead of the form that was refused."""

    def build(request):
        SlotForm({"time": "09:00"}).is_valid()
        EntryForm(request.POST).is_valid()

    refuse({"name": ["a", "b"], "kind": "a", "time": ["1", "2"]}, build)
    context = recorded[0].context()
    assert "form" not in context and "fields" not in context
    recorded.clear()

    def build_two(request):
        EntryForm(request.POST).is_valid()
        EntryForm({"kind": "zz", "name": "ok"}).is_valid()

    refuse({"name": ["a", "b"], "kind": "a"}, build_two)
    context = recorded[0].context()
    assert context["fields"] == ["kind"] and context["codes"] == ["invalid_choice"]


def test_formset_rows_keep_their_prefix_and_index(recorded):
    """A row's field is ``prefix-N-name``; the formset's own rule adds its
    code with no field."""
    data = {
        "slots-TOTAL_FORMS": "3",
        "slots-INITIAL_FORMS": "0",
        "slots-0-time": "09:00",
        "slots-1-time": "bad",
        "slots-2-time": "10:00",
    }
    refuse(data, lambda request: Slots(request.POST, prefix="slots").is_valid())
    (record,) = recorded
    context = record.context()
    assert context["form"] == "slot_form"
    assert context["fields"] == ["slots-1-time"]
    assert context["codes"] == ["invalid", "too_many_slots"]


Deletable = formset_factory(SlotForm, formset=AdminFormSet, extra=0, can_delete=True)


def test_a_row_marked_for_deletion_is_skipped(recorded):
    """Django leaves a deleted row's errors out of its formset; so does the
    refusal log."""
    data = {
        "slots-TOTAL_FORMS": "2",
        "slots-INITIAL_FORMS": "2",
        "slots-0-time": "bad",
        "slots-0-DELETE": "on",
        "slots-1-time": "worse",
    }
    refuse(data, lambda request: Deletable(request.POST, prefix="slots").is_valid())
    assert recorded[0].context()["fields"] == ["slots-1-time"]


def test_a_formset_without_its_management_form_gives_its_code(recorded):
    """Django's own formset error is a closed code too."""
    refuse({}, lambda request: Slots(request.POST, prefix="slots").is_valid())
    (record,) = recorded
    assert record.context()["codes"] == ["missing_management_form"]
    # The formset class formset_factory names, in snake form.
    assert record.context()["form"].startswith("slot_form")


def test_errors_are_read_as_the_page_shows_them(recorded):
    """An error the form forgets after validating (as a refresh-schedule
    row forgets the fields its shape does not use) is not recorded."""

    def build(request):
        form = EntryForm(request.POST)
        form.is_valid()
        form._errors.pop("kind")

    refuse({"name": "toolong"}, build)
    assert recorded[0].context()["fields"] == ["name"]


def test_typed_errors_come_first(recorded):
    """A typed refusal's own fields and codes lead; the forms' follow."""
    refuse(
        {"kind": "a"},
        lambda request: EntryForm(request.POST).is_valid(),
        response=validation_response(
            [FieldError(ErrorCode.INVALID, "schedule-date")], status=400
        ),
    )
    context = recorded[0].context()
    assert context["fields"] == ["schedule-date", "name"]
    assert context["codes"] == ["invalid", "required"]


@pytest.mark.parametrize("status", [200, 302])
def test_an_answer_that_is_not_a_refusal_records_nothing(recorded, status):
    """Form errors alone are not a refusal: the answer decides."""
    refuse({}, lambda request: EntryForm(request.POST).is_valid(), status=status)
    assert recorded == []


def test_a_valid_or_unbound_form_adds_nothing(recorded):
    """A refused request whose forms were clean records no form."""

    def build(request):
        EntryForm(request.POST).is_valid()
        EntryForm().is_valid()

    refuse({"name": "ok", "kind": "a"}, build)
    context = recorded[0].context()
    assert "form" not in context and "fields" not in context


def test_the_collector_is_gone_after_a_view_raises():
    """A view that raises still leaves no collector behind."""
    request = RequestFactory().post(ROUTE, {"name": "toolong"})
    request.resolver_match = resolve(ROUTE)

    def view(request):
        EntryForm(request.POST).is_valid()
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        AdminRefusalMiddleware(view)(request)
    assert admin_forms._VALIDATED.get() is None


def test_a_second_request_starts_empty(recorded):
    """A form refused in one request is never part of the next one's entry."""
    refuse({"kind": "zz"}, lambda request: EntryForm(request.POST).is_valid())
    refuse({}, lambda request: None)
    first, second = recorded
    assert first.context()["form"] == "entry_form"
    assert "form" not in second.context() and "codes" not in second.context()


def test_only_bound_forms_validated_in_the_request_are_collected():
    """Unbound forms are never collected; outside a request nothing is."""
    with collecting() as validated:
        unbound = EntryForm()
        unbound.is_valid()
        bound = EntryForm({"name": "toolong"})
        bound.is_valid()
        bound.full_clean()
    assert validated == [bound]
    # No collector bound: validating keeps nothing anywhere.
    EntryForm({"name": "x"}).is_valid()


def test_a_code_less_error_fails_under_the_test_settings():
    """The test settings refuse an Admin form error with no code (#1437),
    whether a view adds it or the form's own validation raises it."""
    form = EntryForm({"kind": "a"})
    form.is_valid()
    with pytest.raises(AssertionError, match="without a closed code"):
        form.add_error("note", "Not coded.")
    form = EntryForm({"kind": "a"})
    form.is_valid()
    form.add_error("note", forms.ValidationError("Coded.", code="clash"))
    assert form.has_error("note", "clash")
