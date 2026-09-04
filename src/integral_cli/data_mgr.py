"""
Local Data Management, HEASARC Async HTTP/2 Downloaders, and Staging Tools for INTEGRAL.
"""

import asyncio
import re
import shutil
import sys
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
from rich.prompt import Confirm
from rich.table import Table

from integral_cli.config import config
from integral_cli.scw_utils import filter_pointing_scws

console = Console()
data_app = typer.Typer(
    help="Manage INTEGRAL archive data, local imports, and async HEASARC downloads"
)
download_app = typer.Typer(
    help="Download Science Windows, calibration, and support data from HEASARC"
)
data_app.add_typer(download_app, name="download")

KNOWN_MIRRORS = {
    "heasarc": "https://heasarc.gsfc.nasa.gov/FTP/integral/data",
    "isdc": "https://isdc.unige.ch/ftp/arc/rev_3",
}
DEFAULT_MIRROR_NAME = "heasarc"
HEASARC_FTP_BASE = KNOWN_MIRRORS["heasarc"]


def resolve_mirror_base(mirror: str | None = None) -> tuple[str, str]:
    """Resolve mirror identifier or custom URL into (mirror_name, base_url)."""
    raw = (mirror or config.archive_mirror or DEFAULT_MIRROR_NAME).strip()
    key = raw.lower()
    if key in KNOWN_MIRRORS:
        return key, KNOWN_MIRRORS[key]
    # If a full URL was provided
    if raw.startswith(("http://", "https://")):
        return "custom", raw.rstrip("/")
    # Default fallback
    return DEFAULT_MIRROR_NAME, KNOWN_MIRRORS[DEFAULT_MIRROR_NAME]


async def probe_mirror_health(base_url: str, timeout_sec: float = 5.0) -> tuple[bool, float, str]:
    """Ping a mirror to assess latency and availability. Returns (is_available, latency_seconds, error_msg)."""
    import time

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            resp = await client.head(f"{base_url}/")
            latency = time.perf_counter() - start
            if resp.status_code in [200, 301, 302]:
                return True, latency, ""
            return False, latency, f"HTTP {resp.status_code}"
    except Exception as e:
        latency = time.perf_counter() - start
        return False, latency, str(e)


ALL_INSTRUMENTS = ["ibis", "jmx1", "jmx2", "omc", "spi", "sc", "irem"]
INSTRUMENT_ALIASES = {"jemx": "jmx1", "jemx1": "jmx1", "jemx2": "jmx2"}


def _resolve_instruments(instruments_opt: str) -> list[str]:
    """Parse the --instruments option into IC directory names (default: the configured
    default_instrument + the general 'sc'/'irem' categories; 'all' widens to every instrument)."""
    if not instruments_opt:
        default = config.default_instrument.lower()
        return sorted({INSTRUMENT_ALIASES.get(default, default), "sc", "irem"})
    if instruments_opt.strip().lower() == "all":
        return ALL_INSTRUMENTS
    names = [n.strip().lower() for n in instruments_opt.split(",") if n.strip()]
    return [INSTRUMENT_ALIASES.get(n, n) for n in names]


def _validate_scope_flags(science_only: bool, calib_only: bool) -> None:
    if science_only and calib_only:
        console.print(
            "[bold red]Error: --science-only and --calib-only are mutually exclusive.[/bold red]"
        )
        raise typer.Exit(code=1)


async def async_download_file(
    client: httpx.AsyncClient,
    url: str,
    dest_path: Path,
    progress: Progress | None = None,
    task_id=None,
    force: bool = False,
) -> bool:
    """Asynchronously download a file with atomic .tmp write and resume safety."""
    if dest_path.exists() and not force:
        if progress is not None and task_id is not None:
            progress.advance(task_id, 1)
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".tmp")

    try:
        async with client.stream("GET", url, timeout=60.0) as response:
            if response.status_code != 200:
                if progress is not None and task_id is not None:
                    progress.advance(task_id, 1)
                return False

            # Blocking file I/O inside an async function - a real anti-pattern, but fixing it
            # properly needs either the `aiofiles` dependency or per-chunk asyncio.to_thread
            # (which would add thread-pool overhead on every 64KB chunk). Deferred rather than
            # fixed here; each concurrent download's disk writes briefly block the event loop.
            with open(temp_path, "wb") as f:  # noqa: ASYNC230
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

        temp_path.rename(dest_path)
        if progress is not None and task_id is not None:
            progress.advance(task_id, 1)
        return True
    except Exception as e:
        console.print(f"[dim red]Warning: failed to download {url}: {e}[/dim red]")
        if temp_path.exists():
            temp_path.unlink()
        if progress is not None and task_id is not None:
            progress.advance(task_id, 1)
        return False


async def async_list_scw_files(
    client: httpx.AsyncClient, scw_id: str, base_url: str = HEASARC_FTP_BASE
) -> list[str]:
    """List the files present in a Science Window's remote directory."""
    rev = scw_id[:4]
    scw_url = f"{base_url}/scw/{rev}/{scw_id}.001/"
    resp = await client.get(scw_url, timeout=30.0)
    if resp.status_code != 200:
        console.print(
            f"[red]ScW {scw_id} directory not found on server (HTTP {resp.status_code})[/red]"
        )
        return []

    file_matches = re.findall(r'href="([^"?/][^"]*)"', resp.text)
    return [f for f in file_matches if not f.startswith("?") and not f.startswith("/")]


