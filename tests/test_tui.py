"""Tests for the `integral tui` Textual app, using Textual's headless test harness."""

from unittest.mock import patch

import pytest
from textual.widgets import Input

from integral_cli.tui import IntegralTUI


class _FakeProcess:
    """Stand-in for subprocess.Popen so tests never launch a real `integral analyse` run."""

    def __init__(self, lines: list[str], returncode: int = 0):
        self.stdout = iter(lines)
        self._returncode = returncode

    def wait(self) -> int:
        return self._returncode


@pytest.mark.asyncio
async def test_composes_expected_widgets():
    app = IntegralTUI()
    async with app.run_test():
        assert app.query_one("#instrument") is not None
        assert app.query_one("#scw_input") is not None
        assert app.query_one("#workdir") is not None
        assert app.query_one("#run") is not None
        assert app.query_one("#log") is not None


@pytest.mark.asyncio
async def test_run_without_scw_input_logs_error_and_never_launches_subprocess():
    app = IntegralTUI()
    async with app.run_test() as pilot:
        with (
            patch.object(app, "_log") as mock_log,
            patch("integral_cli.tui.subprocess.Popen") as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        mock_popen.assert_not_called()
        messages = [call.args[0] for call in mock_log.call_args_list]
        assert any("ScW input is required" in m for m in messages)


@pytest.mark.asyncio
async def test_run_streams_subprocess_output_and_reports_success():
    app = IntegralTUI()
    async with app.run_test() as pilot:
        app.query_one("#scw_input", Input).value = "006000010010"
        fake_proc = _FakeProcess(["line one", "line two"], returncode=0)

        with (
            patch.object(app, "_log") as mock_log,
            patch("integral_cli.tui.subprocess.Popen", return_value=fake_proc) as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        argv = mock_popen.call_args.args[0]
        assert "analyse" in argv
        assert "ibis" in argv  # default Select value
        assert "006000010010" in argv

        messages = [call.args[0] for call in mock_log.call_args_list]
        assert any("line one" in m for m in messages)
        assert any("line two" in m for m in messages)
        assert any("completed successfully" in m for m in messages)


@pytest.mark.asyncio
async def test_run_reports_failure_on_nonzero_exit():
    app = IntegralTUI()
    async with app.run_test() as pilot:
        app.query_one("#scw_input", Input).value = "006000010010"
        fake_proc = _FakeProcess(["something went wrong"], returncode=1)

        with (
            patch.object(app, "_log") as mock_log,
            patch("integral_cli.tui.subprocess.Popen", return_value=fake_proc),
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        messages = [call.args[0] for call in mock_log.call_args_list]
        assert any("Run failed" in m and "1" in m for m in messages)


@pytest.mark.asyncio
async def test_workdir_passed_through_when_provided():
    app = IntegralTUI()
    async with app.run_test() as pilot:
        app.query_one("#scw_input", Input).value = "006000010010"
        app.query_one("#workdir", Input).value = "/tmp/my-workdir"
        fake_proc = _FakeProcess([], returncode=0)

        with (
            patch.object(app, "_log"),
            patch("integral_cli.tui.subprocess.Popen", return_value=fake_proc) as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        argv = mock_popen.call_args.args[0]
        assert "--workdir" in argv
        assert "/tmp/my-workdir" in argv
