#!/usr/bin/env python3
"""
validate_science_products.py (Shim)
Preserves backwards compatibility for scripts invoking scripts/validate_science_products.py.
"""

from integral.validation.compare import compare, compare_images, compare_tables, main

__all__ = ["compare", "compare_images", "compare_tables", "main"]

if __name__ == "__main__":
    main()
