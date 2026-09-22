#!/usr/bin/env uv run python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.34.0",
#     "rich>=13.7.0",
#     "typer>=0.12.0",
# ]
# ///
"""
AWS Cloud Benchmark Orchestrator for INTEGRAL OSA Modernisation.

Provisions spot/on-demand compute-optimized instances (c7g.4xlarge ARM64 and c7i.4xlarge x86_64),
boots them with autonomous cloud-init scripts that stream Rev 0060 test data directly from S3
(or fallback to HEASARC), executes 10, 25, or full revolution (~100 ScWs) IBIS/ISGRI benchmarks
with optional multi-instance parallel fleet execution, collects JSON results, and self-terminates.
"""

import base64
import gzip
import json
from pathlib import Path
from typing import Any

import boto3
import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(help="AWS Cloud Benchmark Runner")
console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "benchmark_runs" / "cloud"

# 4 known unobserved / aborted pointings in Rev 0060
ABORTED_SCWS = {"006000010010", "006001020010", "006001030010", "006001040010"}

# All 104 potential pointing ScW IDs for Rev 0060 (006000010010 through 006001040010)
ALL_REV60_POINTINGS = [f"0060{i:04d}0010" for i in range(1, 105)]

# Cleaned list of 100 valid pointing ScWs with raw science telemetry
VALID_REV60_100 = [s for s in ALL_REV60_POINTINGS if s not in ABORTED_SCWS]
VALID_REV60_10 = VALID_REV60_100[:10]
VALID_REV60_25 = VALID_REV60_100[:25]
# Latest Amazon Linux 2023 AMIs in us-east-1 (Release 2023.12.20260914, Kernel 6.12)
AMI_MAP = {
    "arm64": "ami-08bb9a392e39dc6e6",  # AL2023 ARM64 (20260914)
    "x86_64": "ami-05dee78f58650ed2c",  # AL2023 x86_64 (20260914)
}

INSTANCE_TYPES = {
    "arm64": "c7g.xlarge",   # 4 vCPUs (Graviton3 Neoverse V1), 8 GB RAM
    "x86_64": "c7i.xlarge",  # 4 vCPUs (Intel Xeon Sapphire Rapids), 8 GB RAM
}

CONTAINER_IMAGES = {
    "arm64": "cadarn/osa:11-native-arm64",
    "x86_64": "cadarn/osa:11-modern-amd64",
}


