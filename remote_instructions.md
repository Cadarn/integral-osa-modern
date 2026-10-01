# INTEGRAL OSA 11.2: Remote Linux Benchmark & Validation Guide

This is a complete, self-contained guide to set up a clean Linux host (x86_64 or ARM64), stage the scientific data, and run both **Phase A** (multi-instrument verification against official ESA test data) and **Phase B** (Rev 0060 IBIS/ISGRI 18–60 keV scaling benchmarks up to `IMA2`).

---

## 1. System & Container Engine Setup

### 1.1 Check Architecture and Hardware
```bash
uname -m
lscpu | grep -E "Model name|CPU\(s\):|Thread|Architecture"
free -h
```
* Note whether your architecture is `x86_64` or `aarch64` (`arm64`).

### 1.2 Verify & Configure Docker
```bash
# Verify Docker service is active
docker --version
sudo systemctl status docker

# Ensure current user can run Docker without sudo
sudo usermod -aG docker $USER
newgrp docker

# Test Docker execution
docker run --rm hello-world
```

### 1.3 Pull Container Images
```bash
# For x86_64 host (Native x86 modern container):
docker pull cadarn/osa:11-modern-amd64

# For ARM64 host (Native ARM64 container):
docker pull cadarn/osa:11-native-arm64

# (Optional control image) Official ISDC legacy container:
docker pull integralsw/osa:latest
```

### 1.4 Install Astral `uv` (Fast Python Manager)
```bash
# Clean out any conflicting local virtual environment
rm -rf ~/.venv

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

---

## 2. Directory Layout & `screen` Setup

We will organize everything under `~/experiments/`:

```
~/experiments/
├── integral-osa-modern/      # Code repository
├── integral_test_data/       # Phase A ESA canonical test package
└── integral_data_archive/    # Phase B Rev 0060 telemetry, CALDB & catalogs
```

### 2.1 Start a Persistent `screen` Session
```bash
screen -L -Logfile ~/integral_benchmark_$(date +%Y%m%d_%H%M%S).log -S integral_bench
```
> **Screen Cheatsheet**:
> * **Detach**: Press `Ctrl + A`, release, then press `D`. (You can now safely disconnect SSH).
> * **Reattach**: `screen -r integral_bench`
> * **List sessions**: `screen -ls`

---

## 3. Clone Repository & Install Dependencies

```bash
mkdir -p ~/experiments
cd ~/experiments

# Clone repo
git clone https://github.com/adamboche/integral-osa-modern.git
cd ~/experiments/integral-osa-modern

# Install Python dependencies in an isolated Linux virtualenv
uv sync
```

---

## 4. Data Staging

### 4.1 Phase A Test Data (ESA Official Dataset, ~2.5 GB)

```bash
cd ~/experiments

# Direct download from university mirror
curl -L --retry 5 -o osa_testdata-11.2.tar.gz \
     "https://www.astro.unige.ch/integral/download/osa/testdata/11.2/osa_testdata-11.2.tar.gz"

# Unpack
mkdir -p ~/experiments/integral_test_data
tar -xzf osa_testdata-11.2.tar.gz -C ~/experiments/integral_test_data --strip-components=1
rm -f osa_testdata-11.2.tar.gz

# Verify unpacked directories
ls -l ~/experiments/integral_test_data
```

---

### 4.2 Phase B Science Data (Rev 0060 & CALDB)

#### Option A: Sync from AWS S3 Bucket
If AWS CLI is not installed:
```bash
uv tool install awscli
aws configure
# Enter Access Key, Secret Key, and region: us-east-1
```

Run the sync:
```bash
ARCHIVE_DIR="$HOME/experiments/integral_data_archive"
mkdir -p "$ARCHIVE_DIR/scw/0060" "$ARCHIVE_DIR/aux/adp/0060.001" "$ARCHIVE_DIR/aux/adp/ref"

