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
validation/run_testdata_validation.py
Multi-instrument experimental testdata runner and numerical validator for Phase A.
"""

import platform
import subprocess
import time
from pathlib import Path

import numpy as np
import typer
from astropy.io import fits
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(help="Phase A Experimental Validation against ESA/ISDC Test Data")
console = Console()

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEST_DATA = PROJECT_ROOT.parent / "integral_test_data"
DEFAULT_IC_DATA = PROJECT_ROOT.parent / "integral_data_archive"


def compare_fits_tree(
    ref_dir: Path, test_dir: Path, tolerance: float = 1e-3
) -> tuple[int, int, int, list[tuple[str, str, str]]]:
    """
    Recursively compare FITS products generated in test_dir against official ref_dir.
    Returns: (passed_count, diff_count, missing_count, log_rows)
    """
    ref_fits = sorted(ref_dir.rglob("*.fits*"))
    passed = 0
    diffed = 0
    missing = 0
    rows: list[tuple[str, str, str]] = []

    for ref_f in ref_fits:
        rel = ref_f.relative_to(ref_dir)
        test_f = test_dir / rel

        if not test_f.exists():
            # In some OSA versions, intermediate grouping files or index files have slight path shifts
            missing += 1
            rows.append((str(rel), "[yellow]MISSING[/yellow]", "Not produced in test run"))
            continue

        try:
            with fits.open(ref_f) as hr, fits.open(test_f) as ht:
                max_rel = 0.0
                max_abs = 0.0
                file_ok = True

                for idx in range(min(len(hr), len(ht))):
                    dr = hr[idx].data
                    dt = ht[idx].data
                    if (
                        dr is None
                        or dt is None
                        or not isinstance(dr, np.ndarray)
                        or not isinstance(dt, np.ndarray)
                    ):
                        continue

                    if dr.dtype.kind in "fc" and dt.dtype.kind in "fc":
                        mask = np.isfinite(dr) & np.isfinite(dt)
                        if np.any(mask):
                            delta = np.abs(dr[mask] - dt[mask])
                            abs_val = float(np.max(delta))
                            denom = float(np.max(np.abs(dr[mask]))) + 1e-10
                            rel_val = abs_val / denom
                            max_abs = max(max_abs, abs_val)
                            max_rel = max(max_rel, rel_val)
                            if rel_val > tolerance:
                                file_ok = False

                if file_ok:
                    passed += 1
                    status = "[green]VERIFIED[/green]"
                    details = f"Rel Diff <= {max_rel:.2e} (Abs <= {max_abs:.2e})"
                else:
                    diffed += 1
                    status = "[red]DIFF[/red]"
                    details = f"Max Rel Diff = {max_rel:.2e} > {tolerance:.2e}"

                rows.append((str(rel), status, details))

        except Exception as err:
            diffed += 1
            rows.append((str(rel), "[red]ERROR[/red]", str(err)[:60]))

    return passed, diffed, missing, rows


@app.command()
def run(
    instrument: str = typer.Argument(
        ..., help="Instrument to validate: 'ibis', 'jemx', 'omc', 'spi', 'picsit', or 'all'"
    ),
    image: str = typer.Option(
        "cadarn/osa:11-native-arm64", "--image", "-i", help="Docker image to test"
    ),
    testdata_dir: Path = typer.Option(
        DEFAULT_TEST_DATA, "--testdata-dir", "-d", help="Path to unpacked integral_test_data"
    ),
    ic_dir: Path = typer.Option(
        DEFAULT_IC_DATA, "--ic-dir", help="Path to integral_data_archive with IC and catalogs"
    ),
    tolerance: float = typer.Option(
        1e-3, "--tolerance", "-t", help="Floating point relative tolerance"
    ),
    workdir: Path | None = typer.Option(None, "--workdir", "-w", help="Working directory for runs"),
):
    """Execute validation run for instrument and numerically verify against reference outputs."""
    instruments = ["ibis", "jemx", "omc", "spi"] if instrument == "all" else [instrument.lower()]

    console.print(
        Panel(
            f"[bold green]INTEGRAL OSA Phase A Experimental Validation[/bold green]\n\n"
            f"• Target Instruments : [cyan]{', '.join(instruments)}[/cyan]\n"
            f"• Container Image    : [cyan]{image}[/cyan]\n"
            f"• Host Architecture  : [cyan]{platform.machine()}[/cyan]\n"
            f"• Test Data Base     : [cyan]{testdata_dir}[/cyan]\n"
            f"• IC & Catalogs Base : [cyan]{ic_dir}[/cyan]\n"
            f"• Numerical Tolerance: [cyan]{tolerance:.1e}[/cyan]",
            title="Validation Suite Setup",
        )
    )

    if not testdata_dir.exists():
        console.print(
            f"[bold red]ERROR: Test data directory '{testdata_dir}' not found.[/bold red]"
        )
        raise typer.Exit(code=1)

    overall_results = Table(title="Phase A Scientific Validation Summary")
    overall_results.add_column("Instrument", style="cyan")
    overall_results.add_column("Duration", style="magenta")
    overall_results.add_column("Products Verified", style="green")
    overall_results.add_column("Diffs", style="red")
    overall_results.add_column("Missing in Run", style="yellow")
    overall_results.add_column("Status", style="bold")

    for instr in instruments:
        script_name = f"run_{instr}_test.sh"
        script_path = SCRIPTS_DIR / script_name

        if not script_path.exists():
            console.print(f"[bold red]Error: Script {script_path} not found.[/bold red]")
            continue

        target_work = workdir if workdir else PROJECT_ROOT / f"validation_runs/{instr}"
        ref_outref_dir = testdata_dir / f"{instr}docker_outref/run/obs/osatest"
        if not ref_outref_dir.exists():
            # Fallback to standard outref if docker_outref is not present (e.g. picsit)
            ref_outref_dir = testdata_dir / f"{instr}_outref/run/obs/osatest"

        console.print(
            f"\n[bold blue]─── Launching {instr.upper()} Validation Pipeline ───[/bold blue]"
        )
        t0 = time.perf_counter()

        cmd = [
            str(script_path),
            "osatest",
            image,
            str(testdata_dir),
            str(ic_dir),
            str(target_work),
        ]

        try:
            subprocess.run(cmd, check=True)
            duration = time.perf_counter() - t0
            console.print(f"[bold green]✓ Pipeline completed in {duration:.1f}s[/bold green]")
        except subprocess.CalledProcessError as err:
            duration = time.perf_counter() - t0
            console.print(f"[bold red]✗ Pipeline failed with exit code {err.returncode}[/bold red]")
            overall_results.add_row(
                instr.upper(), f"{duration:.1f}s", "0", "0", "0", "[red]FAILED[/red]"
            )
            continue

        # Compare outputs against reference directory
        test_obs_dir = target_work / "obs/osatest"
        if not ref_outref_dir.exists():
            console.print(
                f"[dim yellow]Warning: Reference directory '{ref_outref_dir}' not found. Skipping diff.[/dim yellow]"
            )
            overall_results.add_row(
                instr.upper(), f"{duration:.1f}s", "N/A", "0", "0", "[green]COMPLETED[/green]"
            )
            continue

        console.print(f"Comparing products against reference: [dim]{ref_outref_dir}[/dim]...")
        passed, diffed, missing, rows = compare_fits_tree(
            ref_outref_dir, test_obs_dir, tolerance=tolerance
        )

        detail_table = Table(title=f"{instr.upper()} FITS Product Comparisons")
        detail_table.add_column("Relative Product Path", style="cyan")
        detail_table.add_column("Status", style="bold")
        detail_table.add_column("Details", style="dim")

        for r in rows[:30]:  # show top 30
            detail_table.add_row(*r)
        if len(rows) > 30:
            detail_table.add_row(f"... and {len(rows) - 30} more products", "", "")

        console.print(detail_table)

        status_str = (
            "[bold green]PASS[/bold green]" if diffed == 0 else "[bold red]FAIL (DIFF)[/bold red]"
        )
        overall_results.add_row(
            instr.upper(),
            f"{duration:.1f}s",
            str(passed),
            str(diffed),
            str(missing),
            status_str,
        )

    console.print("\n")
    console.print(overall_results)


if __name__ == "__main__":
    app()
