"""The Preview's image recipe names its comparison generation (#2331)."""

from uuid import UUID

from parishkit.stewardship.reports.report_email_preview import _comparison_generation

OWN, HISTORICAL = str(UUID(int=1)), str(UUID(int=2))


def test_a_recipe_names_the_comparisons_generation():
    """``h`` names it (None: no comparison); a recipe signed before #2331
    has no ``h`` and its own generation was the historical one then."""
    assert _comparison_generation({"f": OWN, "h": HISTORICAL}) == UUID(HISTORICAL)
    assert _comparison_generation({"f": OWN, "h": None}) is None
    assert _comparison_generation({"f": OWN}) == UUID(OWN)
