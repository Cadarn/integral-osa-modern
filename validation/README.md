# Phase A: Experimental Validation Suite (ESA/ISDC Test Data)

This directory contains the modernized experimental validation framework for **INTEGRAL OSA 11.2**, designed to verify scientific correctness and numerical reproducibility of our **native ARM64** (`cadarn/osa:11-native-arm64` / `integralsw/osa:11-native-arm64`) and slim **modern x86_64** (`cadarn/osa:11-modern-amd64`) containers against ESA/ISDC reference outputs.

---

## 🔬 Motivation & Experimental Design

As described in the [OSA Installation Guide (Section 5)](https://www.cosmos.esa.int/documents/332075/24154101/osa_inst_guide.pdf), ESA packages canonical Crab observations across multiple instruments with pre-generated 64-bit reference outputs (`*_outref` and `*docker_outref`).

In **Phase A**, we use this official dataset to:
1. **Validate All 4 Instruments**: Test **IBIS** (4 ScWs, Rev 1667), **JEM-X 2** (2 ScWs, Rev 0102), **OMC** (2 ScWs, Rev 0102), and **SPI** (10 ScWs, Rev 0102).
2. **Prove Floating-Point Equivalence**: Verify that ARM64 NEON vector arithmetic and IEEE-754 rounding produce scientific results identical to official x86_64 builds within $<0.1\%$ relative tolerance.
3. **Capture Reproducible Benchmarks**: Measure total runtime, per-stage wall-clock performance, and memory consumption across architectures to support our MNRAS publication.

---

## 🔄 Differences Relative to Official ESA Test Scripts

The official test scripts distributed by ISDC (`isdc_osa_*_testscript.sh` and `isdc_osa_*docker_testscript.sh`) date back to 2012–2022 and rely on several legacy assumptions. Our scripts under [`validation/scripts/`](scripts/) make the following improvements:

| Feature / Behavior | Official ESA Test Scripts | Modernized Validation Suite (`validation/`) | Rationale / Benefit |
| :--- | :--- | :--- | :--- |
| **Container Engine & Image** | Hardcodes `curl ... osa-docker.sh` downloading and pulling `integralsw/osa:latest` (legacy, ROOT-heavy). | Fully parameterized image argument; defaults to `cadarn/osa:11-native-arm64` or local builds. | Enables direct testing of native ARM64 vs emulated x86_64 vs modern x86. |
| **GUI & Display Dependency** | Requires active X11 server socket (`/tmp/.X11-unix` and `$DISPLAY`) or fails startup. | Headless execution by default (`DISPLAY` optional). | Works reliably in CI/CD runners, background batch pods, and headless cloud nodes. |
| **Numerical Validation** | Only checks script exit code `0`, or depends on the legacy `isdc_dircmp` tool (absent in modern containers). | Integrated with Astropy-powered FITS comparison (`run_testdata_validation.py`), inspecting image arrays, catalogs (`DETSIG`, flux), and table HDUs. | Quantifies floating-point differences down to machine epsilon and reports max absolute/relative deltas. |
| **Execution Logging & Timings**| Simple echo commands without automated runtime tracking. | High-resolution wall-clock stopwatch per instrument and pipeline stage. | Provides exact timing data and variance reporting needed for scientific benchmarks. |
| **Data & Volume Mounts** | Relies on symbolic links within `$REP_BASE_PROD`, which break inside Docker without manual `$CURRENT_IC`. | Explicit read-only container bind-mounts (`/data/scw`, `/data/aux`, `/data/ic`, `/data/cat`). | Prevents container filesystem confusion and protects reference data from accidental mutation. |

---

## 🚀 How to Run the Validation Suite

### 1. Prerequisites
1. **Test Data Package**: Unpack `osa_testdata-11.2.tar.gz` into `../integral_test_data` (adjacent to this repository):
   ```bash
   cd ..
   # Already downloaded or curl from ISDC:
   # wget https://www.isdc.unige.ch/integral/download/osa/testdata/11.2/osa_testdata-11.2.tar.gz
   tar -xzf osa_testdata-11.2.tar.gz
   ```
2. **Instrument Characteristics & Reference Catalogs**:
   Ensure `../integral_data_archive` contains the reference catalogs (`cat/hec/gnrl_refr_cat_0043.fits`, `cat/omc/omc_refr_cat_0005.fits`) and IC indices.

---

### 2. Running via the Python Orchestrator (Recommended)

The Python test runner automatically executes the analysis and runs the FITS comparison against official reference outputs:

```bash
# Validate IBIS/ISGRI Crab analysis (4 ScWs, Rev 1667)
uv run python validation/run_testdata_validation.py ibis

# Validate JEM-X 2 (2 ScWs, Rev 0102)
uv run python validation/run_testdata_validation.py jemx

# Validate OMC optical monitor (2 ScWs, Rev 0102)
uv run python validation/run_testdata_validation.py omc

# Validate SPI gamma-ray spectrometer (10 ScWs, Rev 0102)
uv run python validation/run_testdata_validation.py spi

# Run the complete multi-instrument validation suite
uv run python validation/run_testdata_validation.py all
```

#### Custom Options:
* `--image`: Docker image to validate (e.g. `--image cadarn/osa:11-modern-amd64` to test x86_64 baseline under Rosetta).
* `--tolerance`: Relative difference threshold for scientific products (default: `1e-3` / `0.1%`).
* `--workdir`: Target directory for output observation groups (default: `validation_runs/<instrument>`).

---

### 3. Running Individual Shell Scripts Directly

Each instrument script can also be executed independently:

```bash
# Usage: ./validation/scripts/run_<instrument>_test.sh [OGID] [DOCKER_IMAGE] [TEST_DATA_DIR] [IC_DATA_DIR] [WORK_DIR]

# Example: Run IBIS test
./validation/scripts/run_ibis_test.sh osatest cadarn/osa:11-native-arm64 ../integral_test_data ../integral_data_archive validation_runs/ibis
```

---

## 📊 Interpreting Results

* **VERIFIED**: The generated FITS arrays match the reference `*docker_outref` within the specified relative numerical tolerance ($\Delta < 0.1\%$).
* **DIFF**: Differences exceed tolerance (investigate via `uv run python scripts/validate_science_products.py compare <ref_dir> <test_dir>`).
* **MISSING**: Expected product was not produced (inspect `validation_runs/<instrument>/common_log.txt` for pipeline errors).
