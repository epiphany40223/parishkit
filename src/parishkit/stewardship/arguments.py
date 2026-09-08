"""Selective CLI diagnostics built only from explicitly public, authored text."""

import argparse
from collections.abc import Mapping


class StewardshipArgumentParser(argparse.ArgumentParser):
    """Keep known syntax hints while never rendering argparse's raw error text.

    Invalid input is untrusted even for public enums. Unknown tokens may contain
    secrets in the option name itself, so never echo them. Callers provide static
    hints keyed by argparse's registered argument name, not by user input.
    """

    def __init__(self, prog: str, *, description: str, error_hints: Mapping[str, str]):
        """Disable abbreviation and catch structured argument errors ourselves."""
        super().__init__(
            prog=prog, description=description, allow_abbrev=False, exit_on_error=False
        )
        self.error_hints = dict(error_hints)

    def parse_args(self, args=None, namespace=None):
        """Replace conversion/choice errors with reviewed hints, omitting values."""
        try:
            return super().parse_args(args, namespace)
        except argparse.ArgumentError as exc:
            self.usage_error(
                self.error_hints.get(
                    exc.argument_name,
                    "invalid argument (contents redacted); see --help",
                )
            )

    def error(self, message):
        """Suppress unstructured errors, including unknown tokens and extras."""
        self.usage_error(
            "unrecognized or invalid arguments (contents redacted); see --help"
        )

    def usage_error(self, public_message: str):
        """Report authored text only; never pass input values or exception text.

        This separate path preserves specific application validation messages
        whose command and option names have already passed enum/allowlist checks.
        """
        super().error(public_message)
