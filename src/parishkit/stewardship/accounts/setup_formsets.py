"""Closed wizard formset fields with bounded allocation and one version token."""

from .setup_views import _closed


def closed_formset(request, *, prefix, fields, extra_fields=()):
    """Validate counts before allocating forms; the browser cannot add field names."""
    allowed = {"version", *extra_fields}
    if request.method == "POST":
        total = request.POST.get(f"{prefix}-TOTAL_FORMS", "")
        if (
            not total.isascii()
            or not total.isdecimal()
            or len(total) > 3
            or str(int(total)) != total
            or int(total) > 101
        ):
            raise ValueError("Invalid setup row count.")
        allowed.update(
            f"{prefix}-{name}"
            for name in (
                "TOTAL_FORMS",
                "INITIAL_FORMS",
                "MIN_NUM_FORMS",
                "MAX_NUM_FORMS",
            )
        )
        allowed.update(
            f"{prefix}-{index}-{name}" for index in range(int(total)) for name in fields
        )
    _closed(request, allowed)
