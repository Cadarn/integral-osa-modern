"""
Local Data Management, HEASARC Async HTTP/2 Downloaders, and Staging Tools for INTEGRAL.
"""

import asyncio
import re
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm
from rich.table import Table

from integral.core.config import config
from integral.core.http import (
    RETRY_ATTEMPTS,
    RETRY_STATUSES,
    backoff,
    describe_error,
    get_with_retry,
)
from integral.core.isla import (
    ISLA_BASE,
    ISLA_MAX_CONCURRENCY,
    IslaError,
    isla_download_scw,
    isla_list_scws,
)
from integral.core.s3 import NASA_HEASARC_S3_BASE, is_s3_url, s3_list, s3_uri_to_https
from integral.core.scw import filter_pointing_scws
from integral.core.transfer_stats import TransferStats, active, record_file

console = Console()
data_app = typer.Typer(
    help="Manage INTEGRAL archive data, local imports, and async HEASARC/ISLA downloads"
)
download_app = typer.Typer(
    help="Download Science Windows, calibration, and support data from HEASARC or ESA ISLA. "
    "Every download prints a per-phase timing/throughput summary."
)
data_app.add_typer(download_app, name="download")

KNOWN_MIRRORS = {
    "heasarc": "https://heasarc.gsfc.nasa.gov/FTP/integral/data",
    "isla": ISLA_BASE,
    "s3": NASA_HEASARC_S3_BASE,
}
MIRROR_DESCRIPTIONS = {
    "heasarc": "NASA HEASARC (USA) - full archive tree",
    "isla": "ESA ISLA (Spain) - ScW data; catalogs/IC/aux from HEASARC",
    "s3": "NASA HEASARC on AWS S3 (us-east-1, public, no credentials) - full archive tree",
}
# Mirrors that used to be built in but no longer serve data. The value explains what to do instead.
RETIRED_MIRRORS = {
    "isdc": "The ISDC (Geneva) HTTP archive at isdc.unige.ch is offline (the hostname no "
    "longer resolves). Use 'isla' for ESA's European archive, 'heasarc' or 's3' for NASA's, or pass a working ISDC base "
    "URL with --mirror https://...",
}
# Which mirror to offer when one is down (interactive runs only). HEASARC HTTPS and S3 hold
# identical copies of the archive, so they back each other up.
PREFERRED_ALTERNATIVE = {"heasarc": "s3", "s3": "heasarc", "isla": "heasarc"}
DEFAULT_MIRROR_NAME = "heasarc"
HEASARC_FTP_BASE = KNOWN_MIRRORS["heasarc"]


@dataclass(frozen=True)
class ArchiveSource:
    """Where each kind of data comes from for one download run.

    kind='tree' means the mirror serves the full archive directory tree (scw/, aux/, cat/, ic/,
    idx/) over HTTP, as HEASARC does. kind='isla' means ScW data comes from ESA ISLA, and the
    support data it doesn't serve (catalogs, IC, aux, rev.001) comes from `support_url`.
    """

    name: str
    base_url: str
    kind: str = "tree"
    support_url: str = HEASARC_FTP_BASE
    probe_latency_s: float | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.base_url})"


class RetiredMirrorError(ValueError):
    pass


def resolve_mirror_base(mirror: str | None = None) -> tuple[str, str]:
    """Resolve mirror identifier or custom URL into (mirror_name, base_url).

    Raises RetiredMirrorError for mirrors that no longer serve data (e.g. 'isdc')."""
    raw = (mirror or config.archive_mirror or DEFAULT_MIRROR_NAME).strip()
    key = raw.lower()
    if key in RETIRED_MIRRORS:
        raise RetiredMirrorError(f"Mirror '{key}' is retired. {RETIRED_MIRRORS[key]}")
    if key in KNOWN_MIRRORS:
        return key, KNOWN_MIRRORS[key]
    # s3://bucket/prefix (anonymous, public buckets only) -> virtual-hosted HTTPS URL
    if raw.startswith("s3://"):
        return "custom-s3", s3_uri_to_https(raw)
    # If a full URL was provided
    if raw.startswith(("http://", "https://")):
        return "custom", raw.rstrip("/")
    # Default fallback
    return DEFAULT_MIRROR_NAME, KNOWN_MIRRORS[DEFAULT_MIRROR_NAME]


