"""Every Reports and queries page names the Families its figures count (#2324).

ui-conventions "Population of report figures": a page either offers the
shared Families counted choice or names its one population in a fixed line,
in the choice's words, so a new report page cannot leave its readers to
guess which Families it counts.
"""

import pytest
from django.template.loader import get_template

from parishkit.stewardship.accounts import admin_navigation as navigation

# Each Reports and queries menu entry's page template, and how it names its
# population: "choice" for the shared Families counted choice, "fixed" for
# the fixed population line. A new menu entry fails the inventory below
# until it is added here and its template follows the rule.
REPORT_PAGES = {
    "response_dashboard": ("stewardship/response-dashboard.html", "choice"),
    "campaign_comparison": ("stewardship/campaign-comparison.html", "fixed"),
    "financial_report": ("stewardship/financial-report.html", "choice"),
    "family_directory": ("stewardship/directory.html", "fixed"),
}
# What each way looks like in a template: the shared segmented choice under
# the shared label, or the shared fixed line's tag in a marked paragraph.
MARKERS = {
    "choice": ('"stewardship/components/segmented-choice.html"', "scope_choice_label"),
    "fixed": ("data-report-population", "{% fixed_population "),
}


def _report_entries():
    """The Reports and queries menu entries, in menu order."""
    return [
        entry.name
        for entry in navigation.MENU
        if navigation.PAGES[entry.name].section == "reports"
    ]


def test_every_report_menu_entry_is_in_the_population_inventory():
    """No Reports and queries page escapes the check below."""
    assert sorted(_report_entries()) == sorted(REPORT_PAGES)


@pytest.mark.parametrize("name", sorted(REPORT_PAGES))
def test_every_report_page_names_population(name):
    """Each page offers the Families counted choice or a fixed population line."""
    template, way = REPORT_PAGES[name]
    source = get_template(template).template.source
    missing = [marker for marker in MARKERS[way] if marker not in source]
    assert not missing, f"{name} ({template}) lacks {missing}"


def test_the_fixed_line_speaks_in_the_choices_words():
    """The fixed line is the choice's label and a population's, from their one place."""
    from parishkit.stewardship.accounts.templatetags.stewardship import (
        fixed_population,
    )

    assert fixed_population("historical") == (
        "Families counted: Including Families that left"
    )
    assert fixed_population("current") == "Families counted: In this campaign today"
