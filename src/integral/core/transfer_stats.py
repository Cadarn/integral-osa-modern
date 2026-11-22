"""
Download timing and throughput accounting for the archive downloader.

A `TransferStats` instance is activated for the duration of one CLI download command. Download
helpers anywhere in the call tree report per-file outcomes via `record_file()`. Wall-clock time
is attributed to named phases (ScW listing, Science Windows, catalogs, aux, ...) via the
`TransferStats.phase()` context manager.

Both the active stats object and the current phase are held in `contextvars`. `asyncio.gather`
copies the current context into each task it creates, so concurrent downloads started inside a
phase are attributed to it without threading a stats argument through every helper.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

_ACTIVE: ContextVar[TransferStats | None] = ContextVar("integral_transfer_stats", default=None)
_PHASE: ContextVar[str] = ContextVar("integral_transfer_phase", default="other")

MAX_RECORDED_FAILURES = 50


@dataclass
class PhaseStats:
    name: str
    seconds: float = 0.0
    files_new: int = 0
    files_cached: int = 0
    files_failed: int = 0
    # Items the source reported it does not hold (e.g. ISLA "No public products found") -
    # an archive coverage gap, not a transfer error.
    files_unavailable: int = 0
    retries: int = 0
    bytes: int = 0

    @property
    def mb_per_s(self) -> float | None:
        return self.bytes / 1e6 / self.seconds if self.seconds > 0 and self.bytes else None


@dataclass
class TransferStats:
    """Timing and volume accounting for one download command."""

    command: str
    source: str
    science_url: str
    support_url: str
    concurrency: int
    probe_latency_s: float | None = None
    # Parallel requests actually used for Science Window data, when it differs from
    # `concurrency` (ISLA caps it).
    science_streams: int | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    wall_seconds: float = 0.0
    completed: bool = False
    scw_count: int = 0
    revolutions: list[str] = field(default_factory=list)
    phases: dict[str, PhaseStats] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter, repr=False)

    # --- activation & phases ------------------------------------------------------------------

    @contextmanager
    def activate(self) -> Iterator[TransferStats]:
        """Make this the stats object that `record_file()` reports into; stop the wall clock
        on exit (including on errors or Ctrl-C, so partial runs are still reported)."""
        token = _ACTIVE.set(self)
        self._t0 = time.perf_counter()
        try:
            yield self
        finally:
            self.wall_seconds = time.perf_counter() - self._t0
            _ACTIVE.reset(token)

    @contextmanager
    def phase(self, name: str) -> Iterator[PhaseStats]:
        """Attribute wall-clock time, and any files recorded meanwhile, to the named phase.
        Re-entering a phase (e.g. aux for several revolutions) accumulates into it."""
        ph = self.phases.setdefault(name, PhaseStats(name))
        token = _PHASE.set(name)
        t0 = time.perf_counter()
        try:
            yield ph
        finally:
            ph.seconds += time.perf_counter() - t0
            _PHASE.reset(token)

    def _current_phase(self) -> PhaseStats:
        name = _PHASE.get()
        return self.phases.setdefault(name, PhaseStats(name))

    # --- totals -------------------------------------------------------------------------------

    @property
    def total_bytes(self) -> int:
        return sum(p.bytes for p in self.phases.values())

    @property
    def files_new(self) -> int:
        return sum(p.files_new for p in self.phases.values())

    @property
    def files_cached(self) -> int:
        return sum(p.files_cached for p in self.phases.values())

    @property
    def files_failed(self) -> int:
        return sum(p.files_failed for p in self.phases.values())

    @property
    def files_unavailable(self) -> int:
        return sum(p.files_unavailable for p in self.phases.values())

    @property
    def retries(self) -> int:
        return sum(p.retries for p in self.phases.values())

    @property
    def mb_per_s(self) -> float | None:
        if self.wall_seconds <= 0 or not self.total_bytes:
            return None
        return self.total_bytes / 1e6 / self.wall_seconds

    # --- output -------------------------------------------------------------------------------

    def render(self, console: Console) -> None:
        """Print a per-phase summary table followed by totals."""
        title = f"Transfer summary · source: {self.source}"
        if self.support_url != self.science_url:
            title += " (catalogs/IC/aux from HEASARC)"
        table = Table(title=title, title_justify="left")
        table.add_column("Phase", style="cyan")
        table.add_column("New", justify="right")
        table.add_column("Cached", justify="right", style="dim")
        table.add_column("Failed", justify="right")
        show_unavail = self.files_unavailable > 0
        if show_unavail:
            table.add_column("Not on source", justify="right", style="yellow")
        table.add_column("Downloaded", justify="right", style="green")
        table.add_column("Time", justify="right")
        table.add_column("Rate", justify="right", style="bold")

        def row(label, new, cached, failed, unavail, nbytes, secs, rate):
            cells = [label, str(new), str(cached), f"[red]{failed}[/red]" if failed else "0"]
            if show_unavail:
                cells.append(str(unavail))
            cells += [format_bytes(nbytes), format_duration(secs), format_rate(rate)]
            table.add_row(*cells)

        for ph in self.phases.values():
            active_phase = (
                ph.files_new or ph.files_cached or ph.files_failed or ph.files_unavailable
            )
            if not (active_phase or ph.seconds >= 0.05):
                continue
            row(
                ph.name,
                ph.files_new,
                ph.files_cached,
                ph.files_failed,
                ph.files_unavailable,
                ph.bytes,
                ph.seconds,
                ph.mb_per_s,
            )
        table.add_section()
        row(
            "[bold]Total (wall clock)[/bold]",
            self.files_new,
            self.files_cached,
            self.files_failed,
            self.files_unavailable,
            self.total_bytes,
            self.wall_seconds,
            self.mb_per_s,
        )
        console.print(table)

        streams = f"{self.concurrency} concurrent streams"
        if self.science_streams is not None and self.science_streams != self.concurrency:
            streams += f" ({self.science_streams} for ScW requests)"
        notes = [streams]
        if self.probe_latency_s is not None:
            notes.append(f"mirror probe latency {self.probe_latency_s:.2f}s")
        if self.retries:
            notes.append(f"{self.retries} retried request(s)")
        if not self.completed:
            notes.append("[yellow]run did not complete[/yellow]")
        console.print(f"[dim]{' · '.join(notes)}[/dim]")

        if self.unavailable:
            console.print(
                f"[bold yellow]{len(self.unavailable)} item(s) are not available from "
                f"'{self.source}'[/bold yellow] (the source has no public product for them; "
                "this is an archive coverage gap, not a transfer error):"
            )
            for line in self.unavailable[:8]:
                console.print(f"  [yellow]•[/yellow] {line}")
            if len(self.unavailable) > 8:
                console.print(f"  [dim]... and {len(self.unavailable) - 8} more[/dim]")

        if self.failures:
            console.print(
                f"[bold red]{self.files_failed} file(s) failed to download.[/bold red] "
                "Re-run the same command to retry (completed files are skipped)."
            )
            for line in self.failures[:5]:
                console.print(f"  [red]•[/red] {line}")
            if len(self.failures) > 5:
                console.print(f"  [dim]... and {len(self.failures) - 5} more[/dim]")

    def to_record(self) -> dict:
        """Flat, JSON-serialisable record for --stats-file."""
        return {
            "started_at": self.started_at,
            "command": self.command,
            "source": self.source,
            "science_url": self.science_url,
            "support_url": self.support_url,
            "concurrency": self.concurrency,
            "science_streams": self.science_streams or self.concurrency,
            "probe_latency_s": _round(self.probe_latency_s),
            "completed": self.completed,
            "scw_count": self.scw_count,
            "revolutions": self.revolutions,
            "wall_seconds": _round(self.wall_seconds),
            "bytes": self.total_bytes,
            "mb_per_s": _round(self.mb_per_s),
            "files_new": self.files_new,
            "files_cached": self.files_cached,
            "files_failed": self.files_failed,
            "files_unavailable": self.files_unavailable,
            "retries": self.retries,
            "phases": [
                {**asdict(p), "seconds": _round(p.seconds), "mb_per_s": _round(p.mb_per_s)}
                for p in self.phases.values()
            ],
            "failures": self.failures,
            "unavailable": self.unavailable,
        }

    def append_to(self, path: Path) -> None:
        """Append this run as one JSON line (JSONL), so repeated runs accumulate in one file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(self.to_record()) + "\n")