def resolve_source(mirror: str | None = None) -> ArchiveSource:
    name, url = resolve_mirror_base(mirror)
    if name == "isla" or url.rstrip("/") == ISLA_BASE:
        return ArchiveSource("isla", url, kind="isla", support_url=HEASARC_FTP_BASE)
    return ArchiveSource(name, url, kind="tree", support_url=url)


async def probe_mirror_health(base_url: str, timeout_sec: float = 5.0) -> tuple[bool, float, str]:
    """Ping a mirror to assess latency and availability. Returns (is_available, latency_seconds, error_msg)."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            if is_s3_url(base_url):
                # A bucket "directory" is not an object, so HEAD would 404; list one key instead.
                resp = await s3_list(client, base_url, timeout=timeout_sec, max_keys=1)
            else:
                resp = await client.head(f"{base_url}/")
            latency = time.perf_counter() - start
            if resp.status_code in [200, 301, 302]:
                return True, latency, ""
            return False, latency, f"HTTP {resp.status_code}"
    except Exception as e:
        latency = time.perf_counter() - start
        return False, latency, describe_error(e)


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


async def _get(client: httpx.AsyncClient, dir_url: str) -> httpx.Response:
    """Fetch a directory listing (with retries). HTTP mirrors return their HTML index; S3 mirrors
    are listed via ListObjectsV2 and rendered in the same HTML shape, so every listing parser in
    this module works against either."""
    if is_s3_url(dir_url):
        return await s3_list(client, dir_url)
    return await get_with_retry(client, dir_url)


async def async_download_file(
    client: httpx.AsyncClient,
    url: str,
    dest_path: Path,
    progress: Progress | None = None,
    task_id=None,
    force: bool = False,
) -> bool:
    """Asynchronously download a file with atomic .tmp write and resume safety.

    Transient failures (connection errors, timeouts, 429/502/503/504) are retried with
    backoff. The outcome (new / cached / failed, plus bytes received) is reported to the active
    TransferStats, if there is one."""

    def _advance() -> None:
        if progress is not None and task_id is not None:
            progress.advance(task_id, 1)

    if dest_path.exists() and not force:
        record_file("cached")
        _advance()
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_name(dest_path.name + ".tmp")

    try:
        for attempt in range(RETRY_ATTEMPTS):
            last = attempt == RETRY_ATTEMPTS - 1
            received = 0
            try:
                async with client.stream("GET", url, timeout=60.0) as response:
                    if response.status_code != 200:
                        if response.status_code in RETRY_STATUSES and not last:
                            await backoff(attempt)
                            continue
                        record_file("failed", url=url, detail=f"HTTP {response.status_code}")
                        return False

                    # Blocking file I/O inside an async function - a real anti-pattern, but fixing it
                    # properly needs either the `aiofiles` dependency or per-chunk asyncio.to_thread
                    # (which would add thread-pool overhead on every 64KB chunk). Deferred rather than
                    # fixed here; each concurrent download's disk writes briefly block the event loop.
                    with open(temp_path, "wb") as f:  # noqa: ASYNC230
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            received += len(chunk)
            except httpx.TransportError:
                temp_path.unlink(missing_ok=True)
                if last:
                    raise
                await backoff(attempt)
                continue

            temp_path.rename(dest_path)
            record_file("new", nbytes=received, url=url)
            return True
        return False
    except Exception as e:
        console.print(f"[dim red]Warning: failed to download {url}: {describe_error(e)}[/dim red]")
        temp_path.unlink(missing_ok=True)
        record_file("failed", url=url, detail=describe_error(e))
        return False
    finally:
        _advance()


async def async_list_scw_files(
    client: httpx.AsyncClient, scw_id: str, base_url: str = HEASARC_FTP_BASE
) -> list[str]:
    """List the files present in a Science Window's remote directory."""
    rev = scw_id[:4]
    scw_url = f"{base_url}/scw/{rev}/{scw_id}.001/"
    try:
        resp = await _get(client, scw_url)
    except httpx.HTTPError as e:
        console.print(f"[red]Could not list ScW {scw_id}: {describe_error(e)}[/red]")
        record_file("failed", url=scw_url, detail=f"listing: {describe_error(e)}")
        return []
    if resp.status_code != 200:
        console.print(
            f"[red]ScW {scw_id} directory not found on server (HTTP {resp.status_code})[/red]"
        )
        record_file("failed", url=scw_url, detail=f"listing: HTTP {resp.status_code}")
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
    client: httpx.AsyncClient,
    rev_id: str,
    base_url: str = HEASARC_FTP_BASE,
    kind: str = "tree",
) -> list[str]:
    """List available ScW IDs for a revolution directly from the remote archive server.

    Network errors are reported and give an empty list rather than a traceback."""
    if kind == "isla":
        try:
            return await isla_list_scws(client, rev_id, base_url=base_url)
        except (httpx.HTTPError, IslaError) as e:
            console.print(
                f"[red]Could not list revolution {rev_id} on ISLA: {describe_error(e)}[/red]"
            )
            return []

    rev_url = f"{base_url}/scw/{rev_id}/"
    try:
        resp = await _get(client, rev_url)
    except httpx.HTTPError as e:
        console.print(
            f"[red]Could not list revolution {rev_id} at {rev_url}: {describe_error(e)}[/red]"
        )
        return []
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
        resp = await _get(client, aux_url)
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
        resp = await _get(client, rev_url)
        if resp.status_code != 200:
            console.print(
                f"[dim yellow]Notice: Revolution context rev.001 not found for rev {rev_id} (HTTP {resp.status_code})[/dim yellow]"
            )
            return 0

        # Scan and download rev.001 subdirectories (idx, aca, cfg, osm, prp, raw)
        async def fetch_dir(rel_path: str, target_dir: Path) -> int:
            target_dir.mkdir(parents=True, exist_ok=True)
            u = f"{rev_url}{rel_path}"
            r = await _get(client, u)
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
        resp = await _get(client, tcor_url)
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

            # Also validate HDU 3 (GNRL-CHAR-LST) active aliases to ensure configured version numbers resolve locally
            hdu3: Any = master[3] if len(master) >= 4 else None
            if hdu3 is not None and hdu3.data is not None:
                d3 = hdu3.data
                osa_rows = [r for r in d3 if r["MNEMONIC"] == "OSA"]
                if osa_rows:
                    active_row = osa_rows[0]
                    missing_subsystems: list[str] = []
                    for col in d3.names:
                        val = active_row[col]
                        if isinstance(val, (int, float)) and val > 0:
                            cand_idx = col.replace("_", "-") + "-IDX.fits"
                            idx_file = idx_dir / cand_idx
                            if not idx_file.exists():
                                continue
                            try:
                                with fits.open(idx_file) as sub_hdul:
                                    sub_hdu1: Any = sub_hdul[1] if len(sub_hdul) > 1 else None
                                    if sub_hdu1 is not None and sub_hdu1.data is not None:
                                        s_data = sub_hdu1.data
                                        if "VERSION" in s_data.names:
                                            matches = s_data[s_data["VERSION"] == val]
                                            if len(matches) == 0:
                                                missing_subsystems.append(
                                                    f"{col} (ver {val} not in {cand_idx})"
                                                )
                                            elif "MEMBER_LOCATION" in s_data.names:
                                                loc = str(matches[0]["MEMBER_LOCATION"])
                                                target_file = (idx_dir / loc).resolve()
                                                if not target_file.exists():
                                                    missing_subsystems.append(
                                                        f"{col} ({target_file.name} missing)"
                                                    )
                            except (OSError, KeyError, ValueError) as sub_err:
                                console.print(
                                    f"[dim]Debug: failed checking {idx_file.name}: {sub_err}[/dim]"
                                )
                    if missing_subsystems:
                        console.print(
                            f"[dim yellow]Notice: {len(missing_subsystems)} calibration subsystem(s) in active alias 'OSA' unresolved: "
                            f"{', '.join(missing_subsystems[:3])}{'...' if len(missing_subsystems) > 3 else ''}[/dim yellow]"
                        )

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
        resp = await _get(client, idx_url)
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
        resp = await _get(client, base_url)
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
                record_file("cached")
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

    for existing in (target_fits, omc_target):
        if existing.exists() and not force:
            record_file("cached")

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
    with _phase("Catalogs"):
        await _download_catalogs(client, dest_base, cat_version, force=force, base_url=base_url)
    with _phase("IC index"):
        await async_download_ic_index(client, dest_base, force=force, base_url=base_url)
    if ic_trees:
        for inst in instruments:
            with _phase(f"IC tree ({inst})"):
                await async_download_ic_tree(
                    client, inst, dest_base, semaphore, force=force, base_url=base_url
                )


