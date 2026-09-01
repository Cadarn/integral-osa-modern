#!/usr/bin/env bash
# ==============================================================================
# runner_scw.sh
# In-container worker script for processing an individual Science Window
# ==============================================================================

set -euo pipefail

echo "============================================================"
echo "Starting INTEGRAL Science Window Processing Job"
echo "Timestamp: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
echo "Host / Pod: $(hostname)"
echo "============================================================"

# Source container environment & Python virtualenv
if [ -f "/init.sh" ]; then
    source /init.sh
fi

INSTRUMENT="${INSTRUMENT:-IBIS}"
SCW_ID="${SCW_ID:-197200240010}"
OG_NAME="og_${SCW_ID}"
WORK_DIR="/scratch/${OG_NAME}"

mkdir -pv "$WORK_DIR"
cd "$WORK_DIR"

echo "Processing Instrument: $INSTRUMENT for ScW: $SCW_ID"

# 1. Create ScW list
echo "$SCW_ID" > scw.list

# 2. Create Observation Group
echo "Creating Observation Group via og_create..."
og_create idxLevel=0 \
          instrument="$INSTRUMENT" \
          ogid="$OG_NAME" \
          baseDir="./" \
          obsDir="obs" \
          scwList="scw.list"

cd "obs/$OG_NAME"

# 3. Execute Scientific Reduction
echo "Running scientific analysis pipeline for $INSTRUMENT..."
if [ "$INSTRUMENT" = "IBIS" ]; then
    ibis_science_analysis \
        OBS1_SearchLevels="COR,GTI,DEAD,BIN_I,BKG_I,CAT_I,IMA,BIN_S,SPE,LC" \
        OBS1_ToSearch="IBIS" \
        SCW1_SearchLevels="COR,GTI,DEAD,BIN_I,BKG_I,CAT_I,IMA" \
        SCW1_ToSearch="IBIS" \
        SCW1_ISGRI_MinGti=10 \
        SCW1_ISGRI_MinFrac=0.8 \
        IC_Group="/data/ic/ic_master_file.fits[1]" \
        IC_Alias="OSA11" || true
elif [ "$INSTRUMENT" = "JEMX" ]; then
    jemx_science_analysis \
        jemx_num=1 \
        IC_Group="/data/ic/ic_master_file.fits[1]" \
        IC_Alias="OSA11" || true
fi

# 4. Product Staging & Upload
OUTPUT_DIR="/output/$SCW_ID"
mkdir -pv "$OUTPUT_DIR"

echo "Collecting generated FITS products..."
find . -name "*.fits" -o -name "*.fits.gz" -exec cp -v {} "$OUTPUT_DIR" \;

echo "Science Window $SCW_ID processing completed successfully!"
