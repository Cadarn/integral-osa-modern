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

from astropy.io import fits
from pydantic import BaseModel, Field
from rich.console import Console

from integral_cli.config import config

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
                if len(hdul) > 1 and hdul[1].data is not None:
                    data = hdul[1].data
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
                        hdul[1].data = data[mask]
                        hdul.flush()
        except Exception as err:
            console.print(f"[dim yellow]Warning: could not filter {rule.index}: {err}[/dim yellow]")

    return cal_cache_dir
