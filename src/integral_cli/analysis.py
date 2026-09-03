"""
Analysis execution and benchmarking helpers for INTEGRAL instruments (IBIS, JEM-X, SPI).
"""

import shutil
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from integral_cli.config import config
from integral_cli.docker_mgr import run_container
from integral_cli.scw_utils import filter_pointing_scws

console = Console()
analysis_app = typer.Typer(help="Run INTEGRAL scientific reduction pipelines and benchmarks")


def parse_energy_bands(band_spec: str) -> tuple[str, str, int]:
    """Parse and validate one or more energy bands.

    Accepts formats like:
      - "18-60"
      - "18 60"
      - "20-40, 40-100"
      - "20-40; 40-100"

    Returns (min_bands_str, max_bands_str, num_bands) where strings are space-separated
    as required by ISDC parameters (e.g. IBIS_II_E_band_min="20 40").
    """
    parts = [p.strip() for p in band_spec.replace(";", ",").split(",") if p.strip()]
    if not parts:
        raise ValueError("Energy band specification cannot be empty.")

    mins: list[float] = []
    maxs: list[float] = []
    for part in parts:
        if "-" in part:
            lo_str, hi_str = part.split("-", 1)
        elif " " in part.strip():
            tokens = part.strip().split()
            if len(tokens) != 2:
                raise ValueError(f"Cannot parse band boundaries from '{part}'. Use 'Emin-Emax'.")
            lo_str, hi_str = tokens[0], tokens[1]
        else:
            raise ValueError(
                f"Invalid energy band format: '{part}'. Expected 'Emin-Emax' (e.g. 18-60)."
            )

        try:
            lo = float(lo_str.strip())
            hi = float(hi_str.strip())
        except ValueError as err:
            raise ValueError(f"Non-numeric energy boundary in '{part}': {err}") from err

        if lo >= hi:
            raise ValueError(
                f"Lower bound ({lo} keV) must be strictly less than upper bound ({hi} keV)."
            )
        mins.append(lo)
        maxs.append(hi)

    # Check for non-overlapping contiguous/strictly increasing intervals
    for i in range(len(mins) - 1):
        if maxs[i] > mins[i + 1]:
            raise ValueError(
                f"Overlapping energy bands detected: [{mins[i]}, {maxs[i]}] and [{mins[i + 1]}, {maxs[i + 1]}]. "
                "OSA requires non-overlapping energy bands."
            )

    def _fmt(val: float) -> str:
        return str(int(val)) if val.is_integer() else str(val)

    return " ".join(_fmt(x) for x in mins), " ".join(_fmt(x) for x in maxs), len(mins)


