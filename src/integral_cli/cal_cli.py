"""
integral_cli.cal_cli
Typer CLI sub-app for managing calibration profiles and history replay.
"""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from integral_cli.cal_profiles import (
    CalibrationProfile,
    CalibrationRule,
    get_profile,
    list_profiles,
    provision_profile_tree,
    save_user_profile,
)

console = Console()
cal_app = typer.Typer(help="Manage calibration profiles and historical replay environments")


@cal_app.command("list")
def list_cmd():
    """List available calibration profiles (built-in and custom)."""
    profiles = list_profiles()
    table = Table(title="INTEGRAL Calibration Profiles", title_style="bold green")
    table.add_column("Profile Name", style="bold cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Rules", justify="right", style="yellow")
    table.add_column("Description", style="white")

    for name, prof in sorted(profiles.items()):
        is_builtin = name in ["latest", "esa-2022"]
        type_str = "Built-in" if is_builtin else "User Custom"
        table.add_row(name, type_str, str(len(prof.rules)), prof.description)

    console.print(table)


@cal_app.command("show")
def show_cmd(name: str = typer.Argument(..., help="Name of calibration profile to inspect")):
    """Display rules and details of a calibration profile."""
    try:
        prof = get_profile(name)
    except ValueError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise typer.Exit(code=1)

    table = Table(title=f"Rules for Profile '{prof.name}'", title_style="bold cyan")
    table.add_column("Index File", style="bold yellow")
    table.add_column("Constraint", style="magenta")
    table.add_column("Description", style="white")

    if not prof.rules:
        table.add_row("None", "Unconstrained", "Default dynamic archive")
    else:
        for r in prof.rules:
            constraint = []
            if r.max_version is not None:
                constraint.append(f"VERSION <= {r.max_version}")
            if r.exact_version is not None:
                constraint.append(f"VERSION == {r.exact_version}")
            if r.override_target:
                constraint.append(f"target='{r.override_target}'")
            table.add_row(r.index, ", ".join(constraint), r.description or "")

    console.print(Panel(prof.description, title=f"Profile: {prof.name}", border_style="green"))
    console.print(table)


@cal_app.command("provision")
def provision_cmd(name: str = typer.Argument(..., help="Name of calibration profile to provision")):
    """Provision/pre-build the isolated IC tree for a calibration profile."""
    try:
        prof = get_profile(name)
    except ValueError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise typer.Exit(code=1)

    with console.status(f"[cyan]Provisioning IC tree for '{prof.name}'...[/cyan]"):
        path = provision_profile_tree(prof)

    console.print(
        f"[bold green]✓ Calibration environment provisioned at:[/bold green] [cyan]{path}[/cyan]"
    )


@cal_app.command("create")
def create_cmd(
    name: str | None = typer.Argument(None, help="Name of profile to create"),
    from_file: Path | None = typer.Option(
        None, "--from-file", "-f", help="Create from an existing JSON config file"
    ),
):
    """Create a new calibration profile via config file or interactive wizard."""
    if from_file:
        if not from_file.exists():
            console.print(f"[bold red]Error: File {from_file} does not exist.[/bold red]")
            raise typer.Exit(code=1)
        with open(from_file) as f:
            data = json.load(f)
        prof = CalibrationProfile(**data)
        saved = save_user_profile(prof)
        console.print(
            f"[bold green]✓ Profile '{prof.name}' imported and saved to {saved}[/bold green]"
        )
        return

    prof_name = name or Prompt.ask("Profile name (e.g. 'flight-model-2015')")
    desc = Prompt.ask("Description of this calibration profile")

    rules = []
    if Confirm.ask("Pin IBIS/ISGRI response matrix (ISGR-RMF)?", default=True):
        max_v = int(Prompt.ask("  Maximum ISGR-RMF version integer", default="1"))
        rules.append(
            CalibrationRule(
                index="ISGR-RMF.-RSP-IDX.fits",
                max_version=max_v,
                description=f"Pins ISGRI response matrix to Version <= {max_v}",
            )
        )

    if Confirm.ask("Pin IBIS/ISGRI background model (ISGR-BACK-BKG)?", default=True):
        max_v = int(Prompt.ask("  Maximum ISGR-BACK-BKG version integer", default="7"))
        rules.append(
            CalibrationRule(
                index="ISGR-BACK-BKG-IDX.fits",
                max_version=max_v,
                description=f"Pins ISGRI background models to Version <= {max_v}",
            )
        )

    if Confirm.ask("Pin JEM-X Instrument Models (JMX-IMOD)?", default=True):
        max_v = int(Prompt.ask("  Maximum JEM-X IMOD version integer", default="25"))
        rules.append(
            CalibrationRule(
                index="JMX2-IMOD-GRP-IDX.fits",
                max_version=max_v,
                description=f"Pins JEM-X 2 instrument model to Version <= {max_v}",
            )
        )
        rules.append(
            CalibrationRule(
                index="JMX1-IMOD-GRP-IDX.fits",
                max_version=max_v,
                description=f"Pins JEM-X 1 instrument model to Version <= {max_v}",
            )
        )

    prof = CalibrationProfile(name=prof_name, description=desc, rules=rules)
    saved = save_user_profile(prof)
    console.print(
        f"[bold green]✓ Profile '{prof.name}' created with {len(rules)} rules: {saved}[/bold green]"
    )


@cal_app.command("export")
def export_cmd(
    name: str = typer.Argument(..., help="Name of profile to export"),
    output: Path = typer.Option(
        Path("cal_profile.json"), "--output", "-o", help="Output file path"
    ),
):
    """Export calibration profile definition to JSON for publications/git."""
    try:
        prof = get_profile(name)
    except ValueError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        raise typer.Exit(code=1)

    with open(output, "w") as f:
        json.dump(prof.model_dump(), f, indent=2)

    console.print(f"[bold green]✓ Profile '{prof.name}' exported to {output}[/bold green]")
