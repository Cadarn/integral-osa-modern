"""
Shared Science Window (ScW) ID selection logic, used by both local resolution
(analysis.py, for running a reduction on already-downloaded data) and remote
resolution (data_mgr.py, for deciding what to download from HEASARC).

Backwards-compatibility shim importing from src/integral/core/scw.py.
"""

from integral.core.scw import filter_pointing_scws, validate_scws_have_data

__all__ = ["filter_pointing_scws", "validate_scws_have_data"]
