"""Tests for download timing/throughput accounting, HTTP retries, and the ESA ISLA client.

Network access is never exercised: real httpx.AsyncClient instances run against
httpx.MockTransport handlers.
"""

import asyncio
import io
import zipfile

import httpx
import pytest

from integral.core import data, http, isla, s3
from integral.core.transfer_stats import (
    TransferStats,
    format_bytes,
    format_duration,
    format_rate,
    record_file,
)


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(http, "_sleep", no_sleep)


def _stats() -> TransferStats:
    return TransferStats(
        command="test", source="heasarc", science_url="s", support_url="s", concurrency=4
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- TransferStats ------------------------------------------------------------------------------


def test_phases_accumulate_and_totals_sum():
    stats = _stats()
    with stats.activate():
        with stats.phase("Aux (adp)"):
            record_file("new", nbytes=1000)
        with stats.phase("Aux (adp)"):  # re-entered for a second revolution
            record_file("new", nbytes=500)
            record_file("cached")
        with stats.phase("Catalogs"):
            record_file("failed", url="https://x/cat.fits", detail="HTTP 404")

    aux = stats.phases["Aux (adp)"]
    assert (aux.files_new, aux.files_cached, aux.bytes) == (2, 1, 1500)
    assert stats.total_bytes == 1500
    assert stats.files_failed == 1
    assert stats.failures == ["https://x/cat.fits (HTTP 404)"]
    assert stats.wall_seconds > 0


def test_record_file_without_active_stats_is_a_noop():
    record_file("new", nbytes=10)  # must not raise


def test_record_counts_multiple_files_for_one_transfer():
    stats = _stats()
    with stats.activate(), stats.phase("Science Windows"):
        record_file("new", nbytes=65_000_000, count=38)
    assert stats.files_new == 38 and stats.total_bytes == 65_000_000


def test_formatters():
    assert format_bytes(0) == "-"
    assert format_bytes(1_500) == "1.50 kB"
    assert format_bytes(65_163_534) == "65.16 MB"
    assert format_bytes(2_000_000_000) == "2.00 GB"
    assert format_duration(4.25) == "4.2s"
    assert format_duration(125) == "2m05s"
    assert format_duration(3725) == "1h02m05s"
    assert format_rate(None) == "-"
    assert format_rate(0.884) == "0.88 MB/s"


# --- async_download_file -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_file_records_bytes_and_writes_atomically(tmp_path):
    payload = b"x" * 200_000
    client = _client(lambda req: httpx.Response(200, content=payload))
    dest = tmp_path / "scw" / "isgri_events.fits.gz"

    stats = _stats()
    with stats.activate(), stats.phase("Science Windows"):
        ok = await data.async_download_file(client, "https://m/isgri_events.fits.gz", dest)

    assert ok and dest.read_bytes() == payload
    assert not list(tmp_path.rglob("*.tmp"))
    ph = stats.phases["Science Windows"]
    assert (ph.files_new, ph.bytes) == (1, 200_000)


@pytest.mark.asyncio
async def test_download_file_retries_transient_errors(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("")
        if calls["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    stats = _stats()
    with stats.activate(), stats.phase("Aux (adp)"):
        ok = await data.async_download_file(_client(handler), "https://m/f.fits", tmp_path / "f")

    assert ok and calls["n"] == 3
    assert stats.retries == 2 and stats.files_new == 1 and stats.files_failed == 0


@pytest.mark.asyncio
async def test_download_file_failure_is_counted_not_raised(tmp_path):
    stats = _stats()
    with stats.activate(), stats.phase("Catalogs"):
        ok = await data.async_download_file(
            _client(lambda req: httpx.Response(404)), "https://m/missing.fits", tmp_path / "m"
        )
    assert not ok
    assert stats.files_failed == 1
    assert "HTTP 404" in stats.failures[0]


@pytest.mark.asyncio
async def test_download_file_counts_existing_file_as_cached(tmp_path):
    dest = tmp_path / "already.fits"
    dest.write_bytes(b"data")

    def handler(req):
        raise AssertionError("must not hit the network for cached files")

    stats = _stats()
    with stats.activate(), stats.phase("IC index"):
        assert await data.async_download_file(_client(handler), "https://m/already.fits", dest)
    assert stats.files_cached == 1 and stats.total_bytes == 0


# --- ISLA ----------------------------------------------------------------------------------------


def _scw_zip(scw_id: str, members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name if "/" in name else f"{scw_id}.001/{name}", content)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_isla_list_scws_parses_tap_csv():
    def handler(req):
        assert req.url.path.endswith("/tap/sync")
        body = req.content.decode()
        assert "revno+%3D+%270060%27" in body or "revno = '0060'" in body
        return httpx.Response(200, text="scwid\n006000050010\n006000040010\n006000040010\n")

    ids = await isla.isla_list_scws(_client(handler), "0060")
    assert ids == ["006000040010", "006000050010"]


@pytest.mark.asyncio
async def test_isla_list_scws_rejects_non_numeric_revolution():
    with pytest.raises(ValueError):
        await isla.isla_list_scws(_client(lambda r: httpx.Response(200)), "60' OR 1=1 --")


@pytest.mark.asyncio
async def test_isla_tap_error_votable_raises():
    votable = '<VOTABLE><INFO name="QUERY_STATUS" value="ERROR">Unknown column "x"</INFO></VOTABLE>'
    with pytest.raises(isla.IslaError, match="Unknown column"):
        await isla.isla_list_scws(_client(lambda r: httpx.Response(400, text=votable)), "0060")


@pytest.mark.asyncio
async def test_isla_download_scw_unpacks_into_archive_layout(tmp_path):
    scw = "006000040010"
    payload = _scw_zip(scw, {"isgri_events.fits.gz": b"E" * 1000, "swg.fits": b"S" * 10})

    def handler(req):
        assert req.url.params["RETRIEVAL_TYPE"] == "SCW"
        assert req.url.params["scwid"] == scw
        return httpx.Response(200, content=payload)

    seen = []
    stats = _stats()
    with stats.activate(), stats.phase("Science Windows"):
        ok = await isla.isla_download_scw(
            _client(handler), scw, tmp_path, asyncio.Semaphore(2), on_bytes=seen.append
        )

    scw_dir = tmp_path / "scw" / "0060" / f"{scw}.001"
    assert ok
    assert sorted(p.name for p in scw_dir.iterdir()) == ["isgri_events.fits.gz", "swg.fits"]
    assert sum(seen) == len(payload)
    assert stats.files_new == 2 and stats.total_bytes == len(payload)
    # No staging/partial leftovers next to the ScW directory
    assert [p.name for p in (tmp_path / "scw" / "0060").iterdir()] == [f"{scw}.001"]

    # Second call is a cache hit: no request made
    stats2 = _stats()
    with stats2.activate():
        assert await isla.isla_download_scw(
            _client(lambda r: (_ for _ in ()).throw(AssertionError("network"))),
            scw,
            tmp_path,
            asyncio.Semaphore(2),
        )
    assert stats2.files_cached == 2


@pytest.mark.asyncio
async def test_isla_download_scw_reports_service_error_message(tmp_path):
    html = "<html><b>Message:</b> Internal failure<br/></html>"
    stats = _stats()
    with stats.activate():
        ok = await isla.isla_download_scw(
            _client(lambda r: httpx.Response(500, text=html)),
            "006000010010",
            tmp_path,
            asyncio.Semaphore(1),
        )
    assert not ok
    assert "Internal failure" in stats.failures[0]
    assert not (tmp_path / "scw" / "0060" / "006000010010.001").exists()


@pytest.mark.asyncio
async def test_isla_download_scw_rejects_path_traversal(tmp_path):
    scw = "006000040010"
    evil = _scw_zip(scw, {"../../escape.txt": b"nope", f"{scw}.001/ok.fits": b"ok"})
    stats = _stats()
    with stats.activate():
        ok = await isla.isla_download_scw(
            _client(lambda r: httpx.Response(200, content=evil)),
            scw,
            tmp_path,
            asyncio.Semaphore(1),
        )
    assert not ok
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "scw" / "0060" / f"{scw}.001").exists()
    assert "Unexpected entry" in stats.failures[0]


# --- S3 listings ----------------------------------------------------------------------------------


_S3_NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def _s3_page(prefix, keys=(), prefixes=(), token=None):
    body = "".join(f"<Contents><Key>{prefix}{k}</Key></Contents>" for k in keys)
    body += "".join(
        f"<CommonPrefixes><Prefix>{prefix}{p}</Prefix></CommonPrefixes>" for p in prefixes
    )
    trunc = (
        f"<IsTruncated>true</IsTruncated><NextContinuationToken>{token}</NextContinuationToken>"
        if token
        else "<IsTruncated>false</IsTruncated>"
    )
    return f'<?xml version="1.0"?><ListBucketResult {_S3_NS}>{body}{trunc}</ListBucketResult>'


def test_is_s3_url_and_uri_conversion():
    assert s3.is_s3_url("https://nasa-heasarc.s3.amazonaws.com/integral/data/scw/")
    assert s3.is_s3_url("https://bucket.s3.us-east-1.amazonaws.com/x")
    assert not s3.is_s3_url("https://heasarc.gsfc.nasa.gov/FTP/integral/data")
    assert s3.s3_uri_to_https("s3://nasa-heasarc/integral/data/") == (
        "https://nasa-heasarc.s3.amazonaws.com/integral/data"
    )


@pytest.mark.asyncio
async def test_s3_listing_paginates_and_feeds_existing_parsers():
    prefix = "integral/data/scw/0102/"

    def handler(req):
        assert req.url.params["prefix"] == prefix and req.url.params["delimiter"] == "/"
        if "continuation-token" not in req.url.params:
            return httpx.Response(
                200, text=_s3_page(prefix, prefixes=["010200010010.001/", "rev.001/"], token="t1")
            )
        assert req.url.params["continuation-token"] == "t1"
        return httpx.Response(200, text=_s3_page(prefix, prefixes=["010200020010.001/"]))

    base = "https://nasa-heasarc.s3.amazonaws.com/integral/data"
    ids = await data.async_list_remote_scws(_client(handler), "0102", base_url=base)
    assert ids == ["010200010010", "010200020010"]


@pytest.mark.asyncio
async def test_s3_aux_download_uses_listing_and_object_urls(tmp_path):
    base = "https://nasa-heasarc.s3.amazonaws.com/integral/data"
    prefix = "integral/data/aux/adp/0102.001/"
    fetched = []

    def handler(req):
        if req.url.path == "/":
            return httpx.Response(
                200,
                text=_s3_page(prefix, keys=["attitude_historic.fits.gz", "orbit_historic.fits"]),
            )
        fetched.append(req.url.path)
        return httpx.Response(200, content=b"fits")

    stats = _stats()
    with stats.activate(), stats.phase("Aux (adp)"):
        n = await data.async_download_aux(
            _client(handler), "0102", tmp_path, asyncio.Semaphore(4), base_url=base
        )
    assert n == 2
    assert sorted(fetched) == [
        f"/{prefix}attitude_historic.fits.gz",
        f"/{prefix}orbit_historic.fits",
    ]
    assert (tmp_path / "aux" / "adp" / "0102.001" / "orbit_historic.fits").read_bytes() == b"fits"
    assert stats.files_new == 2 and stats.total_bytes == 8


@pytest.mark.asyncio
async def test_isla_not_public_is_reported_as_unavailable_not_failed(tmp_path):
    # Real ISLA error page shape: the message follows a </b> tag.
    page = (
        "<html><body><h2>SERVICE ERROR - 500</h2><ul><li><b>Context: </b>DataRetrieval</li>"
        "<li><b>Message: </b>No public products found</li></ul></body></html>"
    )
    stats = _stats()
    with stats.activate(), stats.phase("Science Windows"):
        ok = await isla.isla_download_scw(
            _client(lambda r: httpx.Response(500, text=page)),
            "010200080010",
            tmp_path,
            asyncio.Semaphore(1),
        )
    assert not ok
    assert stats.files_failed == 0 and stats.files_unavailable == 1
    assert stats.unavailable == ["010200080010 (ISLA: No public products found)"]
    assert stats.to_record()["files_unavailable"] == 1


def test_isla_error_message_extraction():
    page = "<li><b>Message: </b>Something else broke</li>"
    assert isla._service_error_message(page) == "Something else broke"
