"""
ESA INTEGRAL Science Legacy Archive (ISLA) client for Science Window data.

ISLA (https://isla.esac.esa.int) is ESA's European archive for INTEGRAL. Unlike HEASARC, it
does not expose the raw archive as a browsable directory tree. Instead:

* ScW IDs are listed with an ADQL query against its TAP service (table `ila.science_window`).
* Each ScW is served as one zip from `<base>/data?RETRIEVAL_TYPE=SCW&scwid=<id>`. The zip
  holds `<scwid>.001/*.fits.gz`, the same layout as `scw/<rev>/<scwid>.001/` locally.

ISLA serves Science Window data only. Catalogs, IC calibration, aux/adp and the revolution
context (`scw/<rev>/rev.001`) are still fetched from a directory-tree mirror (HEASARC).
"""

import asyncio
import csv
import io
import re
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import httpx

from integral.core.http import RETRY_ATTEMPTS, RETRY_STATUSES, backoff, describe_error
from integral.core.transfer_stats import record_file

ISLA_BASE = "https://isla.esac.esa.int/tap"

# ISLA builds each zip on request. Keep parallel requests to this public service modest
# whatever --concurrency is set to.
ISLA_MAX_CONCURRENCY = 4


class IslaError(RuntimeError):
    pass


async def isla_tap_query(
    client: httpx.AsyncClient, adql: str, base_url: str = ISLA_BASE
) -> list[dict[str, str]]:
    """Run a synchronous ADQL query and return rows as dicts (CSV output format)."""
    resp = await client.post(
        f"{base_url}/tap/sync",
        data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql},
        timeout=60.0,
    )
    if resp.status_code != 200 or resp.text.lstrip().startswith("<"):
        # TAP errors come back as VOTable XML, sometimes with HTTP 200.
        msg = re.search(r'QUERY_STATUS" value="ERROR">\s*([^<]+)', resp.text)
        raise IslaError(
            f"ISLA TAP query failed (HTTP {resp.status_code}): "
            f"{msg.group(1).strip() if msg else resp.text[:200]}"
        )
    return list(csv.DictReader(io.StringIO(resp.text)))


async def isla_list_scws(
    client: httpx.AsyncClient, rev_id: str, base_url: str = ISLA_BASE
) -> list[str]:
    """List the ScW IDs that ISLA holds for a revolution."""
    if not (len(rev_id) == 4 and rev_id.isdigit()):
        raise ValueError(f"Revolution must be 4 digits, got {rev_id!r}")
    rows = await isla_tap_query(
        client,
        f"SELECT scwid FROM ila.science_window WHERE revno = '{rev_id}' ORDER BY scwid",
        base_url=base_url,
    )
    return sorted({r["scwid"] for r in rows if r.get("scwid")})


def _service_error_message(body: str) -> str:
    """Extract 'Message: ...' from ISLA's HTML error page (the text follows a </b> tag)."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
    m = re.search(r"Message:\s*(.+?)(?:\s{2,}|$)", text)
    return (m.group(1) if m else text[-160:]).strip()


# ISLA's answer for ScWs it lists in its catalogue but does not distribute.
NOT_PUBLIC_MARKERS = ("no public products",)


def _safe_extract_scw_zip(zip_path: Path, scw_id: str, staging: Path) -> list[Path]:
    """Extract a ScW zip into `staging`, accepting only members under '<scwid>.001/'.
    Returns the extracted file paths."""
    prefix = f"{scw_id}.001"
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if (
                len(parts) != 2
                or parts[0] != prefix
                or parts[1] in ("", ".", "..")
                or info.filename.startswith("/")
            ):
                raise IslaError(f"Unexpected entry {info.filename!r} in ISLA zip for {scw_id}")
            target = staging / prefix / parts[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)
            extracted.append(target)
    if not extracted:
        raise IslaError(f"ISLA zip for {scw_id} was empty")
    return extracted


async def isla_download_scw(
    client: httpx.AsyncClient,
    scw_id: str,
    dest_base: Path,
    semaphore: asyncio.Semaphore,
    force: bool = False,
    base_url: str = ISLA_BASE,
    on_bytes: Callable[[int], None] | None = None,
) -> bool:
    """Download one Science Window zip from ISLA and unpack it into scw/<rev>/<scwid>.001/.

    The final directory only appears once the unpacked zip is complete (staged, then renamed), so an
    interrupted run never leaves a half-populated ScW that a later run would treat as cached.
    """
    rev = scw_id[:4]
    rev_dir = dest_base / "scw" / rev
    final_dir = rev_dir / f"{scw_id}.001"
    url = f"{base_url}/data?RETRIEVAL_TYPE=SCW&scwid={scw_id}"

    if final_dir.is_dir() and not force:
        existing = [p for p in final_dir.iterdir() if p.is_file()]
        if existing:
            record_file("cached", count=len(existing))
            return True

    rev_dir.mkdir(parents=True, exist_ok=True)
    zip_tmp = rev_dir / f".{scw_id}.zip.part"
    staging = rev_dir / f".{scw_id}.staging"

    received = 0
    async with semaphore:
        for attempt in range(RETRY_ATTEMPTS):
            last = attempt == RETRY_ATTEMPTS - 1
            received = 0
            try:
                async with client.stream("GET", url, timeout=120.0) as response:
                    if response.status_code != 200:
                        if response.status_code in RETRY_STATUSES and not last:
                            await backoff(attempt)
                            continue
                        body = (await response.aread()).decode(errors="replace")
                        message = _service_error_message(body)
                        if any(m in message.lower() for m in NOT_PUBLIC_MARKERS):
                            record_file("unavailable", url=scw_id, detail=f"ISLA: {message}")
                        else:
                            record_file(
                                "failed",
                                url=url,
                                detail=f"HTTP {response.status_code}: {message}",
                            )
                        return False
                    with open(zip_tmp, "wb") as f:  # noqa: ASYNC230 - same trade-off as data.py
                        async for chunk in response.aiter_bytes(chunk_size=1 << 16):
                            f.write(chunk)
                            received += len(chunk)
                            if on_bytes is not None:
                                on_bytes(len(chunk))
                break
            except httpx.TransportError as e:
                if on_bytes is not None and received:
                    on_bytes(-received)  # roll the progress bar back before retrying
                zip_tmp.unlink(missing_ok=True)
                if last:
                    record_file("failed", url=url, detail=describe_error(e))
                    return False
                await backoff(attempt)

    try:
        shutil.rmtree(staging, ignore_errors=True)
        files = await asyncio.to_thread(_safe_extract_scw_zip, zip_tmp, scw_id, staging)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        (staging / f"{scw_id}.001").rename(final_dir)
    except (zipfile.BadZipFile, IslaError, OSError) as e:
        record_file("failed", url=url, detail=describe_error(e))
        return False
    finally:
        zip_tmp.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)

    record_file("new", nbytes=received, url=url, count=len(files))
    return True
