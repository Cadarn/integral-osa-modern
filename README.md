# INTEGRAL Cloud & Apple Silicon Analysis

Modernized analysis pipeline and container runtime for the **INTEGRAL (INTErnational Gamma-Ray Astrophysics Laboratory)** space observatory.

The software is optimized for **local analysis on Apple Silicon (M1/M2/M3/M4 macOS)** with native ARM64 containerization (avoiding Rosetta emulation) and modular data management, designed to easily scale into Kubernetes/Cloud batch pipelines in subsequent stages.

All Python environment and package workflows are managed with **`uv`**.

---

## Unified Typer CLI (`integral`)

The project includes a single, modular Typer CLI called **`integral`** for all container lifecycle management, data archiving, downloading, and scientific reduction.

### 1. Initialize Python Environment with `uv`
```bash
uv sync
```

### 2. Check Environment & Hardware Status
```bash
uv run integral status
```

### 3. Initialize Data Archive & Import Local Revolution Data
You can initialize the local data repository and import/link local revolution data (e.g. from `~/Sites/0060/0060`):
```bash
# Initialize data archive directory at ~/science/integral_data_archive
uv run integral data init

# Import / symlink Revolution 0060 data
uv run integral data import-local ~/Sites/0060/0060 --link

# Inspect the archive
uv run integral data list
```

### 4. Download Reference Catalogs & Science Windows from HEASARC
```bash
# Download General & OMC Reference Catalogs
uv run integral data download --catalogs

# Download a specific Science Window (e.g. 006000010010)
uv run integral data download --scw 006000010010
```

### 5. Build & Launch Docker Image
The CLI automatically detects your CPU architecture (e.g. Apple Silicon `arm64`) and selects the native `Dockerfile.arm64` image with `uv` virtualenv support:
```bash
# Build native container
uv run integral docker build

# Launch interactive container session with auto-mounted data & UID/GID mapping
uv run integral docker run

# Run a one-line science analysis command inside container
uv run integral docker run "og_create --help"
```

### 6. Run Instrument Science Pipelines
```bash
# Run IBIS/ISGRI reduction on a Science Window
uv run integral analyze ibis 006000010010
```

---

## Directory Structure

```
.
├── src/integral_cli/           # Unified Typer CLI Application
│   ├── main.py                 # CLI entry point
│   ├── config.py               # Local configuration manager (~/.integralrc.json)
│   ├── docker_mgr.py           # Docker build, run, and architecture manager
│   ├── data_mgr.py             # Data importer, archive manager & HEASARC downloader
│   └── analysis.py             # Scientific pipeline launcher (IBIS/JEM-X)
├── osa-docker/                 # Container definitions & build targets
│   ├── Dockerfile.arm64        # Native ARM64 (Apple Silicon & AWS Graviton)
│   ├── Dockerfile.x86          # Reference x86_64 baseline
│   ├── Dockerfile.batch        # Lean headless worker image for K8s jobs
│   ├── Makefile                # Multi-target container build automation
│   └── init.d/                 # Modular init scripts (HEASoft, OSA, uv /opt/venv)
├── pyproject.toml              # Astronomy & CLI dependencies
├── uv.lock                     # Locked dependency tree across ARM64 & x86_64
└── docs/                       # Reference notes and documentation
```
