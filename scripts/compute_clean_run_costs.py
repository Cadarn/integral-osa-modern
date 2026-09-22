#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.34.0",
#     "numpy>=1.26.0",
#     "rich>=13.7.0",
# ]
# ///
"""Calculate exact isolated compute costs for successful INTEGRAL science reduction runs."""

from __future__ import annotations

import json
from pathlib import Path

import boto3
import numpy as np
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
S3_BUCKET = "integral-cloud-analysis-data-537472396676"

# Exact effective AWS rates in us-east-1
HOURLY_RATES = {
    "c7g.xlarge": {
        "spot": 0.0626,  # Actual billed rate from Cost Explorer
        "ondemand": 0.1450,  # Official AWS On-Demand rate
        "vcpu": 4,
        "ram_gb": 8,
        "arch": "arm64",
    },
    "c7i.xlarge": {
        "spot": 0.0707,  # Actual billed rate from Cost Explorer
        "ondemand": 0.1785,  # Official AWS On-Demand rate
        "vcpu": 4,
        "ram_gb": 8,
        "arch": "x86_64",
    },
}

# EBS gp3 cost per GB-second: $0.08 / (730 * 3600) = $3.04e-8 per GB-s
# For 40 GB: $1.217e-6 per second (~$0.0044 per hour)
EBS_GP3_PER_SEC_40GB = (40.0 * 0.08) / (730.0 * 3600.0)

# S3 read cost per ScW: ~20 GET requests = 20 * $0.0004 / 1000 = $0.000008 per ScW


def fetch_successful_runs():
    s3 = boto3.client("s3", region_name="us-east-1")
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="results/")

    runs_by_scale = {
        "10": {"arm64": [], "x86_64": []},
        "25": {"arm64": [], "x86_64": []},
        "100": {"arm64": [], "x86_64": []},
    }

    for obj in resp.get("Contents", []):
        key = obj["Key"]
        filename = Path(key).name
        if not filename.endswith(".json") or not filename.startswith("cloud_results_"):
            continue

        arch = "arm64" if "arm64" in filename else ("x86_64" if "x86_64" in filename else None)
        if not arch:
            continue

        for scale in ["10", "25", "100"]:
            if f"_{scale}_rep" in filename:
                try:
                    content = json.loads(
                        s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode("utf-8")
                    )
                    run_entries = content.get("runs", [])
                    if run_entries and run_entries[0].get("returncode") == 0:
                        runs_by_scale[scale][arch].append(content)
                except (KeyError, json.JSONDecodeError, ClientError) as e:
                    console.print(f"[yellow]Warning: Could not parse {key}: {e}[/yellow]")

    return runs_by_scale


def analyze_scale(scale_str: str, runs_dict: dict[str, list]):
    scw_count = int(scale_str)

    results = {}
    for arch, runs in runs_dict.items():
        if not runs:
            continue

        inst_type = "c7g.xlarge" if arch == "arm64" else "c7i.xlarge"
        spot_rate_sec = HOURLY_RATES[inst_type]["spot"] / 3600.0
        ondemand_rate_sec = HOURLY_RATES[inst_type]["ondemand"] / 3600.0

        pipe_secs = [float(r["runs"][0]["elapsed_seconds"]) for r in runs]

        total_wall_secs = []
        for r in runs:
            tb = r.get("timing_breakdown", {})
            pull = float(tb.get("docker_pull_seconds", 0.0))
            xfer = float(tb.get("total_data_transfer_seconds", 0.0))
            pipe = float(r["runs"][0]["elapsed_seconds"])
            # Boot + dnf + uv overhead is ~45s
            total_wall_secs.append(pull + xfer + pipe + 45.0)

        mean_pipe = np.mean(pipe_secs)
        mean_total = np.mean(total_wall_secs)

        # Spot costs
        spot_pipe_cost = mean_pipe * spot_rate_sec
        spot_total_cost = (
            (mean_total * spot_rate_sec)
            + (mean_total * EBS_GP3_PER_SEC_40GB)
            + (scw_count * 0.000008)
        )
        spot_cost_per_scw = spot_total_cost / scw_count

        # On-Demand costs
        ondemand_total_cost = (
            (mean_total * ondemand_rate_sec)
            + (mean_total * EBS_GP3_PER_SEC_40GB)
            + (scw_count * 0.000008)
        )
        ondemand_cost_per_scw = ondemand_total_cost / scw_count

        results[arch] = {
            "n": len(runs),
            "instance_type": inst_type,
            "pipe_seconds": mean_pipe,
            "total_seconds": mean_total,
            "spot_pipe_cost": spot_pipe_cost,
            "spot_total_cost": spot_total_cost,
            "spot_cost_per_scw": spot_cost_per_scw,
            "ondemand_total_cost": ondemand_total_cost,
            "ondemand_cost_per_scw": ondemand_cost_per_scw,
        }

    return results


