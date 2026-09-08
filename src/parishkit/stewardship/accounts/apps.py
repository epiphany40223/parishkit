"""Django registration for the Stewardship accounts boundary."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Use a namespaced, stable app label for future model migrations."""

    name = "parishkit.stewardship.accounts"
    label = "stewardship_accounts"
