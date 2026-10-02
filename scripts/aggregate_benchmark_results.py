#!/usr/bin/env python3
"""Statistical Aggregator for AWS Cloud Benchmark Results.

Aggregates N=10 parallel results for ARM64 and x86_64 from S3, computing
pipeline runtimes, data transfer overhead, and scientific detection consistency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import boto3
import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
S3_BUCKET = "integral-cloud-analysis-data-537472396676"
RESULTS_PREFIX = "results/"


def fetch_results_from_s3(scale: str = "10") -> dict[str, list[dict[str, Any]]]:
    s3 = boto3.client("s3", region_name="us-east-1")
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=RESULTS_PREFIX)

    results: dict[str, list[dict[str, Any]]] = {"arm64": [], "x86_64": []}

    for obj in resp.get("Contents", []):
        key = obj["Key"]
        filename = Path(key).name
        if not filename.endswith(".json") or not filename.startswith("cloud_results_"):
            continue

        # Match requested scale: e.g. cloud_results_arm64_10_rep*.json
        if f"_{scale}_rep" not in filename:
            continue

        arch = "arm64" if "arm64" in filename else ("x86_64" if "x86_64" in filename else None)
        if not arch:
            continue

        data_obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        try:
            content = json.loads(data_obj["Body"].read().decode("utf-8"))
            content["_filename"] = filename
            content["_last_modified"] = obj["LastModified"].isoformat()
            results[arch].append(content)
        except Exception as e:
            console.print(f"[dim yellow]Warning parsing {filename}: {e}[/dim yellow]")

    return results


def format_stats(values: list[float], unit: str = "s") -> str:
    if not values:
        return "N/A"
    arr = np.array(values)
    mean = np.mean(arr)
    s = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
    se = float(s / np.sqrt(len(arr))) if len(arr) > 1 else 0.0
    median = float(np.median(arr))
    q25, q75 = np.percentile(arr, [25, 75])
    iqr = float(q75 - q25)
    return f"{mean:.2f} ± {se:.2f}{unit} (s={s:.2f}, med: {median:.2f}, IQR: {iqr:.2f})"


def summarize_fleet(arch_label: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    pipeline_times = []
    per_scw_times = []
    pull_times = []
    transfer_times = []
    source_sigs = []
    source_fluxes = []
    source_name = "Target"
    success_count = 0

    for r in runs:
        tb = r.get("timing_breakdown", {})
        if "docker_pull_seconds" in tb:
            pull_times.append(float(tb["docker_pull_seconds"]))
        if "total_data_transfer_seconds" in tb:
            transfer_times.append(float(tb["total_data_transfer_seconds"]))

        for run_entry in r.get("runs", []):
            if run_entry.get("returncode") == 0:
                success_count += 1
                pipeline_times.append(float(run_entry.get("elapsed_seconds", 0.0)))
                per_scw_times.append(float(run_entry.get("per_scw_seconds", 0.0)))
                sci = run_entry.get("scientific_results", {})
                if sci.get("detected"):
                    source_name = sci.get("name", "Target")
                    source_sigs.append(float(sci.get("detsig", 0.0)))
                    source_fluxes.append(float(sci.get("flux", 0.0)))

    return {
        "count": len(runs),
        "success_count": success_count,
        "pipeline_times": pipeline_times,
        "per_scw_times": per_scw_times,
        "pull_times": pull_times,
        "transfer_times": transfer_times,
        "source_name": source_name,
        "source_sigs": source_sigs,
        "source_fluxes": source_fluxes,
    }


def main():
    scale = "10"
    for arg in sys.argv[1:]:
        if arg.isdigit():
            scale = arg
        elif arg.startswith("--scale="):
            scale = arg.split("=")[1]

    console.print(
        Panel(
            f"[bold cyan]INTEGRAL Cloud Benchmark Fleet Aggregator (Rev 0060, {scale} ScWs to IMA2)[/bold cyan]"
        )
    )

    results = fetch_results_from_s3(scale=scale)
    arm_runs = sorted(results["arm64"], key=lambda x: int(x.get("repeat_index", 0)))
    x86_runs = sorted(results["x86_64"], key=lambda x: int(x.get("repeat_index", 0)))

    console.print(
        f"Discovered results in S3: [bold green]{len(arm_runs)} ARM64[/bold green] | [bold yellow]{len(x86_runs)} x86_64[/bold yellow]"
    )

    # Detailed per-node table
    table = Table(title=f"Parallel Node Results ({scale} ScWs, COR -> IMA2)")
    table.add_column("Arch", style="cyan")
    table.add_column("Node / Rep", justify="center")
    table.add_column("Status", justify="center")
    table.add_column("Docker Pull", justify="right")
    table.add_column("Data Transfer", justify="right")
    table.add_column("Pipeline (IMA2)", justify="right", style="bold green")
    table.add_column("Per-ScW", justify="right")
    table.add_column("DetSig", justify="right", style="bold yellow")
    table.add_column("Flux", justify="right")

    all_entries = [("ARM64", r) for r in arm_runs] + [("x86_64", r) for r in x86_runs]
    for arch, r in all_entries:
        rep_idx = r.get("repeat_index", "?")
        tb = r.get("timing_breakdown", {})
        pull_s = f"{float(tb.get('docker_pull_seconds', 0)):.1f}s"
        xfer_s = f"{float(tb.get('total_data_transfer_seconds', 0)):.1f}s"

        runs_list = r.get("runs", [])
        if runs_list:
            re = runs_list[0]
            status = (
                "[green]SUCCESS[/green]"
                if re.get("returncode") == 0
                else f"[red]FAIL ({re.get('returncode')})[/red]"
            )
            pipe_s = f"{float(re.get('elapsed_seconds', 0)):.1f}s"
            pscw_s = f"{float(re.get('per_scw_seconds', 0)):.1f}s"
            sci = re.get("scientific_results", {})
            if sci.get("detected"):
                detsig = f"{float(sci.get('detsig', 0)):.1f}σ"
                flux = f"{float(sci.get('flux', 0)):.2f} c/s"
            else:
                detsig = "[red]No detection[/red]"
                flux = "N/A"
        else:
            status = "[dim]No runs[/dim]"
            pipe_s = "N/A"
            pscw_s = "N/A"
            detsig = "N/A"
            flux = "N/A"

        table.add_row(arch, f"Node {rep_idx}", status, pull_s, xfer_s, pipe_s, pscw_s, detsig, flux)

    console.print(table)

    # Statistical comparison
    arm_stats = summarize_fleet("ARM64", arm_runs)
    x86_stats = summarize_fleet("x86_64", x86_runs)

    summary_table = Table(title="Statistical Comparison & Architecture Speedup")
    summary_table.add_column("Metric", style="bold")
    summary_table.add_column("Native ARM64 (Graviton3)", style="cyan")
    summary_table.add_column("Modern x86_64 (Xeon Sapphire Rapids)", style="magenta")
    summary_table.add_column("Ratio / Speedup", style="bold green")

    summary_table.add_row(
        "Nodes Completed",
        f"{arm_stats['success_count']}/{arm_stats['count']}",
        f"{x86_stats['success_count']}/{x86_stats['count']}",
        "—",
    )
    summary_table.add_row(
        "Docker Image Pull",
        format_stats(arm_stats["pull_times"]),
        format_stats(x86_stats["pull_times"]),
        "—",
    )
    summary_table.add_row(
        "S3 Data Transfer",
        format_stats(arm_stats["transfer_times"]),
        format_stats(x86_stats["transfer_times"]),
        "—",
    )

    arm_pipe_mean = np.mean(arm_stats["pipeline_times"]) if arm_stats["pipeline_times"] else None
    x86_pipe_mean = np.mean(x86_stats["pipeline_times"]) if x86_stats["pipeline_times"] else None
    pipe_ratio = (
        f"{arm_pipe_mean / x86_pipe_mean:.2f}x faster (x86_64)"
        if (arm_pipe_mean and x86_pipe_mean)
        else "N/A"
    )

    arm_pscw_mean = np.mean(arm_stats["per_scw_times"]) if arm_stats["per_scw_times"] else None
    x86_pscw_mean = np.mean(x86_stats["per_scw_times"]) if x86_stats["per_scw_times"] else None
    pscw_ratio = (
        f"{arm_pscw_mean / x86_pscw_mean:.2f}x faster (x86_64)"
        if (arm_pscw_mean and x86_pscw_mean)
        else "N/A"
    )

    summary_table.add_row(
        "Pipeline Runtime (COR->IMA2)",
        format_stats(arm_stats["pipeline_times"]),
        format_stats(x86_stats["pipeline_times"]),
        pipe_ratio,
    )
    summary_table.add_row(
        "Per-ScW Runtime",
        format_stats(arm_stats["per_scw_times"]),
        format_stats(x86_stats["per_scw_times"]),
        pscw_ratio,
    )

    target_name = arm_stats["source_name"] or x86_stats["source_name"] or "Target"
    summary_table.add_row(
        f"{target_name} Detection Sig",
        format_stats(arm_stats["source_sigs"], unit="σ"),
        format_stats(x86_stats["source_sigs"], unit="σ"),
        f"Δ = {abs(np.mean(arm_stats['source_sigs']) - np.mean(x86_stats['source_sigs'])):.3f}σ"
        if arm_stats["source_sigs"] and x86_stats["source_sigs"]
        else "N/A",
    )
    summary_table.add_row(
        f"{target_name} Net Flux",
        format_stats(arm_stats["source_fluxes"], unit=" c/s"),
        format_stats(x86_stats["source_fluxes"], unit=" c/s"),
        f"Agreement: {100 * (1 - abs(np.mean(arm_stats['source_fluxes']) - np.mean(x86_stats['source_fluxes'])) / np.mean(arm_stats['source_fluxes'])):.2f}%"
        if arm_stats["source_fluxes"] and x86_stats["source_fluxes"]
        else "N/A",
    )

    console.print(summary_table)


if __name__ == "__main__":
    main()
