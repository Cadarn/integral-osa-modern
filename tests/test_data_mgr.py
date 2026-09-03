"""Tests for the data_mgr.py revolution/scw/file/calibration download subcommands and helpers.

Network access is never exercised here: httpx clients and the async download/orchestration
functions are mocked at module level, matching this project's existing testing conventions.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
import typer
from typer.testing import CliRunner

from integral_cli import data_mgr
from integral_cli.main import app

runner = CliRunner()


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
    async def fake_list_remote_scws(client, rev_id):
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
    async def fake_list_remote_scws(client, rev_id):
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
    async def fake_list_remote_scws(client, rev_id):
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
