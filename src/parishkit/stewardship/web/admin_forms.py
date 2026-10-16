"""The shared base of every Admin form and formset (#1403).

Operations spec, Admin input refusals: the refusal log records a refused
form's own field names and Django error codes (``required``,
``invalid_choice``, ``max_length`` and so on). About 110 views answer a
refused form by drawing it again with a 400 or 409, and none of them makes
a logging call. Instead every form validated during a request joins that
request's collector here, and the refusal log middleware
(``web.refusal_log``) reads the forms' final errors only when the answer is
a refusal. A form that validated cleanly, or a request that was not
refused, records nothing.

The collector holds the forms, not their errors, because a form's errors
can still change after it is validated: a view adds one with ``add_error``
after ``is_valid()``, and a refresh-schedule row forgets the errors of the
fields its shape does not use. Reading them once, as the answer leaves the
view, sees exactly the errors the page shows.

Outside a request (the command line, workers, tests that call a form
directly) no collector is bound, and nothing is kept. The forms' behavior
and rendering are Django's, unchanged.

``tests/stewardship/test_admin_forms.py`` refuses any form or formset class
under ``parishkit.stewardship`` that does not derive from these bases.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from django import forms
from django.forms.formsets import BaseFormSet

# The bound forms and formsets validated so far in this request, in the
# order they were validated; None outside a request.
_VALIDATED = ContextVar("stewardship_validated_admin_forms", default=None)


@contextmanager
def collecting():
    """Collect the Admin forms validated within this block; yields the list.

    ``web.refusal_log.AdminRefusalMiddleware`` binds it around each request.
    """
    token = _VALIDATED.set([])
    try:
        yield _VALIDATED.get()
    finally:
        _VALIDATED.reset(token)


def _collect(form):
    """Add a validated form or formset to the request's collector, once."""
    validated = _VALIDATED.get()
    if validated is not None and not any(item is form for item in validated):
        validated.append(form)


class AdminForm(forms.Form):
    """An Admin form whose validation the refusal log can read; never drawn
    or validated differently from a plain ``forms.Form``."""

    def full_clean(self):
        """Validate as Django does, then join the request's collector."""
        super().full_clean()
        if self.is_bound:
            _collect(self)


class AdminFormSet(BaseFormSet):
    """An Admin formset whose own (non-form) errors the refusal log can read.

    Each row is an ``AdminForm`` that joins the collector itself, with its
    prefixed field names (``slots-0-time``); this adds the formset's own
    errors, such as a missing management form or a rule across rows.
    """

    def full_clean(self):
        """Validate as Django does, then join the request's collector."""
        super().full_clean()
        if self.is_bound:
            _collect(self)
