# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A modernisation of ESA's INTEGRAL (INTErnational Gamma-Ray Astrophysics Laboratory) Off-line
Scientific Analysis (OSA 11.2) pipeline: a native ARM64/Apple Silicon container build of the
~20-year-old ISDC C/C++/Fortran science software, plus a unified Python/Typer CLI (`integral`)
that wraps the legacy binaries for local reduction and cloud/Kubernetes batch processing.

The actual science executables (`ibis_science_analysis`, `jemx_science_analysis`,
`omc_science_analysis`, `spi_science_analysis`, `og_create`, etc.) live inside the Docker images
built from `docker/` — they are not part of this Python source tree. This repo's Python code is
an orchestration/automation layer around those containerized binaries.

## Commands

```bash
# Environment setup
uv sync

# Run the CLI (all functionality goes through this)
uv run integral <command>
uv run integral status                 # architecture/config info
uv run integral data init              # init local data archive
uv run integral data download revolution 0060 --count 5    # first 5 pointing ScWs + catalogs/IC-index/aux
uv run integral data download scw 006000010010,006000020010
uv run integral data download file scw_list.txt
uv run integral data download calibration --ic-trees --instruments ibis,sc  # large per-instrument IC trees, opt-in
uv run integral analyse ibis rev:0060:10 --e-min 18 --e-max 60 --mosaic
uv run integral analyse jemx rev:0060:5 --unit 1
uv run integral analyse omc rev:0060:5
uv run integral analyse spi rev:0060:5
uv run integral view image <fits-file>
uv run integral view sources <fits-file>
uv run integral benchmark <...>
uv run integral docker build --arch auto
uv run integral docker run
uv run integral tui                    # interactive terminal UI for launching analyse runs

# Lint / type-check (config in pyproject.toml; also run in CI, see below)
uv run ruff check .
uv run pyright src

# Tests (tests/, mirrors src/integral_cli/ layout)
uv run pytest
uv run pytest tests/test_analysis.py::test_bare_scw_id_passthrough   # single test

# Docker images (definitions in docker/, matrix built by .github/workflows/docker-build-publish.yml)
docker build --platform linux/arm64 -t integralsw/osa:11-native-arm64 -f docker/Dockerfile.native-arm64 .
docker build --platform linux/amd64 -t integralsw/osa:11-modern-amd64 -f docker/Dockerfile.modern .
```

## Architecture

The codebase follows the modern Python `src-layout` centered in `src/integral/` with backwards-compatibility shims provided in `src/integral_cli/`, `scripts/`, and `pipeline/`.

### Core layer (`src/integral/core/`)
- `config.py` — `IntegralConfig` (pydantic), persisted to `~/.integralrc.json`. Holds
  `data_dir`/`ic_dir` and resolves the ISDC env vars `REP_BASE_PROD` / `CURRENT_IC` (env var
  overrides config file). `host_arch` detects arm64 vs x86_64 to pick the right Docker image.
- `docker.py` — builds/runs the OSA containers. `run_container()` is the shared execution
  primitive: mounts local data (`scw/`, `aux/`, `ic/`, `idx/`, `cat/` as read-only
  volumes keyed off `config.rep_base_prod`/`config.current_ic`), resolves host symlinks,
  matches host UID:GID, and executes within the container environment.
- `data.py` — async httpx/HTTP2 downloader for HEASARC and ISDC archives; handles local archive layout
  (`scw/<rev>/`, `idx/ic/`, `aux/adp/<rev>.001/`), atomic `.tmp`-then-rename downloads, mirror health
  probing (`integral data mirror --test`), and local data import.
- `calibration.py` — declarative calibration profiles (`CalibrationProfile`, `CalibrationRule`)
  and index filtering engine for historical re-play and modern baselines.
- `scw.py` — `filter_pointing_scws()`, the pointing-ScW-selection convention (IDs ending `0010`,
  falling back to all IDs) shared between local and remote resolution.
- `batch.py` — partitions Science Window lists into fixed-size JSON batch manifests for distributed
  cloud and Kubernetes batch worker execution.

### Instrument pipelines (`src/integral/instruments/`)
- `common.py` — shared parameter parsing (`parse_energy_bands`, `parse_jemx_energy_channels`,
  `validate_time_step`, `resolve_scw_ids`).
- `ibis.py` — IBIS/ISGRI pipeline runner (`run_ibis`).
- `jemx.py` — JEM-X 1 & 2 pipeline runner (`run_jemx`).
- `omc.py` — OMC optical monitor pipeline runner (`run_omc`).
- `spi.py` — SPI gamma-ray spectrometer pipeline runner (`run_spi`).

### Validation & Analytics (`src/integral/validation/`)
- `compare.py` — FITS numerical verification engine (images and binary tables) across architectures.
- `benchmark.py` — cross-architecture execution timing and multi-run delta comparison suite.

