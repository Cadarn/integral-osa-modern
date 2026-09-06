"""
Shared utilities, parsers, and validation for INTEGRAL instrument pipelines.
"""

from pathlib import Path

import typer
from rich.console import Console

from integral.core.config import config
from integral.core.scw import filter_pointing_scws

console = Console()


def parse_energy_bands(band_spec: str) -> tuple[str, str, int]:
    """Parse and validate one or more energy bands.

    Accepts formats like:
      - "18-60"
      - "18 60"
      - "20-40, 40-100"
      - "20-40; 40-100"

    Returns (min_bands_str, max_bands_str, num_bands) where strings are space-separated
    as required by ISDC parameters (e.g. IBIS_II_E_band_min="20 40").
    """
    parts = [p.strip() for p in band_spec.replace(";", ",").split(",") if p.strip()]
    if not parts:
        raise ValueError("Energy band specification cannot be empty.")

    mins: list[float] = []
    maxs: list[float] = []
    for part in parts:
        if "-" in part:
            lo_str, hi_str = part.split("-", 1)
        elif " " in part.strip():
            tokens = part.strip().split()
            if len(tokens) != 2:
                raise ValueError(f"Cannot parse band boundaries from '{part}'. Use 'Emin-Emax'.")
            lo_str, hi_str = tokens[0], tokens[1]
        else:
            raise ValueError(
                f"Invalid energy band format: '{part}'. Expected 'Emin-Emax' (e.g. 18-60)."
            )

        try:
            lo = float(lo_str.strip())
            hi = float(hi_str.strip())
        except ValueError as err:
            raise ValueError(f"Non-numeric energy boundary in '{part}': {err}") from err

        if lo >= hi:
            raise ValueError(
                f"Lower bound ({lo} keV) must be strictly less than upper bound ({hi} keV)."
            )
        mins.append(lo)
        maxs.append(hi)

    # Check for non-overlapping contiguous/strictly increasing intervals
    for i in range(len(mins) - 1):
        if maxs[i] > mins[i + 1]:
            raise ValueError(
                f"Overlapping energy bands detected: [{mins[i]}, {maxs[i]}] and [{mins[i + 1]}, {maxs[i + 1]}]. "
                "OSA requires non-overlapping energy bands."
            )

    def _fmt(val: float) -> str:
        return str(int(val)) if val.is_integer() else str(val)

    return " ".join(_fmt(x) for x in mins), " ".join(_fmt(x) for x in maxs), len(mins)


def parse_jemx_energy_channels(band_spec: str) -> tuple[str, str, int]:
    """Convert JEM-X energy band specifications (in keV) to JEM-X channel bounds."""
    parts = [p.strip() for p in band_spec.replace(";", ",").split(",") if p.strip()]
    if not parts:
        raise ValueError("JEM-X energy band specification cannot be empty.")

    canon = {
        "3-10": ("46", "82", 1),
        "10-25": ("83", "159", 1),
        "3-25": ("46 129", "128 223", 2),
        "3-10,10-25": ("46 129", "128 223", 2),
        "3-35": ("46", "223", 1),
    }
    clean_key = ",".join(parts).replace(" ", "")
    if clean_key in canon:
        return canon[clean_key]

    def _kev_to_chan(e_kev: float) -> int:
        chan = round(e_kev * 6.4)
        return max(0, min(255, chan))

    chan_low: list[int] = []
    chan_high: list[int] = []

    for part in parts:
        if "-" in part:
            lo_str, hi_str = part.split("-", 1)
        elif " " in part.strip():
            tokens = part.strip().split()
            if len(tokens) != 2:
                raise ValueError(f"Cannot parse JEM-X band '{part}'. Use 'Emin-Emax'.")
            lo_str, hi_str = tokens[0], tokens[1]
        else:
            raise ValueError(
                f"Invalid JEM-X band '{part}'. Expected 'Emin-Emax' in keV (e.g. 3-10)."
            )

        try:
            lo_k = float(lo_str.strip())
            hi_k = float(hi_str.strip())
        except ValueError as err:
            raise ValueError(f"Non-numeric energy boundary in '{part}': {err}") from err

        if lo_k >= hi_k:
            raise ValueError(
                f"Lower bound ({lo_k} keV) must be less than upper bound ({hi_k} keV)."
            )

        c_lo = _kev_to_chan(lo_k)
        c_hi = _kev_to_chan(hi_k)
        if c_lo >= c_hi:
            c_hi = c_lo + 1
        chan_low.append(c_lo)
        chan_high.append(c_hi)

    return " ".join(str(c) for c in chan_low), " ".join(str(c) for c in chan_high), len(chan_low)


def validate_time_step(time_step: float, timing_mode: str = "standard") -> None:
    """Validate lightcurve time step parameter (seconds)."""
    if time_step <= 0:
        raise ValueError(f"Time step / bin size must be strictly positive (> 0), got {time_step}s.")
    if timing_mode == "standard" and time_step < 0.05:
        raise ValueError(
            f"Standard lightcurve mode time step ({time_step}s) is too fine (< 0.05s) and will cause "
            "extreme memory/runtime overhead. Use '--timing-mode pif' for high-resolution timing."
        )
    if timing_mode == "pif" and time_step < 0.00001:
        raise ValueError(
            f"PIF timing step ({time_step}s) cannot be smaller than 10 microseconds (0.00001s)."
        )


def resolve_scw_ids(scw_input: str) -> list[str]:
    """Resolve a revolution spec, scw.list path, comma-separated list, or bare ScW ID into IDs."""
    if scw_input.startswith("rev:"):
        parts = scw_input.split(":")
        rev_id = f"{int(parts[1]):04d}"
        limit = int(parts[2]) if len(parts) > 2 else None

        scw_dir = config.rep_base_prod / "scw" / rev_id
        if not scw_dir.exists():
            console.print(
                f"[bold red]Error: Revolution {rev_id} directory {scw_dir} does not exist.[/bold red]"
            )
            raise typer.Exit(code=1)

        all_ids = sorted(
            d.name.split(".")[0]
            for d in scw_dir.iterdir()
            if d.is_dir() and len(d.name.split(".")[0]) == 12
        )
        found_scws = filter_pointing_scws(all_ids, rev_id)
        return found_scws[:limit] if limit else found_scws
    elif Path(scw_input).exists():
        return [
            line.strip()
            for line in Path(scw_input).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    elif "," in scw_input:
        return [s.strip() for s in scw_input.split(",") if s.strip()]
    else:
        return [scw_input.strip()]
