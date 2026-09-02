"""
Shared Science Window (ScW) ID selection logic, used by both local resolution
(analysis.py, for running a reduction on already-downloaded data) and remote
resolution (data_mgr.py, for deciding what to download from HEASARC).
"""


def filter_pointing_scws(scw_ids: list[str], rev_id: str) -> list[str]:
    """Select pointing ScWs (IDs ending '0010') for a revolution, falling back to all IDs.

    Mirrors the convention used throughout this project: a revolution's ScWs are mostly
    "pointing" observations (ending in 0010) interleaved with slews/other IDs; when no
    ScW ends in 0010 (unusual, but seen on some revolutions), fall back to every ID.
    """
    pointing = sorted(s for s in scw_ids if s.startswith(rev_id) and s.endswith("0010"))
    if pointing:
        return pointing
    return sorted(s for s in scw_ids if len(s) >= 12)
