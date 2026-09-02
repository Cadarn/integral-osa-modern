"""
Docker manager for building, configuring, and launching INTEGRAL OSA container images.
"""

from pathlib import Path
import os
import subprocess
from typing import Optional, Set
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from integral_cli.config import config

console = Console()
docker_app = typer.Typer(help="Manage and run INTEGRAL OSA Docker container images")


@docker_app.command("build")
def build_image(
    arch: str = typer.Option(
        "auto",
        "--arch",
        "-a",
        help="Target architecture: auto, arm64 (Apple Silicon/Graviton), x86 (Intel/AMD)",
    ),
    tag: str = typer.Option("latest", "--tag", "-t", help="Tag for the built image"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Build image without using cache"),
):
    """Build the optimised INTEGRAL OSA Docker container image for local hardware."""
    project_root = Path(__file__).resolve().parent.parent.parent
    docker_dir = project_root / "docker"

    if arch == "auto":
        target_arch = config.host_arch
    else:
        target_arch = arch

    if target_arch == "arm64":
        dockerfile = docker_dir / "Dockerfile.native-arm64"
        image_name = f"integralsw/osa:{tag}-native-arm64" if tag != "latest" else "integralsw/osa:11-native-arm64"
        platform_arg = "--platform=linux/arm64"
    else:
        dockerfile = docker_dir / "Dockerfile.modern"
        image_name = f"integralsw/osa:{tag}-modern-amd64" if tag != "latest" else "integralsw/osa:11-modern-amd64"
        platform_arg = "--platform=linux/amd64"


    console.print(
        Panel(
            f"[bold green]Building INTEGRAL OSA Container Image[/bold green]\n\n"
            f"• Architecture: [cyan]{target_arch}[/cyan] ({platform_arg})\n"
            f"• Dockerfile:   [cyan]{dockerfile.name}[/cyan]\n"
            f"• Image Tag:    [cyan]{image_name}[/cyan]\n"
            f"• Python env:   [cyan]uv virtualenv (/opt/venv)[/cyan]",
            title="Docker Build Configuration",
        )
    )

    cmd = [
        "docker",
        "build",
        platform_arg,
        "-f",
        str(dockerfile),
        "-t",
        image_name,
        str(project_root),
    ]
    if no_cache:
        cmd.append("--no-cache")

    try:
        subprocess.run(cmd, check=True)
        console.print(f"[bold green]✓ Successfully built {image_name}[/bold green]")
        config.docker_image = image_name
        config.save()
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Docker build failed with exit code {e.returncode}[/bold red]")
        raise typer.Exit(code=e.returncode)


def find_symlink_targets(base_path: Path) -> Set[Path]:
    """Find all unique external parent directories targeted by symlinks."""
    targets = set()
    if not base_path.exists():
        return targets

    for p in base_path.rglob("*"):
        if p.is_symlink():
            try:
                resolved = p.resolve()
                if resolved.exists():
                    targets.add(resolved.parent)
                    targets.add(resolved.parents[1])
            except Exception:
                pass
    return targets


@docker_app.command("run")
def run_container(
    command: Optional[str] = typer.Argument(None, help="Command to run inside the container"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Docker image override"),
    workdir: Optional[Path] = typer.Option(None, "--workdir", "-w", help="Host directory to mount as /home/integral"),
    gui: bool = typer.Option(False, "--gui", "-g", help="Enable X11 GUI forwarding"),
):
    """Launch the INTEGRAL OSA container with local data mounts and correct UID/GID."""
    chosen_image = image if (image and isinstance(image, str)) else config.docker_image
    data_path = config.rep_base_prod.resolve()
    ic_path = config.current_ic.resolve()
    target_workdir = (workdir if (workdir and isinstance(workdir, Path)) else Path.cwd()).resolve()

    target_workdir.mkdir(parents=True, exist_ok=True)
    pfiles_dir = target_workdir / "pfiles"
    pfiles_dir.mkdir(parents=True, exist_ok=True)

    uid = os.getuid()
    gid = os.getgid()

    platform_flag = (
        "--platform=linux/arm64" if ("arm64" in str(chosen_image) or "apple-silicon" in str(chosen_image)) else "--platform=linux/amd64"
    )

    docker_args = [
        "docker",
        "run",
        "--rm",
        platform_flag,
        "--user",
        f"{uid}:{gid}",
        "-v",
        f"{target_workdir}:/home/integral",
    ]

    # Mount Data paths
    if (data_path / "scw").exists():
        docker_args.extend(["-v", f"{data_path / 'scw'}:/data/scw:ro"])
    if (data_path / "aux").exists():
        docker_args.extend(["-v", f"{data_path / 'aux'}:/data/aux:ro"])
    if (ic_path / "ic").exists():
        docker_args.extend(["-v", f"{ic_path / 'ic'}:/data/ic:ro"])
    if (ic_path / "idx").exists():
        docker_args.extend(["-v", f"{ic_path / 'idx'}:/data/idx:ro"])
    if (data_path / "cat").exists():
        docker_args.extend(["-v", f"{data_path / 'cat'}:/data/cat:ro"])

    # Auto-mount symlink target trees so host symlinks resolve inside container
    symlink_parents = find_symlink_targets(data_path)
    for parent_dir in symlink_parents:
        docker_args.extend(["-v", f"{parent_dir}:{parent_dir}:ro"])

    # X11 GUI forwarding
    if gui and "DISPLAY" in os.environ:
        docker_args.extend(["-e", f"DISPLAY={os.environ['DISPLAY']}"])
        if Path("/tmp/.X11-unix").exists():
            docker_args.extend(["-v", "/tmp/.X11-unix:/tmp/.X11-unix:ro"])

    if not command:
        # Interactive session
        docker_args.extend(["-it", chosen_image, "bash", "-c", "source /init.sh 2>/dev/null || true; exec bash"])
        console.print(f"[bold blue]Launching interactive OSA session ({chosen_image})...[/bold blue]")
        subprocess.run(docker_args)
    else:
        # Non-interactive command
        docker_args.extend([chosen_image, "bash", "-c", f"source /init.sh 2>/dev/null || true; {command}"])
        subprocess.run(docker_args, check=True)


@docker_app.command("status")
def docker_status():
    """Display local Docker environment status and image information."""
    table = Table(title="Docker & Architecture Status")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Host CPU Architecture", config.host_arch)
    table.add_row("Configured OSA Image", config.docker_image)
    table.add_row("Data Directory (REP_BASE_PROD)", str(config.rep_base_prod))
    table.add_row("IC Directory (CURRENT_IC)", str(config.current_ic))

    console.print(table)