def _resolve_scw_ids(scw_input: str) -> list[str]:
    """Resolve a revolution spec, scw.list path, comma-separated list, or bare ScW ID into IDs."""
    if scw_input.startswith("rev:"):
        parts = scw_input.split(":")
        rev_id = f"{int(parts[1]):04d}"
        limit = int(parts[2]) if len(parts) > 2 else None

        scw_dir = config.rep_base_prod / "scw" / rev_id
        if not scw_dir.exists():
            console.print(
                f"[bold red]Error: Revolution {rev_id} directory {scw_dir} does not exist.[/bold red]"
            )
            raise typer.Exit(code=1)

        all_ids = sorted(
            d.name.split(".")[0]
            for d in scw_dir.iterdir()
            if d.is_dir() and len(d.name.split(".")[0]) == 12
        )
        found_scws = filter_pointing_scws(all_ids, rev_id)
        return found_scws[:limit] if limit else found_scws
    elif Path(scw_input).exists():
        return [
            line.strip()
            for line in Path(scw_input).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    elif "," in scw_input:
        return [s.strip() for s in scw_input.split(",") if s.strip()]
    else:
        return [scw_input.strip()]


def _prompt_for_scws() -> str:
    """Interactively prompt user for Science Window selection if missing."""
    scw_dir = config.rep_base_prod / "scw"
    avail_revs: list[str] = []
    if scw_dir.exists():
        avail_revs = sorted(
            [
                d.name
                for d in scw_dir.iterdir()
                if d.is_dir() and len(d.name) == 4 and d.name.isdigit()
            ]
        )

    console.print("\n[bold cyan]─── Science Window Selection ───[/bold cyan]")
    if avail_revs:
        console.print(
            f"Available local revolutions: [green]{', '.join(avail_revs[:10])}{'...' if len(avail_revs) > 10 else ''}[/green]"
        )

    default_choice = f"rev:{avail_revs[0]}:5" if avail_revs else "rev:0060:5"
    return Prompt.ask(
        "Enter ScW ID, comma-separated list, or revolution spec (e.g. 'rev:0060:5')",
        default=default_choice,
    )


def _prompt_for_energy_bands(default_preset: str = "18-60") -> str:
    """Interactively prompt for energy bands with validation."""
    console.print("\n[bold cyan]─── Energy Band Selection ───[/bold cyan]")
    console.print("Presets:")
    console.print("  [cyan]1[/cyan]: Standard 18-60 keV (Single band)")
    console.print("  [cyan]2[/cyan]: Hard X-ray 20-40, 40-100 keV (Two contiguous bands)")
    console.print("  [cyan]3[/cyan]: Broadband 20-100 keV (Single band)")
    console.print("  [cyan]4[/cyan]: Custom band definition")

    preset = Prompt.ask(
        "Choose preset or enter custom band", default="1", choices=["1", "2", "3", "4"]
    )
    if preset == "1":
        return "18-60"
    elif preset == "2":
        return "20-40, 40-100"
    elif preset == "3":
        return "20-100"
    else:
        while True:
            custom = Prompt.ask("Enter energy bands (e.g. '20-40, 40-100')", default=default_preset)
            try:
                parse_energy_bands(custom)
                return custom
            except ValueError as e:
                console.print(f"[bold red]Invalid energy bands: {e}[/bold red]")


def _prompt_for_products() -> tuple[str, bool]:
    """Interactively ask which processing products/levels are desired."""
    console.print("\n[bold cyan]─── Processing Products ───[/bold cyan]")
    console.print("  [cyan]1[/cyan]: Single Pointing Sky Images + Mosaic (IMA2) [Recommended]")
    console.print("  [cyan]2[/cyan]: Single Pointing Sky Images only (IMA)")
    console.print("  [cyan]3[/cyan]: Full Pipeline up to Spectra extraction (SPE)")
    console.print("  [cyan]4[/cyan]: Full Pipeline up to Lightcurves (LCR)")

    choice = Prompt.ask("Select pipeline level", default="1", choices=["1", "2", "3", "4"])
    if choice == "1":
        return "IMA2", True
    elif choice == "2":
        return "IMA", False
    elif choice == "3":
        return "SPE", True
    else:
        return "LCR", True


@analysis_app.command("ibis")
def run_ibis(
    scw_input: str = typer.Argument(
        None,
        help="Science Window ID (e.g. 006000010010), comma-separated list, revolution (e.g. 'rev:0060' or 'rev:0060:5'), or path to scw.list. If omitted in interactive mode, prompts for input.",
    ),
    energy_bands: str = typer.Option(
        None,
        "--bands",
        "-b",
        help="Energy band specification in keV: e.g. '18-60' or '20-40, 40-100' (multiple non-overlapping contiguous bands supported). Overrides --e-min/--e-max.",
    ),
    e_min: str = typer.Option(
        "18", "--e-min", help="Minimum energy in keV (used if --bands is not set, default: 18)"
    ),
    e_max: str = typer.Option(
        "60", "--e-max", help="Maximum energy in keV (used if --bands is not set, default: 60)"
    ),
    start_level: str = typer.Option(
        "DEAD", "--start-level", help="Pipeline start level (COR, GTI, DEAD, BIN_I, CAT_I, IMA)"
    ),
    end_level_opt: str | None = typer.Option(
        None, "--end-level", help="Pipeline end level override (IMA, IMA2, SPE, LCR)"
    ),
    image: str | None = typer.Option(
        None, "--image", "-i", help="Docker image to run (default: config.docker_image)"
    ),
    workdir: Path = typer.Option(
        Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"
    ),
    og_name: str = typer.Option("obs_ibis", "--og", "-o", help="Observation group name"),
    mosaic: bool = typer.Option(
        True, "--mosaic/--no-mosaic", help="Run multi-ScW mosaicing stage (IMA2)"
    ),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="Clean prior observation group directory before run"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Non-interactive mode: accept all defaults and skip confirmation"
    ),
):
    """Run IBIS/ISGRI science analysis pipeline (imaging & mosaicing) with interactive wizard or direct CLI flags."""
    is_interactive = sys.stdin.isatty() and not yes

    # 1. Resolve ScW input (interactive prompt if omitted)
    if not scw_input:
        if is_interactive:
            scw_input = _prompt_for_scws()
        else:
            console.print("[bold red]Error: Missing required argument 'SCW_INPUT'.[/bold red]")
            raise typer.Exit(code=1)

    # 2. Resolve Energy Bands
    if energy_bands:
        min_bands, max_bands, num_bands = parse_energy_bands(energy_bands)
    elif is_interactive and not (e_min != "18" or e_max != "60"):
        band_spec = _prompt_for_energy_bands()
        min_bands, max_bands, num_bands = parse_energy_bands(band_spec)
    else:
        min_bands, max_bands, num_bands = parse_energy_bands(f"{e_min}-{e_max}")

    # 3. Resolve End Level
    if end_level_opt:
        end_level = end_level_opt.upper()
    elif is_interactive and not mosaic:
        chosen_level, is_mosaic = _prompt_for_products()
        end_level = chosen_level
        mosaic = is_mosaic
    else:
        end_level = "IMA2" if mosaic else "IMA"

    workdir.mkdir(parents=True, exist_ok=True)
    scw_file = workdir / "scw.list"
    obs_dir = workdir / "obs" / og_name

    if clean and obs_dir.exists():
        shutil.rmtree(obs_dir)

    scws = _resolve_scw_ids(scw_input)

    if not scws:
        console.print("[bold red]No valid Science Windows found for analysis.[/bold red]")
        raise typer.Exit(code=1)

    # Format ScWs with .001 extension for og_create DOL lookup
    formatted_scws = [f"{s}.001" if (len(s) == 12 and not s.endswith(".001")) else s for s in scws]
    scw_file.write_text("\n".join(formatted_scws) + "\n")

    if end_level == "IMA2" and len(scws) <= 1:
        end_level = "IMA"

    target_image = image or config.docker_image

    band_desc = f"{num_bands} band(s): " + ", ".join(
        f"{lo}-{hi} keV" for lo, hi in zip(min_bands.split(), max_bands.split())
    )
    setup_panel = Panel(
        f"[bold green]IBIS/ISGRI Science Reduction Setup[/bold green]\n\n"
        f"• ScW Count:       [cyan]{len(scws)} Science Windows[/cyan] ({scws[0]} ... {scws[-1]})\n"
        f"• Energy Bands:    [cyan]{band_desc}[/cyan]\n"
        f"• Analysis Level:  [cyan]startLevel={start_level} -> endLevel={end_level}[/cyan]\n"
        f"• Mosaicing (IMA2):[cyan]{'Enabled' if end_level == 'IMA2' else 'Single Pointing (IMA)'}[/cyan]\n"
        f"• Workdir:         [cyan]{workdir}[/cyan]\n"
        f"• Data Archive:    [cyan]{config.rep_base_prod}[/cyan]\n"
        f"• Docker Image:    [cyan]{target_image}[/cyan]",
        title="Analysis Plan",
    )
    console.print(setup_panel)

    if is_interactive:
        proceed = Confirm.ask("Proceed with pipeline execution?", default=True)
        if not proceed:
            console.print("[yellow]Analysis cancelled by user.[/yellow]")
            raise typer.Exit(code=0)

    bash_pipeline = f"""
    set -e
    [ -f /init.sh ] && source /init.sh 2>/dev/null || true
    [ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true

    export ISDC_ENV=/opt/osa
    export REP_BASE_PROD=/data
    export CFITSIO_INCLUDE_FILES=/opt/osa/templates
    export ISDC_REF_CAT="{config.ref_catalog}"
    export HOME=/home/integral
    export PFILES="/home/integral/pfiles;/opt/osa/pfiles"
    mkdir -pv /home/integral/pfiles

    export COMMONSCRIPT=1
    export COMMONLOGFILE=+/home/integral/commonlog.txt
    export DISPLAY=""

    cd /home/integral

    echo "=== 1. Initialising Observation Group ({og_name}) ==="
    og_create idxSwg="scw.list" \
              instrument="IBIS" \
              ogid="{og_name}" \
              baseDir="./" \
              obsDir="obs"

    echo "=== 2. Running ibis_science_analysis ({band_desc}, endLevel={end_level}) ==="
    cd obs/{og_name}

    ibis_science_analysis \
        startLevel="{start_level}" \
        endLevel="{end_level}" \
        IBIS_II_ChanNum={num_bands} \
        IBIS_II_E_band_min="{min_bands}" \
        IBIS_II_E_band_max="{max_bands}" \

        SWITCH_disableIsgri="no" \
        SWITCH_disablePICsIT="yes" \
        SWITCH_disableCompton="yes" \
        CAT_refCat="{config.ref_catalog}[ISGRI_FLAG>0]" \
        brSrcDOL="{config.ref_catalog}[ISGRI_FLAG2==5&&ISGR_FLUX_1>100]" \
        IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
        IC_Alias="OSA"
    """

    start_time = time.perf_counter()
    try:
        run_container(command=bash_pipeline, workdir=workdir, image=target_image)
        elapsed_sec = time.perf_counter() - start_time

        console.print(
            f"[bold green]✓ Pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec / len(scws):.2f}s per ScW)[/bold green]"
        )

        fits_files = list(obs_dir.glob("**/*.fits"))
        console.print(
            f"[cyan]Generated {len(fits_files)} FITS science products in {obs_dir}[/cyan]"
        )
    except Exception as e:
        elapsed_sec = time.perf_counter() - start_time
        console.print(f"[bold red]Pipeline stopped after {elapsed_sec:.2f}s: {e}[/bold red]")
        raise typer.Exit(code=1)