async def async_download_scw_files(
    client: httpx.AsyncClient,
    scw_id: str,
    filenames: list[str],
    dest_base: Path,
    semaphore: asyncio.Semaphore,
    progress: Progress | None = None,
    task_id=None,
    force: bool = False,
    base_url: str = HEASARC_FTP_BASE,
) -> int:
    """Download a Science Window's files (already listed via async_list_scw_files) concurrently."""
    rev = scw_id[:4]
    scw_dir = dest_base / "scw" / rev / f"{scw_id}.001"
    scw_dir.mkdir(parents=True, exist_ok=True)
    scw_url = f"{base_url}/scw/{rev}/{scw_id}.001/"

    async def fetch(filename: str) -> bool:
        async with semaphore:
            target = scw_dir / filename
            return await async_download_file(
                client,
                f"{scw_url}{filename}",
                target,
                progress=progress,
                task_id=task_id,
                force=force,
            )

    results = await asyncio.gather(*(fetch(f) for f in filenames))
    return sum(1 for r in results if r)


async def async_list_remote_scws(
    client: httpx.AsyncClient, rev_id: str, base_url: str = HEASARC_FTP_BASE
) -> list[str]:
    """List available ScW IDs for a revolution directly from the remote archive server."""
    rev_url = f"{base_url}/scw/{rev_id}/"
    resp = await client.get(rev_url, timeout=30.0)
    if resp.status_code != 200:
        console.print(
            f"[red]Revolution {rev_id} not found on server (HTTP {resp.status_code})[/red]"
        )
        return []

    matches = re.findall(r'href="(\d{12})\.001/"', resp.text)
    return sorted(set(matches))


async def async_download_aux(
    client: httpx.AsyncClient,
    rev_id: str,
    dest_base: Path,
    semaphore: asyncio.Semaphore,
    force: bool = False,
    base_url: str = HEASARC_FTP_BASE,
) -> int:
    """Asynchronously download a revolution's aux/adp attitude, orbit, and planning files."""
    aux_dir = dest_base / "aux" / "adp" / f"{rev_id}.001"
    aux_dir.mkdir(parents=True, exist_ok=True)
    aux_url = f"{base_url}/aux/adp/{rev_id}.001/"

    try:
        resp = await client.get(aux_url, timeout=30.0)
        if resp.status_code != 200:
            console.print(
                f"[red]Aux data for revolution {rev_id} not found on server (HTTP {resp.status_code})[/red]"
            )
            return 0

        file_matches = re.findall(r'href="([^"?/][^"]*)"', resp.text)
        valid_files = [f for f in file_matches if not f.startswith("?") and not f.endswith("/")]

        async def fetch(filename: str) -> bool:
            async with semaphore:
                target = aux_dir / filename
                return await async_download_file(
                    client, f"{aux_url}{filename}", target, force=force
                )

        results = await asyncio.gather(*(fetch(f) for f in valid_files))
        return sum(1 for r in results if r)
    except Exception as e:
        console.print(f"[red]Failed to download aux data for revolution {rev_id}: {e}[/red]")
        return 0


async def async_download_rev_context(
    client: httpx.AsyncClient,
    rev_id: str,
    dest_base: Path,
    semaphore: asyncio.Semaphore,
    force: bool = False,
    base_url: str = HEASARC_FTP_BASE,
) -> int:
    """Download the revolution context tree under scw/<REV>/rev.001/ required by OSA pipelines."""
    rev_url = f"{base_url}/scw/{rev_id}/rev.001/"
    local_rev_dir = dest_base / "scw" / rev_id / "rev.001"
    local_rev_dir.mkdir(parents=True, exist_ok=True)

    try:
        resp = await client.get(rev_url, timeout=30.0)
        if resp.status_code != 200:
            console.print(
                f"[dim yellow]Notice: Revolution context rev.001 not found for rev {rev_id} (HTTP {resp.status_code})[/dim yellow]"
            )
            return 0

        # Scan and download rev.001 subdirectories (idx, aca, cfg, osm, prp, raw)
        async def fetch_dir(rel_path: str, target_dir: Path) -> int:
            target_dir.mkdir(parents=True, exist_ok=True)
            u = f"{rev_url}{rel_path}"
            r = await client.get(u, timeout=30.0)
            if r.status_code != 200:
                return 0
            hrefs = re.findall(r'href="([^"?/][^"]*)"', r.text)
            subdirs = [
                h.rstrip("/")
                for h in hrefs
                if h.endswith("/") and not h.startswith(".") and not h.startswith("/")
            ]
            files = [
                h
                for h in hrefs
                if not h.endswith("/")
                and not h.startswith("?")
                and not h.startswith("/")
                and "." in h
            ]

            tasks = []
            for fn in files:
                target = target_dir / fn
                tasks.append(async_download_file(client, f"{u}{fn}", target, force=force))

            res = await asyncio.gather(*tasks) if tasks else []
            cnt = sum(1 for x in res if x)
            for sd in subdirs:
                cnt += await fetch_dir(f"{rel_path}{sd}/", target_dir / sd)
            return cnt

        console.print(
            f"[bold blue]Syncing revolution context (scw/{rev_id}/rev.001/)...[/bold blue]"
        )
        count = await fetch_dir("", local_rev_dir)
        console.print(
            f"[bold green]✓ Revolution {rev_id} context synced ({count} files).[/bold green]"
        )
        return count
    except Exception as e:
        console.print(
            f"[dim red]Warning: failed downloading revolution context for {rev_id}: {e}[/dim red]"
        )
        return 0


