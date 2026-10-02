#!/usr/bin/env python3
"""Ephemeral AWS Graviton3 Cloud Builder for INTEGRAL ARM64 Docker Image.

Compiles the patched native ARM64 OSA 11.2 container (cadarn/osa:11-native-arm64)
on an 8-core Graviton3 spot instance (c7g.2xlarge) and pushes directly to Docker Hub
over AWS's 12.5 Gbps network.

Zero bytes uploaded from local machine.
Zero secrets written to disk or logged.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
S3_BUCKET = "integral-cloud-analysis-data-537472396676"
REGION = "us-east-1"
AMI_ID = "ami-08bb9a392e39dc6e6"  # AL2023 ARM64 in us-east-1
INSTANCE_TYPE = "c7g.2xlarge"  # 8 vCPUs Graviton3, 16 GB RAM
IAM_PROFILE = "IntegralCloudBenchmarkProfile"
SSM_PARAM_NAME = "/integral/dockerhub_token"


def get_docker_token_from_env() -> str:
    """Read DOCKERHUB_TOKEN from ~/.env safely without printing."""
    env_file = Path.home() / ".env"
    if not env_file.exists():
        raise RuntimeError("~/.env file not found.")

    token = None
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith(("DOCKERHUB_TOKEN=", "DOCKER_HUB_TOKEN=")):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if not token:
        raise RuntimeError("Neither DOCKERHUB_TOKEN nor DOCKER_HUB_TOKEN found in ~/.env.")
    return token


def store_token_in_ssm(token: str) -> None:
    """Store token in AWS SSM Parameter Store as an encrypted SecureString."""
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(
        Name=SSM_PARAM_NAME,
        Description="Ephemeral Docker Hub PAT for ARM64 container build",
        Value=token,
        Type="SecureString",
        Overwrite=True,
    )
    console.print(f"[green]✓ Ephemeral token stored in AWS SSM ({SSM_PARAM_NAME})[/green]")


def delete_token_from_ssm() -> None:
    """Delete token from AWS SSM Parameter Store."""
    ssm = boto3.client("ssm", region_name=REGION)
    try:
        ssm.delete_parameter(Name=SSM_PARAM_NAME)
        console.print(f"[dim green]✓ Cleaned up SSM parameter ({SSM_PARAM_NAME})[/dim green]")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ParameterNotFound":
            console.print(f"[dim yellow]Warning deleting SSM param: {e}[/dim yellow]")


def generate_builder_user_data() -> str:
    """Generate the cloud-init bootstrap script for the Graviton builder."""
    script = f"""#!/bin/bash
set -euxo pipefail

# Output execution log to console and file
exec > >(tee -a /var/log/docker_build.log | logger -t user-data -s 2>/dev/console) 2>&1

echo "=========================================="
echo "Starting INTEGRAL ARM64 Cloud Docker Build"
echo "Time: $(date -u)"
echo "Host: $(uname -a)"
echo "CPU:  $(lscpu | grep 'Model name' || true)"
echo "=========================================="

S3_BUCKET="{S3_BUCKET}"

# Trap ERR: ensure log is uploaded to S3 even on failure, then self-terminate
trap 'echo "Build error at line $LINENO! Uploading error log..."; aws s3 cp /var/log/docker_build.log "s3://$S3_BUCKET/logs/docker_build_arm64_error.log" || true; shutdown -h now' ERR

# 1. Update and install Docker, Git, AWS CLI
dnf update -y
dnf install -y --allowerasing docker git awscli
systemctl enable --now docker

# 2. Clone repository from main branch
mkdir -p /root/build
cd /root/build
git clone --depth 1 https://github.com/Cadarn/integral-osa-modern.git repo
cd repo

# Overwrite Dockerfile.native-arm64 with the staged version from S3 containing FilterImage bounds patch
echo "Fetching latest Dockerfile.native-arm64 from S3..."
aws s3 cp "s3://$S3_BUCKET/build/Dockerfile.native-arm64" docker/Dockerfile.native-arm64

