#!/usr/bin/env python3
"""Comprehensive Benchmark Matrix Compiler for INTEGRAL OSA Modernisation.

Aggregates benchmark results across all tested architectures:
  - Apple M4 Pro (Native ARM64 & Rosetta 2)
  - AWS Graviton3 (c7g.xlarge)
  - AWS Graviton4 (c8g.xlarge)
  - AWS Intel Xeon (c7i.xlarge)
  - AWS AMD EPYC Genoa (c7a.xlarge)
  - On-premise Workstation Intel Core i7-3770 (purplespark)

Computes statistical means, standard errors, per-ScW runtimes,
cost efficiencies, and energy ratings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
S3_BUCKET = "integral-cloud-analysis-data-537472396676"
RESULTS_PREFIX = "results/"

# Static baseline results for Apple M4 Pro (measured with N=3)
APPLE_M4_PRO = {
    "10": {
        "native": {"total": 199.76, "se": 0.19, "per_scw": 19.98, "n": 3, "watts": 28.0},
        "rosetta": {"total": 520.09, "se": 0.35, "per_scw": 52.01, "n": 3, "watts": 32.0},
    },
    "25": {
        "native": {"total": 513.14, "se": 1.25, "per_scw": 20.53, "n": 3, "watts": 28.0},
        "rosetta": {"total": 1333.50, "se": 1.58, "per_scw": 53.34, "n": 3, "watts": 32.0},
    },
    "100": {
        "native": {"total": 2361.27, "se": 0.0, "per_scw": 23.61, "n": 1, "watts": 28.0},
        "rosetta": {"total": 5887.61, "se": 0.0, "per_scw": 56.61, "n": 1, "watts": 32.0},
    },
}

POWER_ESTIMATES = {
    "c8g.xlarge": 30.0,    # ~30W allocated socket power (TSMC 4nm)
    "c7g.xlarge": 35.0,    # ~35W allocated socket power (TSMC 5nm)
    "c7a.xlarge": 70.0,    # ~70W allocated socket power (Zen 4, TSMC 5nm)
    "c7i.xlarge": 85.0,    # ~85W allocated socket power (Sapphire Rapids)
    "i7_3770": 70.0,       # ~70W under load (22nm Ivy Bridge desktop)
}

HOURLY_SPOT_PRICING = {
    "c8g.xlarge": 0.0600,
    "c7g.xlarge": 0.0581,
    "c7a.xlarge": 0.0652,
    "c7i.xlarge": 0.0711,
    "i7_3770": 0.0000,     # On-premise owned hardware
}


def load_all_json_data() -> dict[str, list[dict[str, Any]]]:
    """Load all JSON files either from local folder or S3."""
    datasets: dict[str, list[dict[str, Any]]] = {}

    # Check local directories first
    candidate_dirs = [
        Path.home() / "benchmark_results_all",
        Path.home() / "experiments" / "integral-osa-modern" / "benchmark_runs" / "phase_b",
        Path.cwd() / "benchmark_runs" / "phase_b",
    ]

    local_files: list[Path] = []
    for d in candidate_dirs:
        if d.exists():
            local_files.extend(d.glob("*.json"))

    if local_files:
        console.print(f"[dim]Reading {len(local_files)} JSON files from local directories...[/dim]")
        for f in local_files:
            try:
                with open(f) as fp:
                    content = json.load(fp)
                    fname = f.name
                    datasets[fname] = content
            except Exception as e:
                console.print(f"[dim yellow]Warning reading {f}: {e}[/dim yellow]")

    # Also try S3 if boto3 is configured
    try:
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=RESULTS_PREFIX)
        s3_json_keys = [x["Key"] for x in resp.get("Contents", []) if x["Key"].endswith(".json")]
        console.print(f"[dim]Discovered {len(s3_json_keys)} JSON files in S3...[/dim]")
        for k in s3_json_keys:
            fname = Path(k).name
            if fname not in datasets:
                obj = s3.get_object(Bucket=S3_BUCKET, Key=k)
                content = json.loads(obj["Body"].read().decode("utf-8"))
                datasets[fname] = content
    except Exception as e:
        console.print(f"[dim yellow]Note: S3 direct query skipped ({e}); using local files.[/dim yellow]")

    return datasets


def parse_cloud_runs(datasets: dict[str, Any]) -> dict[str, dict[str, list[float]]]:
    """Extract runtimes per architecture and scale (10, 25, 100)."""
    # Key: arch_label -> scale ('10', '25', '100') -> list of elapsed_seconds
    data_by_platform: dict[str, dict[str, list[float]]] = {
        "Graviton4 (c8g.xlarge)": {"10": [], "25": [], "100": []},
        "AMD Genoa (c7a.xlarge)": {"10": [], "25": [], "100": []},
        "Graviton3 (c7g.xlarge)": {"10": [], "25": [], "100": []},
        "Intel Xeon (c7i.xlarge)": {"10": [], "25": [], "100": []},
        "i7-3770 (purplespark)": {"10": [], "25": [], "100": []},
    }

    for fname, content in datasets.items():
        # Handle purplespark workstation results
        if "i7_3770" in fname or "results_i7_3770" in fname:
            if "cells" in content:
                for cell in content.get("cells", []):
                    scw_cnt = str(cell.get("scw_count", ""))
                    k = "100" if scw_cnt in ("100", "104", "full") else scw_cnt
                    if k in data_by_platform["i7-3770 (purplespark)"]:
                        if cell.get("timings"):
                            for t_val in cell["timings"]:
                                data_by_platform["i7-3770 (purplespark)"][k].append(float(t_val))
                        elif "runs" in cell:
                            for r in cell["runs"]:
                                if "elapsed_seconds" in r:
                                    data_by_platform["i7-3770 (purplespark)"][k].append(float(r["elapsed_seconds"]))
            elif "raw_runs" in content:
                for scale_key, runs in content["raw_runs"].items():
                    k = "100" if scale_key in ("100", "full") else scale_key
                    if k in data_by_platform["i7-3770 (purplespark)"]:
                        for r in runs:
                            if "wall_clock_seconds" in r:
                                data_by_platform["i7-3770 (purplespark)"][k].append(float(r["wall_clock_seconds"]))
            elif "summary_matrix" in content:
                for row in content["summary_matrix"]:
                    sz = str(row.get("scw_size", "")).lower().replace(" scws", "").strip()
                    k = "100" if sz in ("100", "full") else sz
                    if k in data_by_platform["i7-3770 (purplespark)"] and "mean_wallclock" in row:
                        data_by_platform["i7-3770 (purplespark)"][k].append(float(row["mean_wallclock"]))

        # Handle c8g (Graviton4)
        elif "c8g" in fname:
            runs = content.get("runs", [])
            for r in runs:
                scw_cnt = str(r.get("scw_count", ""))
                el = float(r.get("elapsed_seconds", 0.0))
                if scw_cnt in ("10", "25", "100") and el > 0:
                    data_by_platform["Graviton4 (c8g.xlarge)"][scw_cnt].append(el)

        # Handle c7a (AMD Genoa)
        elif "c7a" in fname:
            runs = content.get("runs", [])
            for r in runs:
                scw_cnt = str(r.get("scw_count", ""))
                el = float(r.get("elapsed_seconds", 0.0))
                if scw_cnt in ("10", "25", "100") and el > 0:
                    data_by_platform["AMD Genoa (c7a.xlarge)"][scw_cnt].append(el)

        # Handle older Graviton3 (arm64 without c8g)
        elif "cloud_results_arm64_" in fname and "c8g" not in fname:
            runs = content.get("runs", [])
            for r in runs:
                scw_cnt = str(r.get("scw_count", ""))
                el = float(r.get("elapsed_seconds", 0.0))
                if scw_cnt in ("10", "25", "100") and el > 0:
                    data_by_platform["Graviton3 (c7g.xlarge)"][scw_cnt].append(el)

        # Handle older Intel Xeon (x86_64 without c7a)
        elif "cloud_results_x86_64_" in fname and "c7a" not in fname:
            runs = content.get("runs", [])
            for r in runs:
                scw_cnt = str(r.get("scw_count", ""))
                el = float(r.get("elapsed_seconds", 0.0))
                if scw_cnt in ("10", "25", "100") and el > 0:
                    data_by_platform["Intel Xeon (c7i.xlarge)"][scw_cnt].append(el)

    return data_by_platform


def compute_metrics(times: list[float], scale: int) -> dict[str, float]:
    if not times:
        return {"n": 0, "mean": 0.0, "se": 0.0, "per_scw": 0.0}
    arr = np.array(times)
    mean = float(np.mean(arr))
    n = len(arr)
    se = float(np.std(arr, ddof=1) / np.sqrt(n)) if n > 1 else 0.0
    return {"n": n, "mean": mean, "se": se, "per_scw": mean / scale}


def main():
    console.print(Panel.fit("[bold cyan]INTEGRAL OSA Modernisation: Unified Benchmark Synthesizer[/bold cyan]\n"
                            "Synthesizing all 6 hardware architectures across Tiers A, B, and C."))

    datasets = load_all_json_data()
    platform_data = parse_cloud_runs(datasets)

    for scale_str, scale_int in [("10", 10), ("25", 25), ("100", 100)]:
        t = Table(title=f"Tier: {scale_str} Science Windows (N={scale_int})")
        t.add_column("Platform / Architecture", style="cyan", no_wrap=True)
        t.add_column("Samples", justify="center")
        t.add_column("Mean Wall-Clock [s]", justify="right", style="bold green")
        t.add_column("SE [s]", justify="right")
        t.add_column("Per-ScW [s/ScW]", justify="right", style="bold yellow")
        t.add_column("Speedup vs Rosetta", justify="right")
        t.add_column("Energy / ScW [Joules]", justify="right", style="dim")
        t.add_column("Spot Cost / Run", justify="right", style="dim")

        rosetta_info = APPLE_M4_PRO[scale_str]["rosetta"]
        rosetta_mean = rosetta_info["total"]

        # 1. Apple M4 Pro Native
        m4_nat = APPLE_M4_PRO[scale_str]["native"]
        speedup_m4 = f"{rosetta_mean / m4_nat['total']:.2f}x"
        joules_m4 = m4_nat["per_scw"] * m4_nat["watts"]
        t.add_row(
            "Apple M4 Pro (AArch64 Native)",
            f"N={m4_nat['n']}",
            f"{m4_nat['total']:.2f}",
            f"±{m4_nat['se']:.2f}",
            f"{m4_nat['per_scw']:.2f}",
            speedup_m4,
            f"{joules_m4:.0f} J",
            "— (Local)",
        )

        # 2. Apple M4 Pro Rosetta
        t.add_row(
            "Apple M4 Pro (Rosetta 2 Emulated)",
            f"N={rosetta_info['n']}",
            f"{rosetta_info['total']:.2f}",
            f"±{rosetta_info['se']:.2f}",
            f"{rosetta_info['per_scw']:.2f}",
            "1.00x (Ref)",
            f"{rosetta_info['per_scw'] * rosetta_info['watts']:.0f} J",
            "— (Local)",
        )

        # 3. Dynamic Platforms
        for plat_name in [
            "Graviton4 (c8g.xlarge)",
            "AMD Genoa (c7a.xlarge)",
            "Intel Xeon (c7i.xlarge)",
            "Graviton3 (c7g.xlarge)",
            "i7-3770 (purplespark)",
        ]:
            times = platform_data[plat_name][scale_str]
            m = compute_metrics(times, scale_int)
            if m["n"] > 0:
                speedup = f"{rosetta_mean / m['mean']:.2f}x"
                # Power estimate key
                key = "i7_3770" if "i7-3770" in plat_name else plat_name.split("(")[1].replace(")", "").strip()
                watts = POWER_ESTIMATES.get(key, 50.0)
                joules = m["per_scw"] * watts
                rate = HOURLY_SPOT_PRICING.get(key, 0.0)
                cost = (m["mean"] / 3600.0) * rate
                cost_str = f"${cost:.4f}" if rate > 0 else "$0.00 (On-prem)"

                t.add_row(
                    plat_name,
                    f"N={m['n']}",
                    f"{m['mean']:.2f}",
                    f"±{m['se']:.2f}",
                    f"{m['per_scw']:.2f}",
                    speedup,
                    f"{joules:.0f} J",
                    cost_str,
                )
            else:
                t.add_row(plat_name, "N=0", "—", "—", "—", "—", "—", "—")

        console.print(t)
        console.print()


if __name__ == "__main__":
    main()
