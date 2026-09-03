"""
Textual TUI for launching INTEGRAL science analysis runs without memorising CLI flags.

Wraps the `integral analyse` CLI as a subprocess (rather than calling analysis.py's
functions in-process) so its output streams live into the log widget with zero
changes to the existing, already-tested analysis/docker execution path.
"""

import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    SelectionList,
    Sparkline,
    Static,
    TabbedContent,
    TabPane,
)

from integral_cli.config import config


class ScwBrowseModal(ModalScreen[list[str] | None]):
    """Modal allowing visual discovery and multi-selection of Science Windows from local archive."""

    DEFAULT_CSS = """
    ScwBrowseModal {
        align: center middle;
    }
    #browse_modal_box {
        width: 72;
        height: 26;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #browse_title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
        border-bottom: solid $accent;
    }
    SelectionList {
        height: 1fr;
        margin: 1 0;
        border: solid $primary;
    }
    #browse_buttons {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, scw_options: list[tuple[str, str]]) -> None:
        super().__init__()
        self.scw_options = scw_options

    def compose(self) -> ComposeResult:
        with Vertical(id="browse_modal_box"):
            yield Label("Browse Archive: Select Science Windows", id="browse_title")
            yield SelectionList[str](
                *[(label, scw_id, True) for label, scw_id in self.scw_options],
                id="scw_selection_list",
            )
            with Horizontal(id="browse_buttons"):
                yield Button("Apply Selection", id="browse_apply", variant="success")
                yield Button("Cancel", id="browse_cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "browse_apply":
            selected = list(self.query_one("#scw_selection_list", SelectionList).selected)
            self.dismiss(selected)
        elif event.button.id == "browse_cancel":
            self.dismiss(None)


class SourceDetailModal(ModalScreen[None]):
    """Modal displaying deep-dive astronomical telemetry for a selected detected source."""

    DEFAULT_CSS = """
    SourceDetailModal {
        align: center middle;
    }
    #source_modal_box {
        width: 64;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #modal_title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
        border-bottom: solid $accent;
    }
    .modal-row {
        height: auto;
        margin-bottom: 1;
    }
    .modal-label {
        width: 22;
        text-style: bold;
    }
    .modal-val {
        width: 1fr;
        color: $text;
    }
    #modal_close {
        margin-top: 1;
        width: 100%;
    }
    """

    def __init__(self, source_row: list[str]) -> None:
        super().__init__()
        self.source_row = source_row

    def compose(self) -> ComposeResult:
        name, ra, dec, snr, flux = (
            self.source_row
            if len(self.source_row) >= 5
            else ["Unknown", "N/A", "N/A", "N/A", "N/A"]
        )
        with Vertical(id="source_modal_box"):
            yield Label(f"Source Telemetry: {name}", id="modal_title")
            with Horizontal(classes="modal-row"):
                yield Label("Source Name:", classes="modal-label")
                yield Label(f"[bold]{name}[/bold]", classes="modal-val")
            with Horizontal(classes="modal-row"):
                yield Label("Right Ascension (RA):", classes="modal-label")
                yield Label(f"{ra}°", classes="modal-val")
            with Horizontal(classes="modal-row"):
                yield Label("Declination (Dec):", classes="modal-label")
                yield Label(f"{dec}°", classes="modal-val")
            with Horizontal(classes="modal-row"):
                yield Label("Detection Significance:", classes="modal-label")
                yield Label(f"[bold green]{snr} σ[/bold green]", classes="modal-val")
            with Horizontal(classes="modal-row"):
                yield Label("Mosaic Flux Rate:", classes="modal-label")
                yield Label(f"{flux} cts/s", classes="modal-val")
            yield Button("Close Details (Esc)", id="modal_close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modal_close":
            self.dismiss()


INSTRUMENTS = [
    ("IBIS (Imager)", "ibis"),
    ("SPI (Spectrometer)", "spi"),
    ("JEM-X (X-ray Monitor)", "jemx"),
    ("OMC (Optical Monitoring Camera)", "omc"),
]

TIMING_MODES = [
    ("Standard Lightcurve (time bin >= 0.1s)", "standard"),
    ("Fast / Pulsar Timing (PIF-based mode)", "pif"),
]


INSTRUMENT_ENERGY_PRESETS = {
    "ibis": [
        ("Standard: 18-60 keV (Single band)", "18-60"),
        ("Hard X-ray: 20-40, 40-100 keV (Two bands)", "20-40, 40-100"),
        ("Broadband: 20-100 keV (Single band)", "20-100"),
        ("Custom energy band...", "custom"),
    ],
    "jemx": [
        ("Standard: 3-10 keV (Single band)", "3-10"),
        ("Medium: 10-25 keV (Single band)", "10-25"),
        ("Full range: 3-25 keV (Two bands: 3-10, 10-25)", "3-10, 10-25"),
        ("Broadband: 3-35 keV", "3-35"),
        ("Custom energy band...", "custom"),
    ],
    "omc": [
        ("V-filter standard (500-600 nm)", "V-filter"),
        ("Custom optical filter / window...", "custom"),
    ],
    "spi": [
        ("Standard continuum: 20-40 keV", "20-40"),
        ("Positron line: 505-517 keV", "505-517"),
        ("High-energy: 40-1000 keV", "40-1000"),
        ("Custom energy band...", "custom"),
    ],
}

INSTRUMENT_PRODUCT_LEVELS = {
    "ibis": [
        ("Sky Images & Mosaic (IMA2) [Default]", "IMA2"),
        ("Single Pointing Sky Images (IMA)", "IMA"),
        ("Pipeline up to Spectra (SPE)", "SPE"),
        ("Pipeline up to Lightcurves (LCR)", "LCR"),
    ],
    "jemx": [
        ("Sky Images & Mosaic (IMA2) [Default]", "IMA2"),
        ("Single Pointing Sky Images (IMA)", "IMA"),
        ("Pipeline up to Spectra (SPE)", "SPE"),
        ("Pipeline up to Lightcurves (LCR)", "LCR"),
    ],
    "omc": [
        ("Photometry & Source Extraction (IMA) [Default]", "IMA"),
        ("Flux Correction only (COR)", "COR"),
    ],
    "spi": [
        ("SPIROS Image & Spectral Deconvolution [Default]", "SPIROS"),
        ("SPIMODFIT Maximum Likelihood Fitting", "SPIMODFIT"),
        ("Background Modeling (BKG)", "BKG"),
        ("Pointing & Energy Correction (POINT)", "POINT"),
    ],
}

DETECTOR_MODES = [
    ("ISGRI only (15-1000 keV) [Default]", "isgri"),
    ("PiCSIT only (175 keV - 10 MeV)", "picsit"),
    ("Both ISGRI & PiCSIT", "both"),
    ("Compton Mode (Coincidence)", "compton"),
]

JEMX_UNITS = [
    ("JEM-X Unit 1 (Default)", "1"),
    ("JEM-X Unit 2", "2"),
]

CLEAN_MODES = [
    ("Standard ghost cleaning (OBS1_CleanMode=1) [Default]", "1"),
    ("No ghost cleaning (OBS1_CleanMode=0)", "0"),
]

INSTRUMENT_STAGE_PATTERNS = {
    "ibis": [
        (r"og_create", "Initialising Observation Group", 10),
        (r"Task ibis_science_analysis started", "Starting Pipeline", 20),
        (r"COR", "COR: Energy Correction", 30),
        (r"GTI", "GTI: Good Time Intervals", 40),
        (r"DEAD", "DEAD: Deadtime Calculation", 50),
        (r"BIN_I", "BIN_I: Shadowgram Binning", 60),
        (r"BKG_I", "BKG_I: Background Subtraction", 70),
        (r"CAT_I", "CAT_I: Catalog Matching", 80),
        (r"IMA", "IMA: Image Reconstruction", 90),
        (r"IMA2|mosaicing", "IMA2: Sky Mosaicing", 95),
        (r"Pipeline completed", "Complete", 100),
    ],
    "jemx": [
        (r"og_create", "Initialising JEM-X Group", 10),
        (r"jemx_science_analysis started", "Starting Pipeline", 20),
        (r"COR", "COR: Calibration & Correction", 35),
        (r"DEAD", "DEAD: Deadtime Calculation", 50),
        (r"BIN_I", "BIN_I: Shadowgram Binning", 65),
        (r"IMA", "IMA: Sky Reconstruction", 80),
        (r"IMA2", "IMA2: Mosaic Formation", 95),
        (r"Pipeline completed", "Complete", 100),
    ],
    "omc": [
        (r"og_create", "Initialising OMC Group", 15),
        (r"omc_science_analysis", "Running OMC Pipeline", 35),
        (r"COR", "COR: Dark Current & Flat Fielding", 60),
        (r"IMA", "IMA: Photometry Extraction", 90),
        (r"Pipeline completed", "Complete", 100),
    ],
    "spi": [
        (r"og_create", "Initialising SPI Group", 15),
        (r"spi_science_analysis", "Running SPI Pipeline", 30),
        (r"COR", "COR: Energy Calibration", 45),
        (r"DEAD|POINT", "DEAD/POINT: Gaps & Pointing", 60),
        (r"BKG", "BKG: Background Model Fitting", 75),
        (r"SPIROS|SPIMODFIT", "SPIROS: Deconvolution / Fitting", 90),
        (r"Pipeline completed", "Complete", 100),
    ],
}


class IntegralTUI(App):
    """Enhanced form-based launcher for `integral analyse` with progress tracking, log viewing, and product inspection."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main_layout {
        height: 1fr;
    }
    #sidebar {
        width: 82;
        min-width: 74;
        max-width: 90;
        border-right: heavy $accent;
        padding: 0 1;
    }
    #form_columns {
        height: auto;
    }
    .form-column {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    #sidebar Input, #sidebar Select {
        margin-bottom: 0;
    }
    .field-label {
        margin-top: 1;
        text-style: bold;
    }
    .hidden {
        display: none;
    }
    #custom_bands_box, #detector_mode_box, #jemx_unit_box, #energy_box, #timing_settings_box {
        height: auto;
    }
    .scw-input-row {
        height: auto;
        margin-top: 0;
        margin-bottom: 0;
    }
    #scw_input {
        width: 1fr;
        margin-bottom: 0;
    }
    #btn_browse_scw {
        width: auto;
        min-width: 10;
        margin-left: 1;
    }
    #progress_box {
        height: auto;
        margin-bottom: 1;
        padding: 1;
        border: round $secondary;
        background: $surface;
    }
    #progress_row {
        height: auto;
    }
    #status_label {
        width: 1fr;
        text-style: bold;
    }
    #elapsed_time {
        width: auto;
        color: $accent;
        text-style: bold;
    }
    #spark_box {
        height: auto;
        margin-top: 1;
    }
    .metrics-sublabel {
        color: $text-muted;
    }
    #spark_activity {
        width: 100%;
        height: 2;
        color: $accent;
    }
    #result_banner {
        height: auto;
        padding: 1;
        margin-top: 1;
        text-align: center;
        text-style: bold;
        display: none;
    }
    #result_banner.success {
        display: block;
        background: $success;
        color: $text;
    }
    #result_banner.error {
        display: block;
        background: $error;
        color: $text;
    }
    #buttons {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    #right_panel {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }
    TabbedContent {
        height: 1fr;
    }
    ContentSwitcher {
        height: 1fr;
    }
    TabPane {
        height: 100%;
        padding: 0;
    }
    RichLog {
        border: round $primary;
        height: 100%;
    }
    DataTable {
        height: 100%;
    }
    Collapsible {
        margin-top: 1;
        margin-bottom: 1;
    }
    """

    BINDINGS: ClassVar = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main_layout"):
            with VerticalScroll(id="sidebar"):
                yield Static(
                    f"Archive: {config.rep_base_prod}\nImage: {config.docker_image}",
                    id="header_info",
                )

                with Horizontal(id="buttons"):
                    yield Button("Run Analysis", id="run", variant="success")
                    yield Button("Quit", id="quit", variant="error")

                with Horizontal(id="form_columns"):
                    with Vertical(classes="form-column"):
                        yield Static("Instrument:", classes="field-label")
                        yield Select(INSTRUMENTS, value="ibis", id="instrument")

                        yield Static("Science Windows:", classes="field-label")
                        with Horizontal(classes="scw-input-row"):
                            yield Input(
                                placeholder="e.g. rev:0060:5, 006000010010, scw.list",
                                id="scw_input",
                            )
                            yield Button("Browse", id="btn_browse_scw", variant="default")

                        with Vertical(id="detector_mode_box"):
                            yield Static("Detector Mode (IBIS):", classes="field-label")
                            yield Select(DETECTOR_MODES, value="isgri", id="detector_mode")

                        with Vertical(id="jemx_unit_box", classes="hidden"):
                            yield Static("JEM-X Sensor Unit:", classes="field-label")
                            yield Select(JEMX_UNITS, value="1", id="jemx_unit")

                        yield Static("Observation Group Name:", classes="field-label")
                        yield Input(placeholder="obs_ibis", value="obs_ibis", id="og_name")

                        yield Static("Working Directory:", classes="field-label")
                        yield Input(placeholder="Working directory (default: ./work)", id="workdir")

                    with Vertical(classes="form-column"):
                        with Vertical(id="energy_box"):
                            yield Static(
                                "Energy Band / Filter:", id="energy_label", classes="field-label"
                            )
                            yield Select(
                                INSTRUMENT_ENERGY_PRESETS["ibis"], value="18-60", id="energy_preset"
                            )

                            with Vertical(id="custom_bands_box", classes="hidden"):
                                yield Static(
                                    "Custom Band (if selected above):",
                                    id="custom_bands_label",
                                    classes="field-label",
                                )
                                yield Input(
                                    placeholder="e.g. 20-40, 40-100",
                                    id="custom_bands",
                                    disabled=True,
                                )

                        yield Static("Pipeline Product / Level:", classes="field-label")
                        yield Select(
                            INSTRUMENT_PRODUCT_LEVELS["ibis"], value="IMA2", id="product_level"
                        )

                        with Vertical(id="timing_settings_box", classes="hidden"):
                            yield Static("Timing Analysis Mode:", classes="field-label")
                            yield Select(TIMING_MODES, value="standard", id="timing_mode")
                            yield Static(
                                "Time Bin / Step (seconds):",
                                id="time_step_label",
                                classes="field-label",
                            )
                            yield Input(placeholder="e.g. 10.0", value="10.0", id="time_step")

                        with Collapsible(
                            title="Advanced Settings", collapsed=True, id="advanced_settings"
                        ):
                            with Vertical(id="ibis_cleaning_box"):
                                yield Static("Deconvolution Cleaning Mode:", classes="field-label")
                                yield Select(CLEAN_MODES, value="1", id="clean_mode")
                                yield Static("Bright PIF Threshold:", classes="field-label")
                                yield Input(
                                    placeholder="0.0001", value="0.0001", id="bright_threshold"
                                )
                            yield Checkbox(
                                "Clean previous observation group directory before run",
                                value=True,
                                id="clean_toggle",
                            )

            with Vertical(id="right_panel"):
                with Vertical(id="progress_box"):
                    with Horizontal(id="progress_row"):
                        yield Static("Status: Ready to run", id="status_label")
                        yield Static("Elapsed Time: --:--", id="elapsed_time")
                    yield ProgressBar(total=100, show_eta=False, id="progress_bar")
                    with Vertical(id="spark_box"):
                        yield Static("Activity Telemetry:", classes="metrics-sublabel")
                        yield Sparkline(data=[0, 0, 0, 0, 0], id="spark_activity")
                    yield Static("", id="result_banner")

                with TabbedContent(id="tabs"):
                    with TabPane("Pipeline Output", id="tab_logs"):
                        yield RichLog(id="log", wrap=True, highlight=True, markup=True)
                    with TabPane("Detected Sources", id="tab_sources"):
                        yield DataTable(id="sources_table", cursor_type="row", zebra_stripes=True)
                    with TabPane("Saved Log File", id="tab_saved_log"):
                        yield RichLog(id="saved_log_text", wrap=True, highlight=False, markup=False)

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sources_table", DataTable)
        table.add_columns(
            "Source Name", "RA (deg)", "Dec (deg)", "Significance (σ)", "Flux (cts/s)"
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "instrument":
            inst = str(event.value)

            # Update OG name placeholder and value if default
            og_input = self.query_one("#og_name", Input)
            if og_input.value in ["obs_ibis", "obs_jemx", "obs_omc", "obs_spi"]:
                og_input.value = f"obs_{inst}"
            og_input.placeholder = f"obs_{inst}"

            # Dynamic visibility for detector mode (IBIS only)
            det_box = self.query_one("#detector_mode_box")
            if inst == "ibis":
                det_box.remove_class("hidden")
            else:
                det_box.add_class("hidden")

            # Dynamic visibility for JEM-X unit
            jemx_box = self.query_one("#jemx_unit_box")
            if inst == "jemx":
                jemx_box.remove_class("hidden")
            else:
                jemx_box.add_class("hidden")

            # Dynamic visibility for IBIS deconvolution cleaning
            clean_box = self.query_one("#ibis_cleaning_box")
            if inst == "ibis":
                clean_box.remove_class("hidden")
            else:
                clean_box.add_class("hidden")

            # Update Energy Band / Filter options
            energy_select = self.query_one("#energy_preset", Select)
            energy_label = self.query_one("#energy_label", Static)
            custom_label = self.query_one("#custom_bands_label", Static)
            if inst == "omc":
                energy_label.update("Optical Filter:")
                custom_label.update("Custom Filter (if selected above):")
            else:
                energy_label.update("Energy Band:")
                custom_label.update("Custom Band (if selected above):")

            energy_presets = INSTRUMENT_ENERGY_PRESETS.get(inst, INSTRUMENT_ENERGY_PRESETS["ibis"])
            energy_select.set_options(energy_presets)
            energy_select.value = energy_presets[0][1]

            # Update Product / Level options
            prod_select = self.query_one("#product_level", Select)
            prod_levels = INSTRUMENT_PRODUCT_LEVELS.get(inst, INSTRUMENT_PRODUCT_LEVELS["ibis"])
            prod_select.set_options(prod_levels)
            prod_select.value = prod_levels[0][1]

        elif event.select.id == "product_level":
            # Dynamic visibility for timing controls when LCR level is chosen
            timing_box = next(iter(self.query("#timing_settings_box")), None)
            if timing_box:
                if event.value == "LCR":
                    timing_box.remove_class("hidden")
                    # Set sensible default for current instrument
                    inst = str(self.query_one("#instrument", Select).value)
                    time_step_input = self.query_one("#time_step", Input)
                    if not time_step_input.value or time_step_input.value in [
                        "10.0",
                        "4.0",
                        "0.01",
                    ]:
                        time_step_input.value = "4.0" if inst == "jemx" else "10.0"
                    timing_box.scroll_visible()
                else:
                    timing_box.add_class("hidden")

        elif event.select.id == "timing_mode":
            time_inputs = list(self.query("#time_step"))
            labels = list(self.query("#time_step_label"))
            if time_inputs and labels:
                t_input = self.query_one("#time_step", Input)
                t_label = self.query_one("#time_step_label", Static)

                if event.value == "pif":
                    t_label.update("High-Res Time Bin (seconds, PIF mode):")
                    t_input.placeholder = "e.g. 0.001 (1ms) or 0.01"
                    if t_input.value in ["10.0", "4.0"]:
                        t_input.value = "0.005"
                else:
                    t_label.update("Time Bin / Step (seconds):")
                    t_input.placeholder = "e.g. 10.0"
                    if t_input.value in ["0.005", "0.001"]:
                        t_input.value = "10.0"

        elif event.select.id == "energy_preset":
            custom_input = next(iter(self.query("#custom_bands")), None)
            custom_box = next(iter(self.query("#custom_bands_box")), None)
            if custom_input:
                if event.value == "custom":
                    custom_input.disabled = False
                    if custom_box:
                        custom_box.remove_class("hidden")
                    custom_input.focus()
                else:
                    custom_input.disabled = True
                    if custom_box:
                        custom_box.add_class("hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.run_analysis()
        elif event.button.id == "quit":
            self.exit()
        elif event.button.id == "btn_browse_scw":
            self._browse_archive_scws()

    def _browse_archive_scws(self) -> None:
        scw_dir = Path(config.rep_base_prod) / "scw"
        scw_options: list[tuple[str, str]] = []
        if scw_dir.exists():
            for rev_dir in sorted(scw_dir.iterdir()):
                if rev_dir.is_dir() and not rev_dir.name.startswith("."):
                    rev_id = rev_dir.name
                    for item in sorted(rev_dir.iterdir()):
                        if item.is_dir() and item.name.startswith(rev_id):
                            raw_id = item.name.split(".")[0]
                            is_pointing = raw_id.endswith("0010")
                            label = (
                                f"Rev {rev_id} | {raw_id} ({'Pointing' if is_pointing else 'Slew'})"
                            )
                            if (label, raw_id) not in scw_options:
                                scw_options.append((label, raw_id))

        if not scw_options:
            self._log(
                "[dim yellow]No Science Windows discovered in local archive directory.[/dim yellow]"
            )
            return

        def _on_browse_selected(selected_scws: list[str] | None) -> None:
            if selected_scws:
                scw_str = ", ".join(selected_scws)
                self.query_one("#scw_input", Input).value = scw_str
                self._log(
                    f"[bold cyan]Selected {len(selected_scws)} ScWs from archive: {scw_str}[/bold cyan]"
                )

        self.push_screen(ScwBrowseModal(scw_options), _on_browse_selected)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:

        table = self.query_one("#sources_table", DataTable)
        row_data = [str(cell) for cell in table.get_row(event.row_key)]
        self.push_screen(SourceDetailModal(row_data))

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def _set_running(self, running: bool) -> None:
        self.query_one("#run", Button).disabled = running
        if running:
            banner = self.query_one("#result_banner", Static)
            banner.classes = ""
            banner.update("")
            self.query_one("#elapsed_time", Static).update("Elapsed Time: 00:00")
            self.query_one("#spark_activity", Sparkline).data = [0, 5]

    def _update_stage(self, stage_name: str, progress_val: int) -> None:
        self.query_one("#status_label", Static).update(
            f"Status: [bold cyan]{stage_name}[/bold cyan]"
        )
        self.query_one("#progress_bar", ProgressBar).update(progress=progress_val)
        spark = self.query_one("#spark_activity", Sparkline)
        current_data = list(spark.data) if spark.data else [0]
        current_data.append(progress_val)
        if len(current_data) > 15:
            current_data = current_data[-15:]
        spark.data = current_data

    def _update_elapsed(self, seconds: int) -> None:
        mins, secs = divmod(seconds, 60)
        self.query_one("#elapsed_time", Static).update(f"Elapsed Time: {mins:02d}:{secs:02d}")

    def _show_result_banner(self, success: bool, message: str) -> None:
        banner = self.query_one("#result_banner", Static)
        if success:
            banner.classes = "success"
            banner.update(f"✓ {message}")
            self.query_one("#status_label", Static).update(
                "Status: [bold green]Reduction Completed Successfully[/bold green]"
            )
            self.query_one("#progress_bar", ProgressBar).update(progress=100)
        else:
            banner.classes = "error"
            banner.update(f"✗ {message}")
            self.query_one("#status_label", Static).update(
                "Status: [bold red]Pipeline Execution Failed[/bold red]"
            )

    def _load_saved_log(self, workdir: Path, og_name: str) -> None:
        """Load commonlog.txt or og_run.log into the Saved Log File tab."""
        log_candidates = [
            workdir / "obs" / og_name / f"{og_name}_run.log",
            workdir / "commonlog.txt",
        ]
        found = next((f for f in log_candidates if f.exists()), None)
        if found:
            log_widget = self.query_one("#saved_log_text", RichLog)
            log_widget.clear()
            log_widget.write(f"Log file: {found}\n" + "=" * 60)
            try:
                content = found.read_text(errors="replace")
                lines = content.splitlines()
                if len(lines) > 2000:
                    log_widget.write(f"... truncated ({len(lines) - 2000} older lines omitted) ...")
                    lines = lines[-2000:]
                for line in lines:
                    log_widget.write(line)
            except Exception as e:
                log_widget.write(f"Error reading log: {e}")

    def _populate_sources(self, workdir: Path, og_name: str) -> int:
        """Scan workdir for mosaic or science source results and populate the DataTable."""
        table = self.query_one("#sources_table", DataTable)
        table.clear(columns=True)
        table.add_columns(
            "Source Name", "RA (deg)", "Dec (deg)", "Significance (σ)", "Flux (cts/s)"
        )

        cand_files = [
            workdir / "obs" / og_name / "isgri_mosa_res.fits",
            workdir / "obs" / og_name / "isgri_srcl_res.fits",
            workdir / "obs" / og_name / "jmx1_mosa_res.fits",
            workdir / "obs" / og_name / "jmx2_mosa_res.fits",
            workdir / "obs" / og_name / "jmx1_srcl_res.fits",
            workdir / "obs" / og_name / "jmx2_srcl_res.fits",
            workdir / "obs" / og_name / "omc_srcl_res.fits",
            workdir / "obs" / og_name / "spi_srcl_res.fits",
        ]
        found_file = next((f for f in cand_files if f.exists()), None)
        if not found_file:
            # Check for any mosaic or source results in og_name
            found_file = next(
                (f for f in (workdir / "obs" / og_name).glob("*res*.fits") if f.is_file()), None
            )
        if not found_file:
            return 0

        try:
            from astropy.io import fits

            with fits.open(found_file) as hdul:
                src_table = None
                for h in hdul:
                    if (
                        h.data is not None  # pyright: ignore[reportAttributeAccessIssue]
                        and getattr(h.data, "names", None)  # pyright: ignore[reportAttributeAccessIssue]
                        and any(n in h.data.names for n in ["NAME", "SOURCE_ID", "DETSIG", "SNR"])  # pyright: ignore[reportAttributeAccessIssue]
                    ):
                        src_table = h
                        break

                if not src_table:
                    return 0

                data = src_table.data  # pyright: ignore[reportAttributeAccessIssue]
                cols = data.names
                name_col = (
                    "NAME" if "NAME" in cols else ("SOURCE_ID" if "SOURCE_ID" in cols else cols[0])
                )
                ra_col = "RA_OBJ" if "RA_OBJ" in cols else ("RA_FIN" if "RA_FIN" in cols else "RA")
                dec_col = (
                    "DEC_OBJ" if "DEC_OBJ" in cols else ("DEC_FIN" if "DEC_FIN" in cols else "DEC")
                )
                snr_col = (
                    "DETSIG" if "DETSIG" in cols else ("SNR" if "SNR" in cols else "SIGNIFICANCE")
                )
                flux_col = "FLUX" if "FLUX" in cols else "COUNTS"
                flux_err_col = "FLUX_ERR" if "FLUX_ERR" in cols else None

                count = 0
                for row in data:
                    count += 1
                    snr = float(row[snr_col]) if snr_col in cols else 0.0
                    name = str(row[name_col]).strip()
                    ra = f"{float(row[ra_col]):.4f}" if ra_col in cols else "N/A"
                    dec = f"{float(row[dec_col]):.4f}" if dec_col in cols else "N/A"
                    flux_str = f"{float(row[flux_col]):.2f}" if flux_col in cols else "N/A"
                    if flux_err_col and flux_err_col in cols:
                        flux_str += f" ± {float(row[flux_err_col]):.2f}"
                    table.add_row(name, ra, dec, f"{snr:.1f}", flux_str)
                return count
        except Exception as e:
            self._log(
                f"[dim yellow]Notice: Could not parse sources from {found_file.name}: {e}[/dim yellow]"
            )
            return 0

    @work(thread=True, exclusive=True)
    def run_analysis(self) -> None:
        instrument = str(self.query_one("#instrument", Select).value)
        scw_input = self.query_one("#scw_input", Input).value.strip()
        workdir_str = self.query_one("#workdir", Input).value.strip()
        og_name = self.query_one("#og_name", Input).value.strip() or "obs_ibis"
        energy_preset = str(self.query_one("#energy_preset", Select).value)
        custom_bands = self.query_one("#custom_bands", Input).value.strip()
        product_level = str(self.query_one("#product_level", Select).value)
        detector_mode = str(self.query_one("#detector_mode", Select).value)
        clean_mode = str(self.query_one("#clean_mode", Select).value)
        bright_threshold = self.query_one("#bright_threshold", Input).value.strip() or "0.0001"
        clean_toggle = self.query_one("#clean_toggle", Checkbox).value

        if not scw_input:
            self.call_from_thread(self._log, "[bold red]Error: ScW input is required.[/bold red]")
            self.call_from_thread(self._show_result_banner, False, "ScW input is required.")
            return

        # Determine energy band specification
        bands = custom_bands if (energy_preset == "custom" and custom_bands) else energy_preset

        argv = [
            sys.executable,
            "-m",
            "integral_cli.main",
            "analyse",
            instrument,
            scw_input,
            "--og",
            og_name,
        ]

        if instrument == "ibis":
            argv.append("--yes")
            if bands and bands != "custom":
                argv += ["--bands", bands]
            if product_level:
                argv += ["--end-level", product_level]
            if detector_mode == "isgri":
                argv += ["--isgri", "--no-picsit", "--no-compton"]
            elif detector_mode == "picsit":
                argv += ["--no-isgri", "--picsit", "--no-compton"]
            elif detector_mode == "both":
                argv += ["--isgri", "--picsit", "--no-compton"]
            elif detector_mode == "compton":
                argv += ["--isgri", "--picsit", "--compton"]
            argv += ["--clean-mode", clean_mode]
            argv += ["--bright-threshold", bright_threshold]
        elif instrument == "jemx":
            jemx_unit = str(self.query_one("#jemx_unit", Select).value)
            argv += ["--unit", jemx_unit]
            if bands and bands != "custom":
                argv += ["--bands", bands]
            if product_level:
                argv += ["--end-level", product_level]
        elif instrument in ["omc", "spi"]:
            if product_level:
                argv += ["--end-level", product_level]

        # Timing analysis parameters (when product_level == LCR)
        if product_level == "LCR":
            timing_mode = str(self.query_one("#timing_mode", Select).value)
            time_step_str = self.query_one("#time_step", Input).value.strip() or "10.0"
            try:
                t_step = float(time_step_str)
                if t_step <= 0:
                    raise ValueError("Time step must be strictly positive (> 0).")
                if timing_mode == "standard" and t_step < 0.05:
                    raise ValueError(
                        f"Standard timing step ({t_step}s) too small (< 0.05s). Switch to 'Fast / Pulsar Timing (PIF)' mode for fine sub-second bins."
                    )
                if timing_mode == "pif" and t_step < 0.00001:
                    raise ValueError(
                        f"PIF timing step ({t_step}s) cannot be smaller than 0.00001s."
                    )
            except ValueError as err:
                self.call_from_thread(
                    self._log, f"[bold red]Timing Parameter Error: {err}[/bold red]"
                )
                self.call_from_thread(self._show_result_banner, False, str(err))
                return

            argv += ["--time-step", str(t_step)]
            if instrument == "ibis":
                argv += ["--timing-mode", timing_mode]

        if workdir_str:
            argv += ["--workdir", workdir_str]
        argv += ["--clean" if clean_toggle else "--no-clean"]

        self.call_from_thread(self._log, f"[bold cyan]$ {shlex.join(argv)}[/bold cyan]")
        self.call_from_thread(self._set_running, True)
        self.call_from_thread(self._update_stage, "Initializing Container & Observation Group", 10)

        stage_patterns = INSTRUMENT_STAGE_PATTERNS.get(
            instrument, INSTRUMENT_STAGE_PATTERNS["ibis"]
        )

        start_time = time.monotonic()
        last_elapsed_update = start_time
        returncode = -1
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line_str = line.rstrip()
                self.call_from_thread(self._log, line_str)

                now = time.monotonic()
                if now - last_elapsed_update >= 1.0:
                    last_elapsed_update = now
                    elapsed_secs = int(now - start_time)
                    self.call_from_thread(self._update_elapsed, elapsed_secs)

                # Inspect line for stage transitions
                for pattern, stage_label, pct in stage_patterns:
                    if re.search(pattern, line_str):
                        self.call_from_thread(self._update_stage, stage_label, pct)

            returncode = proc.wait()
            total_secs = int(time.monotonic() - start_time)
            self.call_from_thread(self._update_elapsed, total_secs)
        except Exception as e:
            self.call_from_thread(self._log, f"[bold red]Failed to launch run: {e}[/bold red]")
            self.call_from_thread(self._show_result_banner, False, f"Failed to launch: {e}")
            return
        finally:
            self.call_from_thread(self._set_running, False)

        actual_workdir = Path(workdir_str) if workdir_str else Path.cwd() / "work"
        if returncode == 0:
            self.call_from_thread(
                self._log, "[bold green]✓ Run completed successfully.[/bold green]"
            )

            def update_results():
                source_count = self._populate_sources(actual_workdir, og_name)
                self._load_saved_log(actual_workdir, og_name)
                tabs = self.query_one("#tabs", TabbedContent)
                tab_sources = tabs.get_tab("tab_sources")
                if tab_sources:
                    tab_sources.label = f"Detected Sources ({source_count})"
                self._show_result_banner(
                    True,
                    f"Run finished successfully! Found {source_count} point source(s). Results in obs/{og_name}",
                )
                if source_count > 0:
                    tabs.active = "tab_sources"

            self.call_from_thread(update_results)

        else:
            self.call_from_thread(
                self._log, f"[bold red]✗ Run failed (exit code {returncode}).[/bold red]"
            )

            def update_failure():
                self._load_saved_log(actual_workdir, og_name)
                self._show_result_banner(False, f"Run failed with exit code {returncode}.")

            self.call_from_thread(update_failure)


def launch_tui() -> None:
    """Entry point for `integral tui`."""
    IntegralTUI().run()


if __name__ == "__main__":
    launch_tui()