async def async_download_aux_ref(
    client: httpx.AsyncClient,
    dest_base: Path,
    force: bool = False,
    base_url: str = HEASARC_FTP_BASE,
) -> int:
    """Download essential aux/adp/ref reference data (tcoroffset, leap, de200, irot) for time conversion."""
    tcor_url = f"{base_url}/aux/adp/ref/tcoroffset/"
    local_tcor_dir = dest_base / "aux" / "adp" / "ref" / "tcoroffset"
    local_tcor_dir.mkdir(parents=True, exist_ok=True)

    try:
        resp = await client.get(tcor_url, timeout=30.0)
        if resp.status_code != 200:
            return 0
        file_matches = re.findall(r'href="([^"?/]+\.fits(?:\.gz)?)"', resp.text)
        tasks = [
            async_download_file(client, f"{tcor_url}{fn}", local_tcor_dir / fn, force=force)
            for fn in file_matches
        ]
        results = await asyncio.gather(*tasks)
        return sum(1 for r in results if r)
    except Exception as e:
        console.print(f"[dim red]Warning: failed downloading aux/adp/ref data: {e}[/dim red]")
        return 0


def clean_ic_master_file(dest_base: Path):
    """Prune missing instrument indexes from ic_master_file.fits to prevent DAL status -2004 orphan errors."""
    idx_dir = dest_base / "idx" / "ic"
    master_path = idx_dir / "ic_master_file.fits"
    if not master_path.exists():
        return

    try:
        from astropy.io import fits

        with fits.open(master_path, mode="update") as master:
            if len(master) < 3 or "GROUPING" not in master[2].header.get("EXTNAME", ""):  # pyright: ignore[reportAttributeAccessIssue]
                return
            data = master[2].data  # pyright: ignore[reportAttributeAccessIssue]
            keep_indices = []
            for idx, row in enumerate(data):
                sub_idx_name = row["MEMBER_LOCATION"]
                sub_idx_path = idx_dir / sub_idx_name
                if not sub_idx_path.exists():
                    continue
                all_exist = True
                with fits.open(sub_idx_path) as sub:
                    for sub_row in sub[1].data:  # pyright: ignore[reportAttributeAccessIssue]
                        mem_loc = sub_row["MEMBER_LOCATION"]
                        target = (idx_dir / mem_loc).resolve()
                        if not target.exists():
                            all_exist = False
                            break
                if all_exist:
                    keep_indices.append(idx)

            if len(keep_indices) != len(data):
                new_table = fits.BinTableHDU(
                    data=data[keep_indices],
                    header=master[2].header,  # pyright: ignore[reportAttributeAccessIssue]
                )
                master[2] = new_table
                master.flush()

    except Exception as e:
        console.print(
            f"[dim yellow]Notice: ic_master_file pruning check skipped ({e})[/dim yellow]"
        )


async def async_download_ic_index(
    client: httpx.AsyncClient,
    dest_base: Path,
    force: bool = False,
    base_url: str = HEASARC_FTP_BASE,
):
    """Download index files into idx/ic/."""
    idx_dir = dest_base / "idx" / "ic"
    idx_dir.mkdir(parents=True, exist_ok=True)
    idx_url = f"{base_url}/idx/ic/"

    console.print(f"[bold blue]Scanning IC index files at {idx_url}...[/bold blue]")
    try:
        resp = await client.get(idx_url, timeout=30.0)
        if resp.status_code != 200:
            console.print(f"[red]Error fetching IC index listing (HTTP {resp.status_code})[/red]")
            return

        file_matches = re.findall(r'href="([^"?/]+\.fits(?:\.gz)?)"', resp.text)
        fits_files = sorted(set(file_matches))

        console.print(f"Downloading {len(fits_files)} IC index files via async HTTP/2...")
        tasks = [
            async_download_file(client, f"{idx_url}{fn}", idx_dir / fn, force=force)
            for fn in fits_files
        ]

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
    force: bool = False,
):
    """Recursively scan and download an IC directory."""
    try:
        resp = await client.get(base_url, timeout=30.0)
        if resp.status_code != 200:
            return

        hrefs = re.findall(r'href="([^"?][^"]*)"', resp.text)
        subdirs = [
            h.rstrip("/")
            for h in hrefs
            if h.endswith("/") and not h.startswith(".") and not h.startswith("/")
        ]
        files = [
            h
            for h in hrefs
            if not h.endswith("/") and not h.startswith("?") and not h.startswith("/") and "." in h
        ]

        download_tasks = []
        for fn in files:
            target = local_dir / fn
            if force or not target.exists():

                async def fetch(url, dest):
                    async with semaphore:
                        res = await async_download_file(client, url, dest, force=force)
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
                force=force,
            )
    except Exception as e:
        console.print(f"[dim red]Warning: failed to scan IC directory {base_url}: {e}[/dim red]")