@analysis_app.command("jemx")
def run_jemx(
    scw_input: str = typer.Argument(
        ...,
        help="Science Window ID (e.g. 006000010010), comma-separated list, or revolution (e.g. 'rev:0060:5')",
    ),
    jemx_unit: int = typer.Option(1, "--unit", "-u", help="JEM-X unit number (1 or 2, default: 1)"),
    start_level: str = typer.Option(
        "COR", "--start-level", help="Start level (COR, DEAD, BIN_I, IMA, IMA2)"
    ),
    end_level: str = typer.Option("IMA2", "--end-level", help="End level (IMA, IMA2, SPE, LCR)"),
    image: str | None = typer.Option(None, "--image", "-i", help="Docker image to run"),
    workdir: Path = typer.Option(
        Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"
    ),
    og_name: str = typer.Option("obs_jemx", "--og", "-o", help="Observation group name"),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="Clean prior observation group directory before run"
    ),
):
    """Run JEM-X science analysis pipeline (j_science_analysis) with native ARM64 container."""
    workdir.mkdir(parents=True, exist_ok=True)
    scw_file = workdir / "scw.list"
    obs_dir = workdir / "obs" / og_name

    if clean and obs_dir.exists():
        shutil.rmtree(obs_dir)

    scws = _resolve_scw_ids(scw_input)

    formatted_scws = [f"{s}.001" if (len(s) == 12 and not s.endswith(".001")) else s for s in scws]
    scw_file.write_text("\n".join(formatted_scws) + "\n")

    target_image = image or config.docker_image

    console.print(
        Panel(
            f"[bold green]Starting JEM-X {jemx_unit} Science Reduction[/bold green]\n\n"
            f"• ScW Count:       [cyan]{len(scws)} Science Windows[/cyan]\n"
            f"• Instrument:      [cyan]JEM-X {jemx_unit}[/cyan]\n"
            f"• Analysis Level:  [cyan]startLevel={start_level} -> endLevel={end_level}[/cyan]\n"
            f"• Workdir:         [cyan]{workdir}[/cyan]\n"
            f"• Docker Image:    [cyan]{target_image}[/cyan]",
            title="Analysis Setup",
        )
    )

    inst_name = f"JMX{jemx_unit}"
    bash_pipeline = f"""
    set -e
    [ -f /init.sh ] && source /init.sh 2>/dev/null || true
    [ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true

    export ISDC_ENV=/opt/osa
    export REP_BASE_PROD=/data
    export CFITSIO_INCLUDE_FILES=/opt/osa/templates
    export ISDC_REF_CAT="{config.ref_catalog}"
    export HOME=/home/integral
    export PFILES="/home/integral/pfiles;/opt/osa/pfiles"
    mkdir -pv /home/integral/pfiles
    export DISPLAY=""

    cd /home/integral

    echo "=== 1. Initialising Observation Group ({og_name}) ==="
    og_create idxSwg="scw.list" \
              instrument="{inst_name}" \
              ogid="{og_name}" \
              baseDir="./" \
              obsDir="obs"

    echo "=== 2. Running jemx_science_analysis ({inst_name}) ==="
    cd obs/{og_name}
    jemx_science_analysis \
        ogDOL="og_jmx{jemx_unit}.fits[1]" \
        jemxNum="{jemx_unit}" \
        startLevel="{start_level}" \
        endLevel="{end_level}" \
        nChanBins=4 \
        chanLow="46 83 129 160" \
        chanHigh="82 128 159 223" \
        CAT_I_usrCat="" \
        LCR_timeStep=4.0 \
        IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
        IC_Alias="OSA"



    """

    start_time = time.perf_counter()
    try:
        run_container(command=bash_pipeline, workdir=workdir, image=target_image)
        elapsed_sec = time.perf_counter() - start_time
        console.print(
            f"[bold green]✓ JEM-X pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec / len(scws):.2f}s per ScW)[/bold green]"
        )
    except Exception as e:
        elapsed_sec = time.perf_counter() - start_time
        console.print(f"[bold red]JEM-X pipeline stopped after {elapsed_sec:.2f}s: {e}[/bold red]")
        raise typer.Exit(code=1)


