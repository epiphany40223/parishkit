"""My review work's panels, for its page and Admin Home (#1690)."""

from django import template

from parishkit.stewardship.accounts import review_work

register = template.Library()


@register.simple_tag(takes_context=True)
def review_panels(context, review):
    """Return ``review_work.panels`` for this render and ``review``; see that function.

    A tag rather than a view value because the panels follow the viewer's
    menu, which the chrome's context processor builds at render time, and
    Admin Home's counts are read by that processor's open-count statement
    (``review_work.with_counts``).
    """
    review = review_work.with_counts(getattr(context, "request", None), review or {})
    return review_work.panels(context.get("admin_chrome") or {}, review)


@register.simple_tag(takes_context=True)
def review_wizard_cards(context, review):
    """Return ``review_work.wizard_cards`` for this render and ``review`` (#2123)."""
    return review_work.wizard_cards(context.get("admin_chrome") or {}, review or {})


@register.simple_tag(takes_context=True)
def review_wizard_entry(context, worklist, table):
    """Return the table's Review these one at a time button, or None (#2123).

    ``review_wizard_tables.table_entry`` for the signed-in reader; a tag so
    the shared table component draws it on every render of every review
    table, in-place redraws included, with no page passing it along.
    """
    from parishkit.stewardship.reports.review_wizard_tables import table_entry

    # Only a signed-in reader gets the button (a unit render of the table,
    # with no principal, draws none); the entry itself needs no reader.
    principal = getattr(getattr(context, "request", None), "principal", None)
    if principal is None:
        return None
    return table_entry(worklist, table)