def _phase(name: str):
    """Time a block as a named phase of the active TransferStats (no-op when none is active)."""
    stats = active()
    return stats.phase(name) if stats is not None else nullcontext()


async def _download_science_tree(
    client: httpx.AsyncClient,
    dest_base: Path,
    scw_ids: list[str],
    semaphore: asyncio.Semaphore,
    force: bool,
    dry_run: bool,
    base_url: str,
) -> None:
    """Science Window data from a directory-tree mirror (HEASARC or custom): list each ScW's
    directory, then fetch every file concurrently."""
    console.print(f"[bold blue]Listing files for {len(scw_ids)} Science Window(s)...[/bold blue]")
    with _phase("ScW listing"):
        listings = await asyncio.gather(
            *(async_list_scw_files(client, s, base_url=base_url) for s in scw_ids)
        )
    total_files = sum(len(f) for f in listings)

    if dry_run:
        console.print(
            f"[yellow]Dry run: would download {total_files} file(s) across {len(scw_ids)} ScW(s).[/yellow]"
        )
        return
    if total_files == 0:
        console.print("[yellow]No Science Window files found to download.[/yellow]")
        return

    with (
        _phase("Science Windows"),
        Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]Downloading Science Windows...[/bold cyan]"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total} files)"),
            TimeElapsedColumn(),
            console=console,
        ) as progress,
    ):
        task_id = progress.add_task("scw", total=total_files)
        await asyncio.gather(
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


async def _download_science_isla(
    client: httpx.AsyncClient,
    dest_base: Path,
    scw_ids: list[str],
    concurrency: int,
    force: bool,
    dry_run: bool,
    base_url: str,
) -> None:
    """Science Window data from ESA ISLA: one zip per ScW, unpacked into the archive layout."""
    if dry_run:
        console.print(
            f"[yellow]Dry run: would download {len(scw_ids)} ScW zip(s) from ISLA ({base_url}).[/yellow]"
        )
        return

    isla_streams = min(concurrency, ISLA_MAX_CONCURRENCY)
    if isla_streams < concurrency:
        console.print(
            f"[dim]ISLA serves one zip per ScW; using {isla_streams} parallel requests "
            f"(capped from {concurrency} to be gentle on the service).[/dim]"
        )
    semaphore = asyncio.Semaphore(isla_streams)
    stats = active()
    if stats is not None:
        stats.science_streams = isla_streams

    with (
        _phase("Science Windows"),
        Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress,
    ):
        done = 0
        task_id = progress.add_task(f"Downloading ScWs from ISLA (0/{len(scw_ids)})", total=None)

        async def one(scw_id: str) -> bool:
            nonlocal done
            ok = await isla_download_scw(
                client,
                scw_id,
                dest_base,
                semaphore,
                force=force,
                base_url=base_url,
                on_bytes=lambda n: progress.advance(task_id, n),
            )
            done += 1
            progress.update(
                task_id, description=f"Downloading ScWs from ISLA ({done}/{len(scw_ids)})"
            )
            return ok

        await asyncio.gather(*(one(s) for s in scw_ids))


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
    source: ArchiveSource | None = None,
):
    """Shared orchestration for the revolution/scw/file download subcommands.

    `source` decides where ScW data comes from (a directory-tree mirror or ESA ISLA) and where
    catalogs/IC/aux come from. If it is omitted, `base_url` is treated as a directory-tree mirror
    for everything."""
    source = source or ArchiveSource("custom", base_url, kind="tree", support_url=base_url)
    support_url = source.support_url
    semaphore = asyncio.Semaphore(concurrency)
    revisions = sorted({s[:4] for s in scw_ids})

    if fetch_science and scw_ids:
        if source.kind == "isla":
            await _download_science_isla(
                client, dest_base, scw_ids, concurrency, force, dry_run, source.base_url
            )
        else:
            await _download_science_tree(
                client, dest_base, scw_ids, semaphore, force, dry_run, source.base_url
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
                f"and aux data for revolution(s): {rev_desc} from {support_url}.[/yellow]"
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
                base_url=support_url,
            )
            # Ensure essential mission time reference tables (tcoroffset) are present
            with _phase("Aux reference"):
                await async_download_aux_ref(client, dest_base, force=force, base_url=support_url)
            for rev_id in revisions:
                console.print(
                    f"[bold blue]Fetching aux data for revolution {rev_id}...[/bold blue]"
                )
                with _phase("Aux (adp)"):
                    count = await async_download_aux(
                        client, rev_id, dest_base, semaphore, force=force, base_url=support_url
                    )
                console.print(
                    f"[bold green]✓ Aux data for revolution {rev_id} ready ({count} files).[/bold green]"
                )
                # Fetch revolution context files (rev.001) required for ScW processing
                with _phase("Revolution context"):
                    await async_download_rev_context(
                        client, rev_id, dest_base, semaphore, force=force, base_url=support_url
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


MAX_REVOLUTIONS_PER_COMMAND = 500


def parse_revolutions(spec: str) -> list[str]:
    """Parse '60', '0060-0065' or '0060,0071,0102-0104' into sorted unique 4-digit revolution IDs."""
    revs: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        lo_s, sep, hi_s = part.partition("-")
        if not lo_s.strip().isdigit() or (sep and not hi_s.strip().isdigit()):
            raise ValueError(
                f"Invalid revolution spec {part!r}: use e.g. 0060, 0060-0065, 0060,0071"
            )
        lo, hi = int(lo_s), int(hi_s) if sep else int(lo_s)
        if lo > hi:
            raise ValueError(f"Invalid revolution range {part!r}: start is after end")
        if hi > 9999:
            raise ValueError(f"Revolution {hi} is out of range (max 9999)")
        revs.update(range(lo, hi + 1))
        if len(revs) > MAX_REVOLUTIONS_PER_COMMAND:
            raise ValueError(
                f"More than {MAX_REVOLUTIONS_PER_COMMAND} revolutions requested; split the request."
            )
    if not revs:
        raise ValueError("No revolution given")
    return [f"{r:04d}" for r in sorted(revs)]


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
    "mirror": "Archive source: 'heasarc' (NASA HTTPS, full archive tree), 's3' (the same "
    "archive in NASA's public AWS bucket s3://nasa-heasarc, no credentials needed), 'isla' (ESA; "
    "Science Window data only - catalogs/IC/aux still come from HEASARC), a custom https:// "
    "base URL, or an s3://bucket/prefix of a public HEASARC-style tree. Default: configured "
    "archive_mirror or 'heasarc'",
    "stats_file": "Append a JSON line with this run's timings, volumes and throughput "
    "(per phase and total) to this file - handy for comparing mirrors across runs",
    "yes": "Non-interactive mode: skip confirmation, and never switch mirrors automatically",
}