@analysis_app.command("omc")
def run_omc(
    scw_input: str = typer.Argument(
        ...,
        help="Science Window ID (e.g. 006000010010), comma-separated list, or revolution (e.g. 'rev:0060:5')",
    ),
    start_level: str = typer.Option("COR", "--start-level", help="Start level (COR, IMA)"),
    end_level: str = typer.Option("IMA", "--end-level", help="End level (COR, IMA)"),
    image: str | None = typer.Option(None, "--image", "-i", help="Docker image to run"),
    workdir: Path = typer.Option(
        Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"
    ),
    og_name: str = typer.Option("obs_omc", "--og", "-o", help="Observation group name"),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="Clean prior observation group directory before run"
    ),
):
    """Run OMC science analysis pipeline (omc_science_analysis) with native ARM64 container."""
    workdir.mkdir(parents=True, exist_ok=True)
    scw_file = workdir / "scw.list"
    obs_dir = workdir / "obs" / og_name

    if clean and obs_dir.exists():
        shutil.rmtree(obs_dir)

    scws = _resolve_scw_ids(scw_input)

    formatted_scws = [f"{s}.001" if (len(s) == 12 and not s.endswith(".001")) else s for s in scws]
    scw_file.write_text("\n".join(formatted_scws) + "\n")

    target_image = image or config.docker_image

    bash_pipeline = f"""
    set -e
    [ -f /init.sh ] && source /init.sh 2>/dev/null || true
    [ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true

    export ISDC_ENV=/opt/osa
    export REP_BASE_PROD=/data
    export CFITSIO_INCLUDE_FILES=/opt/osa/templates
    export ISDC_REF_CAT="{config.ref_catalog}"
    export ISDC_OMC_CAT="{config.omc_catalog}"
    export HOME=/home/integral
    export PFILES="/home/integral/pfiles;/opt/osa/pfiles"
    mkdir -pv /home/integral/pfiles
    export DISPLAY=""

    cd /home/integral

    echo "=== 1. Initialising Observation Group ({og_name}) ==="
    og_create idxSwg="scw.list" \
              instrument="OMC" \
              ogid="{og_name}" \
              baseDir="./" \
              obsDir="obs"

    echo "=== 2. Running omc_science_analysis ==="
    cd obs/{og_name}
    omc_science_analysis \
        startLevel="{start_level}" \
        endLevel="{end_level}" \
        IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
        IC_Alias="OSA"
    """

    start_time = time.perf_counter()
    try:
        run_container(command=bash_pipeline, workdir=workdir, image=target_image)
        elapsed_sec = time.perf_counter() - start_time
        console.print(
            f"[bold green]✓ OMC pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec / len(scws):.2f}s per ScW)[/bold green]"
        )
    except Exception as e:
        elapsed_sec = time.perf_counter() - start_time
        console.print(f"[bold red]OMC pipeline stopped after {elapsed_sec:.2f}s: {e}[/bold red]")
        raise typer.Exit(code=1)


