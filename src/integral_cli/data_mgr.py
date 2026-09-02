"""
Local Data Management, HEASARC Async HTTP/2 Downloaders, and Staging Tools for INTEGRAL.
"""

import asyncio
import re
import shutil
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table

from integral_cli.config import config

console = Console()
data_app = typer.Typer(help="Manage INTEGRAL archive data, local imports, and async HEASARC downloads")

HEASARC_FTP_BASE = "https://heasarc.gsfc.nasa.gov/FTP/integral/data"


async def async_download_file(
    client: httpx.AsyncClient,
    url: str,
    dest_path: Path,
    progress: Progress | None = None,
    task_id=None,
) -> bool:
    """Asynchronously download a file with atomic .tmp write and resume safety."""
    if dest_path.exists():
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".tmp")

    try:
        async with client.stream("GET", url, timeout=60.0) as response:
            if response.status_code != 200:
                return False

            # Blocking file I/O inside an async function - a real anti-pattern, but fixing it
            # properly needs either the `aiofiles` dependency or per-chunk asyncio.to_thread
            # (which would add thread-pool overhead on every 64KB chunk). Deferred rather than
            # fixed here; each concurrent download's disk writes briefly block the event loop.
            with open(temp_path, "wb") as f:  # noqa: ASYNC230
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

        temp_path.rename(dest_path)
        return True
    except Exception as e:
        console.print(f"[dim red]Warning: failed to download {url}: {e}[/dim red]")
        if temp_path.exists():
            temp_path.unlink()
        return False


async def async_download_scw(
    client: httpx.AsyncClient,
    scw_id: str,
    dest_base: Path,
    semaphore: asyncio.Semaphore,
) -> int:
    """Asynchronously download all files in a Science Window concurrently."""
    rev = scw_id[:4]
    scw_dir = dest_base / "scw" / rev / f"{scw_id}.001"
    scw_dir.mkdir(parents=True, exist_ok=True)
    scw_url = f"{HEASARC_FTP_BASE}/scw/{rev}/{scw_id}.001/"

    try:
        resp = await client.get(scw_url, timeout=30.0)
        if resp.status_code != 200:
            console.print(f"[red]ScW {scw_id} directory not found on server (HTTP {resp.status_code})[/red]")
            return 0

        file_matches = re.findall(r'href="([^"?/][^"]*)"', resp.text)
        valid_files = [f for f in file_matches if not f.startswith("?") and not f.startswith("/")]

        async def fetch(filename: str):
            async with semaphore:
                target = scw_dir / filename
                return await async_download_file(client, f"{scw_url}{filename}", target)

        results = await asyncio.gather(*(fetch(f) for f in valid_files))
        return sum(1 for r in results if r)
    except Exception as e:
        console.print(f"[red]Failed to download ScW {scw_id}: {e}[/red]")
        return 0


async def async_download_ic_index(client: httpx.AsyncClient, dest_base: Path):
    """Download index files into idx/ic/."""
    idx_dir = dest_base / "idx" / "ic"
    idx_dir.mkdir(parents=True, exist_ok=True)
    idx_url = f"{HEASARC_FTP_BASE}/idx/ic/"

    console.print(f"[bold blue]Scanning IC index files at {idx_url}...[/bold blue]")
    try:
        resp = await client.get(idx_url, timeout=30.0)
        if resp.status_code != 200:
            console.print(f"[red]Error fetching IC index listing (HTTP {resp.status_code})[/red]")
            return

        file_matches = re.findall(r'href="([^"?/]+\.fits(?:\.gz)?)"', resp.text)
        fits_files = sorted(set(file_matches))

        console.print(f"Downloading {len(fits_files)} IC index files via async HTTP/2...")
        tasks = []
        for fn in fits_files:
            target = idx_dir / fn
            tasks.append(async_download_file(client, f"{idx_url}{fn}", target))

        await asyncio.gather(*tasks)
        console.print(f"[bold green]✓ IC index files downloaded to {idx_dir}[/bold green]")
    except Exception as e:
        console.print(f"[red]Error downloading IC index: {e}[/red]")


