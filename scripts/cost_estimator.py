#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "rich>=13.7.0",
#     "typer>=0.12.0",
# ]
# ///
"""
AWS Cloud Cost Model & Accounting Engine for INTEGRAL OSA Science Analysis.

Computes exact and projected costs for:
1. S3 Storage & API Requests (PUT, GET)
2. Ingress & Egress Data Transfer (HEASARC -> AWS, VPC S3 -> EC2, Internet Egress)
3. Compute Costs (ARM64 Graviton3 vs x86_64 Sapphire Rapids; Spot vs On-Demand)
4. EBS gp3 Root Volume Storage
"""

from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

app = typer.Typer(help="AWS Cost Modeling Engine for INTEGRAL OSA")
console = Console()

# --- AWS US-East-1 Standard Pricing Constants ---
PRICING = {
    # Compute: Spot & On-Demand $/hour
    "c7g.4xlarge": {"spot": 0.2323, "ondemand": 0.5840, "vcpu": 16, "ram_gb": 32, "arch": "arm64"},
    "c7i.4xlarge": {"spot": 0.2842, "ondemand": 0.7140, "vcpu": 16, "ram_gb": 32, "arch": "x86_64"},
    "t4g.medium":   {"spot": 0.0101, "ondemand": 0.0336, "vcpu": 2,  "ram_gb": 4,  "arch": "arm64"},
    
    # EBS gp3 Storage $/GB-month
    "ebs_gp3_per_gb_month": 0.08,  # $0.08 per GB-month (~$0.00011 per GB-hour)
    
    # S3 Standard Pricing
    "s3_storage_per_gb_month": 0.023, # $0.023 per GB-month
    "s3_put_per_1000": 0.005,         # $0.005 per 1,000 PUT/POST requests
    "s3_get_per_1000": 0.0004,        # $0.0004 per 1,000 GET requests
    
    # Data Transfer
    "data_transfer_ingress": 0.00,       # Inbound from Internet is FREE
    "data_transfer_s3_to_ec2_same_region": 0.00, # In-region S3 -> EC2 is FREE
    "data_transfer_internet_egress_per_gb": 0.09 # First 100 GB/mo is free, then $0.09/GB
}


def calculate_run_cost(
    arch: str,
    scw_count: int,
    elapsed_seconds: float,
    ebs_gb: float = 80.0,
    is_spot: bool = True,
) -> dict:
    instance_type = "c7g.4xlarge" if arch.lower() in ("arm64", "aarch64") else "c7i.4xlarge"
    rate_hr = PRICING[instance_type]["spot"] if is_spot else PRICING[instance_type]["ondemand"]
    
    elapsed_hours = elapsed_seconds / 3600.0
    compute_cost = elapsed_hours * rate_hr
    
    # EBS cost pro-rated per hour (730 hours in average month)
    ebs_cost = (ebs_gb * PRICING["ebs_gp3_per_gb_month"] / 730.0) * elapsed_hours
    
    # S3 read cost (internal transfer is $0; GET request cost for ~20 files per ScW)
    total_get_requests = scw_count * 20
    s3_get_cost = (total_get_requests / 1000.0) * PRICING["s3_get_per_1000"]
    
    total = compute_cost + ebs_cost + s3_get_cost
    return {
        "instance_type": instance_type,
        "rate_hr": rate_hr,
        "compute_cost": compute_cost,
        "ebs_cost": ebs_cost,
        "s3_get_cost": s3_get_cost,
        "total_cost": total,
        "cost_per_scw": total / scw_count if scw_count > 0 else 0,
    }


