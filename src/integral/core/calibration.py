"""
integral_cli.cal_profiles
Calibration profile management:
- Declarative calibration configuration (JSON/dict)
- Built-in profiles: 'latest' (modern dynamic gold standard) and 'esa-2022' (ISDC testset baseline)
- Profile provisioning and index constraint filtering
- Interactive profile wizard
"""

import json
import shutil
from pathlib import Path
from typing import Any

from astropy.io import fits
from pydantic import BaseModel, Field
from rich.console import Console

from integral.core.config import config

console = Console()

PROFILES_DIR = Path.home() / ".integral" / "cal_profiles"


class CalibrationRule(BaseModel):
    """Rule defining a calibration constraint on an index table."""

    index: str = Field(description="Index filename (e.g. 'ISGR-RMF.-RSP-IDX.fits')")
    max_version: int | None = Field(
        default=None, description="Maximum VERSION integer to retain in the index"
    )
    exact_version: int | None = Field(
        default=None, description="Exact VERSION integer to retain in the index"
    )
    override_target: str | None = Field(
        default=None, description="Specific file pattern or substring required"
    )
    description: str | None = Field(default=None, description="Human explanation of this rule")


class CalibrationProfile(BaseModel):
    """Declarative calibration epoch profile."""

    name: str = Field(description="Profile identifier, e.g. 'esa-2022'")
    description: str = Field(description="Summary of the calibration purpose and epoch")
    rules: list[CalibrationRule] = Field(default_factory=list)


# Built-in baseline profiles
BUILTIN_PROFILES: dict[str, CalibrationProfile] = {
    "latest": CalibrationProfile(
        name="latest",
        description="Modern Gold Standard: unconstrained dynamic IC archive from NASA/HEASARC",
        rules=[],
    ),
    "esa-2022": CalibrationProfile(
        name="esa-2022",
        description="Official ESA/ISDC 2022 Testdata Baseline (pins IBIS RMF 0035, BKG v7, JMX2 IMOD 0382)",
        rules=[
            CalibrationRule(
                index="ISGR-RMF.-RSP-IDX.fits",
                max_version=1,
                description="Pins ISGRI response matrix to Version 1 (isgr_rmf_rsp_0035.fits)",
            ),
            CalibrationRule(
                index="ISGR-BACK-BKG-IDX.fits",
                max_version=7,
                description="Pins ISGRI background models to Version <= 7 (isgr_back_bkg_0007.fits)",
            ),
            CalibrationRule(
                index="JMX2-IMOD-GRP-IDX.fits",
                max_version=25,
                description="Pins JEM-X 2 instrument model to Version <= 25 (jmx2_imod_grp_0382.fits)",
            ),
            CalibrationRule(
                index="JMX1-IMOD-GRP-IDX.fits",
                max_version=25,
                description="Pins JEM-X 1 instrument model to Version <= 25",
            ),
        ],
    ),
}


def get_profiles_dir() -> Path:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILES_DIR


def list_profiles() -> dict[str, CalibrationProfile]:
    """List all built-in and user-defined profiles."""
    profiles = dict(BUILTIN_PROFILES)
    pdir = get_profiles_dir()
    for json_path in pdir.glob("*.json"):
        try:
            with open(json_path) as f:
                data = json.load(f)
            prof = CalibrationProfile(**data)
            profiles[prof.name] = prof
        except Exception as e:
            console.print(f"[dim yellow]Warning: could not read {json_path}: {e}[/dim yellow]")
    return profiles


def get_profile(name: str) -> CalibrationProfile:
    """Retrieve profile by name."""
    profiles = list_profiles()
    if name not in profiles:
        raise ValueError(
            f"Unknown calibration profile '{name}'. Available: {', '.join(sorted(profiles.keys()))}"
        )
    return profiles[name]


def save_user_profile(profile: CalibrationProfile) -> Path:
    """Save user profile to disk."""
    pdir = get_profiles_dir()
    target = pdir / f"{profile.name}.json"
    with open(target, "w") as f:
        json.dump(profile.model_dump(), f, indent=2)
    return target


def provision_profile_tree(profile: CalibrationProfile, base_archive: Path | None = None) -> Path:
    """Provision a customized IC tree applying the profile rules.

    Returns the directory path that should be mounted/used as CURRENT_IC.
    """
    archive = base_archive or config.current_ic
    if profile.name == "latest" and not profile.rules:
        # Standard unconstrained archive
        return archive

    cal_cache_dir = get_profiles_dir() / "envs" / profile.name
    idx_target = cal_cache_dir / "idx" / "ic"
    idx_target.mkdir(parents=True, exist_ok=True)

    # Symlink ic and cat from base archive to avoid duplicating multi-gigabyte data
    ic_link = cal_cache_dir / "ic"
    cat_link = cal_cache_dir / "cat"
    if not ic_link.exists() and (archive / "ic").exists():
        ic_link.symlink_to(archive / "ic")
    if not cat_link.exists() and (archive / "cat").exists():
        cat_link.symlink_to(archive / "cat")

    # Copy master file and index files
    source_idx = archive / "idx" / "ic"
    if not source_idx.exists():
        raise FileNotFoundError(f"Source index directory {source_idx} not found.")

    for f in source_idx.glob("*.fits"):
        shutil.copy2(f, idx_target / f.name)

    # Apply rules
    for rule in profile.rules:
        idx_file = idx_target / rule.index
        if not idx_file.exists():
            continue

        try:
            with fits.open(idx_file, mode="update") as hdul:
                hdu1: Any = hdul[1] if len(hdul) > 1 else None
                if hdu1 is not None and hdu1.data is not None:
                    data = hdu1.data
                    mask = None

                    if "VERSION" in data.names:
                        if rule.max_version is not None:
                            mask = data["VERSION"] <= rule.max_version
                        elif rule.exact_version is not None:
                            mask = data["VERSION"] == rule.exact_version

                    if rule.override_target and "MEMBER_LOCATION" in data.names:
                        target_mask = [
                            rule.override_target in str(loc) for loc in data["MEMBER_LOCATION"]
                        ]
                        mask = target_mask if mask is None else (mask & target_mask)

                    if mask is not None:
                        hdu1.data = data[mask]
                        hdul.flush()
        except Exception as err:
            console.print(f"[dim yellow]Warning: could not filter {rule.index}: {err}[/dim yellow]")

    return cal_cache_dir