async def async_download_ic_tree(
    client: httpx.AsyncClient,
    instrument: str,
    dest_base: Path,
    semaphore: asyncio.Semaphore,
    subtree: str = "",
    force: bool = False,
    base_url: str = HEASARC_FTP_BASE,
):
    """Download the IC calibration tree for an instrument."""
    inst_dir = dest_base / "ic" / instrument
    inst_dir.mkdir(parents=True, exist_ok=True)
    inst_url = f"{base_url}/ic/{instrument}/"

    if subtree:
        inst_dir = inst_dir / subtree
        inst_dir.mkdir(parents=True, exist_ok=True)
        inst_url = f"{inst_url}{subtree}/"

    console.print(f"[bold blue]Scanning IC tree for {instrument} at {inst_url}...[/bold blue]")

    with Progress(
        SpinnerColumn(),
        TextColumn(
            f"[bold cyan]Downloading IC ({instrument}{'/' + subtree if subtree else ''})...[/bold cyan]"
        ),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed} files)"),
        console=console,
    ) as progress:
        task_id = progress.add_task("download", total=None)
        await scan_and_download_ic_dir(
            client, inst_url, inst_dir, semaphore, progress, task_id, force=force
        )

    console.print(f"[bold green]✓ IC tree for {instrument} synced to {inst_dir}[/bold green]")


async def _download_catalogs(
    client: httpx.AsyncClient,
    dest_base: Path,
    cat_version: str,
    force: bool = False,
    base_url: str = HEASARC_FTP_BASE,
):
    """Ensure the general reference catalog and OMC catalog are present."""
    cat_hec_dir = dest_base / "cat" / "hec"
    cat_hec_dir.mkdir(parents=True, exist_ok=True)
    cat_filename = f"gnrl_refr_cat_{cat_version}.fits"
    target_fits = cat_hec_dir / cat_filename

    cat_omc_dir = dest_base / "cat" / "omc"
    cat_omc_dir.mkdir(parents=True, exist_ok=True)
    omc_target = cat_omc_dir / "omc_refr_cat_0005.fits"

    tasks = []
    if force or not target_fits.exists():
        tasks.append(
            async_download_file(
                client, f"{base_url}/cat/hec/{cat_filename}", target_fits, force=force
            )
        )
    if force or not omc_target.exists():
        tasks.append(
            async_download_file(
                client,
                f"{base_url}/cat/omc/omc_refr_cat_0005.fits",
                omc_target,
                force=force,
            )
        )

    if tasks:
        console.print("[bold blue]Fetching reference catalogs...[/bold blue]")
        await asyncio.gather(*tasks)
        console.print("[bold green]✓ Catalogs up to date.[/bold green]")
    else:
        console.print("[green]Catalogs already present.[/green]")


async def _download_calibration(
    client: httpx.AsyncClient,
    dest_base: Path,
    semaphore: asyncio.Semaphore,
    instruments: list[str],
    cat_version: str,
    force: bool,
    ic_trees: bool = False,
    base_url: str = HEASARC_FTP_BASE,
):
    """Ensure catalogs and the IC index are present (small, always safe as a default).

    Per-instrument IC calibration trees are opt-in via ic_trees=True: they can be multiple
    gigabytes per instrument (response matrices, background models, etc.), so they are not
    fetched automatically just because a Science Window or revolution was requested.
    """
    await _download_catalogs(client, dest_base, cat_version, force=force, base_url=base_url)
    await async_download_ic_index(client, dest_base, force=force, base_url=base_url)
    if ic_trees:
        for inst in instruments:
            await async_download_ic_tree(
                client, inst, dest_base, semaphore, force=force, base_url=base_url
            )