async def _check_and_select_mirror(
    mirror_opt: str | None = None, interactive: bool = False
) -> ArchiveSource:
    """Resolve the requested source and test connectivity.

    If the mirror is unreachable, an interactive session is offered the other built-in mirror.
    Non-interactive runs (-y, or no TTY) never switch, so a scripted timing run measures the
    mirror it asked for."""
    try:
        source = resolve_source(mirror_opt)
    except RetiredMirrorError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        if not mirror_opt:
            console.print(
                "[yellow]It is your configured default; change it with "
                "'integral data mirror heasarc' (or 'isla').[/yellow]"
            )
        raise typer.Exit(code=1) from None

    console.print(f"[dim]Checking mirror connectivity: [cyan]{source.label}[/cyan]...[/dim]")
    ok, latency, err = await probe_mirror_health(source.base_url, timeout_sec=6.0)

    if ok:
        if latency > 3.0:
            console.print(
                f"[yellow]⚠️  Notice: Mirror '{source.name}' responded slowly ({latency:.2f}s).[/yellow]"
            )
        else:
            console.print(
                f"[dim green]✓ Mirror '{source.name}' active ({latency:.2f}s response).[/dim green]"
            )
        if source.kind == "isla":
            console.print(
                "[dim]ISLA serves Science Window data; catalogs, IC and aux data will come "
                "from HEASARC.[/dim]"
            )
        return replace(source, probe_latency_s=latency)

    console.print(
        f"[bold yellow]⚠️  Warning: Mirror '{source.name}' is currently unreachable or slow ({err}).[/bold yellow]"
    )
    alt_name = PREFERRED_ALTERNATIVE.get(source.name)
    if interactive and alt_name:
        alt = resolve_source(alt_name)
        console.print(f"[yellow]Testing alternative mirror {alt.label}...[/yellow]")
        alt_ok, alt_latency, _alt_err = await probe_mirror_health(alt.base_url, timeout_sec=5.0)
        if alt_ok:
            console.print(
                f"[bold green]✓ Alternative mirror '{alt_name}' is online ({alt_latency:.2f}s latency)![/bold green]"
            )
            if Confirm.ask(f"Would you like to switch to '{alt_name}' mirror?", default=True):
                return replace(alt, probe_latency_s=alt_latency)
    elif not interactive:
        console.print("[dim]Non-interactive run: not switching mirrors.[/dim]")
    console.print(
        f"[yellow]Proceeding with '{source.name}' as requested (may experience timeouts).[/yellow]"
    )
    return source