def _round(v: float | None, nd: int = 3) -> float | None:
    return None if v is None else round(v, nd)


def active() -> TransferStats | None:
    return _ACTIVE.get()


def record_file(
    outcome: str, nbytes: int = 0, url: str = "", detail: str = "", count: int = 1
) -> None:
    """Report file outcome(s) - 'new', 'cached', 'failed' or 'unavailable' - to the active stats.

    `nbytes` is bytes transferred over the network; `count` lets one transfer stand for several
    files (an ISLA ScW zip unpacks to ~40 FITS files)."""
    stats = _ACTIVE.get()
    if stats is None:
        return
    ph = stats._current_phase()
    if outcome == "new":
        ph.files_new += count
        ph.bytes += nbytes
    elif outcome == "cached":
        ph.files_cached += count
    elif outcome == "unavailable":
        ph.files_unavailable += count
        if len(stats.unavailable) < MAX_RECORDED_FAILURES:
            stats.unavailable.append(f"{url} ({detail})" if detail else url)
    else:
        ph.files_failed += count
        if len(stats.failures) < MAX_RECORDED_FAILURES:
            stats.failures.append(f"{url} ({detail})" if detail else url)


def record_retry() -> None:
    stats = _ACTIVE.get()
    if stats is not None:
        stats._current_phase().retries += 1


def format_bytes(n: int) -> str:
    if n <= 0:
        return "-"
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{n} B"


def format_duration(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(round(s), 60)
    if m < 60:
        return f"{m}m{sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{sec:02d}s"


def format_rate(mb_per_s: float | None) -> str:
    return "-" if mb_per_s is None else f"{mb_per_s:.2f} MB/s"