async def _run_data_download(
    client: httpx.AsyncClient,
    dest_base: Path,
    scw_ids: list[str],
    fetch_science: bool,
    fetch_calib: bool,
    instruments: list[str],
    cat_version: str,
    concurrency: int,
    force: bool,
    dry_run: bool,
    ic_trees: bool = False,
    base_url: str = HEASARC_FTP_BASE,
):
    """Shared orchestration for the revolution/scw/file download subcommands."""
    semaphore = asyncio.Semaphore(concurrency)
    revisions = sorted({s[:4] for s in scw_ids})

    if fetch_science and scw_ids:
        console.print(
            f"[bold blue]Listing files for {len(scw_ids)} Science Window(s)...[/bold blue]"
        )
        listings = await asyncio.gather(
            *(async_list_scw_files(client, s, base_url=base_url) for s in scw_ids)
        )
        total_files = sum(len(f) for f in listings)

        if dry_run:
            console.print(
                f"[yellow]Dry run: would download {total_files} file(s) across {len(scw_ids)} ScW(s).[/yellow]"
            )
        elif total_files == 0:
            console.print("[yellow]No Science Window files found to download.[/yellow]")
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]Downloading Science Windows...[/bold cyan]"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("({task.completed}/{task.total} files)"),
                console=console,
            ) as progress:
                task_id = progress.add_task("scw", total=total_files)
                results = await asyncio.gather(
                    *(
                        async_download_scw_files(
                            client,
                            scw_id,
                            files,
                            dest_base,
                            semaphore,
                            progress=progress,
                            task_id=task_id,
                            force=force,
                            base_url=base_url,
                        )
                        for scw_id, files in zip(scw_ids, listings)
                    )
                )
            console.print(
                f"[bold green]✓ Downloaded {sum(results)} files across {len(scw_ids)} Science Window(s).[/bold green]"
            )

    if fetch_calib:
        if dry_run:
            rev_desc = ", ".join(revisions) if revisions else "none (no Science Windows specified)"
            ic_desc = (
                f"IC trees for {instruments}"
                if ic_trees
                else "IC trees (skipped - pass --ic-trees to include)"
            )
            console.print(
                f"[yellow]Dry run: would ensure catalogs, IC index, {ic_desc}, "
                f"and aux data for revolution(s): {rev_desc}.[/yellow]"
            )
        else:
            await _download_calibration(
                client,
                dest_base,
                semaphore,
                instruments,
                cat_version,
                force,
                ic_trees=ic_trees,
                base_url=base_url,
            )
            # Ensure essential mission time reference tables (tcoroffset) are present
            await async_download_aux_ref(client, dest_base, force=force, base_url=base_url)
            for rev_id in revisions:
                console.print(
                    f"[bold blue]Fetching aux data for revolution {rev_id}...[/bold blue]"
                )
                count = await async_download_aux(
                    client, rev_id, dest_base, semaphore, force=force, base_url=base_url
                )
                console.print(
                    f"[bold green]✓ Aux data for revolution {rev_id} ready ({count} files).[/bold green]"
                )
                # Fetch revolution context files (rev.001) required for ScW processing
                await async_download_rev_context(
                    client, rev_id, dest_base, semaphore, force=force, base_url=base_url
                )

            # Ensure ic_master_file.fits does not reference non-downloaded instrument categories
            clean_ic_master_file(dest_base)


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
        "ic/irem",
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
    ),
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
    source_dir: Path = typer.Argument(
        ..., help="Source directory containing revolution data (e.g. ~/Sites/0060/0060)"
    ),
    revolution: str = typer.Option(
        "", "--rev", "-r", help="Revolution number (e.g. 0060, auto-detected if omitted)"
    ),
    link: bool = typer.Option(
        False, "--link", "-l", help="Create symlinks instead of copying files"
    ),
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
    console.print(
        f"[bold blue]Importing Revolution {rev_id} from {source_dir} into {dest_base}...[/bold blue]"
    )

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


_SCOPE_HELP = {
    "science_only": "Skip catalogs/IC/aux, fetch only Science Window data",
    "calib_only": "Skip Science Window data, only ensure catalogs/IC/aux are present",
    "instruments": "Comma-separated IC instruments (ibis, jmx1, jmx2, omc, spi, sc, irem, or 'all'); "
    "default: the configured default_instrument + sc + irem",
    "cat_version": "Catalog version (e.g. 0043)",
    "concurrency": "Concurrent HTTP/2 download streams",
    "force_refresh": "Re-download even if files already exist locally",
    "dry_run": "Show what would be downloaded without downloading anything",
    "ic_trees": "Also fetch full per-instrument IC calibration trees (response matrices, "
    "background models, etc.) - can be multiple GB per instrument, so opt-in rather than "
    "part of the default catalogs+IC-index+aux fetch",
    "mirror": "Archive mirror to download from ('heasarc', 'isdc', or custom base URL). "
    "Default: configured archive_mirror or 'heasarc'",
}


async def _check_and_select_mirror(mirror_opt: str | None = None) -> tuple[str, str]:
    """Resolve requested mirror and test connectivity. Prompt or warn if unreachable/slow."""
    mirror_name, base_url = resolve_mirror_base(mirror_opt)
    console.print(
        f"[dim]Checking mirror connectivity: [cyan]{mirror_name}[/cyan] ({base_url})...[/dim]"
    )
    ok, latency, err = await probe_mirror_health(base_url, timeout_sec=6.0)

    if not ok:
        console.print(
            f"[bold yellow]⚠️  Warning: Mirror '{mirror_name}' is currently unreachable or slow ({err}).[/bold yellow]"
        )
        # Suggest alternative mirror if using standard mirror
        alt_name = "isdc" if mirror_name == "heasarc" else "heasarc"
        alt_url = KNOWN_MIRRORS.get(alt_name)
        if alt_url:
            console.print(
                f"[yellow]Testing alternative mirror '{alt_name}' ({alt_url})...[/yellow]"
            )
            alt_ok, alt_latency, _alt_err = await probe_mirror_health(alt_url, timeout_sec=5.0)
            if alt_ok:
                console.print(
                    f"[bold green]✓ Alternative mirror '{alt_name}' is online ({alt_latency:.2f}s latency)![/bold green]"
                )
                if Confirm.ask(f"Would you like to switch to '{alt_name}' mirror?", default=True):
                    return alt_name, alt_url
        console.print(
            f"[yellow]Proceeding with '{mirror_name}' as requested (may experience timeouts).[/yellow]"
        )
    elif latency > 3.0:
        console.print(
            f"[yellow]⚠️  Notice: Mirror '{mirror_name}' responded slowly ({latency:.2f}s).[/yellow]"
        )
    else:
        console.print(
            f"[dim green]✓ Mirror '{mirror_name}' active ({latency:.2f}s response).[/dim green]"
        )

    return mirror_name, base_url


