# INTEGRAL OSA Modernisation — Multi-Machine Task Tracker & Handover Guide

**Last Updated**: 5 September 2026  
**Current Active Branch**: `feature/phase-a-validation-suite`  
**Benchmarked Reference Hosts**:
- Primary Development / Benchmark Host: Apple MacBook Pro M4 Pro (12-core CPU, 48 GB Unified Memory), macOS Tahoe 26.6.2 (Build 25G83)  
- Prior Reference Host: Apple MacBook Pro M4 Max (16-core CPU, 36 GB Unified Memory), macOS Tahoe 26.5.2 (Build 25F84)  
**Target Container Images**:
- Native ARM64: `cadarn/osa:11-native-arm64` (`integralsw/osa:11-native-arm64`)
- Modern AMD64: `cadarn/osa:11-modern-amd64` (`integralsw/osa:11-modern-amd64`)
- Legacy Reference: `integralsw/osa:11.0`

---

## 1. Executive Status Overview

This document tracks completed milestones, current progress across instruments and features, and exact instructions for picking up ongoing work on a new host machine.

```
[████████████████████] 100% Phase A Multi-Instrument Validation (ARM64 vs ESA Baseline)
[████████████████████] 100% Full IC Calibration Archive Synchronization (jmx1, jmx2, omc, spi, ibis)
[████████████████████] 100% Archive Mirror Health Probing & Switching CLI (`integral data mirror`)
[████████████████████] 100% Calibration Profile Engine & History Replay (`integral cal`)
[████████████████████] 100% Automated Cross-Run Scientific Evaluation (`integral benchmark compare`)
[██████████░░░░░░░░░░]  50% Phase B Benchmark Scaling Curve (10/25/50 ScW Multi-Repeat Matrix)
[░░░░░░░░░░░░░░░░░░░░]   0% Cloud Scale-Out & Spot Worker Distribution (Kubernetes batch jobs)
```

---

## 2. Completed Milestones (Ready & Committed)

### A. Phase A Multi-Instrument Experimental Validation & Architecture Benchmark
- Completed end-to-end benchmark and cross-architecture comparison across all 4 primary instruments on MacBook Pro M4 Pro (macOS Tahoe 26.6.2):
  - **OMC** (2 ScWs):
    - ARM64: **13.4 s** | x86: **35.7 s** (**2.66x speedup**)
    - 100% bitwise matching on all 297 stars ($\Delta\text{Mag}_V = 0.000$, offset $= 0.00''$). 16/16 FITS products verified.
  - **SPI** (10 ScWs):
    - ARM64: **8.1 s** | x86: **18.0 s** (**2.22x speedup**)
    - 100% numerical match (Crab detection $159.75\sigma$ on both ARM64 and x86, $\Delta = 0.00\sigma$, offset $= 0.00''$). 29/29 FITS products verified.
  - **JEM-X 2** (2 ScWs):
    - ARM64: **61.5 s** | x86: **117.1 s** (**1.90x speedup**)
    - ARM64 yields Crab $38.02\sigma$ exactly matching the ESA reference ($38.02\sigma$), reflecting updated `j_ima_iros 6.2.4`.
  - **IBIS/ISGRI** (4 ScWs):
    - ARM64: **119.3 s** (Modern IC) / **121.0 s** (Legacy IC) | x86: **270.7 s** (Modern IC) / **278.4 s** (Legacy IC) (**2.27x - 2.30x speedup**)
    - Modern IC Crab: ARM64 $299.60\sigma$ ($98.24 \pm 0.33$ cts/s) vs x86 $299.36\sigma$ ($98.20 \pm 0.33$ cts/s) — **99.92% cross-architecture agreement**.
    - Legacy IC Crab (`ISGR_EFFC_MOD=1`): ARM64 $314.36\sigma$ ($141.77 \pm 0.45$ cts/s) vs x86 $313.97\sigma$ ($141.68 \pm 0.45$ cts/s), directly tracing ESA reference ($328.77\sigma$, $145.39 \pm 0.44$ cts/s).
- Detailed guide on modifying `ic_master_file.fits` for legacy/historic analysis created at `docs/calibration_legacy_modifications.md`.
- Detailed report checked into `validation_runs/reports/phase_a_validation_report.md`.

### B. Archive Mirror Switching & Health Probing
- Added `--mirror / -m` CLI support for `heasarc` (default) and European mirrors.
- Implemented latency health check and fallback detection in `src/integral_cli/data_mgr.py`.
- Added `integral data mirror [--test] [name]` command.