def generate_user_data_script(
    arch: str,
    scale: str = "all",
    repeats: int = 1,
    s3_bucket: str | None = None,
    repeat_idx: int | None = None,
) -> str:
    """Generate the cloud-init bash script that runs autonomously on the EC2 instance."""
    image = CONTAINER_IMAGES[arch]
    workdir_path = "/opt/integral_bench"

    # Select target ScWs based on scale
    if scale == "2":
        target_scws = VALID_REV60_10[:2]
        scw_benchmark_plan = [("2", target_scws)]
    elif scale == "10":
        target_scws = VALID_REV60_10
        scw_benchmark_plan = [("10", VALID_REV60_10)]
    elif scale == "25":
        target_scws = VALID_REV60_25
        scw_benchmark_plan = [("25", VALID_REV60_25)]
    elif scale == "100" or scale == "full":
        target_scws = VALID_REV60_100
        scw_benchmark_plan = [("100", VALID_REV60_100)]
    else:  # "all" -> 10, 25, and 100 ScWs
        target_scws = VALID_REV60_100
        scw_benchmark_plan = [
            ("10", VALID_REV60_10),
            ("25", VALID_REV60_25),
            ("100", VALID_REV60_100),
        ]

    scw_list_str = " ".join(target_scws)
    bench_plan_json = json.dumps(scw_benchmark_plan)

    rep_tag = f"_rep{repeat_idx}" if repeat_idx is not None else ""
    result_filename = f"cloud_results_{arch}_{scale}{rep_tag}.json"

    script = f"""#!/bin/bash
set -euxo pipefail

# Output execution log to console and file
exec > >(tee -a /var/log/integral_cloud_benchmark.log | logger -t user-data -s 2>/dev/console) 2>&1

echo "=================================================="
echo "Starting INTEGRAL Cloud Benchmark: Architecture={arch}"
echo "Scale={scale} Repeats={repeats} RepTag={rep_tag}"
echo "Time: $(date -u)"
echo "Host: $(uname -a)"
echo "CPU Model: $(lscpu | grep 'Model name' || true)"
echo "=================================================="

# 1. Update and install Docker, Git, AWS CLI and modern Astral uv
# Use --allowerasing to prevent AL2023 curl vs curl-minimal conflicts
dnf update -y
dnf install -y --allowerasing docker git curl-minimal tar gzip awscli
systemctl enable --now docker

# Install uv (provides modern Python 3.12+ and eliminates pip deprecation)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

# Ensure instance always uploads log and self-terminates on any failure to prevent idle costs
trap 'echo "Error encountered at line $LINENO! Uploading log to S3 before shutdown..."; aws s3 cp /var/log/integral_cloud_benchmark.log "s3://{s3_bucket}/results/cloud_benchmark_{arch}_{scale}{rep_tag}_error.log" || true; shutdown -h now' ERR

# 2. Setup workspace directory
WORKDIR="{workdir_path}"
mkdir -p "$WORKDIR/data/scw/0060" "$WORKDIR/data/aux/adp/0060.001" "$WORKDIR/runs" "$WORKDIR/timings"
cd "$WORKDIR"

# 3. Pull container image and record duration
echo "Pulling Docker image: {image}..."
PULL_START=$(date +%s.%N)
docker pull {image}
PULL_END=$(date +%s.%N)
DOCKER_PULL_SECONDS=$(awk -v s="$PULL_START" -v e="$PULL_END" 'BEGIN {{printf "%.2f", e - s}}')
echo "Docker pull completed in ${{DOCKER_PULL_SECONDS}}s"
echo "$DOCKER_PULL_SECONDS" > "$WORKDIR/timings/docker_pull_seconds.txt"
"""

    if s3_bucket:
        script += f"""
# 4. Stream calibration and Rev 0060 science data from S3 with precise phase timings
s3_sync_retry() {{
    local max_attempts=5
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        echo "Syncing $1 -> $2 (attempt $attempt)..."
        if aws s3 sync "$@"; then
            return 0
        fi
        echo "aws s3 sync failed on attempt $attempt. Retrying in 5s..."
        sleep 5
        attempt=$((attempt + 1))
    done
    echo "ERROR: aws s3 sync failed after $max_attempts attempts." >&2
    return 1
}}

echo "Syncing CALDB calibration data from s3://{s3_bucket}/caldb/..."
CALDB_START=$(date +%s.%N)
s3_sync_retry "s3://{s3_bucket}/caldb/" "$WORKDIR/data/" --exclude "*.log"
CALDB_END=$(date +%s.%N)
CALDB_SYNC_SECONDS=$(awk -v s="$CALDB_START" -v e="$CALDB_END" 'BEGIN {{printf "%.2f", e - s}}')
echo "CALDB sync completed in ${{CALDB_SYNC_SECONDS}}s"
echo "$CALDB_SYNC_SECONDS" > "$WORKDIR/timings/caldb_sync_seconds.txt"

echo "Syncing Rev 0060 auxiliary attitude data from s3://{s3_bucket}/rev0060/data/aux/..."
AUX_START=$(date +%s.%N)
s3_sync_retry "s3://{s3_bucket}/rev0060/data/aux/" "$WORKDIR/data/aux/"

# Ensure global reference auxiliary tables (aux/adp/ref/crst/, leap/, de200/, etc.) exist
# If not present in S3, download directly from HEASARC into S3 and local workspace
echo "Checking global auxiliary reference data (aux/adp/ref/)..."
crst_count=$(aws s3 ls "s3://{s3_bucket}/aux/adp/ref/crst/" 2>/dev/null | wc -l || true)
if [ "$crst_count" -eq 0 ]; then
    echo "aux/adp/ref/ missing in S3. Streaming directly from NASA HEASARC to S3 & local workspace..."
    mkdir -p "$WORKDIR/data/aux/adp/ref"
    cat << 'PY_AUX_EOF' > "$WORKDIR/fetch_aux_ref.py"
import urllib.request, re, os, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
headers = {{"User-Agent": "Mozilla/5.0 (AWS Cloud Staging)"}}
base_url = "https://heasarc.gsfc.nasa.gov/FTP/integral/data/aux/adp/ref/"
target_base = "/opt/integral_bench/data/aux/adp/ref"
subdirs = ["crst/", "leap/", "de200/", "irot/", "tcoroffset/"]

for sub in subdirs:
    dest_dir = os.path.join(target_base, sub)
    os.makedirs(dest_dir, exist_ok=True)
    req = urllib.request.Request(base_url + sub, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            html = r.read().decode("utf-8", errors="ignore")
            for m in re.finditer(r'href="([^"?/]+)"', html):
                fname = m.group(1)
                if fname.endswith(".fits") or fname.endswith(".fits.gz"):
                    file_url = base_url + sub + fname
                    dest_file = os.path.join(dest_dir, fname)
                    print(f"Downloading {{file_url}} -> {{dest_file}}")
                    urllib.request.urlretrieve(file_url, dest_file)
    except Exception as e:
        print(f"Error fetching {{sub}}: {{e}}")
PY_AUX_EOF
    uv run python "$WORKDIR/fetch_aux_ref.py"
    echo "Pushing freshly fetched aux/adp/ref to s3://{s3_bucket}/aux/adp/ref/..."
    s3_sync_retry "$WORKDIR/data/aux/adp/ref/" "s3://{s3_bucket}/aux/adp/ref/"
else
    echo "Syncing aux/adp/ref/ from s3://{s3_bucket}/aux/adp/ref/..."
    s3_sync_retry "s3://{s3_bucket}/aux/adp/ref/" "$WORKDIR/data/aux/adp/ref/"
fi

# Ensure all critical aux reference symlinks and files are present
mkdir -p "$WORKDIR/data/aux/adp/ref/irot" "$WORKDIR/data/aux/adp/ref/de200" "$WORKDIR/data/aux/adp/ref/leap" "$WORKDIR/data/aux/adp/ref/tcoroffset"
[ -f "$WORKDIR/data/aux/adp/ref/irot/inst_misalign_20050328.fits" ] && ln -sf inst_misalign_20050328.fits "$WORKDIR/data/aux/adp/ref/irot/inst_misalign.fits" && ln -sf inst_misalign_20050328.fits "$WORKDIR/data/aux/adp/ref/irot/inst_misalign"
[ -f "$WORKDIR/data/aux/adp/ref/de200/de200_20020705.fits" ] && ln -sf de200_20020705.fits "$WORKDIR/data/aux/adp/ref/de200/de200.fits" && ln -sf de200_20020705.fits "$WORKDIR/data/aux/adp/ref/de200/de200"
[ -f "$WORKDIR/data/aux/adp/ref/leap/leap_seconds_20160720.fits" ] && ln -sf leap_seconds_20160720.fits "$WORKDIR/data/aux/adp/ref/leap/leap_seconds.fits" && ln -sf leap_seconds_20160720.fits "$WORKDIR/data/aux/adp/ref/leap/leap_seconds"
[ -f "$WORKDIR/data/aux/adp/ref/tcoroffset/time_correlation_offset_20210903.fits" ] && ln -sf time_correlation_offset_20210903.fits "$WORKDIR/data/aux/adp/ref/tcoroffset/time_correlation_offset.fits" && ln -sf time_correlation_offset_20210903.fits "$WORKDIR/data/aux/adp/ref/tcoroffset/time_correlation_offset"

AUX_END=$(date +%s.%N)
AUX_SYNC_SECONDS=$(awk -v s="$AUX_START" -v e="$AUX_END" 'BEGIN {{printf "%.2f", e - s}}')
echo "Auxiliary data sync completed in ${{AUX_SYNC_SECONDS}}s"
echo "$AUX_SYNC_SECONDS" > "$WORKDIR/timings/aux_sync_seconds.txt"

echo "Syncing Rev 0060 revolution index data from s3://{s3_bucket}/rev0060/data/scw/0060/rev.001/..."
REV_START=$(date +%s.%N)
s3_sync_retry "s3://{s3_bucket}/rev0060/data/scw/0060/rev.001/" "$WORKDIR/data/scw/0060/rev.001/"
REV_END=$(date +%s.%N)
REV_SYNC_SECONDS=$(awk -v s="$REV_START" -v e="$REV_END" 'BEGIN {{printf "%.2f", e - s}}')
echo "Revolution index sync completed in ${{REV_SYNC_SECONDS}}s"

echo "Syncing {len(target_scws)} Science Windows from s3://{s3_bucket}/rev0060/data/scw/..."
SCW_START=$(date +%s.%N)
for scw in {scw_list_str}; do
    scw_dir="$WORKDIR/data/scw/0060/${{scw}}.001"
    s3_sync_retry "s3://{s3_bucket}/rev0060/data/scw/0060/${{scw}}.001/" "$scw_dir/"
    ln -sf ../rev.001 "$scw_dir/rev.001"
    # Decompress all .fits.gz files in the ScW directory so OSA DAL can find bare .fits names
    gz_count=$(find "$scw_dir" -maxdepth 1 -name "*.fits.gz" | wc -l)
    echo "  Decompressing $gz_count .fits.gz files in $scw_dir ..."
    find "$scw_dir" -maxdepth 1 -name "*.fits.gz" -exec gunzip -f {{}} \\;
    fits_count=$(find "$scw_dir" -maxdepth 1 -name "*.fits" ! -name "swg.fits" | wc -l)
    echo "  ScW $scw: $fits_count uncompressed .fits files ready"
done
# Decompress aux and rev files as well
echo "Decompressing aux files..."
find "$WORKDIR/data/aux" -name "*.fits.gz" -exec gunzip -f {{}} \\;
echo "Decompressing rev.001 index files..."
find "$WORKDIR/data/scw/0060/rev.001" -name "*.fits.gz" -exec gunzip -f {{}} \\;
SCW_END=$(date +%s.%N)
SCW_SYNC_SECONDS=$(awk -v s="$SCW_START" -v e="$SCW_END" 'BEGIN {{printf "%.2f", e - s}}')
echo "ScW science data sync and decompression completed in ${{SCW_SYNC_SECONDS}}s"
echo "$SCW_SYNC_SECONDS" > "$WORKDIR/timings/scw_sync_seconds.txt"

DATA_TRANSFER_SECONDS=$(awk -v c="$CALDB_SYNC_SECONDS" -v a="$AUX_SYNC_SECONDS" -v r="$REV_SYNC_SECONDS" -v s="$SCW_SYNC_SECONDS" 'BEGIN {{printf "%.2f", c + a + r + s}}')
echo "$DATA_TRANSFER_SECONDS" > "$WORKDIR/timings/data_transfer_seconds.txt"
"""
    else:
        script += f"""
# 4. Stream data directly from HEASARC
echo "Fetching auxiliary attitude data from HEASARC..."
AUX_START=$(date +%s.%N)
AUX_URL="https://heasarc.gsfc.nasa.gov/FTP/integral/data/aux/adp/0060.001/"
curl -sSL "$AUX_URL" | grep -o 'href="[^"?/][^"]*"' | cut -d'"' -f2 | while read -r fname; do
    if [[ "$fname" =~ \\.fits ]]; then
        curl -sSL -o "$WORKDIR/data/aux/adp/0060.001/$fname" "$AUX_URL$fname"
    fi
done
AUX_END=$(date +%s.%N)
AUX_SYNC_SECONDS=$(awk -v s="$AUX_START" -v e="$AUX_END" 'BEGIN {{printf "%.2f", e - s}}')
echo "$AUX_SYNC_SECONDS" > "$WORKDIR/timings/aux_sync_seconds.txt"

echo "Fetching {len(target_scws)} Science Windows from HEASARC..."
SCW_START=$(date +%s.%N)
for scw in {scw_list_str}; do
    scw_dir="$WORKDIR/data/scw/0060/${{scw}}.001"
    mkdir -p "$scw_dir"
    SCW_URL="https://heasarc.gsfc.nasa.gov/FTP/integral/data/scw/0060/${{scw}}.001/"
    curl -sSL "$SCW_URL" | grep -o 'href="[^"?/][^"]*"' | cut -d'"' -f2 | while read -r fname; do
        if [[ "$fname" =~ \\.(fits|gz) ]]; then
            curl -sSL -o "$scw_dir/$fname" "$SCW_URL$fname"
        fi
    done
done
SCW_END=$(date +%s.%N)
SCW_SYNC_SECONDS=$(awk -v s="$SCW_START" -v e="$SCW_END" 'BEGIN {{printf "%.2f", e - s}}')
echo "$SCW_SYNC_SECONDS" > "$WORKDIR/timings/scw_sync_seconds.txt"
echo "0.0" > "$WORKDIR/timings/caldb_sync_seconds.txt"
DATA_TRANSFER_SECONDS=$(awk -v a="$AUX_SYNC_SECONDS" -v s="$SCW_SYNC_SECONDS" 'BEGIN {{printf "%.2f", a + s}}')
echo "$DATA_TRANSFER_SECONDS" > "$WORKDIR/timings/data_transfer_seconds.txt"
"""

    script += f"""
echo "Data staging complete: $(du -sh $WORKDIR/data)"

# 4b. Verify critical calibration files exist before running benchmark
echo "Verifying critical calibration files..."
for check_file in \\
    "$WORKDIR/data/idx/ic/ic_master_file.fits" \\
    "$WORKDIR/data/idx/ic/ISGR-BACK-BKG-IDX.fits" \\
    "$WORKDIR/data/ic/ibis/bkg/isgr_back_bkg_0002.fits" \\
    "$WORKDIR/data/aux/adp/ref/crst/clock_reset_20190815.fits" \\
    "$WORKDIR/data/cat/hec/gnrl_refr_cat_0043.fits"; do
    if [ ! -f "$check_file" ]; then
        echo "FATAL: Missing critical file: $check_file"
        echo "Data sync from S3 or archive may have failed. Aborting."
        exit 1
    fi
    echo "  OK $check_file ($(stat -c%s "$check_file" 2>/dev/null || stat -f%z "$check_file") bytes)"
done
echo "All critical calibration and reference files verified."

# 4c. Prune ic_master_file.fits to keep only instrument-supported subsystems (IBIS, ISGR, PICS, COMP, GNRL, INTL, IREM)
# This resolves DAL error -2004 (DAL_FILE_NOT_ACCESSIBLE) caused by DAL validating index members for subsystems not present in CALDB (SPI, JMX, OMC)
echo "Pruning ic_master_file.fits for IBIS analysis..."
uv run --with astropy --with numpy python -c '
from astropy.io import fits
import numpy as np
import os

master_file = "{workdir_path}/data/idx/ic/ic_master_file.fits"
keep_prefixes = ["IBIS", "ISGR", "PICS", "COMP", "GNRL", "INTL", "IREM"]

with fits.open(master_file, mode="update") as hdul:
    if len(hdul) > 2 and hdul[2].data is not None:
        m_data = hdul[2].data
        if "MEMBER_LOCATION" in m_data.names:
            orig_len = len(m_data)
            mask = [
                any(str(loc).startswith(p) for p in keep_prefixes)
                for loc in m_data["MEMBER_LOCATION"]
            ]
            hdul[2].data = m_data[np.array(mask)]
            hdul.flush()
            print(f"Master index pruned: kept {{sum(mask)}} of {{orig_len}} entries.")
'

echo "=== Data directory structure ==="
find "$WORKDIR/data" -maxdepth 3 -type d || true
echo "=== IC index files ==="
ls -l "$WORKDIR/data/idx/ic/" | head -n 20 || true

# 5. Create in-container benchmark execution runner
cat << 'RUNNER_EOF' > "$WORKDIR/run_benchmark.py"
import os, sys, time, json, subprocess
from datetime import datetime, timezone
from pathlib import Path
from astropy.io import fits
import numpy as np

BENCHMARK_PLAN = {bench_plan_json}
ARCH = "{arch}"
IMAGE = "{image}"
REPEATS = {repeats}
WORKDIR = "{workdir_path}"
REP_INDEX = "{repeat_idx or 1}"

def extract_source_stats(mosaic_res_file):
    if not os.path.exists(mosaic_res_file):
        return {{"detected": False, "error": "Mosaic results file not found"}}
    try:
        with fits.open(mosaic_res_file) as hdul:
            for hdu in hdul:
                if hdu.data is not None and hasattr(hdu.data, "names") and "NAME" in hdu.data.names:
                    data = hdu.data
                    for row in data:
                        name = str(row["NAME"]).strip()
                        if "CRAB" in name.upper() or "1ES 0534+220" in name:
                            return {{
                                "detected": True,
                                "name": name,
                                "detsig": float(row["DETSIG"] if "DETSIG" in data.names else row.get("DET_SIG", 0.0)),
                                "flux": float(np.ravel(row["FLUX"])[0]) if "FLUX" in data.names else 0.0,
                                "flux_err": float(np.ravel(row["FLUX_ERR"])[0]) if "FLUX_ERR" in data.names else 0.0,
                                "ra": float(row["RA_OBJ"]) if "RA_OBJ" in data.names else 0.0,
                                "dec": float(row["DEC_OBJ"]) if "DEC_OBJ" in data.names else 0.0,
                            }}
                    if len(data) > 0:
                        sig_col = "DETSIG" if "DETSIG" in data.names else ("DET_SIG" if "DET_SIG" in data.names else None)
                        if sig_col:
                            top_idx = int(np.argmax([float(r[sig_col]) for r in data]))
                            row = data[top_idx]
                            return {{
                                "detected": True,
                                "name": str(row["NAME"]).strip(),
                                "detsig": float(row[sig_col]),
                                "flux": float(np.ravel(row["FLUX"])[0]) if "FLUX" in data.names else 0.0,
                                "flux_err": float(np.ravel(row["FLUX_ERR"])[0]) if "FLUX_ERR" in data.names else 0.0,
                                "ra": float(row["RA_OBJ"]) if "RA_OBJ" in data.names else 0.0,
                                "dec": float(row["DEC_OBJ"]) if "DEC_OBJ" in data.names else 0.0,
                            }}
    except Exception as e:
        return {{"detected": False, "error": str(e)}}
    return {{"detected": False, "error": "No sources found in table"}}

def run_single(scw_list, run_name):
    run_dir = os.path.join(WORKDIR, "runs", run_name)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "scw.list"), "w") as f:
        for s in scw_list:
            f.write(f"{{s}}.001\\n")

    pipeline_script = os.path.join(run_dir, "pipeline.sh")
    with open(pipeline_script, "w") as ps:
        ps.write('''#!/bin/bash
set -e
ulimit -s unlimited || true
[ -f /init.sh ] && source /init.sh 2>/dev/null || true
[ -f /opt/osa/bin/isdc_init_env.sh ] && source /opt/osa/bin/isdc_init_env.sh 2>/dev/null || true
export ISDC_ENV=/opt/osa
export REP_BASE_PROD=/data
export CFITSIO_INCLUDE_FILES=/opt/osa/templates
export ISDC_REF_CAT=/data/cat/hec/gnrl_refr_cat_0043.fits
export HOME=/home/integral
export PFILES="/home/integral/pfiles;/opt/osa/pfiles"
mkdir -pv /home/integral/pfiles
export COMMONSCRIPT=1
export COMMONLOGFILE=+/home/integral/commonlog.txt
export DISPLAY=""

# In modern amd64 CentOS 7 container, prioritize ROOT and compiler runtime libraries
if [ -d /opt/osa/root ]; then
    export ROOTSYS=/opt/osa/root
    export LD_LIBRARY_PATH="/opt/osa/root/lib:/opt/osa/lib:/usr/lib64:/lib64:$LD_LIBRARY_PATH"
fi

cd /home/integral
echo "=== ScW directory contents inside Docker ==="
ls -la /data/scw/0060/ || true
for scw_d in /data/scw/0060/*/; do
    echo "--- $scw_d ---"
    ls "$scw_d" | grep -E "^ibis_mce|^sc_time|^isgri_events|^time_corr" | head -10 || true
    echo "  .fits count: $(ls "$scw_d"*.fits 2>/dev/null | wc -l)"
    echo "  .fits.gz remaining: $(ls "$scw_d"*.fits.gz 2>/dev/null | wc -l)"
done
echo "=== Aux ibis_mce / time_correlation ==="
ls /data/aux/adp/0060.001/time_correlation.fits 2>/dev/null && echo "time_correlation.fits OK" || echo "MISSING: time_correlation.fits"
echo "=== Aux clock_reset ==="
ls -la /data/aux/adp/ref/crst/ 2>/dev/null || echo "MISSING: /data/aux/adp/ref/crst/"
echo "=== Aux irot / inst_misalign ==="
ls -la /data/aux/adp/ref/irot/ 2>/dev/null || echo "MISSING: /data/aux/adp/ref/irot/"
echo "=== Aux de200 ==="
ls -la /data/aux/adp/ref/de200/ 2>/dev/null || echo "MISSING: /data/aux/adp/ref/de200/"
echo "=== End diagnostics ==="
og_create idxSwg="scw.list" instrument="IBIS" ogid="og_bench" baseDir="./" obsDir="obs"
cd obs/og_bench
ibis_science_analysis \\
    startLevel="COR" \\
    endLevel="IMA2" \\
    IBIS_II_ChanNum=1 \\
    IBIS_II_E_band_min="18" \\
    IBIS_II_E_band_max="60" \\
    SWITCH_disableIsgri="no" \\
    SWITCH_disablePICsIT="yes" \\
    SWITCH_disableCompton="yes" \\
    OBS1_CleanMode=1 \\
    brPifThreshold=0.0 \\
    CAT_refCat="/data/cat/hec/gnrl_refr_cat_0043.fits[ISGRI_FLAG>0]" \\
    brSrcDOL="/data/cat/hec/gnrl_refr_cat_0043.fits[ISGRI_FLAG2==5&&ISGR_FLUX_1>100]" \\
    IC_Group="/data/idx/ic/ic_master_file.fits[1]" \\
    IC_Alias="OSA"
''')
    os.chmod(pipeline_script, 0o755)

    cmd = [
        "docker", "run", "--rm",
        "--ulimit", "stack=-1:-1",
        "-v", f"{{run_dir}}:/home/integral",
        "-v", f"{{WORKDIR}}/data/scw:/data/scw",
        "-v", f"{{WORKDIR}}/data/aux:/data/aux",
        "-v", f"{{WORKDIR}}/data/ic:/data/ic",
        "-v", f"{{WORKDIR}}/data/idx:/data/idx",
        "-v", f"{{WORKDIR}}/data/cat:/data/cat",
        "-e", "HOME=/home/integral",
        IMAGE,
        "bash", "/home/integral/pipeline.sh"
    ]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t_elapsed = time.perf_counter() - t0
    commonlog_path = os.path.join(run_dir, "commonlog.txt")
    full_log = ""
    if os.path.exists(commonlog_path):
        with open(commonlog_path, "r", errors="ignore") as cf:
            full_log = cf.read()
    tail_output = full_log[-3000:] if full_log else (p.stdout[-3000:] if p.stdout else "")

    # Always save and upload container stdout & commonlog to S3 for debugging
    stdout_path = os.path.join(run_dir, "pipeline_stdout.txt")
    with open(stdout_path, "w") as sf:
        sf.write(p.stdout or "")

    s3_bucket_env = os.environ.get("S3_RESULT_BUCKET", "")
    if s3_bucket_env:
        if os.path.exists(commonlog_path):
            subprocess.run(
                ["aws", "s3", "cp", commonlog_path, f"s3://{{s3_bucket_env}}/results/commonlog_{{ARCH}}_{{run_name}}.txt"],
                capture_output=True
            )
        if os.path.exists(stdout_path):
            subprocess.run(
                ["aws", "s3", "cp", stdout_path, f"s3://{{s3_bucket_env}}/results/stdout_{{ARCH}}_{{run_name}}.txt"],
                capture_output=True
            )
        print(f"Uploaded diagnostics for {{run_name}} to s3://{{s3_bucket_env}}/results/")

    mosaic_file = os.path.join(run_dir, "obs", "og_bench", "isgri_mosa_res.fits")
    source_stats = extract_source_stats(mosaic_file)

    return {{
        "run_name": run_name,
        "scw_count": len(scw_list),
        "returncode": p.returncode,
        "elapsed_seconds": t_elapsed,
        "per_scw_seconds": t_elapsed / len(scw_list) if len(scw_list) > 0 else 0,
        "scientific_results": source_stats,
        "error_tail": tail_output if p.returncode != 0 else ""
    }}

def read_float_metric(path):
    try:
        with open(path, "r") as f:
            return float(f.read().strip())
    except Exception:
        return 0.0

docker_pull_sec = read_float_metric(f"{{WORKDIR}}/timings/docker_pull_seconds.txt")
caldb_sync_sec = read_float_metric(f"{{WORKDIR}}/timings/caldb_sync_seconds.txt")
aux_sync_sec = read_float_metric(f"{{WORKDIR}}/timings/aux_sync_seconds.txt")
scw_sync_sec = read_float_metric(f"{{WORKDIR}}/timings/scw_sync_seconds.txt")
data_transfer_sec = read_float_metric(f"{{WORKDIR}}/timings/data_transfer_seconds.txt")

results = {{
    "arch": ARCH,
    "image": IMAGE,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "repeat_index": REP_INDEX,
    "timing_breakdown": {{
        "docker_pull_seconds": docker_pull_sec,
        "caldb_sync_seconds": caldb_sync_sec,
        "aux_sync_seconds": aux_sync_sec,
        "scw_sync_seconds": scw_sync_sec,
        "total_data_transfer_seconds": data_transfer_sec,
    }},
    "runs": []
}}

for plan_label, scws in BENCHMARK_PLAN:
    print(f"=== Running {{plan_label}}-ScW Benchmark ({{len(scws)}} ScWs) ===")
    for r in range(1, REPEATS + 1):
        r_name = f"scw{{plan_label}}_rep{{r}}" if REPEATS > 1 else f"scw{{plan_label}}_node{{REP_INDEX}}"
        res = run_single(scws, r_name)
        print(f"{{plan_label}} ScW Rep {{r}}: {{res['elapsed_seconds']:.2f}}s ({{res['per_scw_seconds']:.2f}}s/ScW) [code={{res['returncode']}}]")
        results["runs"].append(res)

out_file = os.path.join(WORKDIR, "{result_filename}")
with open(out_file, "w") as out_f:
    json.dump(results, out_f, indent=2)

print("Benchmark complete! Saved results to " + out_file)
RUNNER_EOF

# 6. Pre-flight: verify ScW .fits files are actually decompressed before running Docker
echo "=== Pre-flight ScW file check ==="
for scw in {scw_list_str}; do
    scw_dir="$WORKDIR/data/scw/0060/${{scw}}.001"
    fits_count=$(find "$scw_dir" -maxdepth 1 -name "*.fits" ! -name "swg.fits" | wc -l)
    gz_remaining=$(find "$scw_dir" -maxdepth 1 -name "*.fits.gz" | wc -l)
    echo "  ScW $scw: $fits_count .fits files, $gz_remaining .fits.gz still compressed"
done
aux_fits=$(find "$WORKDIR/data/aux" -name "*.fits" | wc -l)
aux_gz=$(find "$WORKDIR/data/aux" -name "*.fits.gz" | wc -l)
echo "  Aux: $aux_fits .fits files, $aux_gz .fits.gz still compressed"
echo "=== End pre-flight ==="

# 7. Execute benchmark with modern Python via uv (with astropy & numpy)
S3_RESULT_BUCKET="{s3_bucket or ""}" uv run --with astropy --with numpy python "$WORKDIR/run_benchmark.py"

# 7. Print results to console
echo "================ FINAL BENCHMARK RESULTS ================"
cat "$WORKDIR/{result_filename}"
echo "========================================================="
"""

    if s3_bucket:
        script += f"""
# 8. Upload results directly to S3
aws s3 cp "$WORKDIR/{result_filename}" "s3://{s3_bucket}/results/{result_filename}" || true

# 9. Upload user-data log and auto self-terminate instance to eliminate lingering cost
aws s3 cp /var/log/integral_cloud_benchmark.log "s3://{s3_bucket}/results/cloud_benchmark_{arch}_{scale}{rep_tag}.log" || true
echo "Self-terminating instance at $(date -u)..."
shutdown -h now
"""
    else:
        script += """
# 9. Auto self-terminate instance to eliminate lingering cost
echo "Self-terminating instance at $(date -u)..."
shutdown -h now
"""
    return script