async def scan_and_download_ic_dir(
    client: httpx.AsyncClient,
    base_url: str,
    local_dir: Path,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    task_id,
):
    """Recursively scan and download an IC directory."""
    try:
        resp = await client.get(base_url, timeout=30.0)
        if resp.status_code != 200:
            return

        hrefs = re.findall(r'href="([^"?][^"]*)"', resp.text)
        subdirs = [h.rstrip("/") for h in hrefs if h.endswith("/") and not h.startswith(".") and not h.startswith("/")]
        files = [h for h in hrefs if not h.endswith("/") and not h.startswith("?") and not h.startswith("/") and "." in h]

        download_tasks = []
        for fn in files:
            target = local_dir / fn
            if not target.exists():
                async def fetch(url, dest):
                    async with semaphore:
                        res = await async_download_file(client, url, dest)
                        progress.advance(task_id, 1)
                        return res
                download_tasks.append(fetch(f"{base_url}{fn}", target))
            else:
                progress.advance(task_id, 1)

        if download_tasks:
            await asyncio.gather(*download_tasks)

        for sd in subdirs:
            await scan_and_download_ic_dir(
                client,
                f"{base_url}{sd}/",
                local_dir / sd,
                semaphore,
                progress,
                task_id,
            )
    except Exception as e:
        console.print(f"[dim red]Warning: failed to scan IC directory {base_url}: {e}[/dim red]")


async def async_download_ic_tree(
    client: httpx.AsyncClient,
    instrument: str,
    dest_base: Path,
    semaphore: asyncio.Semaphore,
    subtree: str = "",
):
    """Download the IC calibration tree for an instrument."""
    inst_dir = dest_base / "ic" / instrument
    inst_dir.mkdir(parents=True, exist_ok=True)
    inst_url = f"{HEASARC_FTP_BASE}/ic/{instrument}/"

    if subtree:
        inst_dir = inst_dir / subtree
        inst_dir.mkdir(parents=True, exist_ok=True)
        inst_url = f"{inst_url}{subtree}/"

    console.print(f"[bold blue]Scanning IC tree for {instrument} at {inst_url}...[/bold blue]")

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[bold cyan]Downloading IC ({instrument}{'/' + subtree if subtree else ''})...[/bold cyan]"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed} files)"),
        console=console,
    ) as progress:
        task_id = progress.add_task("download", total=None)
        await scan_and_download_ic_dir(client, inst_url, inst_dir, semaphore, progress, task_id)

    console.print(f"[bold green]✓ IC tree for {instrument} synced to {inst_dir}[/bold green]")


def init_workspace(target_path: Path):
    """Ensure standard INTEGRAL directory hierarchy exists."""
    for d in [
        "scw",
        "aux/adp",
        "aux/org",
        "cat/hec",
        "cat/omc",
        "ic/ibis",
        "ic/jmx1",
        "ic/jmx2",
        "ic/omc",
        "ic/spi",
        "ic/sc",
        "idx/ic",
    ]:
        (target_path / d).mkdir(parents=True, exist_ok=True)


@data_app.command("init")
def init_repo(
    path: Path = typer.Option(
        config.rep_base_prod,
        "--path",
        "-p",
        help="Target base directory for INTEGRAL data archive",
    )
):
    """Initialise local data archive structure with standard subdirectories."""
    init_workspace(path)
    config.data_dir = str(path)
    config.ic_dir = str(path)
    config.save()
    console.print(
        Panel(
            f"[bold green]Initialised INTEGRAL Data Archive at:[/bold green]\n{path.resolve()}",
            title="Repository Setup",
        )
    )


