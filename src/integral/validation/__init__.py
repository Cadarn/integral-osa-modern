"""Scientific verification, FITS diffing, and benchmarking tools."""

from integral.validation.benchmark import benchmark_app
from integral.validation.compare import compare, compare_images, compare_tables

__all__ = [
    "benchmark_app",
    "compare",
    "compare_images",
    "compare_tables",
]
