"""The checkbox widget of an acknowledgment that a form requires before sending.

ui-v1.js keeps the form's submit buttons disabled until every visible checkbox
with ``data-acknowledgment`` is checked, and says why in a hint it adds
after the button (the box's ``data-missing-hint``, or a generic line). It is
progressive enhancement only: each form still validates its acknowledgment on
the server.
"""

from django import forms

# Django copies a field's widget instance, so one shared instance is safe.
ACKNOWLEDGMENT = forms.CheckboxInput(attrs={"data-acknowledgment": True})
