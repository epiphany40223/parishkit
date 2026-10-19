"""My review work's panels, for its page and Admin Home (#1690)."""

from django import template

from parishkit.stewardship.accounts import review_work

register = template.Library()


@register.simple_tag(takes_context=True)
def review_panels(context, review):
    """Return ``review_work.panels`` for this render and ``review``; see that function.

    A tag rather than a view value because the panels follow the viewer's
    menu, which the chrome's context processor builds at render time.
    """
    return review_work.panels(context.get("admin_chrome") or {}, review or {})
