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
uv run integral data download --catalogs
uv run integral analyse ibis rev:0060:10 --e-min 18 --e-max 60 --mosaic
uv run integral analyse jemx rev:0060:5 --unit 1
uv run integral analyse omc rev:0060:5
uv run integral analyse spi rev:0060:5
uv run integral view image <fits-file>
uv run integral view sources <fits-file>
uv run integral benchmark <...>
uv run integral docker build --arch auto
uv run integral docker run

# Lint / type-check (config in pyproject.toml, no wired-up make/CI task — run directly)
uv run ruff check .
uv run mypy src

# Tests (pytest + pytest-asyncio declared as dev deps; no test suite exists yet — write to
# src/integral_cli/ conventions using pytest when adding tests)
uv run pytest
uv run pytest path/to/test_file.py::test_name   # single test

# Docker images (definitions in docker/, matrix built by .github/workflows/docker-build-publish.yml)
docker build --platform linux/arm64 -t integralsw/osa:11-native-arm64 -f docker/Dockerfile.native-arm64 .
docker build --platform linux/amd64 -t integralsw/osa:11-modern-amd64 -f docker/Dockerfile.modern .
```

## Architecture

### CLI layer (`src/integral_cli/`)

- `main.py` — Typer app entry point (`integral` script); registers sub-apps as typer groups.
- `config.py` — `IntegralConfig` (pydantic), persisted to `~/.integralrc.json`. Holds
  `data_dir`/`ic_dir` and resolves the ISDC env vars `REP_BASE_PROD` / `CURRENT_IC` (env var
  overrides config file). `host_arch` detects arm64 vs x86_64 to pick the right Docker image.
- `docker_mgr.py` — builds/runs the OSA containers. `run_container()` is the shared execution
  primitive: it mounts the local data archive (`scw/`, `aux/`, `ic/`, `idx/`, `cat/` as read-only
  volumes keyed off `config.rep_base_prod`/`config.current_ic`), resolves symlink targets on the
  host so they still work inside the container, matches host UID:GID, and runs a bash command
  string inside the image after sourcing `/init.sh`. Every `analyse` subcommand calls this.
- `analysis.py` — one Typer command per instrument (`ibis`, `jemx`, `omc`, `spi`). Each follows
  the same pattern: resolve a `scw_input` argument (a `rev:NNNN[:limit]` revolution spec, a
  comma-separated ScW ID list, or a path to a `scw.list` file) into a list of Science Window IDs,
  write `scw.list` into the workdir, build a bash heredoc that sets ISDC env vars
  (`ISDC_ENV`, `REP_BASE_PROD`, `PFILES`, `ISDC_REF_CAT`, ...), runs `og_create` to build an
  Observation Group, then invokes the instrument's `*_science_analysis` executable with
  instrument-specific parameters, and finally calls `run_container()`. When adding a new
  instrument or pipeline stage, mirror this structure rather than introducing a different one.
- `data_mgr.py` — async httpx/HTTP2 downloader for HEASARC's public INTEGRAL archive
  (`https://heasarc.gsfc.nasa.gov/FTP/integral/data`); handles local archive layout
  (`scw/<rev>/`, `idx/ic/`, `aux/`), atomic `.tmp`-then-rename downloads, and local data import.
- `viewer.py` — FITS mosaic/image viewing (WCS rendering, ZScale) and source-list summaries.
- `benchmark.py` — cross-architecture (native ARM64 vs emulated x86_64) timing comparisons.

`scripts/validate_science_products.py` (wired in as `integral validate`) and
`scripts/fetch_integral_data.py` are standalone entry points also exposed via
`[project.scripts]` in `pyproject.toml` (`integral-validate`, `integral-fetch`).

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

### CI (`.github/workflows/docker-build-publish.yml`)

Matrix-builds `modern-x86`, `batch-pipeline` (multi-arch), and `native-arm64` images and pushes to
Docker Hub (`integralsw/osa`). `native-arm64` only runs on tag pushes or manual dispatch with
`build_arm64=true` (it's the slow QEMU cross-build) — it is skipped on ordinary PRs/pushes. Uses
GHA layer caching (`cache-from`/`cache-to: type=gha`) scoped per matrix entry.

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