@download_app.command("revolution")
def download_revolution(
    rev: str = typer.Argument(..., help="Revolution number, e.g. 0060"),
    count: int | None = typer.Option(
        None,
        "--count",
        "-n",
        help="Fetch only the first N pointing ScWs (default: every pointing ScW)",
    ),
    id_from: str | None = typer.Option(
        None,
        "--from",
        help="Explicit range start (12-digit ScW ID, inclusive) - overrides --count and includes non-pointing ScWs",
    ),
    id_to: str | None = typer.Option(
        None, "--to", help="Explicit range end (12-digit ScW ID, inclusive)"
    ),
    science_only: bool = typer.Option(False, "--science-only", help=_SCOPE_HELP["science_only"]),
    calib_only: bool = typer.Option(False, "--calib-only", help=_SCOPE_HELP["calib_only"]),
    instruments: str = typer.Option("", "--instruments", help=_SCOPE_HELP["instruments"]),
    ic_trees: bool = typer.Option(False, "--ic-trees", help=_SCOPE_HELP["ic_trees"]),
    cat_version: str = typer.Option("0043", "--cat-version", help=_SCOPE_HELP["cat_version"]),
    concurrency: int = typer.Option(16, "--concurrency", "-j", help=_SCOPE_HELP["concurrency"]),
    force_refresh: bool = typer.Option(False, "--force-refresh", help=_SCOPE_HELP["force_refresh"]),
    mirror: str | None = typer.Option(None, "--mirror", "-m", help=_SCOPE_HELP["mirror"]),
    dry_run: bool = typer.Option(False, "--dry-run", help=_SCOPE_HELP["dry_run"]),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Non-interactive mode: accept all defaults and skip confirmation"
    ),
):
    """Download a whole revolution (or a slice of it) plus its calibration/support data."""
    _validate_scope_flags(science_only, calib_only)
    rev_id = f"{int(rev):04d}"
    resolved_insts = _resolve_instruments(instruments)

    async def _main():
        chosen_mirror_name, base_url = await _check_and_select_mirror(mirror)
        if sys.stdin.isatty() and not yes and not dry_run:
            scope_str = (
                "Science only"
                if science_only
                else ("Calibration only" if calib_only else "Science & Calibration")
            )
            console.print(
                Panel(
                    f"[bold green]Download Plan: Revolution {rev_id}[/bold green]\n\n"
                    f"• Target Scope:   [cyan]{scope_str}[/cyan]\n"
                    f"• Mirror:         [cyan]{chosen_mirror_name}[/cyan] ({base_url})\n"
                    f"• Instruments:    [cyan]{', '.join(resolved_insts)}[/cyan]\n"
                    f"• Full IC Trees:  [cyan]{'Enabled (multi-GB)' if ic_trees else 'Minimal (indexes, rev context, reference data)'}[/cyan]\n"
                    f"• Pointing Limit: [cyan]{count if count else 'All pointing Science Windows'}[/cyan]\n"
                    f"• Destination:    [cyan]{config.rep_base_prod}[/cyan]",
                    title="Download Confirmation",
                )
            )
            if not Confirm.ask("Proceed with download?", default=True):
                console.print("[yellow]Download cancelled by user.[/yellow]")
                raise typer.Exit(code=0)

        async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
            all_ids = await async_list_remote_scws(client, rev_id, base_url=base_url)
            if not all_ids:
                console.print(
                    f"[bold red]Error: no Science Windows found for revolution {rev_id} on server {base_url}.[/bold red]"
                )
                raise typer.Exit(code=1)

            if id_from or id_to:
                lo, hi = id_from or min(all_ids), id_to or max(all_ids)
                scw_ids = sorted(s for s in all_ids if lo <= s <= hi)
            else:
                scw_ids = filter_pointing_scws(all_ids, rev_id)
                if count:
                    scw_ids = scw_ids[:count]

            await _run_data_download(
                client,
                config.rep_base_prod,
                scw_ids,
                fetch_science=not calib_only,
                fetch_calib=not science_only,
                instruments=resolved_insts,
                cat_version=cat_version,
                concurrency=concurrency,
                force=force_refresh,
                dry_run=dry_run,
                ic_trees=ic_trees,
                base_url=base_url,
            )

    asyncio.run(_main())