# 1. Sync CALDB (IC trees, master index, catalogs)
aws s3 sync s3://integral-cloud-analysis-data-537472396676/caldb/ "$ARCHIVE_DIR/" --exclude "*.log"

# 2. Sync Rev 0060 auxiliary attitude data
aws s3 sync s3://integral-cloud-analysis-data-537472396676/rev0060/data/aux/ "$ARCHIVE_DIR/aux/"

# 3. Sync global reference tables (clock resets, ephemerides, leap seconds)
aws s3 sync s3://integral-cloud-analysis-data-537472396676/aux/adp/ref/ "$ARCHIVE_DIR/aux/adp/ref/"

# 4. Sync Rev 0060 Science Windows & revolution index
aws s3 sync s3://integral-cloud-analysis-data-537472396676/rev0060/data/scw/0060/ "$ARCHIVE_DIR/scw/0060/"
```

#### Option B: Rsync directly from another machine
From your source machine:
```bash
rsync -avzP /path/to/integral_data_archive/ user@remote-host:~/experiments/integral_data_archive/
```

---

### 4.3 Mandatory Data Post-Processing & Symlinks
OSA's legacy Data Access Layer (DAL) requires:
1. Decompressed `.fits` files.
2. Canonical `rev.001` symlinks in each Science Window folder.
3. Path discovery links for the benchmark runner.

Run this script once on the machine:

```bash
ARCHIVE_DIR="$HOME/experiments/integral_data_archive"

echo "=== 1. Decompressing FITS files ==="
find "$ARCHIVE_DIR/scw/0060" -name "*.fits.gz" -exec gunzip -f {} +
find "$ARCHIVE_DIR/aux" -name "*.fits.gz" -exec gunzip -f {} +

echo "=== 2. Creating rev.001 links ==="
for scw_dir in "$ARCHIVE_DIR"/scw/0060/0060*.001; do
    [ -d "$scw_dir" ] && ln -sf ../rev.001 "$scw_dir/rev.001"
done

echo "=== 3. Creating Aux Reference links ==="
REF_DIR="$ARCHIVE_DIR/aux/adp/ref"
[ -f "$REF_DIR/irot/inst_misalign_20050328.fits" ] && ln -sf inst_misalign_20050328.fits "$REF_DIR/irot/inst_misalign.fits"
[ -f "$REF_DIR/de200/de200_20020705.fits" ] && ln -sf de200_20020705.fits "$REF_DIR/de200/de200.fits"
[ -f "$REF_DIR/leap/leap_seconds_20160720.fits" ] && ln -sf leap_seconds_20160720.fits "$REF_DIR/leap/leap_seconds.fits"
[ -f "$REF_DIR/tcoroffset/time_correlation_offset_20210903.fits" ] && ln -sf time_correlation_offset_20210903.fits "$REF_DIR/tcoroffset/time_correlation_offset.fits"

echo "=== 4. Setting up discovery symlinks for benchmark scripts ==="
mkdir -p ~/science
ln -sf "$ARCHIVE_DIR" ~/science/integral_data_archive
ln -sf "$ARCHIVE_DIR" ~/experiments/integral_data_archive 2>/dev/null || true
ln -sf "$ARCHIVE_DIR" ~/experiments/integral-osa-modern/integral_data_archive 2>/dev/null || true

echo "✓ Data staging complete!"
```

---

### 4.4 Prune Calibration Master Index (Resolves DAL Error -2004)
When only a subset of instrument calibration trees is staged (e.g. IBIS/ISGRI only), prune `ic_master_file.fits` so DAL doesn't crash checking for unstaged instruments (SPI, JEM-X, OMC):

```bash
cd ~/experiments/integral-osa-modern

# Prune for IBIS (creates an automatic .bak backup)
uv run integral cal prune-master

# (If needed later, you can restore the original at any time with: uv run integral cal restore-master)
```

---

## 5. Verify Science Windows on Disk

Run this quick check script:

```bash
cd ~/experiments/integral-osa-modern