def _run_timed_download(
    command: str,
    mirror: str | None,
    concurrency: int,
    dry_run: bool,
    stats_file: Path | None,
    interactive: bool,
    body: Callable[[httpx.AsyncClient, ArchiveSource, TransferStats], Awaitable[None]],
    confirm: Callable[[ArchiveSource], None] | None = None,
) -> None:
    """Run one download command with timing.

    Mirror selection and any confirmation prompt happen *before* the clock starts, so user
    think-time is never counted. Everything in `body` (remote listing, downloads, calibration,
    aux) is timed. A summary table is printed at the end, even if the run is interrupted, and
    appended to `stats_file` if one is given. Exits 1 if any file failed to download."""

    async def _main() -> TransferStats:
        source = await _check_and_select_mirror(mirror, interactive=interactive)
        if confirm is not None:
            confirm(source)
        stats = TransferStats(
            command=command,
            source=source.name,
            science_url=source.base_url,
            support_url=source.support_url,
            concurrency=concurrency,
            probe_latency_s=source.probe_latency_s,
        )
        try:
            with stats.activate():
                async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
                    await body(client, source, stats)
                stats.completed = True
        finally:
            transferred = stats.files_new or stats.files_failed or stats.files_cached
            if not dry_run and (stats.completed or transferred):
                console.print()
                stats.render(console)
                if stats.unavailable and source.kind == "isla":
                    ids = ",".join(u.split(" ", 1)[0] for u in stats.unavailable)
                    console.print(
                        "[yellow]To fetch them from NASA instead:[/yellow] "
                        f"integral data download scw {ids} --mirror heasarc --science-only -y"
                    )
                if stats_file is not None:
                    stats.append_to(stats_file)
                    console.print(f"[dim]Timing record appended to {stats_file}[/dim]")
        return stats

    stats = asyncio.run(_main())
    if stats.files_failed:
        raise typer.Exit(code=1)