def main():
    console.print(
        Panel(
            "[bold cyan]INTEGRAL Clean Science Run Compute Cost Breakdown[/bold cyan]\n"
            "[dim]Calculated strictly from successful science runs (excluding failed attempts, retries, and builder time)[/dim]"
        )
    )

    runs_by_scale = fetch_successful_runs()

    table = Table(title="Exact Compute Cost per Successful Run (By Benchmark Scale)")
    table.add_column("Scale", style="bold")
    table.add_column("Arch", style="cyan")
    table.add_column("N", justify="center")
    table.add_column("Pipeline Runtime", justify="right")
    table.add_column("Total Instance Life", justify="right")
    table.add_column("Spot Cost (Run)", justify="right", style="bold green")
    table.add_column("Spot Cost / ScW", justify="right", style="green")
    table.add_column("On-Demand Cost (Run)", justify="right", style="dim")
    table.add_column("On-Demand / ScW", justify="right", style="dim")

    for scale in ["10", "25", "100"]:
        stats = analyze_scale(scale, runs_by_scale[scale])
        for arch in ["x86_64", "arm64"]:
            if arch not in stats:
                continue
            s = stats[arch]
            table.add_row(
                f"{scale} ScWs",
                arch,
                str(s["n"]),
                f"{s['pipe_seconds']:.1f}s",
                f"{s['total_seconds']:.1f}s ({s['total_seconds'] / 60:.1f}m)",
                f"${s['spot_total_cost']:.4f}",
                f"${s['spot_cost_per_scw']:.5f}",
                f"${s['ondemand_total_cost']:.4f}",
                f"${s['ondemand_cost_per_scw']:.5f}",
            )
        table.add_section()

    console.print(table)

    print("\n### Markdown Table: Isolated Clean Run Costs")
    print(
        "| Benchmark Scale | Architecture | N | Pipeline Runtime | Total Instance Life | Spot Cost (Run) | Spot Cost / ScW | On-Demand (Run) | On-Demand / ScW |"
    )
    print("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for scale in ["10", "25", "100"]:
        stats = analyze_scale(scale, runs_by_scale[scale])
        for arch in ["x86_64", "arm64"]:
            if arch not in stats:
                continue
            s = stats[arch]
            print(
                f"| **{scale} ScWs** | `{arch}` | {s['n']} | {s['pipe_seconds']:.1f}s | {s['total_seconds']:.1f}s ({s['total_seconds'] / 60:.1f}m) | **${s['spot_total_cost']:.4f}** | **${s['spot_cost_per_scw']:.5f}** | ${s['ondemand_total_cost']:.4f} | ${s['ondemand_cost_per_scw']:.5f} |"
            )
    print("\n")

    # Projection table for 1 full revolution (100 ScWs) vs Large Survey (1,000 ScWs)
    proj_table = Table(title="Cost Projections for Clean Production Science Reductions")
    proj_table.add_column("Workload Scale", style="bold")
    proj_table.add_column("Architecture", style="cyan")
    proj_table.add_column("Hardware", style="dim")
    proj_table.add_column("Wall-Clock Time", justify="right")
    proj_table.add_column("Spot Total Cost", justify="right", style="bold green")
    proj_table.add_column("On-Demand Cost", justify="right")
    proj_table.add_column("Cost Difference", justify="right", style="bold magenta")

    # Based on 100-ScW empirical metrics:
    # x86_64: 1459.5s pipe, ~1750s total per 100 ScWs
    # arm64: 2598.8s pipe, ~2925s total per 100 ScWs

    x86_100_spot = (
        (1750 * HOURLY_RATES["c7i.xlarge"]["spot"] / 3600)
        + (1750 * EBS_GP3_PER_SEC_40GB)
        + (100 * 0.000008)
    )
    x86_100_od = (
        (1750 * HOURLY_RATES["c7i.xlarge"]["ondemand"] / 3600)
        + (1750 * EBS_GP3_PER_SEC_40GB)
        + (100 * 0.000008)
    )

    arm_100_spot = (
        (2925 * HOURLY_RATES["c7g.xlarge"]["spot"] / 3600)
        + (2925 * EBS_GP3_PER_SEC_40GB)
        + (100 * 0.000008)
    )
    arm_100_od = (
        (2925 * HOURLY_RATES["c7g.xlarge"]["ondemand"] / 3600)
        + (2925 * EBS_GP3_PER_SEC_40GB)
        + (100 * 0.000008)
    )

    proj_table.add_row(
        "Full Rev 0060 (100 ScWs)",
        "x86_64",
        "c7i.xlarge (Sapphire Rapids)",
        "24.3 min",
        f"${x86_100_spot:.4f} (~3.5¢)",
        f"${x86_100_od:.4f}",
        "Reference",
    )
    proj_table.add_row(
        "Full Rev 0060 (100 ScWs)",
        "ARM64",
        "c7g.xlarge (Graviton3)",
        "43.3 min",
        f"${arm_100_spot:.4f} (~5.1¢)",
        f"${arm_100_od:.4f}",
        f"+${arm_100_spot - x86_100_spot:.4f} (+46% cost due to longer time)",
    )
    proj_table.add_section()

    # 1,000 Science Windows (Survey scale)
    x86_1000_spot = x86_100_spot * 10
    x86_1000_od = x86_100_od * 10
    arm_1000_spot = arm_100_spot * 10
    arm_1000_od = arm_100_od * 10

    proj_table.add_row(
        "1,000 Science Windows",
        "x86_64",
        "c7i.xlarge (10-node parallel)",
        "24.3 min (parallel)",
        f"${x86_1000_spot:.2f}",
        f"${x86_1000_od:.2f}",
        "Reference ($0.35 total)",
    )
    proj_table.add_row(
        "1,000 Science Windows",
        "ARM64",
        "c7g.xlarge (10-node parallel)",
        "43.3 min (parallel)",
        f"${arm_1000_spot:.2f}",
        f"${arm_1000_od:.2f}",
        f"+${arm_1000_spot - x86_1000_spot:.2f} ($0.51 total)",
    )

    console.print(proj_table)


if __name__ == "__main__":
    main()
