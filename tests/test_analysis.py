"""Tests for the ScW-resolution helper shared by all `integral analyse` commands."""

from pathlib import Path

import pytest
import typer

from integral_cli.analysis import _resolve_scw_ids


def test_bare_scw_id_passthrough():
    assert _resolve_scw_ids("006000010010") == ["006000010010"]


def test_comma_separated_list():
    assert _resolve_scw_ids("006000010010,006000020010") == [
        "006000010010",
        "006000020010",
    ]


def test_comma_separated_list_strips_whitespace():
    assert _resolve_scw_ids("006000010010, 006000020010 ,") == [
        "006000010010",
        "006000020010",
    ]


def test_file_path_skips_blank_lines_and_comments(tmp_path: Path):
    scw_list = tmp_path / "scw.list"
    scw_list.write_text("006000010010.001\n# a comment\n\n006000020010.001\n")
    assert _resolve_scw_ids(str(scw_list)) == [
        "006000010010.001",
        "006000020010.001",
    ]


def test_revolution_spec_selects_pointing_scws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scw_dir = tmp_path / "scw" / "0060"
    scw_dir.mkdir(parents=True)
    for scw_id in ["006000010010", "006000020010", "006000030030"]:
        (scw_dir / f"{scw_id}.001").mkdir()
    monkeypatch.setenv("REP_BASE_PROD", str(tmp_path))

    # Only the two ScWs ending in "0010" are pointing ScWs and should be selected.
    assert _resolve_scw_ids("rev:0060") == ["006000010010", "006000020010"]


def test_revolution_spec_respects_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scw_dir = tmp_path / "scw" / "0060"
    scw_dir.mkdir(parents=True)
    for scw_id in ["006000010010", "006000020010", "006000030010"]:
        (scw_dir / f"{scw_id}.001").mkdir()
    monkeypatch.setenv("REP_BASE_PROD", str(tmp_path))

    assert _resolve_scw_ids("rev:0060:2") == ["006000010010", "006000020010"]


def test_revolution_spec_falls_back_when_no_pointing_scws(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scw_dir = tmp_path / "scw" / "0060"
    scw_dir.mkdir(parents=True)
    # None of these end in "0010", so the fallback (any directory with a long enough name) applies.
    for scw_id in ["006000010020", "006000020030"]:
        (scw_dir / f"{scw_id}.001").mkdir()
    monkeypatch.setenv("REP_BASE_PROD", str(tmp_path))

    assert _resolve_scw_ids("rev:0060") == ["006000010020", "006000020030"]


def test_revolution_spec_missing_directory_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REP_BASE_PROD", str(tmp_path))
    with pytest.raises(typer.Exit):
        _resolve_scw_ids("rev:0099")