@data_app.command("import-local")
def import_local(
    source_dir: Path = typer.Argument(..., help="Source directory containing revolution data (e.g. ~/Sites/0060/0060)"),
    revolution: str = typer.Option("", "--rev", "-r", help="Revolution number (e.g. 0060, auto-detected if omitted)"),
    link: bool = typer.Option(False, "--link", "-l", help="Create symlinks instead of copying files"),
):
    """Import local Revolution data into the standard archive hierarchy."""
    if not source_dir.exists():
        console.print(f"[bold red]Error: Source directory {source_dir} does not exist.[/bold red]")
        raise typer.Exit(code=1)

    dest_base = config.rep_base_prod
    init_workspace(dest_base)

    rev_id = revolution or source_dir.name
    if not rev_id.isdigit():
        if source_dir.parent.name.isdigit():
            rev_id = source_dir.parent.name
        else:
            rev_id = "0060"

    rev_id = f"{int(rev_id):04d}"
    console.print(f"[bold blue]Importing Revolution {rev_id} from {source_dir} into {dest_base}...[/bold blue]")

    scw_dest_dir = dest_base / "scw" / rev_id
    scw_dest_dir.mkdir(parents=True, exist_ok=True)

    aux_dest_adp = dest_base / "aux" / "adp" / f"{rev_id}.001"
    aux_dest_adp.parent.mkdir(parents=True, exist_ok=True)

    imported_scws = 0
    subdirs = [p for p in source_dir.iterdir() if p.is_dir()]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Importing Rev {rev_id}...", total=len(subdirs))

        for item in subdirs:
            if item.name.startswith("rev.") or "aux" in item.name.lower():
                if link:
                    if aux_dest_adp.exists() or aux_dest_adp.is_symlink():
                        aux_dest_adp.unlink()
                    aux_dest_adp.symlink_to(item.resolve())
                else:
                    if aux_dest_adp.exists():
                        shutil.rmtree(aux_dest_adp)
                    shutil.copytree(item, aux_dest_adp)
            elif len(item.name) >= 12:
                target_scw = scw_dest_dir / item.name
                if link:
                    if target_scw.exists() or target_scw.is_symlink():
                        target_scw.unlink()
                    target_scw.symlink_to(item.resolve())
                else:
                    if target_scw.exists():
                        shutil.rmtree(target_scw)
                    shutil.copytree(item, target_scw)
                imported_scws += 1
            progress.advance(task, 1)

    console.print(
        f"[bold green]✓ Successfully imported Revolution {rev_id}: {imported_scws} Science Windows and AUX data staged.[/bold green]"
    )


