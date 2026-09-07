from pathlib import Path


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


def validate_scws_have_data(
    scw_ids: list[str],
    archive_base: "Path",
    instrument: str = "IBIS",
) -> tuple[list[str], list[str]]:
    """Validate that Science Windows exist locally and contain required raw science telemetry.

    Filters out aborted, unobserved, or corrupt pointing ScWs that lack raw science event
    files (e.g., missing isgri_events.fits for IBIS), preventing mosaic segmentation faults
    in ii_skyimage (rotat_) on ARM64 and other modern architectures.

    Returns:
        tuple[list[str], list[str]]: (valid_scws, dropped_scws)
    """
    inst = instrument.upper()
    valid: list[str] = []
    dropped: list[str] = []

    # Map instrument to primary science payload indicator
    required_file_map = {
        "IBIS": "isgri_events.fits",
        "ISGRI": "isgri_events.fits",
        "JEMX": "jmx1_events.fits",  # checked flexibly below
        "JEMX1": "jmx1_events.fits",
        "JEMX2": "jmx2_events.fits",
        "OMC": "omc_shots.fits",
        "SPI": "spi_raw.fits",
    }

    scw_base = archive_base / "scw"

    for scw_id in scw_ids:
        rev = scw_id[:4]
        # Check standard .001 directory or unversioned directory
        scw_dir = scw_base / rev / f"{scw_id}.001"
        if not scw_dir.exists():
            scw_dir = scw_base / rev / scw_id

        if not scw_dir.exists():
            dropped.append(scw_id)
            continue

        if inst in ("IBIS", "ISGRI"):
            evt = scw_dir / "isgri_events.fits"
            evt_gz = scw_dir / "isgri_events.fits.gz"
            # Valid event tables are typically > 50 KB (empty/aborted tables are absent or headers only)
            if (evt.exists() and evt.stat().st_size > 50000) or evt_gz.exists():
                valid.append(scw_id)
            else:
                dropped.append(scw_id)
        elif inst.startswith("JEMX"):
            # Check either JMX1 or JMX2 events depending on instrument specified
            target = "jmx2_events.fits" if "2" in inst else "jmx1_events.fits"
            f_un = scw_dir / target
            f_gz = scw_dir / f"{target}.gz"
            if (f_un.exists() and f_un.stat().st_size > 10000) or f_gz.exists():
                valid.append(scw_id)
            else:
                dropped.append(scw_id)
        else:
            req = required_file_map.get(inst)
            if req:
                f_un = scw_dir / req
                f_gz = scw_dir / f"{req}.gz"
                if f_un.exists() or f_gz.exists():
                    valid.append(scw_id)
                else:
                    dropped.append(scw_id)
            else:
                # Default: accept if directory exists
                valid.append(scw_id)

    return valid, dropped
