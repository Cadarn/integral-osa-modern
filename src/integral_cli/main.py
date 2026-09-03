"""
Main entry point for the unified INTEGRAL OSA Typer CLI.
"""

import typer
from rich.console import Console

from integral_cli.analysis import analysis_app
from integral_cli.benchmark import benchmark_app
from integral_cli.data_mgr import data_app
from integral_cli.docker_mgr import docker_app, docker_status
from integral_cli.tui import launch_tui
from integral_cli.viewer import view_app
from scripts.validate_science_products import compare as validate_cmd

console = Console()

app = typer.Typer(
    name="integral",
    help="INTEGRAL OSA Local Analysis & Container Management CLI",
    no_args_is_help=True,
)

# Register Sub-apps
app.add_typer(docker_app, name="docker", help="Manage & build Docker containers for local hardware")
app.add_typer(data_app, name="data", help="Manage local observation data, imports, and downloads")
app.add_typer(analysis_app, name="analyse", help="Execute science analysis pipelines (IBIS/JEM-X)")
app.add_typer(
    view_app, name="view", help="Visualise and inspect FITS images, mosaics, and source lists"
)
app.add_typer(
    benchmark_app, name="benchmark", help="Run performance benchmarks and multi-tier comparisons"
)

# Top level convenience commands
app.command("status")(docker_status)
app.command("validate")(validate_cmd)
app.command("tui", help="Launch the interactive terminal UI for configuring and running analyses")(launch_tui)


@app.command("info")
def show_info():
    """Display overall system architecture, paths, and Docker configuration."""
    docker_status()


if __name__ == "__main__":
    app()
