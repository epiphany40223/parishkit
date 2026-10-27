"""Human-readable campaign diffs never expose configuration UUIDs as UI labels."""

from django.utils.translation import gettext_lazy as _

LABELS = {
    "name": _("Campaign name"),
    "year_label": _("Stewardship year label"),
    "timezone": _("Campaign timezone"),
    "start_date": _("Campaign start date"),
    "end_date": _("Campaign end date"),
    "modules": _("Enabled modules"),
    "ministry_duids": _("Included Ministries"),
    "financial": _("Commitment and comparison periods"),
    "share_options": _("Share options"),
    "content_versions": _("Included content blocks"),
    "additional_information": _("Collect additional information"),
    "ministry_leader_roles": _("Ministry leader roles"),
}
MODULES = {
    "census": _("Census"),
    "ministry": _("Ministry stewardship"),
    "financial": _("Financial stewardship"),
}
SLOTS = {
    "welcome": _("Welcome"),
    "review": _("Review"),
    "thank_you": _("Thank you"),
    "closing": _("Closing page"),
    "additional": _("Additional information"),
    **MODULES,
}


def _choices(ids, choices):
    """Name each chosen Ministry or fund, never its DUID (#1123).

    A retained ID with no current choice stays visible as "Unavailable", so
    the review still counts it, without inventing a replacement name.
    """
    names = dict(choices)
    return ", ".join(str(names.get(str(key), _("Unavailable"))) for key in ids) or str(
        _("None")
    )


def display_value(key, value, *, ministries=(), funds=()):
    """Use exact civil dates and translated labels instead of Python repr output."""
    if value is None:
        return str(_("Not set"))
    if key == "modules":
        return ", ".join(str(MODULES[name]) for name in value)
    if key == "ministry_duids":
        return _choices(value, ministries)
    if key == "additional_information":
        return str(_("Yes") if value else _("No"))
    if key == "content_versions":
        return ", ".join(str(SLOTS[name]) for name in value) or str(_("None"))
    if key == "ministry_leader_roles":
        return ", ".join(value)
    if key == "share_options":
        return "; ".join(row["label"] for row in value) or str(_("None"))
    if key == "financial":
        return "\n".join(
            [
                _("Commitment period: %(start)s through %(end)s") % value,
                _("Pledge funds: %(funds)s")
                % {"funds": _choices(value["fund_duids"], funds)},
                _("Comparison: %(comparison_start)s through %(comparison_end)s")
                % value,
                _("Comparison funds: %(funds)s")
                % {"funds": _choices(value["comparison_fund_duids"], funds)},
            ]
        )
    return str(value)


def describe_changes(before, after, *, ministries=(), funds=()):
    """The receiving template escapes labels and values; none are marked safe."""
    return [
        {
            "label": LABELS[key],
            "before": display_value(
                key, before.get(key), ministries=ministries, funds=funds
            ),
            "after": display_value(key, value, ministries=ministries, funds=funds),
        }
        for key, value in after.items()
        if before.get(key) != value
    ]
