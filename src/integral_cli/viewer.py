"""
Image and source visualisation helpers for INTEGRAL data products (FITS images, source lists).
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()
view_app = typer.Typer(help="Inspect and visualise INTEGRAL FITS products and source lists")


@view_app.command("image")
def view_image(
    fits_path: Path = typer.Argument(
        ..., help="Path to FITS image file (e.g. isgri_mosa_ima.fits, isgri_sky_ima.fits)"
    ),
    ext: int | None = typer.Option(
        None, "--ext", "-e", help="Optional extension number (HDU index) to render"
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Optional output PNG image path"
    ),
    title: str | None = typer.Option(None, "--title", "-t", help="Plot title"),
):
    """Render a 2D FITS image with WCS equatorial coordinates and save/display."""
    if not fits_path.exists():
        console.print(f"[bold red]Error: File {fits_path} does not exist.[/bold red]")
        raise typer.Exit(code=1)

    try:
        import matplotlib
        from astropy.io import fits
        from astropy.visualization import ImageNormalize, ZScaleInterval

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from astropy.wcs import WCS

        with fits.open(fits_path) as hdul:
            target_hdu = None
            if ext is not None and ext < len(hdul):
                h = hdul[ext]
                # astropy's HDUList.__getitem__ stub returns HDUList instead of the actual HDU
                # subtype, so pyright can't see `.data` here even though it exists at runtime.
                if getattr(h, "data", None) is not None and getattr(h.data, "ndim", 0) == 2:  # pyright: ignore[reportAttributeAccessIssue]
                    target_hdu = h

            if target_hdu is None:
                # Search for first HDU with valid 2D image data
                for i, h in enumerate(hdul):
                    if getattr(h, "data", None) is not None and getattr(h.data, "ndim", 0) == 2:
                        target_hdu = h
                        break

            if target_hdu is None:
                console.print(
                    f"[bold red]Error: No 2D image HDU found in {fits_path.name}.[/bold red]"
                )
                raise typer.Exit(code=1)

            # Same astropy HDUList/HDU stub imprecision as above.
            data = target_hdu.data  # pyright: ignore[reportAttributeAccessIssue]
            header = target_hdu.header  # pyright: ignore[reportAttributeAccessIssue]
            wcs = WCS(header) if "CRVAL1" in header else None

            fig = plt.figure(figsize=(10, 8), dpi=150)
            if wcs and wcs.is_celestial:
                ax = fig.add_subplot(111, projection=wcs)
                ax.set_xlabel("Right Ascension (J2000)", fontsize=11)
                ax.set_ylabel("Declination (J2000)", fontsize=11)
                ax.grid(color="white", ls="--", alpha=0.3)
            else:
                ax = fig.add_subplot(111)
                ax.set_xlabel("X (pixels)")
                ax.set_ylabel("Y (pixels)")

            norm = ImageNormalize(data, interval=ZScaleInterval())
            # ImageNormalize genuinely subclasses matplotlib's Normalize at runtime; astropy's
            # stub just doesn't declare that relationship for the type checker.
            im = ax.imshow(data, cmap="inferno", origin="lower", norm=norm)  # pyright: ignore[reportArgumentType]
            cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            cbar.set_label("Flux / Significance (Counts)", fontsize=10)

            plot_title = title or f"{fits_path.name} [{target_hdu.name}]"  # pyright: ignore[reportAttributeAccessIssue]
            ax.set_title(plot_title, fontsize=13, fontweight="bold", pad=12)

            out_file = output or (fits_path.parent / f"{fits_path.stem}.png")
            plt.tight_layout()
            plt.savefig(out_file, bbox_inches="tight")
            plt.close(fig)

            target_name = getattr(target_hdu, "name", "IMAGE")
            console.print(
                f"[bold green]✓ Rendered {fits_path.name} [{target_name}] -> {out_file}[/bold green]"
            )

    except Exception as e:
        console.print(f"[bold red]Failed to visualise FITS image: {e}[/bold red]")
        raise typer.Exit(code=1)


@view_app.command("sources")
def view_sources(
    fits_path: Path = typer.Argument(
        ..., help="Path to source results FITS file (e.g. isgri_mosa_res.fits, isgri_srcl_res.fits)"
    ),
    min_snr: float = typer.Option(
        3.0, "--min-snr", "-s", help="Minimum detection significance (SNR)"
    ),
):
    """Parse and print detected point sources with coordinates, flux, and detection SNR."""
    if not fits_path.exists():
        console.print(f"[bold red]Error: File {fits_path} does not exist.[/bold red]")
        raise typer.Exit(code=1)

    try:
        from astropy.io import fits

        with fits.open(fits_path) as hdul:
            src_table = None
            for h in hdul:
                # Same astropy HDUList/HDU stub imprecision as in view_image() above.
                if (
                    h.data is not None  # pyright: ignore[reportAttributeAccessIssue]
                    and getattr(h.data, "names", None)  # pyright: ignore[reportAttributeAccessIssue]
                    and any(n in h.data.names for n in ["NAME", "SOURCE_ID", "DETSIG", "SNR"])  # pyright: ignore[reportAttributeAccessIssue]
                ):
                    src_table = h
                    break

            if src_table is None:
                console.print(
                    f"[bold red]Error: No source catalog/results table found in {fits_path.name}.[/bold red]"
                )
                raise typer.Exit(code=1)

            data = src_table.data  # pyright: ignore[reportAttributeAccessIssue]
            cols = data.names

            name_col = (
                "NAME" if "NAME" in cols else ("SOURCE_ID" if "SOURCE_ID" in cols else cols[0])
            )
            ra_col = "RA_OBJ" if "RA_OBJ" in cols else ("RA_FIN" if "RA_FIN" in cols else "RA")
            dec_col = (
                "DEC_OBJ" if "DEC_OBJ" in cols else ("DEC_FIN" if "DEC_FIN" in cols else "DEC")
            )
            snr_col = "DETSIG" if "DETSIG" in cols else ("SNR" if "SNR" in cols else "SIGNIFICANCE")
            flux_col = "FLUX" if "FLUX" in cols else "COUNTS"
            flux_err_col = "FLUX_ERR" if "FLUX_ERR" in cols else None

            table = Table(title=f"Detected Sources ({fits_path.name})", title_style="bold magenta")
            table.add_column("Source Name", style="cyan", no_wrap=True)
            table.add_column("RA (deg)", justify="right")
            table.add_column("Dec (deg)", justify="right")
            table.add_column("Significance (σ)", justify="right", style="bold green")
            table.add_column("Flux (cts/s)", justify="right")

            count = 0
            for row in data:
                snr = float(row[snr_col]) if snr_col in cols else 0.0
                if snr >= min_snr:
                    count += 1
                    name = str(row[name_col]).strip()
                    ra = f"{float(row[ra_col]):.4f}" if ra_col in cols else "N/A"
                    dec = f"{float(row[dec_col]):.4f}" if dec_col in cols else "N/A"
                    flux_str = f"{float(row[flux_col]):.2f}" if flux_col in cols else "N/A"
                    if flux_err_col and flux_err_col in cols:
                        flux_str += f" ± {float(row[flux_err_col]):.2f}"
                    table.add_row(name, ra, dec, f"{snr:.1f}", flux_str)

            console.print(table)
            table_name = getattr(src_table, "name", "SOURCES")
            console.print(
                f"[dim]Total: {count} sources detected with SNR ≥ {min_snr}σ (Table: {table_name})[/dim]"
            )

    except Exception as e:
        console.print(f"[bold red]Failed to view sources: {e}[/bold red]")
        raise typer.Exit(code=1)
