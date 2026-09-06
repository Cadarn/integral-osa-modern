"""
integral_cli (Compatibility Layer)

This package maintains backward compatibility for code importing from `integral_cli`.
Users and internal modules should migrate to importing from `integral`.
"""

from integral.core.config import config
from integral.core.docker import run_container
from integral.core.scw import filter_pointing_scws

__all__ = [
    "config",
    "filter_pointing_scws",
    "run_container",
]
