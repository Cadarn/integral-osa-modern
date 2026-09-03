"""
Textual TUI for launching INTEGRAL science analysis runs without memorising CLI flags.

Wraps the `integral analyse` CLI as a subprocess (rather than calling analysis.py's
functions in-process) so its output streams live into the log widget with zero
changes to the existing, already-tested analysis/docker execution path.
"""

import shlex
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from integral_cli.config import config

INSTRUMENTS = ["ibis", "jemx", "omc", "spi"]
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


class IntegralTUI(App):
    """Enhanced form-based launcher for `integral analyse` with interactive parameters and product inspection."""

    CSS = """
    #form {
        height: auto;
        padding: 1 2;
        border: round $accent;
    }
    #form Input, #form Select {
        margin-bottom: 1;
    }
    .form-row {
        height: auto;
        margin-bottom: 1;
    }
    .form-col {
        width: 1fr;
        height: auto;
        margin-right: 1;
    }
    #buttons {
        height: auto;
        margin-top: 1;
    }
    RichLog {
        border: round $primary;
        height: 100%;
    }
    DataTable {
        height: 100%;
    }
    """

    BINDINGS: ClassVar = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="form"):
            yield Static(f"Data archive: {config.rep_base_prod}  |  Image: {config.docker_image}")
            with Horizontal(classes="form-row"):
                with Vertical(classes="form-col"):
                    yield Static("Instrument:", classes="label")
                    yield Select(
                        [(i.upper(), i) for i in INSTRUMENTS], value="ibis", id="instrument"
                    )
                with Vertical(classes="form-col"):
                    yield Static("Science Windows:", classes="label")
                    yield Input(
                        placeholder="e.g. rev:0060:5, 006000010010, scw.list", id="scw_input"
                    )

            with Horizontal(classes="form-row"):
                with Vertical(classes="form-col"):
                    yield Static("Energy Band:", classes="label")
                    yield Select(ENERGY_PRESETS, value="18-60", id="energy_preset")
                with Vertical(classes="form-col"):
                    yield Static("Custom Band (if selected above):", classes="label")
                    yield Input(placeholder="e.g. 20-40, 40-100", id="custom_bands", disabled=True)

            with Horizontal(classes="form-row"):
                with Vertical(classes="form-col"):
                    yield Static("Pipeline Product / Level:", classes="label")
                    yield Select(PRODUCT_LEVELS, value="IMA2", id="product_level")
                with Vertical(classes="form-col"):
                    yield Static("Working directory:", classes="label")
                    yield Input(placeholder="Working directory (default: ./work)", id="workdir")

            with Horizontal(id="buttons"):
                yield Button("Run Analysis", id="run", variant="success")
                yield Button("Quit", id="quit", variant="error")

        with TabbedContent(id="tabs"):
            with TabPane("Pipeline Output", id="tab_logs"):
                yield RichLog(id="log", wrap=True, highlight=True, markup=True)
            with TabPane("Detected Sources", id="tab_sources"):
                yield DataTable(id="sources_table")

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

    def _populate_sources(self, workdir: Path, instrument: str) -> None:
        """Scan workdir for mosaic or science source results and populate the DataTable."""
        table = self.query_one("#sources_table", DataTable)
        table.clear(columns=True)
        table.add_columns(
            "Source Name", "RA (deg)", "Dec (deg)", "Significance (σ)", "Flux (cts/s)"
        )

        # Candidate result files based on instrument
        cand_files = [
            workdir / "obs" / f"obs_{instrument}" / "isgri_mosa_res.fits",
            workdir / "obs" / f"obs_{instrument}" / "isgri_srcl_res.fits",
        ]
        found_file = next((f for f in cand_files if f.exists()), None)
        if not found_file:
            return

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
                    return

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

                for row in data:
                    snr = float(row[snr_col]) if snr_col in cols else 0.0
                    name = str(row[name_col]).strip()
                    ra = f"{float(row[ra_col]):.4f}" if ra_col in cols else "N/A"
                    dec = f"{float(row[dec_col]):.4f}" if dec_col in cols else "N/A"
                    flux_str = f"{float(row[flux_col]):.2f}" if flux_col in cols else "N/A"
                    if flux_err_col and flux_err_col in cols:
                        flux_str += f" ± {float(row[flux_err_col]):.2f}"
                    table.add_row(name, ra, dec, f"{snr:.1f}", flux_str)
        except Exception as e:
            self._log(
                f"[dim yellow]Notice: Could not parse sources from {found_file.name}: {e}[/dim yellow]"
            )

    @work(thread=True, exclusive=True)
    def run_analysis(self) -> None:
        instrument = str(self.query_one("#instrument", Select).value)
        scw_input = self.query_one("#scw_input", Input).value.strip()
        workdir_str = self.query_one("#workdir", Input).value.strip()
        energy_preset = str(self.query_one("#energy_preset", Select).value)
        custom_bands = self.query_one("#custom_bands", Input).value.strip()
        product_level = str(self.query_one("#product_level", Select).value)

        if not scw_input:
            self.call_from_thread(self._log, "[bold red]Error: ScW input is required.[/bold red]")
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
        ]
        if bands and bands != "custom":
            argv += ["--bands", bands]
        if product_level:
            argv += ["--end-level", product_level]
        if workdir_str:
            argv += ["--workdir", workdir_str]

        self.call_from_thread(self._log, f"[bold cyan]$ {shlex.join(argv)}[/bold cyan]")
        self.call_from_thread(self._set_running, True)

        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self.call_from_thread(self._log, line.rstrip())
            returncode = proc.wait()
        except Exception as e:
            self.call_from_thread(self._log, f"[bold red]Failed to launch run: {e}[/bold red]")
            return
        finally:
            self.call_from_thread(self._set_running, False)

        if returncode == 0:
            self.call_from_thread(
                self._log, "[bold green]✓ Run completed successfully.[/bold green]"
            )
            actual_workdir = Path(workdir_str) if workdir_str else Path.cwd() / "work"
            self.call_from_thread(self._populate_sources, actual_workdir, instrument)
        else:
            self.call_from_thread(
                self._log, f"[bold red]✗ Run failed (exit code {returncode}).[/bold red]"
            )


def launch_tui() -> None:
    """Entry point for `integral tui`."""
    IntegralTUI().run()


if __name__ == "__main__":
    launch_tui()
