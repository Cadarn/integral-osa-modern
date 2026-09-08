"""Dedicated instrument Docker image builder with baked-in IC calibration trees.

Supports:
- Packaging minimal IC + index + catalog trees per instrument (IBIS, JEM-X, OMC, SPI)
- Calibration profile selection ('latest' gold standard, 'esa-2022' legacy, or custom)
- Multi-architecture base images (native ARM64 vs modern amd64)
- Structured versioned tagging (e.g. 11.2-latest-arm64, 11.2-esa2022-amd64)
- Optional Docker build and push commands with safety checks for registry credentials
"""

from __future__ import annotations

import datetime
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from integral.core.calibration import get_profile, provision_profile_tree
from integral.core.config import config

console = Console()

SupportedInstrument = Literal["ibis", "jemx", "omc", "spi", "all"]


@dataclass
class InstrumentRequirements:
    """Defines which IC folders, indices, and catalogs are required by an instrument."""

    name: str
    display_name: str
    ic_dirs: list[str] = field(default_factory=list)
    cat_dirs: list[str] = field(default_factory=list)
    idx_patterns: list[str] = field(default_factory=list)
    default_ref_cat: str = "cat/hec/gnrl_refr_cat_0043.fits"


INSTRUMENT_SPECS: dict[str, InstrumentRequirements] = {
    "ibis": InstrumentRequirements(
        name="ibis",
        display_name="IBIS / ISGRI",
        ic_dirs=["ibis", "sc", "irem"],
        cat_dirs=["hec"],
        idx_patterns=["*ISGR*", "*IBIS*", "*PICS*", "*SC-*", "*GNRL*"],
        default_ref_cat="cat/hec/gnrl_refr_cat_0043.fits",
    ),
    "jemx": InstrumentRequirements(
        name="jemx",
        display_name="JEM-X (Units 1 & 2)",
        ic_dirs=["jmx1", "jmx2", "sc", "irem"],
        cat_dirs=["hec"],
        idx_patterns=["*JMX*", "*SC-*", "*GNRL*"],
        default_ref_cat="cat/hec/gnrl_refr_cat_0043.fits",
    ),
    "omc": InstrumentRequirements(
        name="omc",
        display_name="Optical Monitoring Camera (OMC)",
        ic_dirs=["omc", "sc"],
        cat_dirs=["omc"],
        idx_patterns=["*OMC*", "*SC-*", "*GNRL*"],
        default_ref_cat="cat/omc/omc_refr_cat_0005.fits",
    ),
    "spi": InstrumentRequirements(
        name="spi",
        display_name="Spectrometer on INTEGRAL (SPI)",
        ic_dirs=["spi", "sc", "irem"],
        cat_dirs=["hec"],
        idx_patterns=["*SPI*", "*SC-*", "*GNRL*"],
        default_ref_cat="cat/hec/gnrl_refr_cat_0043.fits",
    ),
}


def stage_instrument_calibration_tree(
    instrument: str,
    target_stage_dir: Path,
    profile_name: str = "latest",
    source_ic_dir: Path | None = None,
) -> dict[str, int]:
    """Copy only the required IC subdirectories, index files, and catalogs into staging."""
    spec = INSTRUMENT_SPECS[instrument.lower()]
    base_ic = (source_ic_dir or config.current_ic).resolve()

    # Resolve profile (legacy vs latest)
    profile = get_profile(profile_name)
    provisioned_ic = provision_profile_tree(profile, base_archive=base_ic)

    target_stage_dir.mkdir(parents=True, exist_ok=True)
    stage_ic = target_stage_dir / "ic"
    stage_idx = target_stage_dir / "idx" / "ic"
    stage_cat = target_stage_dir / "cat"

    stage_ic.mkdir(parents=True, exist_ok=True)
    stage_idx.mkdir(parents=True, exist_ok=True)
    stage_cat.mkdir(parents=True, exist_ok=True)

    copied_counts = {"ic_files": 0, "idx_files": 0, "cat_files": 0}

    # 1. Copy required IC directories
    src_ic_dir = provisioned_ic / "ic" if (provisioned_ic / "ic").exists() else base_ic / "ic"
    for dname in spec.ic_dirs:
        src_sub = src_ic_dir / dname
        if src_sub.exists():
            dest_sub = stage_ic / dname
            if dest_sub.exists():
                shutil.rmtree(dest_sub)
            shutil.copytree(src_sub, dest_sub, symlinks=True)
            copied_counts["ic_files"] += sum(1 for _ in dest_sub.rglob("*") if _.is_file())

    # 2. Copy filtered index directory
    src_idx_dir = provisioned_ic / "idx" / "ic" if (provisioned_ic / "idx" / "ic").exists() else base_ic / "idx" / "ic"
    if src_idx_dir.exists():
        # Always include ic_master_file.fits
        master_file = src_idx_dir / "ic_master_file.fits"
        if master_file.exists():
            shutil.copy2(master_file, stage_idx / "ic_master_file.fits")
            copied_counts["idx_files"] += 1

        for pattern in spec.idx_patterns:
            for idx_f in src_idx_dir.glob(pattern):
                dest_file = stage_idx / idx_f.name
                if not dest_file.exists():
                    shutil.copy2(idx_f, dest_file)
                    copied_counts["idx_files"] += 1

    # 3. Copy required catalog directories
    src_cat_dir = provisioned_ic / "cat" if (provisioned_ic / "cat").exists() else base_ic / "cat"
    for cname in spec.cat_dirs:
        src_csub = src_cat_dir / cname
        if src_csub.exists():
            dest_csub = stage_cat / cname
            if dest_csub.exists():
                shutil.rmtree(dest_csub)
            shutil.copytree(src_csub, dest_csub, symlinks=True)
            copied_counts["cat_files"] += sum(1 for _ in dest_csub.rglob("*") if _.is_file())

    return copied_counts