### Presentation & UI (`src/integral/cli/` & `src/integral/viewer/`)
- `cli/main.py` — root Typer app entry point (`integral` script).
- `cli/cal_cli.py` — calibration profile management CLI (`integral cal`).
- `cli/tui/app.py` — Textual TUI (`integral tui`) for configuring and launching runs interactively.
- `viewer/fits_view.py` — FITS mosaic/image viewing (WCS rendering, ZScale) and source-list summaries.

### Backwards Compatibility Shims
- `src/integral_cli/` — compatibility layer re-exporting modules so `import integral_cli` continues to work.
- `scripts/validate_science_products.py` — CLI shim forwarding to `integral.validation.compare`.
- `scripts/fetch_integral_data.py` — CLI shim forwarding to `integral.core.data`.
- `pipeline/scw_distributor.py` — CLI shim forwarding to `integral.core.batch`.

### Science Window (ScW) addressing convention

Used consistently across `analysis.py`, `data_mgr.py`, and `pipeline/scw_distributor.py`:
a ScW ID is a 12-digit string (e.g. `006000010010`), whose first 4 digits are the revolution
number (e.g. `0060`). On disk/archive it's suffixed `.001` (e.g. `006000010010.001`). Revolution
shorthand `rev:0060:10` means "first 10 pointing ScWs (IDs ending `0010`) of revolution 0060".

### Docker images (`docker/`)

Multiple Dockerfiles target different use cases — check which is relevant before editing:
- `Dockerfile.native-arm64` — full native ARM64 compile of all instrument binaries (IBIS, JEM-X,
  OMC, SPI), the flagship "no emulation" image. Long build (QEMU cross-compile in CI, ~30–45 min,
  180 min timeout).
- `Dockerfile.modern` — slim modern x86_64 multi-stage build (Python 3.12 + `uv`, CERN ROOT
  removed, ~1.02 GB vs ~2.8 GB legacy).
- `Dockerfile.batch` — lightweight worker image for Kubernetes batch jobs (`k8s/job-template.yaml`
  runs it via `pipeline/runner_scw.sh`).
- `Dockerfile.apple-silicon` / `Dockerfile.arm64` / `Dockerfile.x86` — earlier/alternate build
  variants; `Dockerfile` is the legacy baseline.
- `docker/init.d/*.sh` — sourced in order at container startup (`00-init-writable-home`,
  `10-heasoft`, `20-osa`, `30-python-uv`) to set up ISDC env vars, HEASoft, OSA, and the `uv`
  venv. `docker/init.sh` is the umbrella entrypoint these are chained from.

Native ARM64 recompilation required source patches to the ~20-year-old ISDC codebase (config.guess/
config.sub ARM64 detection, `-fallow-argument-mismatch` for modern gfortran, reserved-label
renames, etc.) — see `docs/technical_rebuild_arm64.md` for the full catalogue of build fixes if
touching the native-arm64 build.

### CI

Two independent workflows:
- `.github/workflows/docker-build-publish.yml` — matrix-builds `modern-x86`, `batch-pipeline`
  (multi-arch), and `native-arm64` images and pushes to Docker Hub (`integralsw/osa`).
  `native-arm64` only runs on tag pushes or manual dispatch with `build_arm64=true` (it's the slow
  QEMU cross-build) — it is skipped on ordinary PRs/pushes. Uses GHA layer caching
  (`cache-from`/`cache-to: type=gha`) scoped per matrix entry.
- `.github/workflows/python-ci.yml` — runs `ruff check .`, `pyright src`, and `pytest`, all
  blocking, on every push/PR touching `src/`, `scripts/`, `pipeline/`, `tests/`, or
  `pyproject.toml`/`uv.lock`. Type checking uses Pyright rather than mypy — mypy's
  `ignore_missing_imports` was silently skipping real type
  errors in astropy-touching code (see `viewer.py`/`benchmark.py`'s `# pyright: ignore[...]`
  comments for astropy's own stub imprecisions that Pyright does catch and that are suppressed
  deliberately, one rule+line at a time, rather than a blanket ignore).

### Cloud/batch path (`pipeline/`, `k8s/`)

`pipeline/scw_distributor.py` (`integral-distribute`) partitions a ScW list into fixed-size JSON
batch manifests for parallel workers. `k8s/job-template.yaml` defines the Kubernetes Job that runs
`Dockerfile.batch` workers against those batches, reading `REP_BASE_PROD`/`CURRENT_IC` from env
and S3/GCS buckets for data. `pipeline/runner_scw.sh` is the in-container batch entrypoint.

## Key environment variables

These mirror the legacy ISDC pipeline's own conventions and are set both on the host (via
`IntegralConfig`) and inside containers (via the bash heredocs in `analysis.py` / `init.d/`):
`REP_BASE_PROD` (data archive root), `CURRENT_IC` (instrument characteristics root), `ISDC_ENV`
(OSA install root, `/opt/osa` in-container), `ISDC_REF_CAT`/`ISDC_OMC_CAT` (reference catalogs),
`PFILES` (IRAF-style parameter file search path).
