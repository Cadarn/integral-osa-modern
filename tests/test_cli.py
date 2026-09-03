"""Smoke tests for the top-level `integral` CLI wiring."""

from typer.testing import CliRunner

from integral_cli.main import app

runner = CliRunner()


def test_help_lists_analyse_not_analyze():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "analyse" in result.output
    assert "analyze" not in result.output


def test_old_analyze_spelling_is_gone():
    result = runner.invoke(app, ["analyze", "--help"])
    assert result.exit_code != 0


def test_analyse_subcommands_are_registered():
    result = runner.invoke(app, ["analyse", "--help"])
    assert result.exit_code == 0
    for instrument in ["ibis", "jemx", "omc", "spi"]:
        assert instrument in result.output


def test_analyse_ibis_help_shows_detector_and_advanced_flags():
    result = runner.invoke(app, ["analyse", "ibis", "--help"])
    assert result.exit_code == 0
    assert "--isgri" in result.output
    assert "--picsit" in result.output
    assert "--compton" in result.output
    assert "--og" in result.output
    assert "--clean-mode" in result.output
    assert "--bright-threshold" in result.output