def _scope_str(science_only: bool, calib_only: bool) -> str:
    if science_only:
        return "Science only"
    return "Calibration only" if calib_only else "Science & Calibration"


def _source_lines(source: ArchiveSource) -> str:
    if source.kind == "isla":
        return (
            f"• Science data:   [cyan]{source.label}[/cyan]\n"
            f"• Support data:   [cyan]heasarc ({source.support_url})[/cyan]\n"
        )
    return f"• Mirror:         [cyan]{source.label}[/cyan]\n"


@download_app.command("revolution")
def download_revolution(
    rev: str = typer.Argument(
        ...,
        help="Revolution(s): a number (0060), a range (0060-0065), or a comma list (0060,0071,0102-0104)",
    ),
    count: int | None = typer.Option(
        None,
        "--count",
        "-n",
        help="Fetch only the first N pointing ScWs of each revolution (default: every pointing ScW)",
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
    stats_file: Path | None = typer.Option(None, "--stats-file", help=_SCOPE_HELP["stats_file"]),
    yes: bool = typer.Option(False, "--yes", "-y", help=_SCOPE_HELP["yes"]),
):
    """Download whole revolution(s) (or a slice) plus their calibration/support data."""
    _validate_scope_flags(science_only, calib_only)
    try:
        rev_ids = parse_revolutions(rev)
    except ValueError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise typer.Exit(code=1) from None
    if (id_from or id_to) and len(rev_ids) > 1:
        console.print(
            "[bold red]Error: --from/--to can only be used with a single revolution.[/bold red]"
        )
        raise typer.Exit(code=1)
    rev_desc = rev_ids[0] if len(rev_ids) == 1 else f"{len(rev_ids)} revolutions ({rev})"
    resolved_insts = _resolve_instruments(instruments)
    interactive = sys.stdin.isatty() and not yes

    def confirm(source: ArchiveSource) -> None:
        if not interactive or dry_run:
            return
        console.print(
            Panel(
                f"[bold green]Download Plan: Revolution {rev_desc}[/bold green]\n\n"
                f"• Target Scope:   [cyan]{_scope_str(science_only, calib_only)}[/cyan]\n"
                f"{_source_lines(source)}"
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

    async def body(client: httpx.AsyncClient, source: ArchiveSource, stats: TransferStats):
        with stats.phase("Revolution listing"):
            listings = await asyncio.gather(
                *(
                    async_list_remote_scws(client, r, base_url=source.base_url, kind=source.kind)
                    for r in rev_ids
                )
            )

        scw_ids: list[str] = []
        found_revs: list[str] = []
        for rev_id, all_ids in zip(rev_ids, listings):
            if not all_ids:
                console.print(
                    f"[bold yellow]Warning: no Science Windows found for revolution {rev_id} on {source.label}.[/bold yellow]"
                )
                continue
            found_revs.append(rev_id)
            if id_from or id_to:
                lo, hi = id_from or min(all_ids), id_to or max(all_ids)
                scw_ids += sorted(s for s in all_ids if lo <= s <= hi)
            else:
                picked = filter_pointing_scws(all_ids, rev_id)
                scw_ids += picked[:count] if count else picked
        if not scw_ids:
            console.print(
                f"[bold red]Error: no Science Windows found for revolution(s) {rev} on {source.label}.[/bold red]"
            )
            raise typer.Exit(code=1)
        console.print(
            f"[dim]Selected {len(scw_ids)} Science Window(s) across {len(found_revs)} revolution(s).[/dim]"
        )
        stats.scw_count, stats.revolutions = len(scw_ids), found_revs

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
            base_url=source.base_url,
            source=source,
        )

    _run_timed_download(
        f"revolution {rev}", mirror, concurrency, dry_run, stats_file, interactive, body, confirm
    )


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
    stats_file: Path | None = typer.Option(None, "--stats-file", help=_SCOPE_HELP["stats_file"]),
    yes: bool = typer.Option(False, "--yes", "-y", help=_SCOPE_HELP["yes"]),
):
    """Download one or more specific Science Windows plus their calibration/support data."""
    _validate_scope_flags(science_only, calib_only)
    scw_ids = [s.strip() for s in scw_spec.split(",") if s.strip()]
    for scw_id in scw_ids:
        if len(scw_id) != 12 or not scw_id.isdigit():
            console.print(f"[bold red]Error: '{scw_id}' is not a valid 12-digit ScW ID.[/bold red]")
            raise typer.Exit(code=1)

    resolved_insts = _resolve_instruments(instruments)
    interactive = sys.stdin.isatty() and not yes

    def confirm(source: ArchiveSource) -> None:
        if not interactive or dry_run:
            return
        console.print(
            Panel(
                f"[bold green]Download Plan: {len(scw_ids)} Science Window(s)[/bold green]\n\n"
                f"• Target ScWs:    [cyan]{', '.join(scw_ids[:5])}{'...' if len(scw_ids) > 5 else ''}[/cyan]\n"
                f"• Target Scope:   [cyan]{_scope_str(science_only, calib_only)}[/cyan]\n"
                f"{_source_lines(source)}"
                f"• Instruments:    [cyan]{', '.join(resolved_insts)}[/cyan]\n"
                f"• Full IC Trees:  [cyan]{'Enabled (multi-GB)' if ic_trees else 'Minimal (indexes, rev context, reference data)'}[/cyan]\n"
                f"• Destination:    [cyan]{config.rep_base_prod}[/cyan]",
                title="Download Confirmation",
            )
        )
        if not Confirm.ask("Proceed with download?", default=True):
            console.print("[yellow]Download cancelled by user.[/yellow]")
            raise typer.Exit(code=0)

    async def body(client: httpx.AsyncClient, source: ArchiveSource, stats: TransferStats):
        stats.scw_count, stats.revolutions = len(scw_ids), sorted({s[:4] for s in scw_ids})
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
            base_url=source.base_url,
            source=source,
        )

    _run_timed_download(
        f"scw ({len(scw_ids)})",
        mirror,
        concurrency,
        dry_run,
        stats_file,
        interactive,
        body,
        confirm,
    )


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
    stats_file: Path | None = typer.Option(None, "--stats-file", help=_SCOPE_HELP["stats_file"]),
    yes: bool = typer.Option(False, "--yes", "-y", help=_SCOPE_HELP["yes"]),
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

    async def body(client: httpx.AsyncClient, source: ArchiveSource, stats: TransferStats):
        stats.scw_count, stats.revolutions = len(scw_ids), sorted({s[:4] for s in scw_ids})
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
            base_url=source.base_url,
            source=source,
        )

    _run_timed_download(
        f"file {path.name} ({len(scw_ids)})",
        mirror,
        concurrency,
        dry_run,
        stats_file,
        sys.stdin.isatty() and not yes,
        body,
    )