uv run python -c "
from pathlib import Path
from integral.core.scw import filter_pointing_scws, validate_scws_have_data
archive = Path.home() / 'science/integral_data_archive'
scws = [d.name.split('.')[0] for d in (archive / 'scw/0060').iterdir() if d.is_dir() and len(d.name.split('.')[0]) == 12]
valid, dropped = validate_scws_have_data(filter_pointing_scws(scws, '0060'), archive, instrument='IBIS')
print(f'\n>>> Found {len(valid)} valid pointing ScWs (Dropped {len(dropped)} aborted ScWs) <<<\n')
"
```
*Expected output*: `>>> Found 100 valid pointing ScWs (Dropped 4 aborted ScWs) <<<`

---

## 6. Phase A Benchmark: Multi-Instrument Speed & Numerical Verification

Phase A runs all 4 instruments (**IBIS, JEM-X, OMC, SPI**) against official ESA reference data and computes Pearson correlation and IEEE-754 numerical tolerance.

```bash
cd ~/experiments/integral-osa-modern

# Set image depending on host architecture:
# For x86_64: cadarn/osa:11-modern-amd64
# For ARM64:  cadarn/osa:11-native-arm64
TARGET_IMAGE="cadarn/osa:11-modern-amd64"

uv run python validation/run_testdata_validation.py all \
    --image "$TARGET_IMAGE" \
    --testdata-dir ~/experiments/integral_test_data \
    --ic-dir ~/experiments/integral_data_archive
```

### Optional Control Test: Upstream Official ISDC Image
Compare against the un-modified ESA upstream image (`integralsw/osa:latest`):
```bash
uv run python validation/run_testdata_validation.py ibis \
    --image "integralsw/osa:latest" \
    --workdir ~/experiments/integral-osa-modern/validation_runs/ibis_isdc_default \
    --testdata-dir ~/experiments/integral_test_data \
    --ic-dir ~/experiments/integral_data_archive
```

---

## 7. Phase B Benchmark: Rev 0060 IBIS/ISGRI Scaling Matrix (18–60 keV, IMA2)

### 7.1 Quick Sanity Check (10 ScWs, ~3–5 minutes)
Verifies observation group creation, pipeline execution, and Crab source detection ($\sim 30\text{--}40\sigma$):

```bash
cd ~/experiments/integral-osa-modern

# Set arch flag: "x86" for x86_64 hosts, or "arm64" for ARM64 hosts
ARCH="x86"

uv run python scripts/run_phase_b_benchmark.py \
    --sizes "10" \
    --repeats 1 \
    --archs "$ARCH" \
    --output ~/experiments/integral-osa-modern/benchmark_runs/phase_b/sanity_10scw.json
```

### 7.2 Full Scaling Benchmark (10, 25, and all 100 ScWs × 3 Repeats)
Once the sanity check completes with Crab detection, run the full benchmark:

```bash
cd ~/experiments/integral-osa-modern

uv run python scripts/run_phase_b_benchmark.py \
    --sizes "10,25,full" \
    --repeats 3 \
    --archs "$ARCH" \
    --output ~/experiments/integral-osa-modern/benchmark_runs/phase_b/results_native_linux.json
```

*Detach from screen with **`Ctrl + A` then `D`** and let the benchmark run to completion.*

---

## 8. Inspecting Results

When complete, reattach to screen (`screen -r integral_bench`) or inspect the structured JSON results:

```bash
cat ~/experiments/integral-osa-modern/benchmark_runs/phase_b/results_native_linux.json | jq .metadata
cat ~/experiments/integral-osa-modern/benchmark_runs/phase_b/results_native_linux.json | jq '.cells[] | {arch, scw_count, mean_elapsed, mean_per_scw, crab_sigma: .mean_source_stats.detsig}'
```

To compare against your M4 Max and AWS benchmark data:
```bash
uv run python scripts/aggregate_benchmark_results.py
```
