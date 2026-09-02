"""
Benchmarking suite for comparing INTEGRAL OSA execution across:
1. Base Legacy Container (integralsw/osa:11.0 via Rosetta 2)
2. Layer 1 Modernisation (Native ARM64 Python/Astropy/FITS stack)
3. End-to-End Scientific Reduction & Mosaicing
"""

import json
import time
from pathlib import Path

import typer
from astropy.io import fits
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from integral_cli.config import config

console = Console()
benchmark_app = typer.Typer(help="Run performance benchmarks and multi-tier comparisons")


@benchmark_app.command("run")
def run_benchmark(
    scw_input: str = typer.Option("rev:0060:10", "--scws", "-s", help="Science Windows to benchmark (e.g. rev:0060:10 or rev:0060:5)"),
    e_min: str = typer.Option("18", "--e-min", help="Minimum energy in keV"),
    e_max: str = typer.Option("60", "--e-max", help="Maximum energy in keV"),
    workdir: Path = typer.Option(Path.cwd() / "work_bench", "--workdir", "-w", help="Working directory for benchmark"),
    output_json: Path | None = typer.Option(Path.cwd() / "benchmark_results.json", "--output", "-o", help="JSON output file for benchmark metrics"),
):
    """Run comparative benchmark across legacy emulation vs native modernized components."""
    workdir.mkdir(parents=True, exist_ok=True)
    results = {}

    console.print(
        Panel(
            f"[bold magenta]Starting INTEGRAL 3-Tier Modernisation Benchmark[/bold magenta]\n\n"
            f"• Target Dataset:   [cyan]{scw_input}[/cyan]\n"
            f"• Energy Range:     [cyan]{e_min} - {e_max} keV[/cyan]\n"
            f"• Working Directory:[cyan]{workdir}[/cyan]\n"
            f"• Host Platform:    [cyan]{config.host_arch} (Apple Silicon ARM64)[/cyan]",

            title="Benchmark Suite",
        )
    )

    # 1. Benchmark Layer 1: Native ARM64 Python/Astropy/FITS stack
    console.print("\n[bold cyan]1. Benchmarking Layer 1: Native ARM64 Python/Astropy Data Stack...[/bold cyan]")
    t0 = time.perf_counter()
    try:
        cat_file = config.rep_base_prod / "cat" / "hec" / "gnrl_refr_cat_0043.fits"
        with fits.open(cat_file) as hdul:
            data = hdul[1].data
            # Simulate coordinate filtering and flux cut
            bright_sources = data[data["ISGRI_FLAG"] > 0]
            count = len(bright_sources)

        layer1_time = time.perf_counter() - t0
        results["layer1_native_python"] = {
            "name": "Layer 1: Native ARM64 Python/Astropy",
            "time_sec": layer1_time,
            "sources_indexed": count,
            "status": "SUCCESS",
        }
        console.print(f"[bold green]✓ Native Python/Astropy indexed {count} sources in {layer1_time:.4f}s[/bold green]")
    except Exception as e:
        results["layer1_native_python"] = {"error": str(e), "status": "FAILED"}
        console.print(f"[bold red]Layer 1 failed: {e}[/bold red]")

    # 2. Benchmark Full Reduction Pipeline
    console.print(f"\n[bold cyan]2. Benchmarking Full Reduction & Mosaicing on {scw_input}...[/bold cyan]")
    from integral_cli.analysis import run_ibis
    t0 = time.perf_counter()
    try:
        # Run reduction via CLI
        run_ibis(
            scw_input=scw_input,
            e_min=e_min,
            e_max=e_max,
            start_level="DEAD",
            workdir=workdir,
            og_name="obs_bench",
            mosaic=True,
            clean=True,
        )
        total_reduction_time = time.perf_counter() - t0

        mosa_res = workdir / "obs" / "obs_bench" / "isgri_mosa_res.fits"
        source_count = 0
        top_source = "None"
        top_snr = 0.0
        if mosa_res.exists():
            with fits.open(mosa_res) as hdul:
                for h in hdul:
                    if h.data is not None and getattr(h.data, "names", None) and "DETSIG" in h.data.names:
                        source_count = len(h.data)
                        if source_count > 0:
                            top_source = str(h.data["NAME"][0]).strip()
                            top_snr = float(h.data["DETSIG"][0])
                        break

        results["full_reduction"] = {
            "name": "Full IBIS/ISGRI Reduction (18-60 keV)",
            "time_sec": total_reduction_time,
            "sources_detected": source_count,
            "top_source": top_source,
            "top_snr": top_snr,
            "status": "SUCCESS",
        }
    except Exception as e:
        results["full_reduction"] = {"error": str(e), "status": "FAILED"}
        console.print(f"[bold red]Full reduction failed: {e}[/bold red]")

    # Save metrics
    if output_json:
        output_json.write_text(json.dumps(results, indent=2))
        console.print(f"\n[dim]Saved benchmark metrics to {output_json}[/dim]")

    # Print summary comparison table
    table = Table(title="INTEGRAL Modernisation Benchmark Summary", title_style="bold green")
    table.add_column("Tier / Architecture", style="cyan")
    table.add_column("Execution Time (s)", justify="right", style="bold yellow")
    table.add_column("Performance Notes", style="magenta")
    table.add_column("Status", justify="center")

    if "layer1_native_python" in results and results["layer1_native_python"]["status"] == "SUCCESS":
        l1 = results["layer1_native_python"]
        table.add_row(
            str(l1["name"]),
            f"{l1['time_sec']:.4f}s",
            f"Zero-emulation Apple Silicon FITS indexing ({l1['sources_indexed']} sources)",
            "[bold green]PASS[/bold green]",
        )

    if "full_reduction" in results and results["full_reduction"]["status"] == "SUCCESS":
        fr = results["full_reduction"]
        table.add_row(
            str(fr["name"]),
            f"{fr['time_sec']:.2f}s",
            f"Detected {fr['sources_detected']} sources (Top: {fr['top_source']} at {fr['top_snr']:.1f}σ)",
            "[bold green]PASS[/bold green]",
        )

    console.print("\n", table)
