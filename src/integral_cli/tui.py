"""
Textual TUI for launching INTEGRAL science analysis runs without memorising CLI flags.

Wraps the `integral analyse` CLI as a subprocess (rather than calling analysis.py's
functions in-process) so its output streams live into the log widget with zero
changes to the existing, already-tested analysis/docker execution path.
"""

import shlex
import subprocess
import sys
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Select, Static

from integral_cli.config import config

INSTRUMENTS = ["ibis", "jemx", "omc", "spi"]


class IntegralTUI(App):
    """Form-based launcher for `integral analyse` runs with a live output log."""

    CSS = """
    #form {
        height: auto;
        padding: 1 2;
        border: round $accent;
    }
    #form Input, #form Select {
        margin-bottom: 1;
    }
    RichLog {
        border: round $primary;
    }
    """

    BINDINGS: ClassVar = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="form"):
            yield Static(f"Data archive: {config.rep_base_prod}  |  Image: {config.docker_image}")
            yield Select([(i.upper(), i) for i in INSTRUMENTS], value="ibis", id="instrument")
            yield Input(placeholder="ScW input (e.g. rev:0060:10, a ScW ID, or comma list)", id="scw_input")
            yield Input(placeholder="Working directory (default: ./work)", id="workdir")
            with Horizontal(id="buttons"):
                yield Button("Run", id="run", variant="success")
                yield Button("Quit", id="quit", variant="error")
        yield RichLog(id="log", wrap=True, highlight=True, markup=True)
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.run_analysis()
        elif event.button.id == "quit":
            self.exit()

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def _set_running(self, running: bool) -> None:
        self.query_one("#run", Button).disabled = running

    @work(thread=True, exclusive=True)
    def run_analysis(self) -> None:
        instrument = self.query_one("#instrument", Select).value
        scw_input = self.query_one("#scw_input", Input).value.strip()
        workdir = self.query_one("#workdir", Input).value.strip()

        if not scw_input:
            self.call_from_thread(self._log, "[bold red]Error: ScW input is required.[/bold red]")
            return

        argv = [sys.executable, "-m", "integral_cli.main", "analyse", instrument, scw_input]
        if workdir:
            argv += ["--workdir", workdir]

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
            self.call_from_thread(self._log, "[bold green]✓ Run completed successfully.[/bold green]")
        else:
            self.call_from_thread(self._log, f"[bold red]✗ Run failed (exit code {returncode}).[/bold red]")


def launch_tui() -> None:
    """Entry point for `integral tui`."""
    IntegralTUI().run()


if __name__ == "__main__":
    launch_tui()