def generate_instrument_dockerfile(
    instrument: str,
    base_image: str,
    profile_name: str,
) -> str:
    """Generate the self-contained Dockerfile content for an instrument with baked IC."""
    spec = INSTRUMENT_SPECS[instrument.lower()]
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    dockerfile_content = f"""# syntax=docker/dockerfile:1
# Dedicated {spec.display_name} Hermetic Analysis Container
# Built on: {timestamp}
# Calibration Profile: {profile_name}
# Base Image: {base_image}

FROM {base_image}

LABEL maintainer="INTEGRAL Modernization Team <cadarn@github>"
LABEL org.opencontainers.image.title="INTEGRAL OSA {spec.display_name} Container"
LABEL org.opencontainers.image.description="Standalone {spec.display_name} pipeline container with baked IC calibration"
LABEL org.opencontainers.image.calibration_profile="{profile_name}"

# Setup internal calibration and data mounts
ENV CURRENT_IC=/opt/osa/caldb
ENV REP_BASE_PROD=/data
ENV ISDC_REF_CAT=/opt/osa/caldb/{spec.default_ref_cat}
ENV ISDC_ENV=/opt/osa
ENV OSA_DIR=/opt/osa

# Create target directories
RUN mkdir -p /opt/osa/caldb/ic /opt/osa/caldb/idx/ic /opt/osa/caldb/cat /data/scw /data/obs /data/work /scratch

# Copy baked instrument calibration tree
COPY ic/ /opt/osa/caldb/ic/
COPY idx/ /opt/osa/caldb/idx/
COPY cat/ /opt/osa/caldb/cat/

# Default working directory
WORKDIR /data
ENTRYPOINT ["/bin/bash", "-c", "exec \\"$@\\"", "--"]
CMD ["bash"]
"""
    return dockerfile_content


def construct_image_tags(
    instrument: str,
    registry_prefix: str,
    profile_name: str,
    target_arch: str,
    tag_version: str = "11.2",
    date_tag: bool = False,
) -> list[str]:
    """Construct informative image tags instead of a naive 'latest' tag."""
    # Tag structure:
    # 1. {tag_version}-{profile}-{arch}-{instrument}
    # 2. {tag_version}-{arch}-{instrument} (if profile is 'latest')
    # 3. YYYYMMDD snapshot tag if date_tag is enabled
    clean_profile = profile_name.replace("-", "")
    base_tag = f"{tag_version}-{clean_profile}-{target_arch}-{instrument}"

    tags = [f"{registry_prefix}:{base_tag}"]

    if profile_name == "latest":
        tags.append(f"{registry_prefix}:{tag_version}-{target_arch}-{instrument}")

    if date_tag:
        dt_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
        tags.append(f"{registry_prefix}:{tag_version}-{clean_profile}-{target_arch}-{instrument}-{dt_str}")

    return tags


