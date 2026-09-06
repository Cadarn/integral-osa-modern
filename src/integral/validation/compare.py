"""
validate_science_products.py / compare.py
Scientific validation tool to compare FITS products across ARM64 vs x86_64 or test runs.
"""

from pathlib import Path
from typing import Any

import numpy as np
import typer
from astropy.io import fits
from rich.console import Console
from rich.table import Table

console = Console()
compare_app = typer.Typer(help="INTEGRAL FITS Science Product Validator")


def compare_images(fits_ref: Path, fits_test: Path, tolerance: float = 1e-3) -> bool:
    """Compare image HDUs in two FITS files."""
    with fits.open(fits_ref) as hdu_ref, fits.open(fits_test) as hdu_test:
        if len(hdu_ref) != len(hdu_test):
            console.print(f"[red]HDU count mismatch: {len(hdu_ref)} vs {len(hdu_test)}[/red]")
            return False

        all_passed = True
        for idx in range(len(hdu_ref)):
            h_ref: Any = hdu_ref[idx]
            h_test: Any = hdu_test[idx]

            if h_ref.data is None:
                continue

            if h_test.data is None:
                console.print(
                    f"[red]HDU {idx} ({h_ref.name}) has data in ref but None in test[/red]"
                )
                all_passed = False
                continue

            d_ref = np.nan_to_num(h_ref.data)
            d_test = np.nan_to_num(h_test.data)

            if d_ref.shape != d_test.shape:
                console.print(
                    f"[red]Shape mismatch in HDU {idx}: {d_ref.shape} vs {d_test.shape}[/red]"
                )
                all_passed = False
                continue

            diff = np.abs(d_ref - d_test)
            denom = np.abs(d_ref) + 1e-7
            rel_diff = diff / denom

            max_rel = np.max(rel_diff)
            max_abs = np.max(diff)

            if max_rel > tolerance:
                console.print(
                    f"[yellow]HDU {idx} ({h_ref.name}): Max Rel Diff = {max_rel:.4e}, Max Abs Diff = {max_abs:.4e}[/yellow]"
                )
                all_passed = False

        return all_passed


def compare_tables(fits_ref: Path, fits_test: Path, tolerance: float = 1e-3) -> bool:
    """Compare binary tables in two FITS files."""
    with fits.open(fits_ref) as hdu_ref, fits.open(fits_test) as hdu_test:
        all_passed = True
        for idx in range(1, len(hdu_ref)):
            h_ref: Any = hdu_ref[idx]
            h_test: Any = hdu_test[idx]

            if not isinstance(h_ref, fits.BinTableHDU) or not isinstance(h_test, fits.BinTableHDU):
                continue

            t_ref: Any = h_ref.data
            t_test: Any = h_test.data

            if t_ref is None or t_test is None:
                continue

            if len(t_ref) != len(t_test):
                console.print(
                    f"[red]Table row mismatch in HDU {idx}: {len(t_ref)} vs {len(t_test)}[/red]"
                )
                all_passed = False
                continue

            cols = getattr(t_ref, "columns", None)
            if cols is None or not hasattr(cols, "names"):
                continue

            for c in cols.names:
                t_test_cols = getattr(t_test, "columns", None)
                if t_test_cols is None or c not in t_test_cols.names:
                    console.print(f"[red]Column {c} missing in test table HDU {idx}[/red]")
                    all_passed = False
                    continue

                col_ref: Any = t_ref[c]
                col_test: Any = t_test[c]

                if hasattr(col_ref, "dtype") and np.issubdtype(col_ref.dtype, np.number):
                    diff = np.abs(np.asarray(col_ref) - np.asarray(col_test))
                    denom = np.abs(np.asarray(col_ref)) + 1e-7
                    rel_diff = diff / denom
                    max_rel = np.max(rel_diff)
                    if max_rel > tolerance:
                        console.print(
                            f"[yellow]HDU {idx} Col {c}: Max Rel Diff = {max_rel:.4e}[/yellow]"
                        )
                        all_passed = False
                elif hasattr(col_ref, "dtype") and np.issubdtype(col_ref.dtype, np.str_):
                    mismatch = np.sum(np.asarray(col_ref) != np.asarray(col_test))
                    if mismatch > 0:
                        console.print(
                            f"[yellow]HDU {idx} Col {c}: {mismatch} string mismatches[/yellow]"
                        )
                        all_passed = False

        return all_passed


@compare_app.command("compare")
def compare(
    ref_dir: Path = typer.Argument(..., help="Path to reference run directory (e.g. x86_64 run)"),
    test_dir: Path = typer.Argument(..., help="Path to test run directory (e.g. ARM64 run)"),
    tolerance: float = typer.Option(
        1e-3, "--tol", "-t", help="Relative difference tolerance for science products"
    ),
):
    """Numerically compare all science products between two run directories."""
    if not ref_dir.exists():
        console.print(f"[red]Reference directory {ref_dir} does not exist.[/red]")
        raise typer.Exit(code=1)
    if not test_dir.exists():
        console.print(f"[red]Test directory {test_dir} does not exist.[/red]")
        raise typer.Exit(code=1)

    ref_files = sorted(ref_dir.glob("**/*.fits"))
    if not ref_files:
        console.print(f"[yellow]No FITS files found in {ref_dir}.[/yellow]")
        return

    table = Table(title=f"FITS Science Product Comparison\nRef: {ref_dir} vs Test: {test_dir}")
    table.add_column("File", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    all_ok = True
    for rf in ref_files:
        rel_path = rf.relative_to(ref_dir)
        tf = test_dir / rel_path

        if not tf.exists():
            table.add_row(str(rel_path), "[red]MISSING[/red]", "File not generated in test run")
            all_ok = False
            continue

        try:
            img_ok = compare_images(rf, tf, tolerance=tolerance)
            tab_ok = compare_tables(rf, tf, tolerance=tolerance)

            if img_ok and tab_ok:
                table.add_row(str(rel_path), "[green]PASS[/green]", "Bit-accurate or within tol")
            else:
                table.add_row(
                    str(rel_path),
                    "[yellow]DIFF[/yellow]",
                    "Differences detected (exceeding tolerance)",
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
    compare_app()


if __name__ == "__main__":
    main()