@download_app.command("calibration")
def download_calibration_cmd(
    instruments: str = typer.Option("", "--instruments", help=_SCOPE_HELP["instruments"]),
    ic_trees: bool = typer.Option(False, "--ic-trees", help=_SCOPE_HELP["ic_trees"]),
    cat_version: str = typer.Option("0043", "--cat-version", help=_SCOPE_HELP["cat_version"]),
    concurrency: int = typer.Option(16, "--concurrency", "-j", help=_SCOPE_HELP["concurrency"]),
    force_refresh: bool = typer.Option(False, "--force-refresh", help=_SCOPE_HELP["force_refresh"]),
    mirror: str | None = typer.Option(None, "--mirror", "-m", help=_SCOPE_HELP["mirror"]),
    dry_run: bool = typer.Option(False, "--dry-run", help=_SCOPE_HELP["dry_run"]),
    stats_file: Path | None = typer.Option(None, "--stats-file", help=_SCOPE_HELP["stats_file"]),
    yes: bool = typer.Option(False, "--yes", "-y", help=_SCOPE_HELP["yes"]),
):
    """Download catalogs and the IC index without any Science Window data (no revolution context).

    Pass --ic-trees to also fetch full per-instrument calibration trees (large)."""
    resolved_instruments = _resolve_instruments(instruments)

    async def body(client: httpx.AsyncClient, source: ArchiveSource, stats: TransferStats):
        if dry_run:
            ic_desc = (
                f"IC trees for {resolved_instruments}"
                if ic_trees
                else "IC trees (skipped - pass --ic-trees to include)"
            )
            console.print(
                f"[yellow]Dry run: would ensure catalogs, IC index, and {ic_desc} from {source.support_url}.[/yellow]"
            )
            return
        await _download_calibration(
            client,
            config.rep_base_prod,
            asyncio.Semaphore(concurrency),
            resolved_instruments,
            cat_version,
            force_refresh,
            ic_trees=ic_trees,
            base_url=source.support_url,
        )

    _run_timed_download(
        "calibration",
        mirror,
        concurrency,
        dry_run,
        stats_file,
        sys.stdin.isatty() and not yes,
        body,
    )


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
        None, help="Mirror name ('heasarc', 's3', 'isla') or custom URL to set as default"
    ),
    test: bool = typer.Option(
        False, "--test", "-t", help="Test latency and availability of all known mirrors"
    ),
):
    """Show, test, or configure the default archive mirror."""
    if test or not name:
        table = Table(title="INTEGRAL Archive Mirrors")
        table.add_column("Mirror Name", style="cyan")
        table.add_column("Serves", style="white")
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
                table.add_row(
                    m_name, MIRROR_DESCRIPTIONS.get(m_name, ""), url, is_active, status_str
                )

        asyncio.run(_test_all())
        console.print(table)
        if current in RETIRED_MIRRORS:
            console.print(
                f"[bold yellow]Your configured default '{current}' is retired: "
                f"{RETIRED_MIRRORS[current]}[/bold yellow]"
            )

    if name:
        try:
            resolved_name, base_url = resolve_mirror_base(name)
        except RetiredMirrorError as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            raise typer.Exit(code=1) from None
        config.archive_mirror = resolved_name if resolved_name in KNOWN_MIRRORS else base_url
        config.save()
        console.print(
            f"[bold green]✓ Default archive mirror set to: [cyan]{config.archive_mirror}[/cyan] ({base_url})[/bold green]"
        )
