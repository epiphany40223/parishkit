"""Django registration for the Stewardship jobs boundary."""

from django.apps import AppConfig


class JobsConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.jobs"
    label = "stewardship_jobs"

    def ready(self):
        """Read System health's debug-off switch on every new connection (#530)."""
        from parishkit.stewardship import debug_switch

        debug_switch.install()
