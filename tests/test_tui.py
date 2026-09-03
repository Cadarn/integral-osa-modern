from pathlib import Path
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
        assert app.query_one("#energy_preset") is not None
        assert app.query_one("#custom_bands") is not None
        assert app.query_one("#product_level") is not None
        assert app.query_one("#workdir") is not None
        assert app.query_one("#run") is not None
        assert app.query_one("#log") is not None
        assert app.query_one("#sources_table") is not None


@pytest.mark.asyncio
async def test_energy_preset_custom_enables_input():
    app = IntegralTUI()
    async with app.run_test() as pilot:
        custom_input = app.query_one("#custom_bands", Input)
        assert custom_input.disabled is True

        preset_select = app.query_one("#energy_preset")
        preset_select.value = "custom"
        await pilot.pause()
        assert custom_input.disabled is False


@pytest.mark.asyncio
async def test_run_without_scw_input_logs_error_and_never_launches_subprocess():
    app = IntegralTUI()
    async with app.run_test(size=(100, 40)) as pilot:
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
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#scw_input", Input).value = "006000010010"
        fake_proc = _FakeProcess(["line one", "line two"], returncode=0)

        with (
            patch.object(app, "_log") as mock_log,
            patch.object(app, "_populate_sources") as mock_populate,
            patch("integral_cli.tui.subprocess.Popen", return_value=fake_proc) as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        argv = mock_popen.call_args.args[0]
        assert "analyse" in argv
        assert "ibis" in argv  # default Select value
        assert "006000010010" in argv
        assert "--yes" in argv
        assert "--bands" in argv
        assert "18-60" in argv
        assert "--end-level" in argv
        assert "IMA2" in argv

        messages = [call.args[0] for call in mock_log.call_args_list]
        assert any("line one" in m for m in messages)
        assert any("line two" in m for m in messages)
        assert any("completed successfully" in m for m in messages)
        mock_populate.assert_called_once()


@pytest.mark.asyncio
async def test_run_reports_failure_on_nonzero_exit():
    app = IntegralTUI()
    async with app.run_test(size=(100, 40)) as pilot:
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
async def test_workdir_and_custom_bands_passed_through_when_provided():
    app = IntegralTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#scw_input", Input).value = "006000010010"
        app.query_one("#workdir", Input).value = "/tmp/my-workdir"
        app.query_one("#energy_preset").value = "custom"
        app.query_one("#custom_bands", Input).value = "20-40, 40-100"
        app.query_one("#product_level").value = "SPE"
        fake_proc = _FakeProcess([], returncode=0)

        with (
            patch.object(app, "_log"),
            patch.object(app, "_populate_sources"),
            patch("integral_cli.tui.subprocess.Popen", return_value=fake_proc) as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        argv = mock_popen.call_args.args[0]
        assert "--workdir" in argv
        assert "/tmp/my-workdir" in argv
        assert "--bands" in argv
        assert "20-40, 40-100" in argv
        assert "--end-level" in argv
        assert "SPE" in argv


@pytest.mark.asyncio
async def test_populate_sources_parses_fits(tmp_path: Path):
    import numpy as np
    from astropy.io import fits

    # Create dummy isgri_mosa_res.fits
    obs_dir = tmp_path / "obs" / "obs_ibis"
    obs_dir.mkdir(parents=True)
    fits_file = obs_dir / "isgri_mosa_res.fits"

    col_name = fits.Column(name="NAME", format="20A", array=np.array(["TEST_SRC_1", "TEST_SRC_2"]))
    col_ra = fits.Column(name="RA_OBJ", format="E", array=np.array([123.456, 234.567]))
    col_dec = fits.Column(name="DEC_OBJ", format="E", array=np.array([-45.678, 56.789]))
    col_sig = fits.Column(name="DETSIG", format="E", array=np.array([15.5, 7.2]))
    col_flux = fits.Column(name="FLUX", format="E", array=np.array([42.0, 10.5]))
    col_err = fits.Column(name="FLUX_ERR", format="E", array=np.array([1.2, 0.8]))

    table_hdu = fits.BinTableHDU.from_columns(
        [col_name, col_ra, col_dec, col_sig, col_flux, col_err], name="ISGR-MOSA-RES"
    )
    hdul = fits.HDUList([fits.PrimaryHDU(), table_hdu])
    hdul.writeto(fits_file)

    app = IntegralTUI()
    async with app.run_test(size=(100, 40)):
        app._populate_sources(tmp_path, "ibis")
        table = app.query_one("#sources_table")
        assert table.row_count == 2
        row_0 = table.get_row_at(0)
        assert row_0[0] == "TEST_SRC_1"
        assert row_0[3] == "15.5"
        assert "42.00 ± 1.20" in row_0[4]
