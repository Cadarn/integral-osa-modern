#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "astropy>=6.0.0",
#     "numpy>=1.26.0",
#     "rich>=13.7.0",
#     "typer>=0.12.0",
# ]
# ///
"""
Phase B Scaling Benchmark Runner for INTEGRAL OSA Modernisation.

Automates multi-size and multi-repeat benchmark execution on Revolution 0060
data using the latest IC calibration tree and 18-60 keV energy band.
Measures wall-clock timings across Native ARM64 and Emulated x86_64, records
source detection statistics for the Crab, and dumps structured JSON results.
"""

import json
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import typer
from astropy.io import fits
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from integral.core.scw import filter_pointing_scws, validate_scws_have_data

app = typer.Typer(help="Phase B Scaling Benchmark Suite")
console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = Path.home() / "science" / "integral_data_archive"
OUTPUT_BASE = PROJECT_ROOT / "benchmark_runs" / "phase_b"

ARM64_IMAGE = "cadarn/osa:11-native-arm64"
X86_IMAGE = "cadarn/osa:11-modern-amd64"


def get_rev60_pointing_scws() -> list[str]:
    """Retrieve all valid pointing ScWs (ending in 0010) for Rev 0060 that have event data."""
    scw_dir = ARCHIVE_DIR / "scw" / "0060"
    if not scw_dir.exists():
        raise FileNotFoundError(f"Revolution 0060 directory not found at {scw_dir}")

    all_ids = sorted(
        [
            d.name.split(".")[0]
            for d in scw_dir.iterdir()
            if d.is_dir() and len(d.name.split(".")[0]) == 12
        ]
    )
    pointings = filter_pointing_scws(all_ids, "0060")

    # Filter for complete ScWs with valid isgri_events.fits to prevent mosaic crashes on unobserved/aborted pointings
    valid_pointings, _dropped = validate_scws_have_data(pointings, ARCHIVE_DIR, instrument="IBIS")
    return valid_pointings


def extract_source_stats(mosaic_res_file: Path) -> dict[str, Any]:
    """Extract source detection significance and flux from mosaic source results table."""
    if not mosaic_res_file.exists():
        return {"detected": False, "error": "File not found"}

    try:
        with fits.open(mosaic_res_file) as hdul:
            for hdu in hdul:
                h: Any = hdu
                if h.data is not None and hasattr(h.data, "names") and "NAME" in h.data.names:
                    data = h.data
                    # First check for Crab
                    for row in data:
                        name = str(row["NAME"]).strip()
                        if "CRAB" in name.upper() or "1ES 0534+220" in name:
                            detsig = float(
                                row["DETSIG"] if "DETSIG" in data.names else row.get("DET_SIG", 0.0)
                            )
                            flux = float(np.ravel(row["FLUX"])[0]) if "FLUX" in data.names else 0.0
                            flux_err = (
                                float(np.ravel(row["FLUX_ERR"])[0])
                                if "FLUX_ERR" in data.names
                                else 0.0
                            )
                            ra = float(row["RA_OBJ"]) if "RA_OBJ" in data.names else 0.0
                            dec = float(row["DEC_OBJ"]) if "DEC_OBJ" in data.names else 0.0
                            return {
                                "detected": True,
                                "name": name,
                                "detsig": detsig,
                                "flux": flux,
                                "flux_err": flux_err,
                                "ra": ra,
                                "dec": dec,
                            }
                    # If Crab not in FOV (e.g. Galactic plane pointings), return the brightest source
                    if len(data) > 0:
                        sig_col = (
                            "DETSIG"
                            if "DETSIG" in data.names
                            else ("DET_SIG" if "DET_SIG" in data.names else None)
                        )
                        if sig_col:
                            top_idx = int(np.argmax([float(r[sig_col]) for r in data]))
                            row = data[top_idx]
                            return {
                                "detected": True,
                                "name": str(row["NAME"]).strip(),
                                "detsig": float(row[sig_col]),
                                "flux": float(np.ravel(row["FLUX"])[0])
                                if "FLUX" in data.names
                                else 0.0,
                                "flux_err": float(np.ravel(row["FLUX_ERR"])[0])
                                if "FLUX_ERR" in data.names
                                else 0.0,
                                "ra": float(row["RA_OBJ"]) if "RA_OBJ" in data.names else 0.0,
                                "dec": float(row["DEC_OBJ"]) if "DEC_OBJ" in data.names else 0.0,
                            }
    except Exception as e:
        return {"detected": False, "error": str(e)}

    return {"detected": False, "error": "No sources detected"}