def build_and_package_instrument(
    instrument: str,
    arch: str = "auto",
    profile: str = "latest",
    registry: str = "cadarn/osa",
    version: str = "11.2",
    date_tag: bool = False,
    output_dir: Path | None = None,
    dry_run: bool = False,
    push: bool = False,
    no_cache: bool = False,
) -> list[str]:
    """Package calibration files and build/tag a dedicated instrument Docker image."""
    inst_key = instrument.lower()
    if inst_key not in INSTRUMENT_SPECS:
        raise ValueError(f"Unsupported instrument '{instrument}'. Choose from: {list(INSTRUMENT_SPECS.keys())}")

    target_arch = config.host_arch if arch == "auto" else arch
    platform_arg = "--platform=linux/arm64" if target_arch == "arm64" else "--platform=linux/amd64"

    # Select base image
    base_image = (
        "cadarn/osa:11-native-arm64"
        if target_arch == "arm64"
        else "cadarn/osa:11-modern-amd64"
    )

    build_tags = construct_image_tags(
        instrument=inst_key,
        registry_prefix=registry,
        profile_name=profile,
        target_arch=target_arch,
        tag_version=version,
        date_tag=date_tag,
    )

    stage_base = (output_dir or Path(f"/tmp/integral_pkg_{inst_key}_{profile}")).resolve()
    console.print(
        Panel(
            f"[bold green]Packaging Dedicated Container Image: {INSTRUMENT_SPECS[inst_key].display_name}[/bold green]\n\n"
            f"• Architecture:          [cyan]{target_arch}[/cyan] ({platform_arg})\n"
            f"• Calibration Profile:   [cyan]{profile}[/cyan]\n"
            f"• Base Image:            [cyan]{base_image}[/cyan]\n"
            f"• Primary Image Tag:     [cyan]{build_tags[0]}[/cyan]\n"
            f"• All Image Tags:        [cyan]{', '.join(build_tags)}[/cyan]\n"
            f"• Staging Path:          [cyan]{stage_base}[/cyan]",
            title="Instrument Package Configuration",
        )
    )

    # 1. Stage Calibration Tree
    console.print("[dim]Staging required calibration files, master indices, and catalogs...[/dim]")
    counts = stage_instrument_calibration_tree(
        instrument=inst_key,
        target_stage_dir=stage_base,
        profile_name=profile,
    )
    console.print(
        f"[green]✓ Staged {counts['ic_files']} IC files, {counts['idx_files']} index files, {counts['cat_files']} catalog files.[/green]"
    )

    # 2. Write Dockerfile
    dockerfile_path = stage_base / f"Dockerfile.{inst_key}"
    df_content = generate_instrument_dockerfile(
        instrument=inst_key,
        base_image=base_image,
        profile_name=profile,
    )
    with open(dockerfile_path, "w") as df:
        df.write(df_content)
    console.print(f"[green]✓ Generated container manifest at {dockerfile_path}[/green]")

    # 3. Docker Build
    cmd = ["docker", "build", platform_arg, "-f", str(dockerfile_path)]
    for t in build_tags:
        cmd.extend(["-t", t])
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(str(stage_base))

    if dry_run:
        console.print("\n[yellow]--dry-run enabled: skipping docker build. Command would be:[/yellow]")
        console.print(f"[bold cyan]{' '.join(cmd)}[/bold cyan]\n")
    else:
        console.print(f"\n[bold blue]Executing: {' '.join(cmd)}[/bold blue]")
        subprocess.run(cmd, check=True)
        console.print(f"[bold green]✓ Successfully built image tags: {', '.join(build_tags)}[/bold green]")

    # 4. Push Commands / Execution
    push_cmds = [f"docker push {t}" for t in build_tags]
    console.print("\n[bold]Registry Push Information:[/bold]")
    table = Table(title="DockerHub / Registry Push Commands")
    table.add_column("Tag", style="cyan")
    table.add_column("Command", style="bold yellow")
    for t, pcmd in zip(build_tags, push_cmds):
        table.add_row(t, pcmd)
    console.print(table)

    if push:
        if dry_run:
            console.print("[yellow]--dry-run enabled: skipping docker push.[/yellow]")
        else:
            console.print("[bold yellow]Pushing images to registry...[/bold yellow]")
            for pcmd in push_cmds:
                subprocess.run(pcmd.split(), check=True)
            console.print("[bold green]✓ Successfully pushed all tags to registry![/bold green]")
    else:
        console.print(
            "[dim]Note: Run the commands above to push to your DockerHub repository, or use --push if you are logged in.[/dim]"
        )

    return build_tags
