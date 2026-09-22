#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.34.0",
#     "rich>=13.7.0",
# ]
# ///
"""Pull exact actual AWS billing data from Cost Explorer and compare against empirical run metrics."""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def get_cost_explorer_breakdown():
    ce = boto3.client("ce", region_name="us-east-1")

    # 1. Total Daily Spend
    daily_resp = ce.get_cost_and_usage(
        TimePeriod={"Start": "2026-09-01", "End": "2026-09-23"},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    service_totals: dict[str, float] = {}
    daily_totals: dict[str, float] = {}

    for day_entry in daily_resp.get("ResultsByTime", []):
        day_str = day_entry["TimePeriod"]["Start"]
        daily_sum = 0.0
        for group in day_entry.get("Groups", []):
            svc = group["Keys"][0]
            amt = float(group["Metrics"]["UnblendedCost"]["Amount"])
            service_totals[svc] = service_totals.get(svc, 0.0) + amt
            daily_sum += amt
        daily_totals[day_str] = daily_sum

    # 2. Usage Type breakdown for EC2 and S3
    usage_resp = ce.get_cost_and_usage(
        TimePeriod={"Start": "2026-09-01", "End": "2026-09-23"},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost", "UsageQuantity"],
        GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
    )

    usage_breakdown = []
    for group in usage_resp.get("ResultsByTime", [{}])[0].get("Groups", []):
        utype = group["Keys"][0]
        cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
        qty = float(group["Metrics"]["UsageQuantity"]["Amount"])
        unit = group["Metrics"]["UsageQuantity"]["Unit"]
        if cost > 0.0001:
            usage_breakdown.append(
                {"usage_type": utype, "cost": cost, "quantity": qty, "unit": unit}
            )

    return daily_totals, service_totals, usage_breakdown


def get_empirical_s3_results_spend():
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "integral-cloud-analysis-data-537472396676"
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="results/")

    runs = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if not key.endswith(".json") or "cloud_results_" not in key:
            continue
        try:
            data = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))
            runs.append((key, data))
        except (KeyError, json.JSONDecodeError, ClientError) as e:
            console.print(f"[yellow]Warning: Could not parse {key}: {e}[/yellow]")

    return runs


def main():
    console.print(
        Panel(
            "[bold green]Actual AWS Incurred Costs & Billing Analysis[/bold green]\n"
            "[dim]Data queried live from AWS Cost Explorer API (M-T-D September 2026)[/dim]"
        )
    )

    daily_totals, service_totals, usage_breakdown = get_cost_explorer_breakdown()

    # Table 1: Service Breakdown
    t_svc = Table(title="Total Incurred AWS Charges by Service (Sept 1 – Sept 22, 2026)")
    t_svc.add_column("AWS Service", style="cyan")
    t_svc.add_column("Actual Billed Cost (USD)", justify="right", style="bold green")

    total_spend = sum(service_totals.values())
    for svc, amt in sorted(service_totals.items(), key=lambda x: x[1], reverse=True):
        if amt > 0.0001:
            t_svc.add_row(svc, f"${amt:.4f}")
    t_svc.add_section()
    t_svc.add_row("Total Incurred AWS Spend", f"${total_spend:.4f}", style="bold yellow")
    console.print(t_svc)

    # Table 2: Daily Breakdown
    t_daily = Table(title="Daily Incurred Spend (Key Testing & Benchmark Days)")
    t_daily.add_column("Date", style="cyan")
    t_daily.add_column("Cost (USD)", justify="right", style="green")
    t_daily.add_column("Activity / Milestone", style="dim")

    milestones = {
        "2026-09-17": "HEASARC S3 Staging & Mirroring Worker",
        "2026-09-18": "CALDB Sync Worker & Instrument Characterization Sync",
        "2026-09-19": "DAL Diagnostics & ScW Diagnostics Fleet",
        "2026-09-20": "Initial 10-Node Benchmark Fleet (Phase B)",
        "2026-09-21": "Inter-run idle / Data validation",
        "2026-09-22": "Ephemeral Cloud Builder & 25-ScW / 100-ScW Fleets",
    }

    for date_str, cost in sorted(daily_totals.items()):
        if cost > 0.001 or date_str in milestones:
            note = milestones.get(date_str, "Standard Storage & Requests")
            t_daily.add_row(date_str, f"${cost:.4f}", note)

    console.print(t_daily)

    # Table 3: Usage Type Breakdown (EC2 Compute vs EBS vs S3)
    t_usage = Table(title="Detailed Cost by Usage Type (Compute, Disk, API)")
    t_usage.add_column("Usage Type", style="cyan")
    t_usage.add_column("Quantity", justify="right")
    t_usage.add_column("Unit", justify="left", style="dim")
    t_usage.add_column("Actual Billed Cost (USD)", justify="right", style="bold green")

    for u in sorted(usage_breakdown, key=lambda x: x["cost"], reverse=True):
        t_usage.add_row(u["usage_type"], f"{u['quantity']:.2f}", u["unit"], f"${u['cost']:.4f}")
    console.print(t_usage)


if __name__ == "__main__":
    main()