### C. Calibration Profile Management (`integral cal`)
- Added declarative JSON profiles (`CalibrationProfile`, `CalibrationRule`) in `src/integral_cli/cal_profiles.py`.
- Ships with built-ins:
  - `latest`: Unconstrained modern dynamic IC archive.
  - `esa-2022`: Pinned historical baseline matching ESA reference dataset.
- Added zero-duplication provisioning (`integral cal provision <name>`) using symlinks for multi-GB data.
- Added interactive creation wizard (`integral cal create`) and export tool (`integral cal export`).

### D. Automated Scientific Cross-Run Comparison
- Added `integral benchmark compare <dir_a> <dir_b>` in `src/integral_cli/benchmark.py`.
- Outputs rich comparison tables detailing source names, $\Delta\text{DetSig}$ ($\sigma$), astrometric offset (arcsec), and fluxes.

### E. Code Quality & Test Suite
- Full pytest test suite passing (62/62 tests in `tests/`).
- Ruff linting and formatting 100% clean (`uv run ruff check .`, `uv run ruff format --check .`).

---

## 3. Tasks Still to Complete / Next Workstreams

### Task 1: Complete Phase B Factorial Benchmark Scaling Matrix
*Objective*: Collect multi-repeat statistics for the MNRAS methods paper.
- [ ] Run 3 repeats each across ScW counts: 10, 25, 50 ScWs.
- [ ] Record mean execution time $\pm$ standard deviation.
- [ ] Measure across both architectures on Apple Silicon:
  - Native ARM64 (`cadarn/osa:11-native-arm64`)
  - Emulated AMD64 (`cadarn/osa:11-modern-amd64` under Rosetta 2)
- [ ] Parse `common_log.txt` timestamps to produce a per-pipeline-stage breakdown table (deconvolution vs event correction vs imaging).

### Task 2: Cloud / x86 Reference Run
*Objective*: Disentangle "Apple Silicon hardware speed" from "ARM64 instruction set speed".
- [ ] Run the benchmark matrix on a native Linux x86_64 cloud instance (e.g. AWS c6i / c7i or GCP n2).
- [ ] Compare native x86_64 vs native ARM64 (Graviton/M-series) vs Rosetta emulation.

### Task 3: Cloud Scale-Out & Distributed Kubernetes Execution
*Objective*: Implement distributed reduction of large datasets across cloud spot instances.
- [ ] Wire up `pipeline/scw_distributor.py` with S3/GCS object storage bucket mounts.
- [ ] Verify `k8s/job-template.yaml` worker execution with modern container image.
- [ ] Add CLI orchestration command: `integral distribute run --bucket <s3-uri> --scws <rev>`.

---

## 4. How to Pick Up & Resume on Another Machine

When cloning or switching to a new development workstation or cloud VM:

### 1. Clone and Checkout Branch
```bash
git clone https://github.com/Cadarn/integral-osa-modern.git
cd integral-osa-modern
git checkout feature/phase-a-validation-suite
```

### 2. Environment Setup (Using `uv`)
Ensure `uv` and Docker are installed:
```bash
# Sync all dependencies into isolated virtual environment
uv sync --all-extras

# Verify CLI entrypoints and tests
uv run integral --help
uv run pytest
```

### 3. Data Archive and Test Package Directory Layout
Place the data archives adjacent to the repo (recommended default) or configure paths in `~/.integralrc.json`:
```text
../
├── integral-osa-modern/      # This repository
├── integral_data_archive/    # Main data archive (scw, aux, ic, cat)
└── integral_test_data/       # Official ESA testdata package (osa_testdata-11.2.tar.gz)
```

If setting up on a clean machine without calibration trees:
```bash
# Download core reference catalogs and indexes
uv run integral data download calibration

# Download instrument trees needed for analysis (e.g. IBIS, JEM-X, OMC, SPI)
uv run integral data download calibration --ic-trees --instruments ibis,jmx1,jmx2,omc,spi
```

### 4. Running Benchmarks or Comparisons
```bash
# Run multi-instrument validation suite against ESA testdata
uv run python validation/run_testdata_validation.py all

# Inspect or provision calibration environments
uv run integral cal list
uv run integral cal provision esa-2022

# Cross-compare any two observation runs
uv run integral benchmark compare <run_dir_1> <run_dir_2>
```
