"""Tests for IntegralConfig persistence, including its error-handling paths."""

from pathlib import Path

import pytest

import integral_cli.config as config_module
from integral_cli.config import IntegralConfig


def test_save_then_load_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / ".integralrc.json"
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    original = IntegralConfig(docker_image="integralsw/osa:test-tag")
    original.save()

    loaded = IntegralConfig.load()
    assert loaded.docker_image == "integralsw/osa:test-tag"


def test_load_falls_back_to_defaults_on_corrupt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    config_file = tmp_path / ".integralrc.json"
    config_file.write_text("not valid json")
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    loaded = IntegralConfig.load()

    assert loaded.docker_image == "integralsw/osa:11-native-arm64"
    assert "Warning" in capsys.readouterr().err


def test_save_warns_instead_of_raising_when_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    # Parent directory doesn't exist, so the write will fail.
    config_file = tmp_path / "nonexistent-dir" / ".integralrc.json"
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    IntegralConfig().save()  # must not raise

    assert "Warning" in capsys.readouterr().err


def test_rep_base_prod_prefers_env_var_over_data_dir(monkeypatch: pytest.MonkeyPatch):
    cfg = IntegralConfig(data_dir="/configured/path")
    monkeypatch.setenv("REP_BASE_PROD", "/env/override")
    assert cfg.rep_base_prod == Path("/env/override")

    monkeypatch.delenv("REP_BASE_PROD")
    assert cfg.rep_base_prod == Path("/configured/path")


def test_host_arch_maps_arm_variants(monkeypatch: pytest.MonkeyPatch):
    cfg = IntegralConfig()
    monkeypatch.setattr(config_module.platform, "machine", lambda: "arm64")
    assert cfg.host_arch == "arm64"
    monkeypatch.setattr(config_module.platform, "machine", lambda: "x86_64")
    assert cfg.host_arch == "x86_64"
