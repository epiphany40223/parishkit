#!/usr/bin/env python3
"""Thin wrapper for the parishkit scheduled job runner."""

from parishkit.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