@analysis_app.command("spi")
def run_spi(
    scw_input: str = typer.Argument(
        ...,
        help="Science Window ID (e.g. 006000010010), comma-separated list, or revolution (e.g. 'rev:0060:5')",
    ),
    start_level: str = typer.Option(
        "COR", "--start-level", help="Start level (COR, DEAD, POINT, BKG, SPIROS, SPIMODFIT)"
    ),
    end_level: str = typer.Option(
        "SPIROS", "--end-level", help="End level (COR, DEAD, POINT, BKG, SPIROS, SPIMODFIT)"
    ),
    image: str | None = typer.Option(None, "--image", "-i", help="Docker image to run"),
    workdir: Path = typer.Option(
        Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"
    ),
    og_name: str = typer.Option("obs_spi", "--og", "-o", help="Observation group name"),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="Clean prior observation group directory before run"
    ),
):
    """Run SPI science analysis pipeline (spi_science_analysis) with native ARM64 container."""
    workdir.mkdir(parents=True, exist_ok=True)
    scw_file = workdir / "scw.list"
    obs_dir = workdir / "obs" / og_name

    if clean and obs_dir.exists():
        shutil.rmtree(obs_dir)

    scws = _resolve_scw_ids(scw_input)

    formatted_scws = [f"{s}.001" if (len(s) == 12 and not s.endswith(".001")) else s for s in scws]
    scw_file.write_text("\n".join(formatted_scws) + "\n")

    target_image = image or config.docker_image

    bash_pipeline = f"""
    set -e
    [ -f /init.sh ] && source /init.sh 2>/dev/null || true
    [ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true

    export ISDC_ENV=/opt/osa
    export REP_BASE_PROD=/data
    export CFITSIO_INCLUDE_FILES=/opt/osa/templates
    export ISDC_REF_CAT="{config.ref_catalog}"
    export HOME=/home/integral
    export PFILES="/home/integral/pfiles;/opt/osa/pfiles"
    mkdir -pv /home/integral/pfiles
    export DISPLAY=""

    cd /home/integral

    echo "=== 1. Initialising Observation Group ({og_name}) ==="
    og_create idxSwg="scw.list" \
              instrument="SPI" \
              ogid="{og_name}" \
              baseDir="./" \
              obsDir="obs"

    echo "=== 2. Running spi_science_analysis ==="
    cd obs/{og_name}
    spi_science_analysis \
        startLevel="{start_level}" \
        endLevel="{end_level}" \
        IC_Group="/data/idx/ic/ic_master_file.fits[1]" \
        IC_Alias="OSA"
    """

    start_time = time.perf_counter()
    try:
        run_container(command=bash_pipeline, workdir=workdir, image=target_image)
        elapsed_sec = time.perf_counter() - start_time
        console.print(
            f"[bold green]✓ SPI pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec / len(scws):.2f}s per ScW)[/bold green]"
        )
    except Exception as e:
        elapsed_sec = time.perf_counter() - start_time
        console.print(f"[bold red]SPI pipeline stopped after {elapsed_sec:.2f}s: {e}[/bold red]")
        raise typer.Exit(code=1)
