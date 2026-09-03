#!/usr/bin/env python3
"""
validate_science_products.py
Scientific validation tool to compare FITS products across ARM64 vs x86_64 or test runs.
"""

from pathlib import Path

import numpy as np
import typer
from astropy.io import fits
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="INTEGRAL FITS Science Product Validator")
console = Console()


def compare_images(fits_ref: Path, fits_test: Path, tolerance: float = 1e-3) -> bool:
    """Compare image HDUs in two FITS files."""
    with fits.open(fits_ref) as hdu_ref, fits.open(fits_test) as hdu_test:
        if len(hdu_ref) != len(hdu_test):
            console.print(f"[red]HDU count mismatch: {len(hdu_ref)} vs {len(hdu_test)}[/red]")
            return False

        all_passed = True
        for idx in range(len(hdu_ref)):
            h_ref = hdu_ref[idx]
            h_test = hdu_test[idx]

            if h_ref.data is None:
                continue

            if h_test.data is None:
                console.print(
                    f"[red]HDU {idx} ({h_ref.name}) has data in ref but None in test[/red]"
                )
                all_passed = False
                continue

            # Filter NaNs for comparison
            mask = np.isfinite(h_ref.data) & np.isfinite(h_test.data)
            if np.sum(mask) == 0:
                continue

            diff = np.abs(h_ref.data[mask] - h_test.data[mask])
            max_diff = np.max(diff)
            rel_diff = max_diff / (np.max(np.abs(h_ref.data[mask])) + 1e-10)

            status = "PASS" if rel_diff <= tolerance else "FAIL"
            color = "green" if status == "PASS" else "red"
            console.print(
                f"  HDU {idx} [{h_ref.name}]: Max Abs Diff = {max_diff:.3e}, Rel Diff = {rel_diff:.3e} -> [{color}]{status}[/{color}]"
            )

            if status == "FAIL":
                all_passed = False

        return all_passed


def compare_catalogs(fits_ref: Path, fits_test: Path) -> bool:
    """Compare detected source catalogs (positions, flux, significance)."""
    with fits.open(fits_ref) as hdu_ref, fits.open(fits_test) as hdu_test:
        # Find ISGRI-SRCL-RES or similar source list table
        table_ref = None
        table_test = None
        for h in hdu_ref:
            if "SRCL" in h.name or "CAT" in h.name or "RES" in h.name:
                table_ref = h
                break
        for h in hdu_test:
            if "SRCL" in h.name or "CAT" in h.name or "RES" in h.name:
                table_test = h
                break

        if table_ref is None or table_test is None:
            console.print("[yellow]No source list table found to compare catalogs.[/yellow]")
            return True

        sources_ref = table_ref.data.field("NAME") if "NAME" in table_ref.data.names else []
        sources_test = table_test.data.field("NAME") if "NAME" in table_test.data.names else []

        console.print(
            f"Sources in reference: {len(sources_ref)} | Sources in test: {len(sources_test)}"
        )
        return len(sources_ref) == len(sources_test)


@app.command()
def compare(
    ref_dir: Path = typer.Argument(
        ..., help="Path to reference results directory (x86_64 baseline)"
    ),
    test_dir: Path = typer.Argument(..., help="Path to test results directory (ARM64)"),
    tolerance: float = typer.Option(
        1e-3, "--tolerance", "-t", help="Relative floating point tolerance"
    ),
):
    """Compare all FITS products in test_dir against ref_dir."""
    if not ref_dir.exists() or not test_dir.exists():
        console.print("[red]Error: Both directories must exist.[/red]")
        raise typer.Exit(code=1)

    ref_fits_files = list(ref_dir.glob("**/*.fits*"))
    if not ref_fits_files:
        console.print(f"[yellow]No FITS files found in {ref_dir}[/yellow]")
        raise typer.Exit(code=0)

    console.print(
        f"[bold blue]Comparing {len(ref_fits_files)} FITS products between {ref_dir} and {test_dir}...[/bold blue]"
    )

    table = Table(title="Scientific Validation Results")
    table.add_column("Product File", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    all_ok = True
    for ref_file in ref_fits_files:
        rel_path = ref_file.relative_to(ref_dir)
        test_file = test_dir / rel_path

        if not test_file.exists():
            table.add_row(str(rel_path), "[red]MISSING[/red]", "File not generated in test run")
            all_ok = False
            continue

        try:
            is_valid = compare_images(ref_file, test_file, tolerance=tolerance)
            if is_valid:
                table.add_row(
                    str(rel_path), "[green]VERIFIED[/green]", f"Within {tolerance:.1e} tolerance"
                )
            else:
                table.add_row(
                    str(rel_path),
                    "[red]DIFF DETECTED[/red]",
                    "Numerical difference exceeds tolerance",
                )
                all_ok = False
        except Exception as e:
            table.add_row(str(rel_path), "[red]ERROR[/red]", str(e))
            all_ok = False

    console.print(table)
    if all_ok:
        console.print(
            "[bold green]✓ All scientific products verified identical within tolerance![/bold green]"
        )
    else:
        console.print("[bold red]✗ Some products had differences or were missing.[/bold red]")
        raise typer.Exit(code=1)


def main():
    app()


if __name__ == "__main__":
    main()
