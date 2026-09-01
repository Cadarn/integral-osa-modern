#!/usr/bin/env python3
"""
scw_distributor.py
Partitions and schedules INTEGRAL Science Window batch processing jobs for Kubernetes & Cloud.
"""

from pathlib import Path
import json
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="INTEGRAL Science Window Batch Workload Partitioner")
console = Console()


@app.command()
def partition(
    scw_file: Path = typer.Argument(..., help="Text file containing one ScW ID per line"),
    chunk_size: int = typer.Option(10, "--chunk-size", "-c", help="Number of ScWs per batch worker"),
    output_dir: Path = typer.Option(Path("./k8s/batches"), "--output-dir", "-o", help="Output directory for manifests"),
):
    """Split a list of Science Windows into parallel worker batches."""
    if not scw_file.exists():
        console.print(f"[red]Error: ScW list file {scw_file} not found.[/red]")
        raise typer.Exit(code=1)
        
    with open(scw_file) as f:
        scw_list = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
    total_scws = len(scw_list)
    chunks = [scw_list[i : i + chunk_size] for i in range(0, total_scws, chunk_size)]
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[bold blue]Partitioning {total_scws} Science Windows into {len(chunks)} worker batches...[/bold blue]")
    
    table = Table(title="Batch Allocation Summary")
    table.add_column("Batch Index", style="cyan")
    table.add_column("ScW Count", style="green")
    table.add_column("Manifest File", style="yellow")
    
    for idx, chunk in enumerate(chunks):
        batch_filename = output_dir / f"batch_{idx:04d}.json"
        with open(batch_filename, "w") as f_out:
            json.dump({"batch_id": idx, "scws": chunk}, f_out, indent=2)
            
        table.add_row(f"Batch #{idx+1}", str(len(chunk)), str(batch_filename))
        
    console.print(table)
    console.print(f"[bold green]✓ Generated {len(chunks)} batch specifications in {output_dir}[/bold green]")


def main():
    app()


if __name__ == "__main__":
    main()
