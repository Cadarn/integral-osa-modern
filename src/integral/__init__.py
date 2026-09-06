"""
INTEGRAL OSA Modernisation & Scientific Analysis Pipeline.

A modernised, cloud-native scientific analysis suite and Docker container orchestrator
for ESA's INTEGRAL (INTErnational Gamma-Ray Astrophysics Laboratory) observatory,
optimised for Apple Silicon (ARM64) and cloud environments.
"""

__version__ = "0.1.0"

from integral.core.config import config
from integral.core.docker import run_container
from integral.core.scw import filter_pointing_scws

__all__ = [
    "__version__",
    "config",
    "filter_pointing_scws",
    "run_container",
]
