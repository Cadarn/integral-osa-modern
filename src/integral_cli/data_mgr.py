"""
Backwards-compatibility alias: `integral_cli.data_mgr` *is* `integral.core.data`.

This used to be a full copy of the downloader, which meant the tests exercised a stale copy
while the `integral` CLI ran `integral.core.data`. The module is aliased (not star-imported) so
that `monkeypatch.setattr(integral_cli.data_mgr, ...)` still patches the real implementation.
"""

import sys

from integral.core import data as _data
from integral.core.data import data_app, download_app  # explicit re-exports for static analysis

__all__ = ["data_app", "download_app"]

sys.modules[__name__] = _data
