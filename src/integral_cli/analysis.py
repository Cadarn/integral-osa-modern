"""
Analysis execution and benchmarking helpers for INTEGRAL instruments (IBIS, JEM-X, SPI).
"""

from pathlib import Path
import shutil
import time
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from integral_cli.config import config
from integral_cli.docker_mgr import run_container

console = Console()
analysis_app = typer.Typer(help="Run INTEGRAL scientific reduction pipelines and benchmarks")


def _resolve_scw_ids(scw_input: str) -> list[str]:
    """Resolve a revolution spec, scw.list path, comma-separated list, or bare ScW ID into IDs."""
    if scw_input.startswith("rev:"):
        parts = scw_input.split(":")
        rev_id = f"{int(parts[1]):04d}"
        limit = int(parts[2]) if len(parts) > 2 else None

        scw_dir = config.rep_base_prod / "scw" / rev_id
        if not scw_dir.exists():
            console.print(f"[bold red]Error: Revolution {rev_id} directory {scw_dir} does not exist.[/bold red]")
            raise typer.Exit(code=1)

        # Select pointing Science Windows (ending in 0010)
        found_scws = sorted([d.name.split(".")[0] for d in scw_dir.iterdir() if d.is_dir() and d.name.startswith(rev_id) and d.name.split(".")[0].endswith("0010")])
        if not found_scws:
            found_scws = sorted([d.name.split(".")[0] for d in scw_dir.iterdir() if d.is_dir() and len(d.name) >= 12])
        return found_scws[:limit] if limit else found_scws
    elif Path(scw_input).exists():
        return [line.strip() for line in Path(scw_input).read_text().splitlines() if line.strip() and not line.startswith("#")]
    elif "," in scw_input:
        return [s.strip() for s in scw_input.split(",") if s.strip()]
    else:
        return [scw_input.strip()]


@analysis_app.command("ibis")
def run_ibis(
    scw_input: str = typer.Argument(
        ...,
        help="Science Window ID (e.g. 006000010010), comma-separated list, revolution (e.g. 'rev:0060' or 'rev:0060:5' for first 5 ScWs), or path to scw.list",
    ),
    e_min: str = typer.Option("18", "--e-min", help="Minimum energy in keV (default: 18)"),
    e_max: str = typer.Option("60", "--e-max", help="Maximum energy in keV (default: 60)"),
    start_level: str = typer.Option("DEAD", "--start-level", help="Pipeline start level (COR, GTI, DEAD, BIN_I, CAT_I, IMA)"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Docker image to run (default: config.docker_image)"),
    workdir: Path = typer.Option(Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"),
    og_name: str = typer.Option("obs_ibis", "--og", "-o", help="Observation group name"),
    mosaic: bool = typer.Option(True, "--mosaic/--no-mosaic", help="Run multi-ScW mosaicing stage (IMA2)"),
    clean: bool = typer.Option(True, "--clean/--no-clean", help="Clean prior observation group directory before run"),
):
    """Run IBIS/ISGRI science analysis pipeline (imaging & mosaicing in 18-60 keV band) with benchmarking."""
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

    end_level = "IMA2" if (mosaic and len(scws) > 1) else "IMA"

    target_image = image or config.docker_image

    console.print(
        Panel(
            f"[bold green]Starting IBIS/ISGRI Science Reduction & Benchmark[/bold green]\n\n"
            f"• ScW Count:       [cyan]{len(scws)} Science Windows[/cyan] ({scws[0]} ... {scws[-1]})\n"
            f"• Energy Band:     [cyan]{e_min} - {e_max} keV[/cyan] (IBIS_II_ChanNum=1)\n"
            f"• Analysis Level:  [cyan]startLevel={start_level} -> endLevel={end_level}[/cyan]\n"
            f"• Mosaicing (IMA2):[cyan]{'Enabled' if end_level == 'IMA2' else 'Single Pointing (IMA)'}[/cyan]\n"
            f"• Workdir:         [cyan]{workdir}[/cyan]\n"
            f"• Data Archive:    [cyan]{config.rep_base_prod}[/cyan]\n"
            f"• Docker Image:    [cyan]{target_image}[/cyan]",
            title="Analysis Setup",
        )
    )

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

    echo "=== 2. Running ibis_science_analysis ({e_min}-{e_max} keV, endLevel={end_level}) ==="
    cd obs/{og_name}

    ibis_science_analysis \
        startLevel="{start_level}" \
        endLevel="{end_level}" \
        IBIS_II_ChanNum=1 \
        IBIS_II_E_band_min="{e_min}" \
        IBIS_II_E_band_max="{e_max}" \
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

        console.print(f"[bold green]✓ Pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec/len(scws):.2f}s per ScW)[/bold green]")

        fits_files = list(obs_dir.glob("**/*.fits"))
        console.print(f"[cyan]Generated {len(fits_files)} FITS science products in {obs_dir}[/cyan]")
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
    start_level: str = typer.Option("COR", "--start-level", help="Start level (COR, DEAD, BIN_I, IMA, IMA2)"),
    end_level: str = typer.Option("IMA2", "--end-level", help="End level (IMA, IMA2, SPE, LCR)"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Docker image to run"),
    workdir: Path = typer.Option(Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"),
    og_name: str = typer.Option("obs_jemx", "--og", "-o", help="Observation group name"),
    clean: bool = typer.Option(True, "--clean/--no-clean", help="Clean prior observation group directory before run"),
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
        console.print(f"[bold green]✓ JEM-X pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec/len(scws):.2f}s per ScW)[/bold green]")
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
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Docker image to run"),
    workdir: Path = typer.Option(Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"),
    og_name: str = typer.Option("obs_omc", "--og", "-o", help="Observation group name"),
    clean: bool = typer.Option(True, "--clean/--no-clean", help="Clean prior observation group directory before run"),
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
        console.print(f"[bold green]✓ OMC pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec/len(scws):.2f}s per ScW)[/bold green]")
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
    start_level: str = typer.Option("COR", "--start-level", help="Start level (COR, DEAD, POINT, BKG, SPIROS, SPIMODFIT)"),
    end_level: str = typer.Option("SPIROS", "--end-level", help="End level (COR, DEAD, POINT, BKG, SPIROS, SPIMODFIT)"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Docker image to run"),
    workdir: Path = typer.Option(Path.cwd() / "work", "--workdir", "-w", help="Working directory for analysis"),
    og_name: str = typer.Option("obs_spi", "--og", "-o", help="Observation group name"),
    clean: bool = typer.Option(True, "--clean/--no-clean", help="Clean prior observation group directory before run"),
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
        console.print(f"[bold green]✓ SPI pipeline completed in {elapsed_sec:.2f}s ({elapsed_sec/len(scws):.2f}s per ScW)[/bold green]")
    except Exception as e:
        elapsed_sec = time.perf_counter() - start_time
        console.print(f"[bold red]SPI pipeline stopped after {elapsed_sec:.2f}s: {e}[/bold red]")
        raise typer.Exit(code=1)