@app.command()
def run_cloud(
    arch: str = typer.Option("arm64", "--arch", "-a", help="Target architecture ('arm64' or 'x86_64')"),
    scale: str = typer.Option("100", "--scale", "-s", help="Benchmark scale: '10', '25', '100' (full rev), or 'all'"),
    repeats: int = typer.Option(1, "--repeats", "-r", help="Sequential repeats per node (default: 1)"),
    parallel: int = typer.Option(3, "--parallel", "-p", help="Number of parallel spot instances to launch for fleet repeats (e.g. 3)"),
    s3_bucket: str | None = typer.Option("integral-cloud-analysis-data-537472396676", "--s3-bucket", "-b", help="S3 bucket with staged Rev 0060 data"),
    region: str = typer.Option("us-east-1", "--region", help="AWS region"),
    spot: bool = typer.Option(True, "--spot/--on-demand", help="Use Spot instances for ~70% cost savings"),
    volume_size: int = typer.Option(40, "--volume-size", help="Root EBS volume size in GB (recommended: 40 GB)"),
    subnet_id: str | None = typer.Option("subnet-a9e00af0", "--subnet-id", help="Subnet ID (default: us-east-1c subnet-a9e00af0)"),
    iam_profile: str | None = typer.Option("IntegralCloudBenchmarkProfile", "--iam-profile", help="IAM Instance Profile Name"),
    node_indices: str | None = typer.Option(None, "--node-indices", "-n", help="Specific comma-separated node indices to launch (e.g. '1,5')"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Render user-data script without launching"),
):
    """Launch AWS EC2 benchmark worker(s) for the specified architecture and scale."""
    arch = arch.lower()
    if arch not in ("arm64", "x86_64", "amd64"):
        console.print(f"[bold red]Unsupported architecture: {arch}. Must be 'arm64' or 'x86_64'.[/bold red]")
        raise typer.Exit(1)
    if arch == "amd64":
        arch = "x86_64"

    ami_id = AMI_MAP[arch]
    instance_type = INSTANCE_TYPES[arch]
    image = CONTAINER_IMAGES[arch]

    if subnet_id == "subnet-a9e00af0" and arch == "x86_64":
        subnet_id = "subnet-67212b4f"  # us-east-1a has abundant c7i.xlarge spot capacity

    if node_indices:
        nodes_to_launch = [int(x.strip()) for x in node_indices.split(",") if x.strip()]
        num_nodes = len(nodes_to_launch)
    else:
        num_nodes = max(parallel, 1)
        nodes_to_launch = list(range(1, num_nodes + 1))

    console.print(
        Panel(
            f"[bold green]AWS EC2 INTEGRAL Benchmark Configuration[/bold green]\n\n"
            f"• Architecture:     [cyan]{arch}[/cyan]\n"
            f"• Scale:            [bold yellow]{scale} ScWs[/bold yellow]\n"
            f"• EC2 Instance:     [bold yellow]{instance_type}[/bold yellow] (16 vCPU, 32 GB RAM)\n"
            f"• Node Fleet Count: [bold magenta]{len(nodes_to_launch)} node(s) ({nodes_to_launch})[/bold magenta]\n"
            f"• Repeats per node: [cyan]{repeats}[/cyan]\n"
            f"• Total Repeats:    [bold cyan]{len(nodes_to_launch) * repeats} total run(s)[/bold cyan]\n"
            f"• AMI ID:           [cyan]{ami_id}[/cyan] (AL2023 {arch})\n"
            f"• Docker Image:     [cyan]{image}[/cyan]\n"
            f"• S3 Data Source:   [cyan]{s3_bucket or 'Direct HEASARC Stream'}[/cyan]\n"
            f"• EBS Root Volume:  [cyan]{volume_size} GB (gp3)[/cyan]\n"
            f"• Region:           [cyan]{region}[/cyan]\n"
            f"• Spot Instances:   [cyan]{spot}[/cyan]",
            title="Cloud Fleet Setup",
        )
    )

    if dry_run:
        sample_user_data = generate_user_data_script(
            arch=arch,
            scale=scale,
            repeats=repeats,
            s3_bucket=s3_bucket,
            repeat_idx=nodes_to_launch[0] if nodes_to_launch else 1,
        )
        console.print("[yellow]Dry Run: User Data bootstrap script (Node 1):[/yellow]")
        console.print(sample_user_data[:1200] + "\n...[truncated]...\n")
        return

    ec2 = boto3.client("ec2", region_name=region)
    launched_instances = []

    for node_idx in nodes_to_launch:
        user_data = generate_user_data_script(
            arch=arch,
            scale=scale,
            repeats=repeats,
            s3_bucket=s3_bucket,
            repeat_idx=node_idx,
        )

        launch_params: dict[str, Any] = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": base64.b64encode(gzip.compress(user_data.encode("utf-8"))).decode("ascii"),
            "InstanceInitiatedShutdownBehavior": "terminate",  # Terminate on shutdown -h now
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": volume_size,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"integral-benchmark-{arch}-node{node_idx}"},
                        {"Key": "Project", "Value": "integral-osa-modern"},
                        {"Key": "Arch", "Value": arch},
                        {"Key": "Scale", "Value": scale},
                        {"Key": "Node", "Value": str(node_idx)},
                        {"Key": "CostCenter", "Value": "integral-cloud-benchmark"},
                        {"Key": "Experiment", "Value": f"osa-11.2-{arch}-{scale}scw"},
                    ],
                },
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": "Name", "Value": f"integral-ebs-{arch}-node{node_idx}"},
                        {"Key": "Project", "Value": "integral-osa-modern"},
                        {"Key": "Arch", "Value": arch},
                        {"Key": "Scale", "Value": scale},
                        {"Key": "CostCenter", "Value": "integral-cloud-benchmark"},
                        {"Key": "Experiment", "Value": f"osa-11.2-{arch}-{scale}scw"},
                    ],
                },
            ],
        }

        if subnet_id:
            launch_params["SubnetId"] = subnet_id

        if iam_profile:
            launch_params["IamInstanceProfile"] = {"Name": iam_profile}

        if spot:
            launch_params["InstanceMarketOptions"] = {
                "MarketType": "spot",
                "SpotOptions": {
                    "SpotInstanceType": "one-time",
                    "InstanceInterruptionBehavior": "terminate",
                },
            }

        try:
            resp = ec2.run_instances(**launch_params)
            instance = resp["Instances"][0]
            instance_id = instance["InstanceId"]
            launched_instances.append(instance_id)
            mode_desc = "Spot" if spot else "On-Demand"
            console.print(
                f"[bold green]✓ Launched Node {node_idx}/{num_nodes}: {instance_id} ({instance_type} {mode_desc})[/bold green]"
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if spot and ("Spot" in error_code or "InsufficientInstanceCapacity" in error_code):
                console.print(f"[yellow]Spot unavailable ({error_code}). Falling back to On-Demand for Node {node_idx}...[/yellow]")
                launch_params.pop("InstanceMarketOptions", None)
                try:
                    resp = ec2.run_instances(**launch_params)
                    instance = resp["Instances"][0]
                    instance_id = instance["InstanceId"]
                    launched_instances.append(instance_id)
                    console.print(
                        f"[bold green]✓ Launched Node {node_idx}/{num_nodes}: {instance_id} ({instance_type} On-Demand)[/bold green]"
                    )
                except Exception as fallback_e:
                    console.print(f"[bold red]Failed to launch Node {node_idx} on-demand: {fallback_e}[/bold red]")
                    break
            else:
                console.print(f"[bold red]Failed to launch Node {node_idx}: {e}[/bold red]")
                break

    console.print(
        Panel(
            f"[bold cyan]Launched Fleet of {len(launched_instances)} Instance(s)[/bold cyan]\n"
            f"Instance IDs: {', '.join(launched_instances)}\n\n"
            f"[dim]All nodes will execute the {scale}-ScW benchmark independently, post JSON results to S3 / console log, and self-terminate.[/dim]\n"
            f"[yellow]To monitor any node live:[/yellow] aws ec2 get-console-output --instance-id <instance_id>",
            title="Fleet Status",
        )
    )


if __name__ == "__main__":
    app()