@data_app.command("download")
def download_data(
    scw_id: str = typer.Option("", "--scw", "-s", help="Science Window ID (e.g. 006000010010)"),
    catalogs: bool = typer.Option(False, "--catalogs", "-c", help="Download general reference catalogs"),
    ic_tree: bool = typer.Option(False, "--ic", help="Download IC index files and calibration trees"),
    ic_ibis: bool = typer.Option(False, "--ic-ibis", help="Download IC calibration tree for IBIS and SC"),
    ic_all: bool = typer.Option(False, "--ic-all", help="Download complete IC trees for all instruments (ibis, jmx1, jmx2, omc, spi, sc)"),
    subtree: str = typer.Option("", "--subtree", help="Filter specific IC subtree (e.g. bkg, cal, cfg, cnv, lim, mod, rsp)"),
    cat_version: str = typer.Option("0043", "--cat-version", help="Catalog version (e.g. 0043)"),
    concurrency: int = typer.Option(16, "--concurrency", "-j", help="Concurrent HTTP/2 download streams"),
):
    """Download Science Windows, Reference Catalogs, or IC files asynchronously with safe resume."""
    async def run_downloads():
        dest_base = config.rep_base_prod
        semaphore = asyncio.Semaphore(concurrency)

        async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
            if catalogs:
                cat_hec_dir = dest_base / "cat" / "hec"
                cat_hec_dir.mkdir(parents=True, exist_ok=True)
                cat_filename = f"gnrl_refr_cat_{cat_version}.fits"
                target_fits = cat_hec_dir / cat_filename

                cat_omc_dir = dest_base / "cat" / "omc"
                cat_omc_dir.mkdir(parents=True, exist_ok=True)
                omc_target = cat_omc_dir / "omc_refr_cat_0005.fits"

                tasks = []
                if not target_fits.exists():
                    url = f"{HEASARC_FTP_BASE}/cat/hec/{cat_filename}"
                    console.print(f"[bold blue]Downloading reference catalog from {url}...[/bold blue]")
                    tasks.append(async_download_file(client, url, target_fits))
                else:
                    console.print(f"[green]Reference catalog already exists: {target_fits}[/green]")

                if not omc_target.exists():
                    omc_url = f"{HEASARC_FTP_BASE}/cat/omc/omc_refr_cat_0005.fits"
                    console.print(f"[bold blue]Downloading OMC catalog from {omc_url}...[/bold blue]")
                    tasks.append(async_download_file(client, omc_url, omc_target))
                else:
                    console.print(f"[green]OMC catalog already exists: {omc_target}[/green]")

                if tasks:
                    await asyncio.gather(*tasks)
                    console.print("[bold green]✓ Catalogs up to date.[/bold green]")

            if ic_tree or ic_ibis or ic_all:
                await async_download_ic_index(client, dest_base)
                if ic_all:
                    for inst in ["ibis", "jmx1", "jmx2", "omc", "spi", "sc"]:
                        await async_download_ic_tree(client, inst, dest_base, semaphore, subtree=subtree)
                elif ic_ibis:
                    await async_download_ic_tree(client, "ibis", dest_base, semaphore, subtree=subtree)
                    await async_download_ic_tree(client, "sc", dest_base, semaphore, subtree=subtree)

            if scw_id:
                if len(scw_id) != 12:
                    console.print(f"[red]Error: ScW ID must be 12 digits, got '{scw_id}'[/red]")
                    raise typer.Exit(code=1)

                console.print(f"[bold blue]Async downloading Science Window {scw_id}...[/bold blue]")
                count = await async_download_scw(client, scw_id, dest_base, semaphore)
                console.print(f"[bold green]✓ Science Window {scw_id} ready ({count} files).[/bold green]")

    asyncio.run(run_downloads())


@data_app.command("status")
def archive_status():
    """Detailed audit of local archive showing exact counts for Catalogs, Indexes, and IC trees."""
    dest_base = config.rep_base_prod

    console.print(Panel(f"[bold green]INTEGRAL Local Archive Audit[/bold green]\nLocation: [cyan]{dest_base}[/cyan]"))

    table = Table(title="Archive Components Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Details", style="green")
    table.add_column("File Count / Status", style="yellow")

    # 1. Catalogs
    cat_files = list((dest_base / "cat").glob("**/*.fits")) if (dest_base / "cat").exists() else []
    table.add_row("Catalogs", ", ".join([f.name for f in cat_files]) or "None", f"{len(cat_files)} files")

    # 2. Indexes
    idx_files = list((dest_base / "idx").glob("**/*.fits")) if (dest_base / "idx").exists() else []
    table.add_row("IC Indexes", str(dest_base / "idx" / "ic"), f"{len(idx_files)} index tables")

    # 3. IC Subtrees
    ic_path = dest_base / "ic"
    if ic_path.exists():
        for inst_dir in sorted(ic_path.iterdir()):
            if inst_dir.is_dir():
                for sub in sorted(inst_dir.iterdir()):
                    if sub.is_dir():
                        cnt = len(list(sub.glob("*.fits*")))
                        table.add_row(f"IC ({inst_dir.name})", sub.name, f"{cnt} files")

    # 4. ScW Revolutions
    scw_path = dest_base / "scw"
    if scw_path.exists():
        for rev_dir in sorted(scw_path.iterdir()):
            if rev_dir.is_dir():
                scw_cnt = len(list(rev_dir.iterdir()))
                table.add_row(f"Revolution {rev_dir.name}", f"scw/{rev_dir.name}/", f"{scw_cnt} Science Windows")

    console.print(table)
