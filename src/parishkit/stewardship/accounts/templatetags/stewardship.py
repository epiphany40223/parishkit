"""Only typed values reach the canonical display functions; HTML stays escaped."""

from django import template

from parishkit.stewardship.web import presentation

register = template.Library()
for name in ("number", "usd", "percentage", "out_of", "instant", "parish_date"):
    register.filter(name, getattr(presentation, name))