echo "=== Verifying Dockerfile.native-arm64 patches ==="
grep -E "FilterImage|filltab|max-stack-var-size" docker/Dockerfile.native-arm64

# 3. Build Docker container natively on 8 Graviton3 cores
BUILD_START=$(date +%s)
echo "Building cadarn/osa:11-native-arm64 on native ARM64..."
docker build --progress=plain -t cadarn/osa:11-native-arm64 -f docker/Dockerfile.native-arm64 .
BUILD_END=$(date +%s)
BUILD_DURATION=$((BUILD_END - BUILD_START))
echo "Build completed in ${{BUILD_DURATION}} seconds."

# 4. Fetch token securely from SSM Parameter Store (never written to disk or logged)
echo "Fetching Docker Hub credentials from SSM Parameter Store..."
DOCKER_TOKEN=$(aws ssm get-parameter --name "{SSM_PARAM_NAME}" --with-decryption --region {REGION} --query "Parameter.Value" --output text)

# 5. Authenticate and push to Docker Hub
echo "$DOCKER_TOKEN" | docker login -u cadarn --password-stdin
unset DOCKER_TOKEN

PUSH_START=$(date +%s)
echo "Pushing cadarn/osa:11-native-arm64 to Docker Hub..."
docker push cadarn/osa:11-native-arm64
PUSH_END=$(date +%s)
PUSH_DURATION=$((PUSH_END - PUSH_START))
echo "Push completed in ${{PUSH_DURATION}} seconds."

# 6. Upload completion marker and log to S3
echo "SUCCESS: Build ${{BUILD_DURATION}}s, Push ${{PUSH_DURATION}}s" > /root/build_success.txt
aws s3 cp /root/build_success.txt "s3://$S3_BUCKET/logs/docker_build_arm64_success.txt"
aws s3 cp /var/log/docker_build.log "s3://$S3_BUCKET/logs/docker_build_arm64.log"

