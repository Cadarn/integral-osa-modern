#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.34.0",
#     "rich>=13.7.0",
#     "typer>=0.12.0",
# ]
# ///
"""
Launch an Ephemeral EC2 Worker to Mirror INTEGRAL Calibration Data to S3.

Spins up a cost-capped Spot EC2 instance (c7i.large) in us-east-1 that runs a cloud-init
script to mirror the official Instrument Characteristics (IC) tree and reference catalog
from NASA HEASARC directly into s3://<bucket>/caldb/.

Upon completion, the instance immediately self-terminates (shutdown -h now).
"""

import base64
import sys

import boto3
import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(help="Launch Ephemeral EC2 Worker to Sync Calibration Data to S3")
console = Console()

DEFAULT_BUCKET = "integral-cloud-analysis-data-537472396676"
DEFAULT_REGION = "us-east-1"
DEFAULT_AMI = "ami-05dee78f58650ed2c"  # Amazon Linux 2023 x86_64
DEFAULT_SUBNET = "subnet-a9e00af0"  # us-east-1c default subnet
IAM_PROFILE_NAME = "IntegralCloudBenchmarkProfile"


def generate_cloud_init_sync_script(s3_bucket: str) -> str:
    """Generate the user-data script for the ephemeral EC2 calibration sync worker."""
    return f"""#!/bin/bash
set -euxo pipefail

exec > >(tee -a /var/log/calibration_sync.log | logger -t user-data -s 2>/dev/console) 2>&1

echo "=================================================="
echo "Starting INTEGRAL Calibration Sync Worker"
echo "Target S3 Bucket: s3://{s3_bucket}/caldb/"
echo "Time: $(date -u)"
echo "Host: $(uname -a)"
echo "=================================================="

# Guarantee self-termination on any exit (success or error)
cleanup() {{
    echo "Sync script completed or trapped. Terminating instance immediately..."
    aws s3 cp /var/log/calibration_sync.log "s3://{s3_bucket}/caldb/calibration_sync.log" || true
    shutdown -h now
}}
trap cleanup EXIT ERR

# Install curl-minimal, wget, tar, awscli, and python3
dnf install -y --allowerasing wget tar gzip awscli python3

# Install uv for modern fast Python execution
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

WORKDIR="/opt/caldb_staging"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

# Python script to stream calibration directly from NASA HEASARC to S3
cat << 'PY_SYNC_EOF' > sync_caldb.py
import concurrent.futures
import os
import re
import ssl
import sys
import time
import urllib.request
import boto3
from botocore.exceptions import ClientError

BUCKET = "{s3_bucket}"
PREFIX = "caldb"
BASE_URL = "https://heasarc.gsfc.nasa.gov/FTP/integral/data/"

# Directories required for IBIS/ISGRI analysis
TARGET_DIRS = [
    "idx/ic/",
    "ic/ibis/",
    "ic/sc/",
    "ic/irem/",
    "cat/hec/"
]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

s3 = boto3.client("s3", region_name="us-east-1")

def list_remote_files(url, rel_path=""):
    req = urllib.request.Request(url, headers={{"User-Agent": "Mozilla/5.0 (AWS EC2 Calibration Sync)"}})
    files = []
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
            html = r.read().decode("utf-8", errors="ignore")
            for m in re.finditer(r'href="([^"?/]+/?)(?:\\?[^"]*)?"', html):
                href = m.group(1)
                if href.startswith(".") or href.startswith("/"):
                    continue
                if href.endswith("/"):
                    files.extend(list_remote_files(url + href, rel_path + href))
                else:
                    files.append((rel_path + href, url + href))
    except Exception as e:
        print(f"Error crawling {{url}}: {{e}}")
    return files

def sync_file(item):
    rel_path, file_url = item
    s3_key = f"{{PREFIX}}/{{rel_path}}"
    try:
        # Check if already in S3
        s3.head_object(Bucket=BUCKET, Key=s3_key)
        return "EXISTS", rel_path
    except ClientError:
        pass

    req = urllib.request.Request(file_url, headers={{"User-Agent": "Mozilla/5.0"}})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            data = resp.read()
            s3.put_object(Bucket=BUCKET, Key=s3_key, Body=data)
            return "UPLOADED", rel_path
    except Exception as e:
        print(f"Failed to sync {{file_url}} -> {{s3_key}}: {{e}}")
        return "ERROR", rel_path

print("Crawling HEASARC directories...")
all_files = []
for d in TARGET_DIRS:
    print(f"Listing {{d}}...")
    found = list_remote_files(BASE_URL + d, d)
    print(f"  Found {{len(found)}} files in {{d}}")
    all_files.extend(found)

print(f"Total calibration files to sync: {{len(all_files)}}")

uploaded = 0
existed = 0
failed = 0

t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
    futures = {{executor.submit(sync_file, item): item for item in all_files}}
    for f in concurrent.futures.as_completed(futures):
        status, path = f.result()
        if status == "UPLOADED":
            uploaded += 1
        elif status == "EXISTS":
            existed += 1
        else:
            failed += 1
        if (uploaded + existed + failed) % 100 == 0:
            print(f"Progress: {{uploaded + existed + failed}}/{{len(all_files)}} (Uploaded: {{uploaded}}, Existed: {{existed}}, Failed: {{failed}})")

t_total = time.time() - t0
print(f"Sync complete in {{t_total:.1f}}s! Uploaded: {{uploaded}}, Existed: {{existed}}, Failed: {{failed}}")
PY_SYNC_EOF

# Run calibration sync using uv with boto3
uv run --with boto3 python sync_caldb.py

echo "Calibration sync job finished successfully."
"""


