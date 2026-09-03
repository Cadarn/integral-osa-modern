#!/usr/bin/env python3
"""
fetch_integral_data.py
Download Science Windows, Aux files, Catalogs, and IC from ESA ISLA and HEASARC archives.
"""

import os
from pathlib import Path

import requests
import typer
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

app = typer.Typer(help="INTEGRAL Data Retrieval Utility (ESA ISLA & HEASARC)")
console = Console()

ESA_ISLA_TAP_URL = "https://isla.esac.esa.int/tap-server/tap"
HEASARC_FTP_BASE = "https://heasarc.gsfc.nasa.gov/FTP/integral/data"
ESA_CATALOG_BASE = "https://www.cosmos.esa.int/documents/332075/24154101"


def download_file(url: str, dest_path: Path, description: str = "") -> bool:
    """Download a file with streaming progress bar."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".tmp")

    try:
        response = requests.get(url, stream=True, timeout=30)
        if response.status_code != 200:
            console.print(f"[yellow]Skipped ({response.status_code}): {url}[/yellow]")
            return False

        total_size = int(response.headers.get("content-length", 0))
        desc = description or dest_path.name

        with (
            open(temp_path, "wb") as f,
            tqdm(
                desc=desc,
                total=total_size,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                leave=False,
            ) as bar,
        ):
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))

        temp_path.rename(dest_path)
        console.print(f"[green]✓ Downloaded: {dest_path.name}[/green]")
        return True
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        console.print(f"[red]Failed downloading {url}: {e}[/red]")
        return False


@app.command()
def scw(
    scw_id: str = typer.Argument(..., help="Science Window ID, e.g. 197200240010"),
    dest_dir: Path = typer.Option(
        Path(os.environ.get("REP_BASE_PROD", Path.home() / "integral_data")),
        "--dest-dir",
        "-d",
        help="Base directory for INTEGRAL data repository",
    ),
    mirror: str = typer.Option("heasarc", "--mirror", "-m", help="Archive mirror: heasarc or isla"),
):
    """Download an individual Science Window (ScW) including RAW and PRF data."""
    if len(scw_id) != 12:
        console.print(
            f"[red]Error: ScW ID must be 12 digits (e.g. 197200240010), got {scw_id}[/red]"
        )
        raise typer.Exit(code=1)

    rev = scw_id[:4]
    scw_dir = dest_dir / "scw" / rev / f"{scw_id}.001"
    scw_dir.mkdir(parents=True, exist_ok=True)

    console.print(
        f"[bold blue]Fetching Science Window {scw_id} (Rev {rev}) into {scw_dir}...[/bold blue]"
    )

    # Files required for standard IBIS / JEM-X reduction
    key_files = [
        "swg.fits.gz",
        "swg_raw.fits.gz",
        "swg_prp.fits.gz",
        "isgri_events.fits.gz",
        "ibis_hk.fits.gz",
        "sc_attitude.fits.gz",
        "sc_orbit.fits.gz",
    ]

    downloaded_count = 0
    for fname in key_files:
        url = f"{HEASARC_FTP_BASE}/scw/{rev}/{scw_id}.001/{fname}"
        target = scw_dir / fname
        if target.exists():
            console.print(f"[dim]Already exists: {fname}[/dim]")
            downloaded_count += 1
            continue
        if download_file(url, target, f"{scw_id}/{fname}"):
            downloaded_count += 1

    console.print(
        f"[bold green]Science Window {scw_id} ready ({downloaded_count}/{len(key_files)} files).[/bold green]"
    )


@app.command()
def catalogs(
    dest_dir: Path = typer.Option(
        Path(os.environ.get("REP_BASE_PROD", Path.home() / "integral_data")),
        "--dest-dir",
        "-d",
        help="Base directory for INTEGRAL data repository",
    ),
    version: str = typer.Option("0043", "--version", "-v", help="Reference catalog version"),
):
    """Download general and instrument reference catalogs."""
    cat_dir = dest_dir / "cat" / "hec"
    cat_dir.mkdir(parents=True, exist_ok=True)

    cat_filename = f"gnrl_refr_cat_{version}.fits"
    target = cat_dir / cat_filename

    console.print(f"[bold blue]Downloading Reference Catalog version {version}...[/bold blue]")
    url = f"{HEASARC_FTP_BASE}/aux/cat/hec/{cat_filename}.gz"

    if target.exists():
        console.print(f"[green]Reference catalog already exists: {target}[/green]")
    else:
        # Try gzip version first
        gz_target = cat_dir / f"{cat_filename}.gz"
        if download_file(url, gz_target, cat_filename):
            import gzip
            import shutil

            with gzip.open(gz_target, "rb") as f_in, open(target, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            gz_target.unlink()
            console.print(f"[bold green]Extracted reference catalog to {target}[/bold green]")


@app.command()
def info():
    """Display configured data paths and environment status."""
    table = Table(title="INTEGRAL Environment Configuration")
    table.add_column("Variable / Path", style="cyan")
    table.add_column("Current Value", style="green")
    table.add_column("Status", style="yellow")

    data_dir = os.environ.get("REP_BASE_PROD", str(Path.home() / "integral_data"))
    ic_dir = os.environ.get("CURRENT_IC", data_dir)

    table.add_row("REP_BASE_PROD", data_dir, "Exists" if Path(data_dir).exists() else "Missing")
    table.add_row("CURRENT_IC", ic_dir, "Exists" if Path(ic_dir).exists() else "Missing")
    table.add_row(
        "ISDC_REF_CAT", os.environ.get("ISDC_REF_CAT", "Default (/data/cat/hec/...)"), "OK"
    )

    console.print(table)


def main():
    app()


if __name__ == "__main__":
    main()