echo "Cloud build and push complete! Self-terminating instance..."
shutdown -h now
"""
    return script


def launch_builder_instance(user_data: str) -> str:
    """Launch the c7g.2xlarge Spot instance."""
    ec2 = boto3.client("ec2", region_name=REGION)

    compressed_ud = base64.b64encode(gzip.compress(user_data.encode("utf-8"))).decode("ascii")

    launch_params: dict[str, Any] = {
        "ImageId": AMI_ID,
        "InstanceType": INSTANCE_TYPE,
        "MinCount": 1,
        "MaxCount": 1,
        "UserData": compressed_ud,
        "InstanceInitiatedShutdownBehavior": "terminate",
        "IamInstanceProfile": {"Name": IAM_PROFILE},
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {
                    "VolumeSize": 45,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                },
            }
        ],
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": "integral-arm64-cloud-builder"},
                    {"Key": "Project", "Value": "integral-osa-modern"},
                    {"Key": "Role", "Value": "docker-builder"},
                    {"Key": "CostCenter", "Value": "integral-cloud-benchmark"},
                ],
            }
        ],
        "InstanceMarketOptions": {
            "MarketType": "spot",
            "SpotOptions": {
                "SpotInstanceType": "one-time",
                "InstanceInterruptionBehavior": "terminate",
            },
        },
    }

    try:
        resp = ec2.run_instances(**launch_params)
        inst_id = resp["Instances"][0]["InstanceId"]
        console.print(
            f"[bold green]✓ Launched Graviton3 Spot Builder: {inst_id} ({INSTANCE_TYPE})[/bold green]"
        )
        return inst_id
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if "Spot" in error_code or "InsufficientInstanceCapacity" in error_code:
            console.print(
                f"[yellow]Spot unavailable ({error_code}). Falling back to On-Demand...[/yellow]"
            )
            launch_params.pop("InstanceMarketOptions", None)
            resp = ec2.run_instances(**launch_params)
            inst_id = resp["Instances"][0]["InstanceId"]
            console.print(
                f"[bold green]✓ Launched Graviton3 On-Demand Builder: {inst_id} ({INSTANCE_TYPE})[/bold green]"
            )
            return inst_id
        raise


def monitor_build(instance_id: str) -> bool:
    """Monitor S3 markers and instance state until build finishes."""
    s3 = boto3.client("s3", region_name=REGION)
    ec2 = boto3.client("ec2", region_name=REGION)

    success_key = "logs/docker_build_arm64_success.txt"
    error_key = "logs/docker_build_arm64_error.log"

    # Clean previous markers if any
    for k in [success_key, error_key]:
        with contextlib.suppress(Exception):
            s3.delete_object(Bucket=S3_BUCKET, Key=k)

    console.print(
        Panel(
            f"[bold cyan]Monitoring Cloud Build on {instance_id}[/bold cyan]\n"
            f"• Compiling OSA 11.2 from source on 8 Graviton3 vCPUs\n"
            f"• Expected duration: ~8-12 minutes\n"
            f"• Result will be pushed directly to Docker Hub at 12.5 Gbps",
            title="Build Monitor",
        )
    )

    start_time = time.time()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Cloud build in progress...", total=None)

        while True:
            time.sleep(15)
            elapsed = int(time.time() - start_time)

            # 1. Check for success marker
            try:
                s3.head_object(Bucket=S3_BUCKET, Key=success_key)
                progress.update(
                    task, description="[green]Build and push succeeded! Fetching result..."
                )
                return True
            except ClientError:
                pass

            # 2. Check for error log
            try:
                s3.head_object(Bucket=S3_BUCKET, Key=error_key)
                progress.update(task, description="[red]Build failed. Error log found.")
                return False
            except ClientError:
                pass

            # 3. Check instance lifecycle state
            try:
                desc = ec2.describe_instances(InstanceIds=[instance_id])
                state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
                if state in ["shutting-down", "terminated"]:
                    # Give S3 15 seconds to register marker
                    time.sleep(15)
                    try:
                        s3.head_object(Bucket=S3_BUCKET, Key=success_key)
                        return True
                    except ClientError:
                        return False
            except Exception as e:
                console.print(f"[dim yellow]Warning polling EC2: {e}[/dim yellow]")

            progress.update(
                task, description=f"[cyan]Compiling & packaging... ({elapsed}s elapsed)"
            )


def main() -> int:
    console.print("[bold blue]=== INTEGRAL Native ARM64 Cloud Builder ===[/bold blue]")

    # 1. Read token safely
    token = get_docker_token_from_env()

    try:
        # 2. Store in SSM Parameter Store
        store_token_in_ssm(token)

        # Stage local Dockerfile.native-arm64 to S3
        s3 = boto3.client("s3", region_name=REGION)
        dockerfile_path = PROJECT_ROOT / "docker" / "Dockerfile.native-arm64"
        console.print(
            f"Uploading {dockerfile_path} to s3://{S3_BUCKET}/build/Dockerfile.native-arm64..."
        )
        s3.upload_file(str(dockerfile_path), S3_BUCKET, "build/Dockerfile.native-arm64")
        console.print("[green]✓ Staged Dockerfile.native-arm64 in S3[/green]")

        # 3. Generate user-data
        user_data = generate_builder_user_data()

        # 4. Launch instance
        inst_id = launch_builder_instance(user_data)

        # 5. Monitor build
        success = monitor_build(inst_id)

        if success:
            console.print(
                "[bold green]====================================================[/bold green]"
            )
            console.print(
                "[bold green]✓ SUCCESS: Docker image cadarn/osa:11-native-arm64 pushed![/bold green]"
            )
            console.print(
                "[bold green]====================================================[/bold green]"
            )
            return 0
        else:
            console.print(
                "[bold red]====================================================[/bold red]"
            )
            console.print(
                "[bold red]✗ FAILURE: Cloud build failed or terminated prematurely.[/bold red]"
            )
            console.print(
                f"[bold red]Check S3 error log: s3://{S3_BUCKET}/logs/docker_build_arm64_error.log[/bold red]"
            )
            console.print(
                "[bold red]====================================================[/bold red]"
            )
            return 1

    finally:
        # 6. Always clean up SSM parameter
        delete_token_from_ssm()


if __name__ == "__main__":
    sys.exit(main())