def run_single_benchmark(
    scws: list[str],
    image: str,
    arch_label: str,
    run_id: str,
    workdir: Path,
) -> dict[str, Any]:
    """Execute a single IBIS pipeline run and return performance and scientific metrics."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    scw_file = workdir / "scw.list"
    formatted = [f"{s}.001" if not s.endswith(".001") else s for s in scws]
    scw_file.write_text("\n".join(formatted) + "\n")

    og_name = "og_benchmark"
    obs_dir = workdir / "obs" / og_name
    mosaic_res = obs_dir / "isgri_mosa_res.fits"

    # In-container bash script matching production analysis pipeline
    bash_pipeline = f"""
    set -e
    ulimit -s unlimited || true
    [ -f /init.sh ] && source /init.sh 2>/dev/null || true
    [ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true

    export ISDC_ENV=/opt/osa
    export REP_BASE_PROD=/data
    export CFITSIO_INCLUDE_FILES=/opt/osa/templates
    export ISDC_REF_CAT="/data/cat/hec/gnrl_refr_cat_0043.fits"
    export HOME=/home/integral
    export PFILES="/home/integral/pfiles;/opt/osa/pfiles"
    mkdir -pv /home/integral/pfiles

    export COMMONSCRIPT=1
    export COMMONLOGFILE=+/home/integral/commonlog.txt
    export DISPLAY=""

    # In x86 emulation, ensure CentOS system glibc/libstdc++ and ROOT are prioritized
    if [[ "{arch_label}" == *"x86"* ]]; then
        export ROOTSYS=/opt/osa/root
        export LD_LIBRARY_PATH="/opt/osa/root/lib:/opt/osa/lib:/usr/lib64:/lib64"
    fi

    cd /home/integral

    echo "=== Initialising Observation Group ({og_name}) with {len(scws)} ScWs ==="
    og_create idxSwg="scw.list" \
              instrument="IBIS" \
              ogid="{og_name}" \
              baseDir="./" \
              obsDir="obs"

    echo "=== Running ibis_science_analysis (18-60 keV, IMA2) ==="
    cd obs/{og_name}

    ibis_science_analysis \
        startLevel="COR" \
        endLevel="IMA2" \
        IBIS_II_ChanNum=1 \
        IBIS_II_E_band_min="18" \
        IBIS_II_E_band_max="60" \
        SWITCH_disableIsgri="no" \
        SWITCH_disablePICsIT="yes" \
        SWITCH_disableCompton="yes" \
        OBS1_CleanMode=1 \
        brPifThreshold=0.0 \
        CAT_refCat="/data/cat/hec/gnrl_refr_cat_0043.fits[ISGRI_FLAG>0]" \
        brSrcDOL="/data/cat/hec/gnrl_refr_cat_0043.fits[ISGRI_FLAG2==5&&ISGR_FLUX_1>100]" \
        IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
        IC_Alias="OSA"

    cp -v /home/integral/commonlog.txt /home/integral/obs/{og_name}/{og_name}_run.log 2>/dev/null || true
    """

    uid = os.getuid()
    gid = os.getgid()
    platform_arg = (
        ["--platform", "linux/amd64"]
        if "x86" in arch_label.lower()
        else ["--platform", "linux/arm64"]
    )

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        *platform_arg,
        "-u",
        f"{uid}:{gid}",
        "-v",
        f"{workdir}:/home/integral",
        "-v",
        f"{ARCHIVE_DIR}/scw:/data/scw:ro",
        "-v",
        f"{ARCHIVE_DIR}/aux:/data/aux:ro",
        "-v",
        f"{ARCHIVE_DIR}/ic:/data/ic:ro",
        "-v",
        f"{ARCHIVE_DIR}/idx:/data/idx:ro",
        "-v",
        f"{ARCHIVE_DIR}/cat:/data/cat:ro",
        "-e",
        "HOME=/home/integral",
        image,
        "bash",
        "-c",
        bash_pipeline,
    ]

    console.print(f"[cyan]→ Starting {arch_label} [{run_id}] with {len(scws)} ScWs...[/cyan]")
    t0 = time.perf_counter()
    proc = subprocess.run(docker_cmd, capture_output=True, text=True, check=False)
    t_elapsed = time.perf_counter() - t0

    if proc.returncode != 0:
        console.print(
            f"[bold red]Run failed (exit code {proc.returncode}):[/bold red]\n{proc.stderr[-1000:]}"
        )
        return {
            "success": False,
            "error": proc.stderr[-2000:],
            "elapsed_seconds": t_elapsed,
            "returncode": proc.returncode,
        }

    source_data = extract_source_stats(mosaic_res)
    src_name = source_data.get("name", "Unknown")
    src_sig = source_data.get("detsig", 0.0)
    console.print(
        f"[green]✓ {arch_label} [{run_id}] completed in {t_elapsed:.2f}s "
        f"({t_elapsed / len(scws):.2f}s/ScW) | {src_name}: {src_sig:.2f}σ[/green]"
    )

    return {
        "success": True,
        "arch": arch_label,
        "image": image,
        "scw_count": len(scws),
        "run_id": run_id,
        "elapsed_seconds": t_elapsed,
        "per_scw_seconds": t_elapsed / len(scws),
        "source_stats": source_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.command()
def run(
    sizes: str = typer.Option(
        "10,25,full", "--sizes", "-s", help="ScW subset sizes to run (e.g. '10,25,full')"
    ),
    repeats: int = typer.Option(3, "--repeats", "-r", help="Number of repeats per test cell"),
    archs: str = typer.Option(
        "arm64,x86", "--archs", "-a", help="Architectures to test: 'arm64', 'x86', or 'arm64,x86'"
    ),
    output_file: Path = typer.Option(
        OUTPUT_BASE / "scaling_results.json", "--output", "-o", help="JSON output path"
    ),
):
    """Execute Phase B scaling benchmark matrix and output comprehensive results."""
    all_pointings = get_rev60_pointing_scws()
    total_pointings = len(all_pointings)

    console.print(
        Panel(
            f"[bold magenta]INTEGRAL OSA Phase B Scaling Benchmark[/bold magenta]\n\n"
            f"• Dataset:             Rev 0060 ({total_pointings} pointing ScWs available)\n"
            f"• Test Sizes:          {sizes}\n"
            f"• Repeats per cell:    {repeats}\n"
            f"• Architectures:       {archs}\n"
            f"• Host Machine:        Apple M4 Pro (macOS {platform.mac_ver()[0]}, 48 GB Unified Memory)\n"
            f"• Output File:         {output_file}",
            title="Benchmark Plan",
        )
    )

    size_tokens = [s.strip().lower() for s in sizes.split(",") if s.strip()]
    arch_list = [a.strip().lower() for a in archs.split(",") if a.strip()]

    target_sizes = []
    for token in size_tokens:
        if token in ("full", "all", "rev60"):
            target_sizes.append((total_pointings, f"Full ({total_pointings} ScWs)"))
        else:
            val = int(token)
            target_sizes.append((val, f"{val} ScWs"))

    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        try:
            with open(output_file, "r") as f_in:
                existing = json.load(f_in)
                # Keep non-overlapping cells or allow appending
                results_data = existing
                results_data["metadata"]["date"] = datetime.now(timezone.utc).isoformat()
        except Exception:
            results_data = {
                "metadata": {
                    "date": datetime.now(timezone.utc).isoformat(),
                    "host": {
                        "cpu": "Apple M4 Pro",
                        "cores": os.cpu_count(),
                        "os": f"macOS {platform.mac_ver()[0]}",
                        "machine": platform.machine(),
                    },
                    "energy_band": "18-60 keV",
                    "instrument": "IBIS/ISGRI",
                    "repeats": repeats,
                },
                "cells": [],
            }
    else:
        results_data = {
            "metadata": {
                "date": datetime.now(timezone.utc).isoformat(),
                "host": {
                    "cpu": "Apple M4 Pro",
                    "cores": os.cpu_count(),
                    "os": f"macOS {platform.mac_ver()[0]}",
                    "machine": platform.machine(),
                },
                "energy_band": "18-60 keV",
                "instrument": "IBIS/ISGRI",
                "repeats": repeats,
            },
            "cells": [],
        }

    for n_scw, label in target_sizes:
        scw_subset = all_pointings[:n_scw]
        console.print(
            f"\n[bold yellow]══════════ Benchmarking Subset: {label} ══════════[/bold yellow]"
        )

        for arch in arch_list:
            image = ARM64_IMAGE if arch == "arm64" else X86_IMAGE
            arch_label = "Native ARM64" if arch == "arm64" else "Emulated x86_64"
            timings = []
            runs = []

            for rep in range(1, repeats + 1):
                run_id = f"scw_{n_scw}_{arch}_rep{rep}"
                workdir = OUTPUT_BASE / f"run_scw{n_scw}_{arch}" / f"rep_{rep}"

                res = run_single_benchmark(
                    scws=scw_subset,
                    image=image,
                    arch_label=arch_label,
                    run_id=run_id,
                    workdir=workdir,
                )
                runs.append(res)
                if res.get("success"):
                    timings.append(res["elapsed_seconds"])

            cell_summary = {
                "scw_count": n_scw,
                "label": label,
                "arch": arch,
                "arch_label": arch_label,
                "image": image,
                "runs": runs,
                "timings": timings,
                "mean_seconds": float(np.mean(timings)) if timings else 0.0,
                "std_seconds": float(np.std(timings)) if len(timings) > 1 else 0.0,
                "min_seconds": float(np.min(timings)) if timings else 0.0,
                "max_seconds": float(np.max(timings)) if timings else 0.0,
            }
            # Replace existing matching cell or append
            replaced = False
            for idx_c, c in enumerate(results_data["cells"]):
                if c.get("scw_count") == n_scw and c.get("arch") == arch:
                    results_data["cells"][idx_c] = cell_summary
                    replaced = True
                    break
            if not replaced:
                results_data["cells"].append(cell_summary)

            # Checkpoint output JSON after every architecture set
            with open(output_file, "w") as f_out:
                json.dump(results_data, f_out, indent=2)

    # Display final summary table
    table = Table(title="Phase B Scaling Benchmark Summary Matrix")
    table.add_column("ScW Size", style="cyan")
    table.add_column("Architecture", style="bold")
    table.add_column("Mean Wall-clock (s)", justify="right")
    table.add_column("Std Dev (s)", justify="right")
    table.add_column("Per-ScW (s)", justify="right")
    table.add_column("Top Source", style="yellow")
    table.add_column("DetSig (σ)", justify="right")

    for cell in results_data["cells"]:
        mean_t = cell["mean_seconds"]
        std_t = cell["std_seconds"]
        per_scw = mean_t / cell["scw_count"] if cell["scw_count"] > 0 else 0.0
        # Get source stats from first successful run
        top_name = "-"
        top_sig = 0.0
        for r in cell["runs"]:
            if r.get("source_stats", {}).get("detected"):
                top_name = r["source_stats"]["name"]
                top_sig = r["source_stats"]["detsig"]
                break

        table.add_row(
            cell["label"],
            cell["arch_label"],
            f"{mean_t:.2f}",
            f"±{std_t:.2f}" if std_t > 0 else "-",
            f"{per_scw:.2f}",
            top_name,
            f"{top_sig:.2f}",
        )

    console.print("\n")
    console.print(table)
    console.print(
        f"\n[bold green]✓ Phase B Benchmark complete! Full structured results saved to {output_file}[/bold green]"
    )


if __name__ == "__main__":
    app()
