"""Every validation error the package raises carries a closed code (#1437).

The refusal log (#1403; operations spec, Admin input refusals) records a
refused form's field names and Django ``ValidationError.code`` values, never
a message, because a message can echo what was submitted. A code-less error
names its field and says nothing about why, so every ``ValidationError(...)``
the package builds names its ``code=``, and every ``add_error(field, ...)``
passes one of them, or an error it caught, never a bare message. The guard
reads the source, so it needs no form to be drawn.
"""

import ast
from pathlib import Path

import pytest

from parishkit.stewardship.audit.refusal_context import IDENTIFIER

PACKAGE = Path(__file__).resolve().parents[2] / "src/parishkit/stewardship"
# Calls that may stay code-less, by file and exact source, each with why.
ALLOWED = {
    # ScheduleSet._check_collection: the rules between schedules.
    ("accounts/schedule_forms.py", "forms.ValidationError(errors)"),
    # AdminForm.add_error passes every caller's error on to Django, then
    # checks its code at run time (strict test settings).
    ("web/admin_forms.py", "super().add_error(field, error)"),
}


def _aliases(tree):
    """The names ``ValidationError`` goes by in ``tree``.

    ``ValidationError`` itself, a name it is imported ``as``, and a name
    assigned from it (``VE = forms.ValidationError``).
    """
    names = {"ValidationError"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {
                item.asname
                for item in node.names
                if item.name == "ValidationError" and item.asname
            }
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _names_error(node.value, names):
            names |= {item.id for item in node.targets if isinstance(item, ast.Name)}
    return names


def _names_error(node, aliases):
    """Whether ``node`` names ``ValidationError``: bare, aliased or dotted."""
    return (isinstance(node, ast.Name) and node.id in aliases) or (
        isinstance(node, ast.Attribute) and node.attr == "ValidationError"
    )


def _error_argument(call):
    """The error an ``add_error`` call passes, positional or keyword, or None
    when ``call`` is not an ``add_error`` call that passes one."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "add_error"):
        return None
    if len(call.args) >= 2:
        return call.args[1]
    return next((item.value for item in call.keywords if item.arg == "error"), None)


def _caught(tree, aliases):
    """The ids of ``add_error`` calls that pass on an error they caught.

    Such a call sits in the body of ``except ... ValidationError as name``,
    passes ``name`` itself, and the body never binds ``name`` again, so the
    error keeps the code it was raised with (checked where it is raised).
    """
    allowed = set()
    for handler in ast.walk(tree):
        if not (
            isinstance(handler, ast.ExceptHandler) and handler.name and handler.type
        ):
            continue
        types = (
            handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        )
        if not any(_names_error(item, aliases) for item in types):
            continue
        body = [node for statement in handler.body for node in ast.walk(statement)]
        rebound = any(
            isinstance(node, ast.Name)
            and node.id == handler.name
            and isinstance(node.ctx, ast.Store)
            for node in body
        )
        if rebound:
            continue
        for node in body:
            if isinstance(node, ast.Call):
                error = _error_argument(node)
                if isinstance(error, ast.Name) and error.id == handler.name:
                    allowed.add(id(node))
    return allowed


def _code_problem(call):
    """Why a ``ValidationError(...)`` call's code is unsafe, or None.

    A literal code must be an identifier. A named constant or attribute is
    checked by shape where it is defined. ``None``, or a conditional that
    can give ``None``, is refused: it builds a code-less error.
    """
    value = next((item.value for item in call.keywords if item.arg == "code"), None)
    if value is None:
        return "ValidationError without code="
    choices = [value]
    while any(isinstance(item, ast.IfExp) for item in choices):
        choices = [
            part
            for item in choices
            for part in (
                (item.body, item.orelse) if isinstance(item, ast.IfExp) else (item,)
            )
        ]
    for item in choices:
        if isinstance(item, ast.Constant):
            if not isinstance(item.value, str):
                return f"code {item.value!r} is not a closed code"
            if not IDENTIFIER.fullmatch(item.value):
                return f"code {item.value!r} is not an identifier"
    return None


def problems(source, name="<test>", allowed=frozenset()):
    """``(line, what)`` for each code-less validation error in ``source``.

    ``allowed`` holds the exact source text of calls that may stay code-less.
    """
    tree = ast.parse(source, name)
    aliases = _aliases(tree)
    caught = _caught(tree, aliases)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        error = _error_argument(node)
        is_error = _names_error(node.func, aliases)
        if not (is_error or error is not None):
            continue
        # Only a candidate's text is read: reading every call's is slow.
        if allowed and ast.get_source_segment(source, node) in allowed:
            continue
        if is_error:
            problem = _code_problem(node)
            if problem:
                found.append((node.lineno, problem))
        elif not (
            (isinstance(error, ast.Call) and _names_error(error.func, aliases))
            or id(node) in caught
        ):
            found.append((node.lineno, "add_error without a coded error"))
    return found


def test_every_validation_error_in_the_package_has_a_code():
    """No ``ValidationError`` or ``add_error`` under the package is code-less."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        relative = str(path.relative_to(PACKAGE))
        allowed = {text for where, text in ALLOWED if where == relative}
        found += [
            f"{relative}:{line}: {what}"
            for line, what in problems(path.read_text(), str(path), allowed)
        ]
    assert found == []


def test_each_allowed_exception_still_exists():
    """An allow-list entry whose call is gone is removed, not kept."""
    for where, text in ALLOWED:
        assert text in (PACKAGE / where).read_text(), (where, text)


@pytest.mark.parametrize(
    "source",
    [
        "raise forms.ValidationError('x')",
        "raise ValidationError(_('x'))",
        "form.add_error('a', 'message')",
        "form.add_error(None, _('message'))",
        "form.add_error('a', message)",
        "self.add_error('a', forms.ValidationError('x'))",
        "raise forms.ValidationError('x', code='Not An Identifier')",
        "raise forms.ValidationError('x', code=None)",
        "raise forms.ValidationError('x', code='a' if y else None)",
        "raise forms.ValidationError('x', code=a if y else (b if z else None))",
        "form.add_error(field='a', error='msg')",
        "form.add_error('a', error='msg')",
        "from django.core.exceptions import ValidationError as VE\nraise VE('x')",
        "VE = forms.ValidationError\nraise VE('x')",
        # A caught error is allowed only where it was caught...
        "try:\n    f()\nexcept forms.ValidationError as error:\n    pass\n"
        "form.add_error('a', error)",
        # ...and only while it is still the caught error.
        "try:\n    f()\nexcept forms.ValidationError as error:\n"
        "    error = 'message'\n    form.add_error('a', error)",
    ],
)
def test_the_guard_catches_each_code_less_spelling(source):
    """Each code-less spelling, and a code of the wrong shape, is caught."""
    assert problems(source)


@pytest.mark.parametrize(
    "source",
    [
        "raise forms.ValidationError('x', code='too_large')",
        "raise ValidationError(_('x'), code=CODE)",
        "form.add_error('a', forms.ValidationError('x', code='overlap'))",
        "try:\n    f()\nexcept forms.ValidationError as error:\n"
        "    form.add_error('a', error)",
        "except_ = form.add_error('a', ValidationError('x', code='choose_one'))",
        "raise forms.ValidationError('x', code='a' if y else 'b')",
        "form.add_error(field='a', error=forms.ValidationError('x', code='c'))",
        "from django.core.exceptions import ValidationError as VE\n"
        "raise VE('x', code='c')",
    ],
)
def test_the_guard_passes_coded_errors(source):
    """Coded errors, and a caught error passed on, pass."""
    assert problems(source) == []
