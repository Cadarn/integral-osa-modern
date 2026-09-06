#!/usr/bin/env python3
"""
scw_distributor.py (Shim)
Preserves backwards compatibility for scripts invoking pipeline/scw_distributor.py.
"""

from integral.core.batch import main

if __name__ == "__main__":
    main()