@app.command()
def report(
    arm_100_seconds: float = typer.Option(2350.0, "--arm-seconds", help="Observed ARM64 Graviton3 100-ScW duration (sec)"),
    x86_100_seconds: float = typer.Option(2800.0, "--x86-seconds", help="Observed x86_64 Sapphire Rapids 100-ScW duration (sec)"),
    stager_seconds: float = typer.Option(180.0, "--stager-seconds", help="Stager runtime (sec)"),
):
    """Generate detailed AWS cost accounting table for the INTEGRAL paper/blog."""
    console.print(Panel("[bold green]AWS Cloud Compute & Data Architecture Cost Accounting[/bold green]\nRegion: [cyan]us-east-1 (N. Virginia)[/cyan]", title="Cost Model"))

    # 1. Staging Breakdown
    # Total files in Rev 0060: ~2,000 files, ~2.5 GB
    stager_hours = stager_seconds / 3600.0
    stager_compute = stager_hours * PRICING["t4g.medium"]["spot"]
    stager_ebs = (40.0 * PRICING["ebs_gp3_per_gb_month"] / 730.0) * stager_hours
    s3_put_cost = (2000 / 1000.0) * PRICING["s3_put_per_1000"] # 2,000 PUTs
    s3_storage_month = 2.5 * PRICING["s3_storage_per_gb_month"]
    
    t_stage = Table(title="1. One-Time Data Ingestion & Storage Costs (Rev 0060, ~2.5 GB, 2,000 files)")
    t_stage.add_column("Component", style="cyan")
    t_stage.add_column("Pricing Rule", style="dim")
    t_stage.add_column("Measured Metric", style="yellow")
    t_stage.add_column("Effective Cost", style="bold green")

    t_stage.add_row("Internet Ingress (HEASARC -> AWS)", "$0.00 / GB", "2.5 GB", "$0.0000 (Free)")
    t_stage.add_row("Ephemeral Stager Compute (t4g.medium)", "$0.0101 / hr (Spot)", f"{stager_seconds:.0f} sec ({stager_hours:.3f} hr)", f"${stager_compute:.5f}")
    t_stage.add_row("Stager Root EBS Disk (40 GB gp3)", "$0.08 / GB-mo (pro-rated)", f"{stager_seconds:.0f} sec", f"${stager_ebs:.5f}")
    t_stage.add_row("S3 PUT API Requests", "$0.005 per 1,000 PUTs", "2,000 requests", f"${s3_put_cost:.5f}")
    t_stage.add_row("S3 Standard Storage (per month)", "$0.023 / GB-month", "2.5 GB stored", f"${s3_storage_month:.4f} / mo")
    
    total_one_time_stage = stager_compute + stager_ebs + s3_put_cost
    t_stage.add_row("[bold]Total Ingestion Phase[/bold]", "", "", f"[bold green]${total_one_time_stage:.4f}[/bold green]")
    console.print(t_stage)
    console.print()

    # 2. Benchmark Execution Breakdown
    arm_cost = calculate_run_cost("arm64", 100, arm_100_seconds, 80.0, is_spot=True)
    x86_cost = calculate_run_cost("x86_64", 100, x86_100_seconds, 80.0, is_spot=True)
    
    arm_od_cost = calculate_run_cost("arm64", 100, arm_100_seconds, 80.0, is_spot=False)
    x86_od_cost = calculate_run_cost("x86_64", 100, x86_100_seconds, 80.0, is_spot=False)

    t_bench = Table(title="2. Full Revolution Benchmark Costs (100 Pointing ScWs, Single Repeat)")
    t_bench.add_column("Architecture", style="cyan")
    t_bench.add_column("Instance Tier", style="magenta")
    t_bench.add_column("Runtime", style="yellow")
    t_bench.add_column("Compute (Spot)", style="green")
    t_bench.add_column("EBS Disk (80GB)", style="dim")
    t_bench.add_column("S3 VPC Read", style="dim")
    t_bench.add_column("Total Spot Cost", style="bold green")
    t_bench.add_column("Total On-Demand", style="bold red")

    t_bench.add_row(
        "ARM64 (AWS Graviton3)",
        "c7g.4xlarge (16 vCPU)",
        f"{arm_100_seconds/60:.1f} min",
        f"${arm_cost['compute_cost']:.4f}",
        f"${arm_cost['ebs_cost']:.4f}",
        "$0.0008",
        f"${arm_cost['total_cost']:.4f}",
        f"${arm_od_cost['total_cost']:.4f}",
    )
    t_bench.add_row(
        "x86_64 (Intel Sapphire Rapids)",
        "c7i.4xlarge (16 vCPU)",
        f"{x86_100_seconds/60:.1f} min",
        f"${x86_cost['compute_cost']:.4f}",
        f"${x86_cost['ebs_cost']:.4f}",
        "$0.0008",
        f"${x86_cost['total_cost']:.4f}",
        f"${x86_od_cost['total_cost']:.4f}",
    )
    console.print(t_bench)
    console.print()

    # 3. Triple Repeat Experiment Total
    t_triple = Table(title="3. Complete Phase B Cloud Experiment (3x Repeats in Parallel)")
    t_triple.add_column("Experimental Campaign", style="cyan")
    t_triple.add_column("Fleet Topology", style="magenta")
    t_triple.add_column("Wall Clock Time", style="yellow")
    t_triple.add_column("Total AWS Spot Cost", style="bold green")

    t_triple.add_row("ARM64 3x Repeats (Full Rev 0060)", "3x c7g.4xlarge in parallel", f"{arm_100_seconds/60:.1f} min", f"${arm_cost['total_cost'] * 3:.3f}")
    t_triple.add_row("x86_64 3x Repeats (Full Rev 0060)", "3x c7i.4xlarge in parallel", f"{x86_100_seconds/60:.1f} min", f"${x86_cost['total_cost'] * 3:.3f}")
    t_triple.add_row("Combined Both Architectures (6 Runs)", "6 Spot Instances Total", f"{max(arm_100_seconds, x86_100_seconds)/60:.1f} min", f"${(arm_cost['total_cost'] + x86_cost['total_cost']) * 3:.3f}")
    console.print(t_triple)
    console.print()

    # 4. Large-Scale Science Mission Projection (Mission-Scale Economics)
    t_proj = Table(title="4. Mission-Scale Projection: Processing 1,000 INTEGRAL Revolutions (~100,000 ScWs)")
    t_proj.add_column("Phase", style="cyan")
    t_proj.add_column("Volume / Sizing", style="yellow")
    t_proj.add_column("Cost on ARM64 Graviton3", style="bold green")
    t_proj.add_column("Cost on x86_64 Intel Xeon", style="bold blue")

    t_proj.add_row("Archive Ingestion to S3", "2.5 TB (2,000,000 files)", "$10.00 (PUT APIs)", "$10.00 (PUT APIs)")
    t_proj.add_row("S3 Storage (per year)", "2.5 TB retained in S3", "$690.00 / year", "$690.00 / year")
    t_proj.add_row("Full Pipeline Reduction (Spot)", "1,000 Revolutions processed", f"${arm_cost['total_cost'] * 1000:.2f}", f"${x86_cost['total_cost'] * 1000:.2f}")
    t_proj.add_row("Cost Savings with ARM64", "Graviton3 vs Sapphire Rapids", "BASELINE", f"+${(x86_cost['total_cost'] - arm_cost['total_cost']) * 1000:.2f} (+{(x86_cost['total_cost']/arm_cost['total_cost'] - 1)*100:.1f}%)")
    console.print(t_proj)


if __name__ == "__main__":
    app()