@download_app.command("scw")
def download_scw_cmd(
    scw_spec: str = typer.Argument(
        ..., help="A ScW ID (e.g. 006000010010) or comma-separated list of ScW IDs"
    ),
    science_only: bool = typer.Option(False, "--science-only", help=_SCOPE_HELP["science_only"]),
    calib_only: bool = typer.Option(False, "--calib-only", help=_SCOPE_HELP["calib_only"]),
    instruments: str = typer.Option("", "--instruments", help=_SCOPE_HELP["instruments"]),
    ic_trees: bool = typer.Option(False, "--ic-trees", help=_SCOPE_HELP["ic_trees"]),
    cat_version: str = typer.Option("0043", "--cat-version", help=_SCOPE_HELP["cat_version"]),
    concurrency: int = typer.Option(16, "--concurrency", "-j", help=_SCOPE_HELP["concurrency"]),
    force_refresh: bool = typer.Option(False, "--force-refresh", help=_SCOPE_HELP["force_refresh"]),
    mirror: str | None = typer.Option(None, "--mirror", "-m", help=_SCOPE_HELP["mirror"]),
    dry_run: bool = typer.Option(False, "--dry-run", help=_SCOPE_HELP["dry_run"]),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Non-interactive mode: accept all defaults and skip confirmation"
    ),
):
    """Download one or more specific Science Windows plus their calibration/support data."""
    _validate_scope_flags(science_only, calib_only)
    scw_ids = [s.strip() for s in scw_spec.split(",") if s.strip()]
    for scw_id in scw_ids:
        if len(scw_id) != 12 or not scw_id.isdigit():
            console.print(f"[bold red]Error: '{scw_id}' is not a valid 12-digit ScW ID.[/bold red]")
            raise typer.Exit(code=1)

    resolved_insts = _resolve_instruments(instruments)

    async def _main():
        chosen_mirror_name, base_url = await _check_and_select_mirror(mirror)
        if sys.stdin.isatty() and not yes and not dry_run:
            scope_str = (
                "Science only"
                if science_only
                else ("Calibration only" if calib_only else "Science & Calibration")
            )
            console.print(
                Panel(
                    f"[bold green]Download Plan: {len(scw_ids)} Science Window(s)[/bold green]\n\n"
                    f"• Target ScWs:    [cyan]{', '.join(scw_ids[:5])}{'...' if len(scw_ids) > 5 else ''}[/cyan]\n"
                    f"• Target Scope:   [cyan]{scope_str}[/cyan]\n"
                    f"• Mirror:         [cyan]{chosen_mirror_name}[/cyan] ({base_url})\n"
                    f"• Instruments:    [cyan]{', '.join(resolved_insts)}[/cyan]\n"
                    f"• Full IC Trees:  [cyan]{'Enabled (multi-GB)' if ic_trees else 'Minimal (indexes, rev context, reference data)'}[/cyan]\n"
                    f"• Destination:    [cyan]{config.rep_base_prod}[/cyan]",
                    title="Download Confirmation",
                )
            )
            if not Confirm.ask("Proceed with download?", default=True):
                console.print("[yellow]Download cancelled by user.[/yellow]")
                raise typer.Exit(code=0)

        async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
            await _run_data_download(
                client,
                config.rep_base_prod,
                scw_ids,
                fetch_science=not calib_only,
                fetch_calib=not science_only,
                instruments=_resolve_instruments(instruments),
                cat_version=cat_version,
                concurrency=concurrency,
                force=force_refresh,
                dry_run=dry_run,
                ic_trees=ic_trees,
                base_url=base_url,
            )

    asyncio.run(_main())


@download_app.command("file")
def download_file_cmd(
    path: Path = typer.Argument(
        ..., help="Text file with one ScW ID per line (# comments allowed)"
    ),
    science_only: bool = typer.Option(False, "--science-only", help=_SCOPE_HELP["science_only"]),
    calib_only: bool = typer.Option(False, "--calib-only", help=_SCOPE_HELP["calib_only"]),
    instruments: str = typer.Option("", "--instruments", help=_SCOPE_HELP["instruments"]),
    ic_trees: bool = typer.Option(False, "--ic-trees", help=_SCOPE_HELP["ic_trees"]),
    cat_version: str = typer.Option("0043", "--cat-version", help=_SCOPE_HELP["cat_version"]),
    concurrency: int = typer.Option(16, "--concurrency", "-j", help=_SCOPE_HELP["concurrency"]),
    force_refresh: bool = typer.Option(False, "--force-refresh", help=_SCOPE_HELP["force_refresh"]),
    mirror: str | None = typer.Option(None, "--mirror", "-m", help=_SCOPE_HELP["mirror"]),
    dry_run: bool = typer.Option(False, "--dry-run", help=_SCOPE_HELP["dry_run"]),
):
    """Download every Science Window listed in a text file, plus their calibration/support data."""
    _validate_scope_flags(science_only, calib_only)
    if not path.exists():
        console.print(f"[bold red]Error: {path} does not exist.[/bold red]")
        raise typer.Exit(code=1)

    scw_ids = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not scw_ids:
        console.print(f"[bold red]Error: no ScW IDs found in {path}.[/bold red]")
        raise typer.Exit(code=1)

    async def _main():
        _chosen_mirror_name, base_url = await _check_and_select_mirror(mirror)
        async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
            await _run_data_download(
                client,
                config.rep_base_prod,
                scw_ids,
                fetch_science=not calib_only,
                fetch_calib=not science_only,
                instruments=_resolve_instruments(instruments),
                cat_version=cat_version,
                concurrency=concurrency,
                force=force_refresh,
                dry_run=dry_run,
                ic_trees=ic_trees,
                base_url=base_url,
            )

    asyncio.run(_main())