@app.command()
def launch(
    bucket: str = typer.Option(DEFAULT_BUCKET, "--bucket", "-b", help="Target S3 bucket"),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="AWS region"),
    subnet: str = typer.Option(DEFAULT_SUBNET, "--subnet", "-s", help="Subnet ID"),
    instance_type: str = typer.Option(
        "c6i.large", "--instance-type", "-t", help="EC2 instance type"
    ),
    spot: bool = typer.Option(True, "--spot/--on-demand", help="Use Spot or On-Demand"),
):
    """Spin up an ephemeral EC2 instance to sync calibration files directly from HEASARC to S3."""
    ec2 = boto3.client("ec2", region_name=region)

    user_data_raw = generate_cloud_init_sync_script(bucket)
    user_data_b64 = base64.b64encode(user_data_raw.encode("utf-8")).decode("utf-8")

    tags = [
        {"Key": "Name", "Value": "integral-caldb-sync-worker"},
        {"Key": "CostCenter", "Value": "integral-cloud-benchmark"},
        {"Key": "Project", "Value": "integral-osa-modern"},
        {"Key": "Purpose", "Value": "caldb-staging"},
    ]

    mode_label = "Spot" if spot else "On-Demand"
    console.print(
        Panel(
            f"[bold green]Launching Ephemeral Calibration Sync Worker[/bold green]\n"
            f"• Region: [cyan]{region}[/cyan]\n"
            f"• Instance Type: [cyan]{instance_type} ({mode_label})[/cyan]\n"
            f"• Target S3: [cyan]s3://{bucket}/caldb/[/cyan]\n"
            f"• Subnet: [cyan]{subnet}[/cyan]\n"
            f"• Shutdown Behavior: [bold red]terminate[/bold red]",
            title="EC2 Launch Config",
        )
    )

    launch_kwargs = {
        "ImageId": DEFAULT_AMI,
        "InstanceType": instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "UserData": user_data_b64,
        "InstanceInitiatedShutdownBehavior": "terminate",
        "IamInstanceProfile": {"Name": IAM_PROFILE_NAME},
        "NetworkInterfaces": [
            {
                "DeviceIndex": 0,
                "AssociatePublicIpAddress": True,
                "SubnetId": subnet,
            }
        ],
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {
                    "VolumeSize": 30,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                },
            }
        ],
        "TagSpecifications": [
            {"ResourceType": "instance", "Tags": tags},
            {"ResourceType": "volume", "Tags": tags},
        ],
    }
    if spot:
        launch_kwargs["InstanceMarketOptions"] = {
            "MarketType": "spot",
            "SpotOptions": {
                "SpotInstanceType": "one-time",
            },
        }

    try:
        resp = ec2.run_instances(**launch_kwargs)

        instance_id = resp["Instances"][0]["InstanceId"]
        console.print(f"[bold green]✓ Launched spot instance {instance_id}[/bold green]")
        console.print(
            "[dim]The instance will mirror the IC tree directly from HEASARC to S3 and terminate automatically.[/dim]"
        )
        return instance_id

    except Exception as e:
        console.print(f"[bold red]Failed to launch instance: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    app()
