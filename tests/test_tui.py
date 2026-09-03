from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Checkbox, DataTable, Input

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
        assert app.query_one("#detector_mode") is not None
        assert app.query_one("#og_name") is not None
        assert app.query_one("#energy_preset") is not None
        assert app.query_one("#custom_bands") is not None
        assert app.query_one("#product_level") is not None
        assert app.query_one("#workdir") is not None
        assert app.query_one("#clean_mode") is not None
        assert app.query_one("#bright_threshold") is not None
        assert app.query_one("#clean_toggle") is not None
        assert app.query_one("#status_label") is not None
        assert app.query_one("#progress_bar") is not None
        assert app.query_one("#result_banner") is not None
        assert app.query_one("#run") is not None
        assert app.query_one("#log") is not None
        assert app.query_one("#sources_table") is not None
        assert app.query_one("#saved_log_text") is not None


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
    async with app.run_test(size=(120, 50)) as pilot:
        with (
            patch.object(app, "_log") as mock_log,
            patch("integral_cli.tui.subprocess.Popen") as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        mock_popen.assert_not_called()
        messages = [call.args[0] for call in mock_log.call_args_list]
        assert any("ScW input is required" in m for m in messages)
        assert "error" in app.query_one("#result_banner").classes


@pytest.mark.asyncio
async def test_run_streams_subprocess_output_updates_stage_and_reports_success():
    app = IntegralTUI()
    async with app.run_test(size=(120, 50)) as pilot:
        app.query_one("#scw_input", Input).value = "006000010010"
        fake_proc = _FakeProcess(
            [
                "og_create started",
                "Task ibis_science_analysis started",
                "COR step running",
                "IMA step running",
                "Pipeline completed successfully",
            ],
            returncode=0,
        )

        with (
            patch.object(app, "_log") as mock_log,
            patch.object(app, "_populate_sources", return_value=3) as mock_populate,
            patch("integral_cli.tui.subprocess.Popen", return_value=fake_proc) as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        argv = mock_popen.call_args.args[0]
        assert "analyse" in argv
        assert "ibis" in argv
        assert "006000010010" in argv
        assert "--yes" in argv
        assert "--og" in argv
        assert "obs_ibis" in argv
        assert "--isgri" in argv
        assert "--no-picsit" in argv
        assert "--no-compton" in argv
        assert "--bands" in argv
        assert "18-60" in argv
        assert "--end-level" in argv
        assert "IMA2" in argv

        messages = [call.args[0] for call in mock_log.call_args_list]
        assert any("COR step running" in m for m in messages)
        assert any("completed successfully" in m for m in messages)
        mock_populate.assert_called_once()
        assert "success" in app.query_one("#result_banner").classes
        assert "3 point source(s)" in str(app.query_one("#result_banner").render())


@pytest.mark.asyncio
async def test_run_reports_failure_on_nonzero_exit():
    app = IntegralTUI()
    async with app.run_test(size=(120, 50)) as pilot:
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
        assert "error" in app.query_one("#result_banner").classes


@pytest.mark.asyncio
async def test_detector_modes_and_advanced_settings_passed_to_argv():
    app = IntegralTUI()
    async with app.run_test(size=(120, 50)) as pilot:
        app.query_one("#scw_input", Input).value = "006000010010"
        app.query_one("#workdir", Input).value = "/tmp/custom-work"
        app.query_one("#og_name", Input).value = "obs_myrun"
        app.query_one("#detector_mode").value = "both"
        app.query_one("#clean_mode").value = "0"
        app.query_one("#bright_threshold", Input).value = "0.005"
        app.query_one("#clean_toggle", Checkbox).value = False
        fake_proc = _FakeProcess([], returncode=0)

        with (
            patch.object(app, "_log"),
            patch.object(app, "_populate_sources", return_value=0),
            patch("integral_cli.tui.subprocess.Popen", return_value=fake_proc) as mock_popen,
        ):
            await pilot.click("#run")
            await app.workers.wait_for_complete()

        argv = mock_popen.call_args.args[0]
        assert "--workdir" in argv
        assert "/tmp/custom-work" in argv
        assert "--og" in argv
        assert "obs_myrun" in argv
        assert "--isgri" in argv
        assert "--picsit" in argv
        assert "--no-compton" in argv
        assert "--clean-mode" in argv
        assert "0" in argv
        assert "--bright-threshold" in argv
        assert "0.005" in argv
        assert "--no-clean" in argv


@pytest.mark.asyncio
async def test_saved_log_file_loads(tmp_path: Path):
    from textual.widgets import TabbedContent

    log_file = tmp_path / "commonlog.txt"
    log_file.write_text("INTEGRAL Science Analysis commonlog entry 1\nentry 2\n")

    app = IntegralTUI()
    async with app.run_test(size=(120, 50)) as pilot:
        tabs = app.query_one("#tabs", TabbedContent)
        tabs.active = "tab_saved_log"
        await pilot.pause()
        app._load_saved_log(tmp_path, "obs_ibis")
        await pilot.pause()
        log_widget = app.query_one("#saved_log_text")
        lines = [str(line.text) for line in log_widget.lines]
        assert any("INTEGRAL Science Analysis commonlog entry 1" in l for l in lines)
        assert any("entry 2" in l for l in lines)


@pytest.mark.asyncio
async def test_populate_sources_parses_fits(tmp_path: Path):
    import numpy as np
    from astropy.io import fits

    # Create dummy isgri_mosa_res.fits
    obs_dir = tmp_path / "obs" / "obs_custom"
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
        count = app._populate_sources(tmp_path, "obs_custom")
        assert count == 2
        table = app.query_one("#sources_table")
        assert table.row_count == 2
        row_0 = table.get_row_at(0)
        assert row_0[0] == "TEST_SRC_1"
        assert row_0[3] == "15.5"
        assert "42.00 ± 1.20" in row_0[4]


@pytest.mark.asyncio
async def test_source_detail_modal_opens_on_row_selected():
    from integral_cli.tui import SourceDetailModal

    app = IntegralTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#sources_table", DataTable)
        table.add_row("Cyg X-1", "299.59", "35.20", "88.4", "120.5 ± 2.1")
        table.action_select_cursor()
        await pilot.pause()

        assert len(app.screen_stack) == 2
        assert isinstance(app.screen, SourceDetailModal)
        assert app.screen.source_row[0] == "Cyg X-1"

        modal = app.screen
        modal.dismiss()
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_browse_scw_modal_applies_selection(tmp_path: Path):
    from integral_cli.tui import ScwBrowseModal

    # Setup fake local archive
    scw_dir = tmp_path / "scw" / "0042" / "004200010010.001"
    scw_dir.mkdir(parents=True)

    app = IntegralTUI()
    with patch.dict("os.environ", {"REP_BASE_PROD": str(tmp_path)}):
        async with app.run_test(size=(120, 40)) as pilot:
            btn = app.query_one("#btn_browse_scw")
            btn.scroll_visible()
            await pilot.pause()
            await pilot.click("#btn_browse_scw")
            await pilot.pause()

            assert len(app.screen_stack) == 2
            assert isinstance(app.screen, ScwBrowseModal)

            await pilot.click("#browse_apply")
            await pilot.pause()

            scw_val = app.query_one("#scw_input", Input).value
            assert "004200010010" in scw_val


@pytest.mark.asyncio
async def test_dynamic_instrument_switching():
    from textual.widgets import Select, Static

    app = IntegralTUI()
    async with app.run_test(size=(140, 42)) as pilot:
        inst_sel = app.query_one("#instrument", Select)
        energy_sel = app.query_one("#energy_preset", Select)
        prod_sel = app.query_one("#product_level", Select)
        det_box = app.query_one("#detector_mode_box")
        jemx_box = app.query_one("#jemx_unit_box")
        cleaning_box = app.query_one("#ibis_cleaning_box")
        og_input = app.query_one("#og_name", Input)
        energy_label = app.query_one("#energy_label", Static)

        # 1. IBIS defaults
        assert not det_box.has_class("hidden")
        assert jemx_box.has_class("hidden")
        assert not cleaning_box.has_class("hidden")
        assert og_input.value == "obs_ibis"
        assert energy_sel.value == "18-60"
        assert prod_sel.value == "IMA2"

        # 2. Switch to JEM-X
        inst_sel.value = "jemx"
        await pilot.pause()
        assert det_box.has_class("hidden")
        assert not jemx_box.has_class("hidden")
        assert cleaning_box.has_class("hidden")
        assert og_input.value == "obs_jemx"
        assert energy_sel.value == "3-10"
        assert prod_sel.value == "IMA2"

        # 3. Switch to OMC
        inst_sel.value = "omc"
        await pilot.pause()
        assert det_box.has_class("hidden")
        assert jemx_box.has_class("hidden")
        assert cleaning_box.has_class("hidden")
        assert og_input.value == "obs_omc"
        assert "Optical Filter" in str(energy_label.render())
        assert energy_sel.value == "V-filter"
        assert prod_sel.value == "IMA"

        # 4. Switch to SPI
        inst_sel.value = "spi"
        await pilot.pause()
        assert det_box.has_class("hidden")
        assert jemx_box.has_class("hidden")
        assert cleaning_box.has_class("hidden")
        assert og_input.value == "obs_spi"
        assert energy_sel.value == "20-40"
        assert prod_sel.value == "SPIROS"
