#!/usr/bin/env python3
"""
fetch_integral_data.py (Shim)
Preserves backwards compatibility for scripts invoking scripts/fetch_integral_data.py.
"""

# Provide legacy entry point if invoked directly
from rich.console import Console

from integral.core.data import data_app

console = Console()


def main():
    data_app()


if __name__ == "__main__":
    main()
