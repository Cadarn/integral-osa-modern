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
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    ProgressBar,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from integral_cli.config import config

INSTRUMENTS = ["ibis", "jemx", "omc", "spi"]
DETECTOR_MODES = [
    ("ISGRI only (15-1000 keV) [Default]", "isgri"),
    ("PiCSIT only (175 keV - 10 MeV)", "picsit"),
    ("Both ISGRI & PiCSIT", "both"),
    ("Compton Mode (Coincidence)", "compton"),
]
ENERGY_PRESETS = [
    ("Standard: 18-60 keV (Single band)", "18-60"),
    ("Hard X-ray: 20-40, 40-100 keV (Two bands)", "20-40, 40-100"),
    ("Broadband: 20-100 keV (Single band)", "20-100"),
    ("Custom energy band...", "custom"),
]
PRODUCT_LEVELS = [
    ("Sky Images & Mosaic (IMA2) [Default]", "IMA2"),
    ("Single Pointing Sky Images (IMA)", "IMA"),
    ("Pipeline up to Spectra (SPE)", "SPE"),
    ("Pipeline up to Lightcurves (LCR)", "LCR"),
]
CLEAN_MODES = [
    ("Standard ghost cleaning (OBS1_CleanMode=1) [Default]", "1"),
    ("No ghost cleaning (OBS1_CleanMode=0)", "0"),
]

STAGE_PATTERNS = [
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
]


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
        width: 46;
        min-width: 40;
        max-width: 52;
        border-right: heavy $accent;
        padding: 0 1;
    }
    #sidebar Input, #sidebar Select {
        margin-bottom: 1;
    }
    .field-label {
        margin-top: 1;
        text-style: bold;
    }
    #progress_box {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
        padding: 1;
        border: round $secondary;
        background: $surface;
    }
    #status_label {
        margin-bottom: 1;
        text-style: bold;
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
    }
    TabbedContent {
        height: 100%;
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

                with Vertical(id="progress_box"):
                    yield Static("Status: Ready to run", id="status_label")
                    yield ProgressBar(total=100, show_eta=False, id="progress_bar")
                    yield Static("", id="result_banner")

                with Horizontal(id="buttons"):
                    yield Button("Run Analysis", id="run", variant="success")
                    yield Button("Quit", id="quit", variant="error")

                yield Static("Instrument:", classes="field-label")
                yield Select([(i.upper(), i) for i in INSTRUMENTS], value="ibis", id="instrument")

                yield Static("Science Windows:", classes="field-label")
                yield Input(placeholder="e.g. rev:0060:5, 006000010010, scw.list", id="scw_input")

                yield Static("Detector Mode (IBIS):", classes="field-label")
                yield Select(DETECTOR_MODES, value="isgri", id="detector_mode")

                yield Static("Observation Group Name:", classes="field-label")
                yield Input(placeholder="obs_ibis", value="obs_ibis", id="og_name")

                yield Static("Energy Band:", classes="field-label")
                yield Select(ENERGY_PRESETS, value="18-60", id="energy_preset")

                yield Static("Custom Band (if selected above):", classes="field-label")
                yield Input(placeholder="e.g. 20-40, 40-100", id="custom_bands", disabled=True)

                yield Static("Pipeline Product / Level:", classes="field-label")
                yield Select(PRODUCT_LEVELS, value="IMA2", id="product_level")

                yield Static("Working Directory:", classes="field-label")
                yield Input(placeholder="Working directory (default: ./work)", id="workdir")

                with Collapsible(title="Advanced Settings", collapsed=True, id="advanced_settings"):
                    yield Static("Deconvolution Cleaning Mode:", classes="field-label")
                    yield Select(CLEAN_MODES, value="1", id="clean_mode")
                    yield Static("Bright PIF Threshold:", classes="field-label")
                    yield Input(placeholder="0.0001", value="0.0001", id="bright_threshold")
                    yield Checkbox(
                        "Clean previous observation group directory before run",
                        value=True,
                        id="clean_toggle",
                    )

            with Vertical(id="right_panel"), TabbedContent(id="tabs"):
                with TabPane("Pipeline Output", id="tab_logs"):
                    yield RichLog(id="log", wrap=True, highlight=True, markup=True)
                with TabPane("Detected Sources", id="tab_sources"):
                    yield DataTable(id="sources_table")
                with TabPane("Saved Log File", id="tab_saved_log"):
                    yield RichLog(id="saved_log_text", wrap=True, highlight=False, markup=False)

        yield Footer()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "energy_preset":
            custom_input = self.query_one("#custom_bands", Input)
            if event.value == "custom":
                custom_input.disabled = False
                custom_input.focus()
            else:
                custom_input.disabled = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.run_analysis()
        elif event.button.id == "quit":
            self.exit()

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def _set_running(self, running: bool) -> None:
        self.query_one("#run", Button).disabled = running
        if running:
            banner = self.query_one("#result_banner", Static)
            banner.classes = ""
            banner.update("")

    def _update_stage(self, stage_name: str, progress_val: int) -> None:
        self.query_one("#status_label", Static).update(
            f"Status: [bold cyan]{stage_name}[/bold cyan]"
        )
        self.query_one("#progress_bar", ProgressBar).update(progress=progress_val)

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
        ]
        found_file = next((f for f in cand_files if f.exists()), None)
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
            "--yes",
            "--og",
            og_name,
        ]
        if bands and bands != "custom":
            argv += ["--bands", bands]
        if product_level:
            argv += ["--end-level", product_level]
        if workdir_str:
            argv += ["--workdir", workdir_str]

        # Detector switches
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
        argv += ["--clean" if clean_toggle else "--no-clean"]

        self.call_from_thread(self._log, f"[bold cyan]$ {shlex.join(argv)}[/bold cyan]")
        self.call_from_thread(self._set_running, True)
        self.call_from_thread(self._update_stage, "Initializing Container & Observation Group", 10)

        returncode = -1
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line_str = line.rstrip()
                self.call_from_thread(self._log, line_str)

                # Inspect line for stage transitions
                for pattern, stage_label, pct in STAGE_PATTERNS:
                    if re.search(pattern, line_str):
                        self.call_from_thread(self._update_stage, stage_label, pct)

            returncode = proc.wait()
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
                self._show_result_banner(
                    True,
                    f"Run finished successfully! Found {source_count} point source(s). Results in obs/{og_name}",
                )
                if source_count > 0:
                    self.query_one("#tabs", TabbedContent).active = "tab_sources"

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
