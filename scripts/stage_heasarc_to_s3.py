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
Stage INTEGRAL Revolution 0060 Science Data from HEASARC directly to an AWS S3 Bucket.

Streams auxiliary attitude files and all 100 valid pointing Science Windows (excluding
aborted/empty pointings: 006000010010, 006001020010, 006001030010, 006001040010)
into s3://<bucket>/rev0060/ for fast, internal AWS VPC transfers.
"""

import io
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn
import typer

app = typer.Typer(help="Stage HEASARC INTEGRAL data into S3")
console = Console()

# The 4 known unobserved/aborted pointings in Rev 0060 that lack isgri_events.fits
ABORTED_SCWS = {"006000010010", "006001020010", "006001030010", "006001040010"}


def get_rev60_scws() -> list[str]:
    """Retrieve and filter the 100 valid pointing ScWs for Rev 0060."""
    url = "https://heasarc.gsfc.nasa.gov/FTP/integral/data/scw/0060/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req).read().decode("utf-8")
    matches = re.findall(r"href=[\"'](0060\d{8}\.001)/?[\"']", html)
    all_pointings = [s.replace(".001", "") for s in sorted(set(matches)) if s.endswith("0010.001")]
    valid_scws = [s for s in all_pointings if s not in ABORTED_SCWS]
    return valid_scws


def get_dir_files(base_url: str) -> list[str]:
    """Scrape file list from a HEASARC directory."""
    req = urllib.request.Request(base_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        html = urllib.request.urlopen(req).read().decode("utf-8")
    except Exception as e:
        console.print(f"[yellow]Warning fetching {base_url}: {e}[/yellow]")
        return []
    matches = re.findall(r"href=[\"']([^\"'?/]+\.(?:fits|gz))[\"']", html)
    return sorted(set(matches))


def sync_file_to_s3(s3_client, bucket: str, s3_key: str, src_url: str) -> bool:
    """Stream a single file from HEASARC HTTP into S3 if not already present."""
    try:
        s3_client.head_object(Bucket=bucket, Key=s3_key)
        return False  # Already exists
    except ClientError:
        pass  # Proceed to upload

    req = urllib.request.Request(src_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
            s3_client.put_object(Bucket=bucket, Key=s3_key, Body=data)
            return True
    except Exception as e:
        console.print(f"[red]Failed {src_url} -> {s3_key}: {e}[/red]")
        return False


@app.command()
def stage(
    bucket: str = typer.Option("integral-cloud-analysis-data-537472396676", "--bucket", "-b", help="Target S3 bucket name"),
    region: str = typer.Option("us-east-1", "--region", help="AWS Region"),
    max_workers: int = typer.Option(16, "--workers", "-w", help="Concurrent download threads"),
    scw_limit: Optional[int] = typer.Option(None, "--limit", help="Limit number of ScWs (e.g. 10 or 25 for quick testing)"),
):
    """Stage Rev 0060 Science Windows and auxiliary attitude data from HEASARC to S3."""
    s3 = boto3.client("s3", region_name=region)

    # 1. Ensure bucket exists
    try:
        s3.head_bucket(Bucket=bucket)
        console.print(f"[green]Using existing S3 bucket: [bold]{bucket}[/bold][/green]")
    except ClientError:
        console.print(f"[yellow]Bucket {bucket} does not exist. Creating in {region}...[/yellow]")
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
        console.print(f"[green]✓ Created S3 bucket: {bucket}[/green]")

    # 2. Plan files to sync
    console.print("[cyan]Gathering file manifest from HEASARC...[/cyan]")
    tasks = []

    # Auxiliary files
    aux_url = "https://heasarc.gsfc.nasa.gov/FTP/integral/data/aux/adp/0060.001/"
    aux_files = get_dir_files(aux_url)
    for af in aux_files:
        tasks.append((f"rev0060/data/aux/adp/0060.001/{af}", f"{aux_url}{af}"))

    # Science Windows
    valid_scws = get_rev60_scws()
    if scw_limit:
        valid_scws = valid_scws[:scw_limit]
    console.print(f"[green]Identified {len(valid_scws)} valid Science Windows (excluding aborted pointings).[/green]")

    for scw in valid_scws:
        scw_url = f"https://heasarc.gsfc.nasa.gov/FTP/integral/data/scw/0060/{scw}.001/"
        scw_files = get_dir_files(scw_url)
        for sf in scw_files:
            tasks.append((f"rev0060/data/scw/0060/{scw}.001/{sf}", f"{scw_url}{sf}"))

    console.print(f"[bold cyan]Total files to stage: {len(tasks)} across {len(valid_scws)} ScWs + Aux[/bold cyan]")

    # 3. Execute concurrent sync
    uploaded_count = 0
    skipped_count = 0

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Staging HEASARC -> S3", total=len(tasks))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(sync_file_to_s3, s3, bucket, key, url): (key, url)
                for key, url in tasks
            }
            for fut in as_completed(futures):
                uploaded = fut.result()
                if uploaded:
                    uploaded_count += 1
                else:
                    skipped_count += 1
                progress.advance(task_id)

    console.print(
        f"[bold green]✓ Staging complete! Uploaded: {uploaded_count}, Already Cached: {skipped_count}[/bold green]"
    )
    console.print(f"[cyan]S3 Base URI: s3://{bucket}/rev0060/[/cyan]")


if __name__ == "__main__":
    app()
