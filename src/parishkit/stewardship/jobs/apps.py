"""Django registration for the Stewardship jobs boundary."""

from django.apps import AppConfig


class JobsConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.jobs"
    label = "stewardship_jobs"