INSTRUMENT_SUBSYSTEMS: dict[str, list[str]] = {
    "IBIS": ["IBIS", "ISGR", "PICS", "COMP", "GNRL", "INTL", "IREM"],
    "ISGRI": ["IBIS", "ISGR", "PICS", "COMP", "GNRL", "INTL", "IREM"],
    "JEMX": ["JMX1", "JMX2", "JEMX", "GNRL", "INTL"],
    "JEMX1": ["JMX1", "JEMX", "GNRL", "INTL"],
    "JEMX2": ["JMX2", "JEMX", "GNRL", "INTL"],
    "SPI": ["SPI", "GNRL", "INTL"],
    "OMC": ["OMC", "GNRL", "INTL"],
}


def resolve_ic_master_file(path: Path | None = None) -> Path:
    """Resolve the location of ic_master_file.fits from config or standard locations."""
    if path and path.exists():
        return path
    candidates = [
        config.rep_base_prod / "idx" / "ic" / "ic_master_file.fits",
        Path.home() / "experiments" / "integral_data_archive" / "idx" / "ic" / "ic_master_file.fits",
        Path.home() / "science" / "integral_data_archive" / "idx" / "ic" / "ic_master_file.fits",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "Could not locate ic_master_file.fits. Please specify --master-file or configure rep_base_prod."
    )


def prune_ic_master(
    master_file: Path | None = None,
    instruments: list[str] | None = None,
    backup: bool = True,
) -> tuple[int, int, Path]:
    """Prune ic_master_file.fits to keep only index members for selected instruments.

    This resolves DAL error -2004 (DAL_FILE_NOT_ACCESSIBLE) which occurs when DAL validates
    index members for instruments not staged in the local IC tree.

    Returns:
        tuple[int, int, Path]: (kept_rows, original_rows, master_file_path)
    """
    import numpy as np

    target = resolve_ic_master_file(master_file)
    bak = target.with_suffix(".fits.bak")

    # If backup requested and no backup exists yet, create one (preserving the pristine original)
    if backup and not bak.exists():
        shutil.copy2(target, bak)

    # Determine allowed prefixes
    if not instruments:
        # Auto-detect which instrument directories exist in ic/
        ic_base = target.parent.parent.parent / "ic"
        detected = []
        if ic_base.exists():
            for inst_dir in ic_base.iterdir():
                if inst_dir.is_dir() and inst_dir.name.upper() in INSTRUMENT_SUBSYSTEMS:
                    detected.append(inst_dir.name.upper())
        instruments = detected if detected else ["IBIS"]

    allowed_prefixes: set[str] = set()
    for inst in instruments:
        allowed_prefixes.update(INSTRUMENT_SUBSYSTEMS.get(inst.upper(), [inst.upper()]))

    # Always ensure GNRL, INTL, IREM are kept
    allowed_prefixes.update(["GNRL", "INTL", "IREM"])

    with fits.open(target, mode="update") as hdul:
        tbl_idx = None
        for i, hdu in enumerate(hdul):
            h: Any = hdu
            if (
                h.data is not None
                and hasattr(h.data, "names")
                and "MEMBER_LOCATION" in h.data.names
            ):
                tbl_idx = i
                break

        if tbl_idx is None:
            raise ValueError(f"MEMBER_LOCATION column not found in {target}")

        target_hdu: Any = hdul[tbl_idx]
        m_data = target_hdu.data
        orig_len = len(m_data)
        mask = [
            any(str(loc).startswith(p) for p in allowed_prefixes)
            for loc in m_data["MEMBER_LOCATION"]
        ]
        target_hdu.data = m_data[np.array(mask)]
        hdul.flush()

    return sum(mask), orig_len, target


def restore_ic_master(master_file: Path | None = None) -> tuple[Path, Path]:
    """Restore ic_master_file.fits from its .bak backup.

    Returns:
        tuple[Path, Path]: (master_file_path, backup_path)
    """
    target = resolve_ic_master_file(master_file)
    bak = target.with_suffix(".fits.bak")
    if not bak.exists():
        raise FileNotFoundError(f"No backup file found at {bak}")

    shutil.copy2(bak, target)
    return target, bak
