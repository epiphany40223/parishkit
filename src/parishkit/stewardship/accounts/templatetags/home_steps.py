"""Home's Next steps and Today line, for ``home.html`` (NAV-18)."""

from django import template

from parishkit.stewardship.accounts import home_steps

register = template.Library()


@register.simple_tag(takes_context=True)
def home_next_steps(context):
    """Return ``home_steps.home`` for this render, or None; see that function.

    A tag rather than a view value because the steps follow the viewer's
    menu, which the chrome's context processor builds at render time.
    """
    return home_steps.home(context)
