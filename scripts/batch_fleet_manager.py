#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.34.0",
#     "rich>=13.7.0",
# ]
# ///
"""
Automated Batch Fleet Manager for AWS Cloud Benchmarks.

Sequentially executes N=10 parallel spot fleets for:
1. ARM64 10 ScWs (10 nodes) -> wait for all to terminate
2. x86_64 10 ScWs (10 nodes) -> wait for all to terminate
3. ARM64 25 ScWs (10 nodes) -> wait for all to terminate
4. x86_64 25 ScWs (10 nodes) -> wait for all to terminate

Ensures aggregate vCPUs never exceed the 96 vCPU regional quota.
"""

import subprocess
import sys
import time
from datetime import datetime, timezone

import boto3
from rich.console import Console

console = Console()
ec2 = boto3.client("ec2", region_name="us-east-1")

BATCHES = [
    {"name": "Batch 1: ARM64 10 ScWs (N=10)", "arch": "arm64", "scale": "10", "parallel": "10", "subnet": "subnet-a9e00af0"},
    {"name": "Batch 2: x86_64 10 ScWs (N=10)", "arch": "x86_64", "scale": "10", "parallel": "10", "subnet": "subnet-67212b4f"},
    {"name": "Batch 3: ARM64 25 ScWs (N=10)", "arch": "arm64", "scale": "25", "parallel": "10", "subnet": "subnet-a9e00af0"},
    {"name": "Batch 4: x86_64 25 ScWs (N=10)", "arch": "x86_64", "scale": "25", "parallel": "10", "subnet": "subnet-67212b4f"},
    {"name": "Batch 5: ARM64 100 ScWs Full Rev (N=3)", "arch": "arm64", "scale": "100", "parallel": "3", "subnet": "subnet-a9e00af0"},
    {"name": "Batch 6: x86_64 100 ScWs Full Rev (N=3)", "arch": "x86_64", "scale": "100", "parallel": "3", "subnet": "subnet-67212b4f"},
]


def wait_for_instances_termination(instance_ids: list[str], poll_interval: int = 20):
    console.print(f"[cyan]Waiting for {len(instance_ids)} instance(s) to complete and terminate: {instance_ids}[/cyan]")
    while True:
        resp = ec2.describe_instances(InstanceIds=instance_ids)
        states = []
        for r in resp.get("Reservations", []):
            for inst in r.get("Instances", []):
                states.append(inst.get("State", {}).get("Name"))
        
        non_terminated = [s for s in states if s not in ("terminated", "shutting-down")]
        if not non_terminated:
            console.print("[bold green]✓ All instances in batch have completed and terminated![/bold green]")
            break
        
        console.print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Active instances remaining: {len(non_terminated)}/{len(instance_ids)} (states: {set(non_terminated)})...")
        time.sleep(poll_interval)


def launch_and_manage():
    for idx, b in enumerate(BATCHES, 1):
        console.print("\n[bold magenta]==================================================[/bold magenta]")
        console.print(f"[bold green]Starting {b['name']}[/bold green]")
        console.print("[bold magenta]==================================================[/bold magenta]")
        
        cmd = [
            "uv", "run", "python", "scripts/run_aws_cloud_benchmark.py",
            "--arch", b["arch"],
            "--scale", b["scale"],
            "--parallel", b["parallel"],
            "--subnet-id", b["subnet"],
            "--volume-size", "40",
            "--spot",
        ]
        
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        console.print(p.stdout)
        if p.returncode != 0:
            console.print(f"[bold red]Failed launching {b['name']}: {p.stderr}[/bold red]")
            sys.exit(1)
            
        # Parse launched instance IDs from output
        # Look for "✓ Launched Node ...: i-..."
        instance_ids = []
        for line in p.stdout.splitlines():
            if "Launched Node" in line and "i-" in line:
                parts = line.split("i-")
                if len(parts) > 1:
                    iid = "i-" + parts[1].split()[0]
                    instance_ids.append(iid)
                    
        if not instance_ids:
            console.print("[bold red]Could not find instance IDs in output![/bold red]")
            sys.exit(1)
            
        console.print(f"[green]Successfully tracked {len(instance_ids)} instances for {b['name']}[/green]")
        wait_for_instances_termination(instance_ids)
        console.print(f"[bold cyan]✓ {b['name']} completely finished![/bold cyan]")
        time.sleep(10)

    console.print("\n[bold green]🎉 ALL 4 EXPERIMENTAL BATCHES (N=10) HAVE SUCCESSFULLY COMPLETED! 🎉[/bold green]")


if __name__ == "__main__":
    launch_and_manage()