@download_app.command("calibration")
def download_calibration_cmd(
    instruments: str = typer.Option("", "--instruments", help=_SCOPE_HELP["instruments"]),
    ic_trees: bool = typer.Option(False, "--ic-trees", help=_SCOPE_HELP["ic_trees"]),
    cat_version: str = typer.Option("0043", "--cat-version", help=_SCOPE_HELP["cat_version"]),
    concurrency: int = typer.Option(16, "--concurrency", "-j", help=_SCOPE_HELP["concurrency"]),
    force_refresh: bool = typer.Option(False, "--force-refresh", help=_SCOPE_HELP["force_refresh"]),
    mirror: str | None = typer.Option(None, "--mirror", "-m", help=_SCOPE_HELP["mirror"]),
    dry_run: bool = typer.Option(False, "--dry-run", help=_SCOPE_HELP["dry_run"]),
):
    """Download catalogs and the IC index without any Science Window data (no revolution context).

    Pass --ic-trees to also fetch full per-instrument calibration trees (large)."""
    resolved_instruments = _resolve_instruments(instruments)

    async def _main():
        chosen_mirror_name, base_url = await _check_and_select_mirror(mirror)
        async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
            if dry_run:
                ic_desc = (
                    f"IC trees for {resolved_instruments}"
                    if ic_trees
                    else "IC trees (skipped - pass --ic-trees to include)"
                )
                console.print(
                    f"[yellow]Dry run: would ensure catalogs, IC index, and {ic_desc} from {chosen_mirror_name} ({base_url}).[/yellow]"
                )
                return
            semaphore = asyncio.Semaphore(concurrency)
            await _download_calibration(
                client,
                config.rep_base_prod,
                semaphore,
                resolved_instruments,
                cat_version,
                force_refresh,
                ic_trees=ic_trees,
                base_url=base_url,
            )

    asyncio.run(_main())


@data_app.command("status")
def archive_status():
    """Detailed audit of local archive showing exact counts for Catalogs, Indexes, and IC trees."""
    dest_base = config.rep_base_prod

    console.print(
        Panel(
            f"[bold green]INTEGRAL Local Archive Audit[/bold green]\nLocation: [cyan]{dest_base}[/cyan]"
        )
    )

    table = Table(title="Archive Components Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Details", style="green")
    table.add_column("File Count / Status", style="yellow")

    # 1. Catalogs
    cat_files = list((dest_base / "cat").glob("**/*.fits")) if (dest_base / "cat").exists() else []
    table.add_row(
        "Catalogs", ", ".join([f.name for f in cat_files]) or "None", f"{len(cat_files)} files"
    )

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
                table.add_row(
                    f"Revolution {rev_dir.name}",
                    f"scw/{rev_dir.name}/",
                    f"{scw_cnt} Science Windows",
                )

    # 5. Aux data
    aux_path = dest_base / "aux" / "adp"
    if aux_path.exists():
        for rev_dir in sorted(aux_path.iterdir()):
            if rev_dir.is_dir():
                aux_cnt = len(list(rev_dir.glob("*")))
                table.add_row(
                    f"Aux (Revolution {rev_dir.name.split('.')[0]})",
                    f"aux/adp/{rev_dir.name}/",
                    f"{aux_cnt} files",
                )

    # 6. Configured Mirror
    table.add_row(
        "Archive Mirror",
        config.archive_mirror,
        KNOWN_MIRRORS.get(config.archive_mirror.lower(), config.archive_mirror),
    )

    console.print(table)


@data_app.command("mirror")
def mirror_cmd(
    name: str | None = typer.Argument(
        None, help="Mirror name ('heasarc', 'isdc') or custom URL to set as default"
    ),
    test: bool = typer.Option(
        False, "--test", "-t", help="Test latency and availability of all known mirrors"
    ),
):
    """Show, test, or configure the default archive mirror."""
    if test or not name:
        table = Table(title="INTEGRAL Archive Mirrors")
        table.add_column("Mirror Name", style="cyan")
        table.add_column("Base URL", style="blue")
        table.add_column("Active", style="yellow")
        table.add_column("Status / Latency", style="green")

        current = config.archive_mirror.lower()

        async def _test_all():
            for m_name, url in KNOWN_MIRRORS.items():
                is_active = "✓ Default" if m_name == current else ""
                if test:
                    with console.status(f"Probing {m_name}..."):
                        ok, lat, err = await probe_mirror_health(url, timeout_sec=5.0)
                    if ok:
                        status_str = f"[bold green]Online ({lat:.2f}s)[/bold green]"
                    else:
                        status_str = f"[bold red]Offline ({err})[/bold red]"
                else:
                    status_str = "[dim]Not tested (run with --test)[/dim]"
                table.add_row(m_name, url, is_active, status_str)

        asyncio.run(_test_all())
        console.print(table)

    if name:
        resolved_name, base_url = resolve_mirror_base(name)
        config.archive_mirror = resolved_name if resolved_name in KNOWN_MIRRORS else base_url
        config.save()
        console.print(
            f"[bold green]✓ Default archive mirror set to: [cyan]{config.archive_mirror}[/cyan] ({base_url})[/bold green]"
        )
