# `integral-osa-modern`

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Build & Publish](https://github.com/Cadarn/integral-osa-modern/actions/workflows/docker-build-publish.yml/badge.svg)](https://github.com/Cadarn/integral-osa-modern/actions/workflows/docker-build-publish.yml)
[![Architecture](https://img.shields.io/badge/Arch-ARM64%20%7C%20AMD64-blue.svg)]()

Modernised analysis pipeline, native **ARM64 / Apple Silicon** container runtime, and high-throughput Python CLI for ESA's **INTEGRAL (INTErnational Gamma-Ray Astrophysics Laboratory)** Off-line Scientific Analysis (OSA 11.2).

---

## 🌟 Highlights

* **Native ARM64 Compilation & 2.44× Speedup:** Eliminates Rosetta/QEMU emulation bottlenecks on Apple Silicon (M1/M2/M3/M4) and ARM64 cloud instances (AWS Graviton), achieving a **2.44× wall-clock speedup** with verified sub-0.07% numerical consistency.
* **Complete Multi-Instrument Suite:** Verified native builds for **IBIS** (`ibis_science_analysis`), **JEM-X** (`jemx_science_analysis`), **OMC** (`omc_science_analysis`), and **SPI** (`spi_science_analysis`, `spiros`, `spimodfit`).
* **Slim Modern Containers (1.02 GB):** ~64% smaller than legacy releases (2.8 GB), with CERN ROOT overhead removed and a modern Python 3.12 scientific stack (`astropy`, `numpy`, `scipy`, `matplotlib`) pre-installed via [`uv`](https://github.com/astral-sh/uv).
* **Interactive Terminal User Interface (TUI):** Full-featured [Textual](https://textual.textualize.io/) dashboard (`integral tui`) with dynamic instrument-aware parameter controls, local archive ScW browser, real-time stage progress & telemetry, collapsible sidebars, and interactive source inspection.
* **Unified Typer CLI (`integral`):** Streamlined local and cloud reduction workflows, interactive wizards (`--interactive`), automated data archive initialisation, HEASARC downloads, and FITS mosaic visualisation.
* **Production CI/CD:** GitHub Actions Python testing gate, automated multi-arch Docker image recipes, and Kubernetes distributed processing manifests.

---

## 🚀 Quick Start (Using the `integral` CLI)

### 1. Initialise Python Environment with `uv`
```bash
# Clone the repository
git clone https://github.com/Cadarn/integral-osa-modern.git
cd integral-osa-modern

# Sync virtualenv using uv
uv sync
```

### 2. Launch the Interactive Textual TUI Dashboard
Launch the visual terminal dashboard to configure and run reductions without memorising CLI flags:
```bash
uv run integral tui
```

#### TUI Features & Shortcuts:
* **Dynamic Instrument Forms:** Switch between **IBIS (Imager)**, **SPI (Spectrometer)**, **JEM-X (X-ray monitor)**, and **OMC (Optical)** — energy presets, detector modes, units, filters, and pipeline product levels update reactively.
* **Timing / Lightcurve Controls:** Selecting `LCR` product levels displays timing analysis controls supporting both Standard (`ibis_lc`, default: 10s bins) and High-Resolution PIF modes (`ii_pif`, millisecond pulsar timing).
* **Local ScW Archive Discovery:** Click `[Browse...]` next to Science Windows to visually discover and multi-select available pointings directly from your local archive.
* **Collapsible Configuration Sidebar:** When analysis begins, the left-hand form auto-collapses to give 100% full-screen terminal width to live output logs, telemetry sparklines, and tables. Click `[⮜ Hide Config]` / `[⮞ Show Config]` or press <kbd>c</kbd> anytime to toggle.
* **Interactive Source Inspection & Lightcurves:** Select rows from the **Detected Sources** table to inspect detailed coordinates, detection significance, and flux statistics.
* **Keyboard Shortcuts:**
  * <kbd>c</kbd>: Toggle configuration sidebar (Show / Hide).
  * <kbd>q</kbd>: Quit dashboard.

---

### 3. Inspect Environment & Archive Status
```bash
uv run integral status
```

### 4. Initialise Data Archive & Download Data
```bash
# Initialise local archive (default: ~/science/integral_data_archive)
uv run integral data init

# Import/link existing revolution data (e.g. Revolution 0060)
uv run integral data import-local /path/to/0060 --link

# Download the first 5 pointing Science Windows of Revolution 0060, plus the
# catalogs/IC-index/aux data every reduction needs (skipped automatically if
# already present)
uv run integral data download revolution 0060 --count 5

# Or fetch specific ScWs, or a whole file of ScW IDs
uv run integral data download scw 006000010010,006000020010
uv run integral data download file scw_list.txt

# Full per-instrument IC calibration trees are large (multi-GB) and opt-in
uv run integral data download calibration --ic-trees --instruments ibis,sc
```
> [!NOTE]
> A reduction needs the full IC calibration tree (`--ic-trees`) for its instrument, not just
> the default catalogs/IC-index/aux fetch above. Even with the full tree downloaded, IBIS
> reductions currently fail during background estimation (`ISGR-BACK-BKG status -2004`) when
> data is sourced from this HEASARC-based downloader rather than ISDC's own (currently offline)
> IC distribution — see `docs/status_report.md` for the full investigation.

### 5. Run Scientific Reduction Pipelines via CLI
```bash
# Interactive reduction wizard (prompts for ScWs, energy bands, product levels)
uv run integral analyse ibis --interactive

# Direct CLI flags: Run IBIS/ISGRI reduction & mosaic on 10 Science Windows (18-60 keV)
uv run integral analyse ibis rev:0060:10 --e-min 18 --e-max 60 --mosaic

# High-resolution PIF lightcurve timing run (0.005s bins)
uv run integral analyse ibis rev:0060:5 --end-level LCR --timing-mode pif --time-step 0.005

# Run JEM-X reduction (Unit 1 or 2, 3-35 keV)
uv run integral analyse jemx rev:0060:5 --unit 1 --bands 3-35

# Run OMC optical reduction
uv run integral analyse omc rev:0060:5

# Run SPI gamma-ray spectrometer reduction
uv run integral analyse spi rev:0060:5
```

---

## 🐳 Running Docker Images Directly (Without the CLI)

You can run the pre-built container images directly using `docker run`:

### Pulling Pre-built Images from Docker Hub
```bash
# Pull native ARM64 image (Apple Silicon / Graviton)
docker pull integralsw/osa:11-native-arm64

# Pull modern AMD64 image (x86_64)
docker pull integralsw/osa:11-modern-amd64
```

### Interactive Shell Session
```bash
docker run --rm -it \
  --user $(id -u):$(id -g) \
  -v /path/to/integral_data_archive:/data:ro \
  -v $(pwd)/work:/home/integral \
  integralsw/osa:11-native-arm64 bash
```

### Executing a Pipeline Directly
```bash
docker run --rm \
  --user $(id -u):$(id -g) \
  -v /path/to/integral_data_archive:/data:ro \
  -v $(pwd)/work:/home/integral \
  integralsw/osa:11-native-arm64 \
  bash -c "
    cd /home/integral && \
    og_create idxSwg=scw.list instrument=IBIS ogid=obs_ibis baseDir=./ obsDir=obs && \
    cd obs/obs_ibis && \
    ibis_science_analysis startLevel=DEAD endLevel=IMA2
  "
```

---

## 🛠️ Building Images Locally

All container definitions live under the [`docker/`](docker/) directory:

```bash
# Build native ARM64 image
docker build --platform linux/arm64 \
  -t integralsw/osa:11-native-arm64 \
  -f docker/Dockerfile.native-arm64 .

# Build modern x86_64 image
docker build --platform linux/amd64 \
  -t integralsw/osa:11-modern-amd64 \
  -f docker/Dockerfile.modern .
```

---

## 📂 Repository Structure

```
.
├── docker/                     # Container definitions & build targets
│   ├── Dockerfile.native-arm64 # Complete native ARM64 multi-instrument build
│   ├── Dockerfile.modern       # Slim modern x86_64 multi-stage build
│   ├── Dockerfile.batch        # Lightweight worker pod image for Kubernetes
│   ├── init.sh                 # Container entrypoint environment loader
│   └── init.d/                 # Runtime initialization scripts (OSA, uv, HEASoft)
├── src/integral_cli/           # Unified CLI & Terminal User Interface
│   ├── main.py                 # CLI entry point (`integral`)
│   ├── tui.py                  # Full-featured Textual TUI dashboard (`integral tui`)
│   ├── analysis.py             # Pipeline runners & wizards (IBIS, JEM-X, OMC, SPI)
│   ├── config.py               # Centralised paths, Docker image & environment settings
│   ├── data_mgr.py             # Data archive manager & HEASARC downloader
│   ├── docker_mgr.py           # Docker execution engine & architecture detector
│   ├── scw_utils.py            # Science window parsing & pointing filter utilities
│   ├── viewer.py               # FITS mosaic image viewer & statistics
│   └── benchmark.py            # Cross-architecture benchmark suite
├── pipeline/                   # Distributed execution components
│   ├── scw_distributor.py      # Multi-worker Science Window job distributor
│   └── runner_scw.sh           # Per-ScW container execution wrapper script
├── scripts/                    # Validation & diagnostic tools
│   ├── fetch_integral_data.py  # Standalone archive fetcher
│   └── validate_science_products.py # Numerical verification against ISDC reference runs
├── tests/                      # Automated pytest suite (CLI, TUI, data, analysis)
│   ├── test_analysis.py        # Pipeline invocation & energy band parsing tests
│   ├── test_cli.py             # Typer CLI smoke & help tests
│   ├── test_config.py          # Configuration loading & override tests
│   ├── test_data_mgr.py        # Data archive & download tests
│   ├── test_scw_utils.py       # ScW spec & pointing filter tests
│   └── test_tui.py             # Textual async pilot tests (forms, timing, collapse)
├── docs/                       # Technical publications & documentation
│   ├── technical_rebuild_arm64.md # MNRAS Techniques paper draft
│   ├── status_report.md        # Calibration & background estimation status
│   └── technical_roadmap.md    # Multi-phase development roadmap
├── k8s/                        # Kubernetes spot-instance distributed cluster manifests
│   ├── job-template.yaml       # Distributed worker pod batch job definition
│   └── node-pool-spot.yaml     # Spot instance node pool specifications
├── pyproject.toml              # Dependencies & CLI build configuration (uv)
└── LICENSE                     # MIT License
```

---

## 📜 Acknowledgements & Upstream Lineage

* **INTEGRAL OSA:** Developed by the [ISDC Data Centre for Astrophysics](https://www.isdc.unige.ch/integral/), University of Geneva, and ESA.
* **Legacy Docker Baseline:** Based on and extended from [ISDC-integral/osa-docker](https://github.com/ISDC-integral/osa-docker).
* **Scientific Data Archive:** Hosted by [NASA/GSFC HEASARC](https://heasarc.gsfc.nasa.gov/FTP/integral/).

For technical details on the ARM64 compilation patches, ABI alignments, and benchmark analysis, see our paper draft at [`docs/technical_rebuild_arm64.md`](docs/technical_rebuild_arm64.md).

---

## ⚖️ License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
