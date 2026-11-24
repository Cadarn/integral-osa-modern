"""Tests for the data_mgr.py revolution/scw/file/calibration download subcommands and helpers.

Network access is never exercised here: httpx clients and the async download/orchestration
functions are mocked at module level, matching this project's existing testing conventions.
"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
import typer
from typer.testing import CliRunner

from integral.cli.main import app
from integral.core import data as data_mgr

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_network_mirror_probe(monkeypatch):
    """The download commands probe the mirror before starting; keep tests offline."""

    async def fake_probe(base_url, timeout_sec=5.0):
        return True, 0.01, ""

    monkeypatch.setattr(data_mgr, "probe_mirror_health", fake_probe)


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text


# --- _resolve_instruments / _validate_scope_flags -----------------------------------------


def test_resolve_instruments_defaults_to_configured_default_plus_sc_and_irem(monkeypatch):
    monkeypatch.setattr(data_mgr.config, "default_instrument", "IBIS")
    assert data_mgr._resolve_instruments("") == sorted({"ibis", "sc", "irem"})


def test_resolve_instruments_all_widens_to_every_instrument():
    assert data_mgr._resolve_instruments("all") == data_mgr.ALL_INSTRUMENTS


def test_resolve_instruments_explicit_list_and_aliases():
    assert data_mgr._resolve_instruments("jemx,omc") == ["jmx1", "omc"]


def test_validate_scope_flags_rejects_both_science_and_calib_only():
    with pytest.raises(typer.Exit):
        data_mgr._validate_scope_flags(True, True)


def test_validate_scope_flags_allows_either_or_neither():
    data_mgr._validate_scope_flags(True, False)
    data_mgr._validate_scope_flags(False, True)
    data_mgr._validate_scope_flags(False, False)


# --- async_list_remote_scws ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_remote_scws_parses_directory_listing():
    html = """
    <a href="006000010010.001/">006000010010.001/</a>
    <a href="006000020010.001/">006000020010.001/</a>
    <a href="?C=N;O=D">Name</a>
    """
    client = AsyncMock()
    client.get.return_value = _FakeResponse(text=html)

    result = await data_mgr.async_list_remote_scws(client, "0060")
    assert result == ["006000010010", "006000020010"]


@pytest.mark.asyncio
async def test_async_list_remote_scws_returns_empty_on_404():
    client = AsyncMock()
    client.get.return_value = _FakeResponse(status_code=404)

    result = await data_mgr.async_list_remote_scws(client, "9999")
    assert result == []


# --- async_download_aux ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_download_aux_downloads_files_and_skips_directories(monkeypatch, tmp_path):
    html = """
    <a href="attitude_historic.fits.gz">attitude_historic.fits.gz</a>
    <a href="subdir/">subdir/</a>
    <a href="?C=N;O=D">Name</a>
    """
    client = AsyncMock()
    client.get.return_value = _FakeResponse(text=html)

    downloaded = []

    async def fake_download_file(client, url, dest_path, progress=None, task_id=None, force=False):
        downloaded.append((url, dest_path))
        return True

    monkeypatch.setattr(data_mgr, "async_download_file", fake_download_file)

    count = await data_mgr.async_download_aux(client, "0060", tmp_path, asyncio.Semaphore(4))

    assert count == 1
    assert downloaded[0][1].name == "attitude_historic.fits.gz"
    assert downloaded[0][1].parent == tmp_path / "aux" / "adp" / "0060.001"


@pytest.mark.asyncio
async def test_async_download_aux_returns_zero_on_404(tmp_path):
    client = AsyncMock()
    client.get.return_value = _FakeResponse(status_code=404)

    count = await data_mgr.async_download_aux(client, "9999", tmp_path, asyncio.Semaphore(4))
    assert count == 0


# --- CLI wiring (download subcommands) -------------------------------------------------------


def test_download_revolution_applies_count_after_pointing_filter(monkeypatch):
    async def fake_list_remote_scws(client, rev_id, *args, **kwargs):
        assert rev_id == "0060"
        return ["006000010010", "006000010021", "006000020010"]

    calls = {}

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        calls["scw_ids"] = scw_ids
        calls["kwargs"] = kwargs

    monkeypatch.setattr(data_mgr, "async_list_remote_scws", fake_list_remote_scws)
    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)

    result = runner.invoke(
        app, ["data", "download", "revolution", "0060", "--count", "1", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert calls["scw_ids"] == ["006000010010"]
    assert calls["kwargs"]["dry_run"] is True


def test_download_revolution_explicit_range_bypasses_pointing_filter(monkeypatch):
    async def fake_list_remote_scws(client, rev_id, *args, **kwargs):
        return ["006000010010", "006000010021", "006000020010"]

    calls = {}

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        calls["scw_ids"] = scw_ids

    monkeypatch.setattr(data_mgr, "async_list_remote_scws", fake_list_remote_scws)
    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)

    result = runner.invoke(
        app,
        [
            "data",
            "download",
            "revolution",
            "0060",
            "--from",
            "006000010010",
            "--to",
            "006000010021",
        ],
    )

    assert result.exit_code == 0, result.output
    # Explicit range includes the non-pointing ID (...021), unlike the default pointing filter.
    assert calls["scw_ids"] == ["006000010010", "006000010021"]


def test_download_revolution_errors_when_no_scws_found(monkeypatch):
    async def fake_list_remote_scws(client, rev_id, *args, **kwargs):
        return []

    monkeypatch.setattr(data_mgr, "async_list_remote_scws", fake_list_remote_scws)

    result = runner.invoke(app, ["data", "download", "revolution", "9999"])
    assert result.exit_code != 0


def test_download_scw_rejects_invalid_id():
    result = runner.invoke(app, ["data", "download", "scw", "not-an-id"])
    assert result.exit_code != 0


def test_download_scw_rejects_science_and_calib_only_together():
    result = runner.invoke(
        app, ["data", "download", "scw", "006000010010", "--science-only", "--calib-only"]
    )
    assert result.exit_code != 0


def test_download_scw_passes_parsed_ids_through(monkeypatch):
    calls = {}

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        calls["scw_ids"] = scw_ids

    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)

    result = runner.invoke(
        app, ["data", "download", "scw", "006000010010,006000020010", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert calls["scw_ids"] == ["006000010010", "006000020010"]


def test_download_file_reads_ids_and_skips_comments(monkeypatch, tmp_path):
    scw_list = tmp_path / "scws.txt"
    scw_list.write_text("006000010010\n# a comment\n\n006000020010\n")

    calls = {}

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        calls["scw_ids"] = scw_ids

    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)

    result = runner.invoke(app, ["data", "download", "file", str(scw_list), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls["scw_ids"] == ["006000010010", "006000020010"]


def test_download_file_errors_on_missing_file():
    result = runner.invoke(app, ["data", "download", "file", "/nonexistent/scws.txt"])
    assert result.exit_code != 0


def test_download_calibration_dry_run_never_calls_download_calibration(monkeypatch):
    called = False

    async def fake_download_calibration(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(data_mgr, "_download_calibration", fake_download_calibration)

    result = runner.invoke(app, ["data", "download", "calibration", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert called is False


# --- Mirror selection & health check tests ------------------------------------------------


def test_resolve_mirror_base_known_and_custom():
    name, url = data_mgr.resolve_mirror_base("heasarc")
    assert name == "heasarc"
    assert url == data_mgr.KNOWN_MIRRORS["heasarc"]

    name, url = data_mgr.resolve_mirror_base("isla")
    assert name == "isla"
    assert url == data_mgr.KNOWN_MIRRORS["isla"]

    name, url = data_mgr.resolve_mirror_base("https://my-custom-mirror.org/data/")
    assert name == "custom"
    assert url == "https://my-custom-mirror.org/data"


def test_resolve_mirror_base_rejects_retired_isdc():
    with pytest.raises(data_mgr.RetiredMirrorError, match="isla"):
        data_mgr.resolve_mirror_base("isdc")


def test_resolve_source_kinds():
    heasarc = data_mgr.resolve_source("heasarc")
    assert heasarc.kind == "tree" and heasarc.support_url == heasarc.base_url

    isla = data_mgr.resolve_source("isla")
    assert isla.kind == "isla"
    assert isla.support_url == data_mgr.KNOWN_MIRRORS["heasarc"]

    custom = data_mgr.resolve_source("https://example.org/integral/")
    assert custom.kind == "tree" and custom.support_url == "https://example.org/integral"


def test_data_mirror_command_displays_and_sets(monkeypatch, tmp_path):
    monkeypatch.setattr(type(data_mgr.config), "save", lambda self: None)
    monkeypatch.setattr(data_mgr.config, "archive_mirror", "heasarc")

    result = runner.invoke(app, ["data", "mirror"])
    assert result.exit_code == 0
    assert "heasarc" in result.output
    assert "isla" in result.output

    result_set = runner.invoke(app, ["data", "mirror", "isla"])
    assert result_set.exit_code == 0
    assert data_mgr.config.archive_mirror == "isla"

    result_retired = runner.invoke(app, ["data", "mirror", "isdc"])
    assert result_retired.exit_code != 0
    assert data_mgr.config.archive_mirror == "isla"


def test_download_with_retired_mirror_exits_with_guidance():
    result = runner.invoke(
        app, ["data", "download", "scw", "006000010010", "--mirror", "isdc", "--dry-run"]
    )
    assert result.exit_code == 1
    assert "retired" in result.output


# --- Mirror fallback must not switch silently in non-interactive runs -----------------------


@pytest.mark.asyncio
async def test_check_and_select_mirror_never_switches_when_non_interactive(monkeypatch):
    async def probe(base_url, timeout_sec=5.0):
        return (False, 6.0, "ConnectTimeout") if "heasarc" in base_url else (True, 0.1, "")

    def boom(*args, **kwargs):
        raise AssertionError("must not prompt in non-interactive mode")

    monkeypatch.setattr(data_mgr, "probe_mirror_health", probe)
    monkeypatch.setattr(data_mgr.Confirm, "ask", boom)

    source = await data_mgr._check_and_select_mirror("heasarc", interactive=False)
    assert source.name == "heasarc"
    assert source.probe_latency_s is None


@pytest.mark.asyncio
async def test_check_and_select_mirror_offers_alternative_when_interactive(monkeypatch):
    async def probe(base_url, timeout_sec=5.0):
        return (False, 6.0, "ConnectTimeout") if "gsfc" in base_url else (True, 0.1, "")

    monkeypatch.setattr(data_mgr, "probe_mirror_health", probe)
    monkeypatch.setattr(data_mgr.Confirm, "ask", lambda *a, **k: True)

    # HEASARC HTTPS down -> offer the identical copy in NASA's public S3 bucket
    source = await data_mgr._check_and_select_mirror("heasarc", interactive=True)
    assert source.name == "s3"
    assert source.probe_latency_s == 0.1


# --- Source routing & timing reports ----------------------------------------------------------


def test_download_scw_with_isla_routes_source(monkeypatch):
    calls = {}

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)
    result = runner.invoke(
        app, ["data", "download", "scw", "006000040010", "--mirror", "isla", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert calls["source"].kind == "isla"
    assert calls["source"].support_url == data_mgr.KNOWN_MIRRORS["heasarc"]


def test_download_reports_timing_table_and_appends_stats_file(monkeypatch, tmp_path):
    from integral.core.transfer_stats import record_file

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        with data_mgr._phase("Science Windows"):
            record_file("new", nbytes=5_000_000, url="u1")
            record_file("cached")

    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)
    stats_file = tmp_path / "timings.jsonl"

    for _ in range(2):
        result = runner.invoke(
            app,
            ["data", "download", "scw", "006000010010", "-y", "--stats-file", str(stats_file)],
        )
        assert result.exit_code == 0, result.output

    assert "Transfer summary" in result.output
    assert "Science Windows" in result.output
    assert "5.00 MB" in result.output

    import json

    records = [json.loads(line) for line in stats_file.read_text().splitlines()]
    assert len(records) == 2
    rec = records[0]
    assert rec["source"] == "heasarc"
    assert rec["bytes"] == 5_000_000
    assert rec["files_new"] == 1 and rec["files_cached"] == 1 and rec["files_failed"] == 0
    assert rec["completed"] is True
    assert rec["revolutions"] == ["0060"]
    assert [p["name"] for p in rec["phases"]] == ["Science Windows"]


def test_download_exits_nonzero_when_files_fail(monkeypatch):
    from integral.core.transfer_stats import record_file

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        with data_mgr._phase("Science Windows"):
            record_file("failed", url="https://x/y.fits", detail="HTTP 404")

    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)
    result = runner.invoke(app, ["data", "download", "scw", "006000010010", "-y"])
    assert result.exit_code == 1
    assert "1 file(s) failed" in result.output
    assert "HTTP 404" in result.output


def test_dry_run_prints_no_timing_table(monkeypatch):
    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        pass

    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)
    result = runner.invoke(app, ["data", "download", "scw", "006000010010", "--dry-run"])
    assert result.exit_code == 0
    assert "Transfer summary" not in result.output


# --- Listing errors no longer crash ---------------------------------------------------------


@pytest.mark.asyncio
async def test_async_list_remote_scws_returns_empty_on_timeout(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("integral.core.http._sleep", no_sleep)
    client = AsyncMock()
    client.get.side_effect = httpx.ConnectTimeout("")

    assert await data_mgr.async_list_remote_scws(client, "0060") == []
    assert client.get.await_count == 3  # retried before giving up


# --- Revolution ranges & S3 source ------------------------------------------------------------


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("60", ["0060"]),
        ("0102", ["0102"]),
        ("0060-0062", ["0060", "0061", "0062"]),
        ("0071,0060-0061, 0060", ["0060", "0061", "0071"]),
    ],
)
def test_parse_revolutions(spec, expected):
    assert data_mgr.parse_revolutions(spec) == expected


@pytest.mark.parametrize("spec", ["", "abc", "0065-0060", "60-", "1-99999", "0001-0600"])
def test_parse_revolutions_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        data_mgr.parse_revolutions(spec)


def test_download_revolution_range_lists_each_revolution(monkeypatch):
    listed = []

    async def fake_list_remote_scws(client, rev_id, *args, **kwargs):
        listed.append(rev_id)
        if rev_id == "0062":
            return []  # missing revolution is skipped with a warning, not fatal
        return [f"{rev_id}00010010", f"{rev_id}00010021", f"{rev_id}00020010"]

    calls = {}

    async def fake_run_data_download(client, dest_base, scw_ids, **kwargs):
        calls["scw_ids"] = scw_ids

    monkeypatch.setattr(data_mgr, "async_list_remote_scws", fake_list_remote_scws)
    monkeypatch.setattr(data_mgr, "_run_data_download", fake_run_data_download)

    result = runner.invoke(
        app, ["data", "download", "revolution", "0060-0062", "--count", "1", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert sorted(listed) == ["0060", "0061", "0062"]
    assert calls["scw_ids"] == ["006000010010", "006100010010"]
    assert "0062" in result.output


def test_download_revolution_range_rejects_from_to():
    result = runner.invoke(
        app, ["data", "download", "revolution", "0060-0061", "--from", "006000010010"]
    )
    assert result.exit_code == 1


def test_resolve_s3_sources():
    s3 = data_mgr.resolve_source("s3")
    assert s3.kind == "tree"
    assert s3.base_url == "https://nasa-heasarc.s3.amazonaws.com/integral/data"
    assert s3.support_url == s3.base_url

    custom = data_mgr.resolve_source("s3://my-public-bucket/integral/data/")
    assert custom.base_url == "https://my-public-bucket.s3.amazonaws.com/integral/data"
