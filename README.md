# `integral-osa-modern`

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Build & Publish](https://github.com/OWNER/integral-osa-modern/actions/workflows/docker-build-publish.yml/badge.svg)](https://github.com/OWNER/integral-osa-modern/actions/workflows/docker-build-publish.yml)
[![Architecture](https://img.shields.io/badge/Arch-ARM64%20%7C%20AMD64-blue.svg)]()

Modernized analysis pipeline, native **ARM64 / Apple Silicon** container runtime, and high-throughput Python CLI for ESA's **INTEGRAL (INTErnational Gamma-Ray Astrophysics Laboratory)** Off-line Scientific Analysis (OSA 11.2).

---

## 🌟 Highlights

* **Native ARM64 Compilation & 2.44× Speedup:** Eliminates Rosetta/QEMU emulation bottlenecks on Apple Silicon (M1/M2/M3/M4) and ARM64 cloud instances (AWS Graviton), achieving a **2.44× wall-clock speedup** with verified sub-0.07% numerical consistency.
* **Complete Multi-Instrument Suite:** Verified native builds for **IBIS** (`ibis_science_analysis`), **JEM-X** (`jemx_science_analysis`), **OMC** (`omc_science_analysis`), and **SPI** (`spi_science_analysis`, `spiros`, `spimodfit`).
* **Slim Modern Containers (1.02 GB):** ~64% smaller than legacy releases (2.8 GB), with CERN ROOT overhead removed and a modern Python 3.12 scientific stack (`astropy`, `numpy`, `scipy`, `matplotlib`) pre-installed via [`uv`](https://github.com/astral-sh/uv).
* **Unified Typer CLI (`integral`):** Streamlined local and cloud reduction workflows, automated data archive initialization, HEASARC downloads, and FITS mosaic visualization.
* **Production CI/CD:** Automated multi-arch GitHub Actions building and tagging images on Docker Hub.

---

## 🚀 Quick Start (Using the `integral` CLI)

### 1. Initialize Python Environment with `uv`
```bash
# Clone the repository
git clone https://github.com/OWNER/integral-osa-modern.git
cd integral-osa-modern

# Sync virtualenv using uv
uv sync
```

### 2. Inspect Environment & Archive Status
```bash
uv run integral status
```

### 3. Initialize Data Archive & Import Local Data
```bash
# Initialize local archive (default: ~/science/integral_data_archive)
uv run integral data init

# Import/link existing revolution data (e.g. Revolution 0060)
uv run integral data import-local /path/to/0060 --link

# Download latest general reference catalogs from HEASARC
uv run integral data download --catalogs
```

### 4. Run Scientific Reduction Pipelines
```bash
# Run IBIS/ISGRI reduction & mosaic on 10 Science Windows (18-60 keV)
uv run integral analyze ibis rev:0060:10 --e-min 18 --e-max 60 --mosaic

# Run JEM-X reduction (Unit 1 or 2)
uv run integral analyze jemx rev:0060:5 --unit 1

# Run OMC optical reduction
uv run integral analyze omc rev:0060:5

# Run SPI gamma-ray spectrometer reduction
uv run integral analyze spi rev:0060:5
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
│   └── init.d/                 # Container runtime entrypoints (OSA, uv, HEASoft)
├── src/integral_cli/           # Unified Typer CLI Application
│   ├── main.py                 # CLI entry point
│   ├── analysis.py             # Pipeline runners (IBIS, JEM-X, OMC, SPI)
│   ├── benchmark.py            # Automated cross-architecture benchmark suite
│   ├── data_mgr.py             # Data archive manager & HEASARC downloader
│   ├── docker_mgr.py           # Docker build, run, and architecture detector
│   └── viewer.py               # FITS mosaic image viewer & statistics
├── docs/                       # Technical publications & documentation
│   └── technical_rebuild_arm64.md # MNRAS Techniques paper draft
├── k8s/                        # Kubernetes spot-instance distributed cluster manifests
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
