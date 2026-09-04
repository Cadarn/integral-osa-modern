#!/usr/bin/env bash
# ==============================================================================
# validation/scripts/run_ibis_test.sh
#
# Adapted from ESA/ISDC official test scripts
# ==============================================================================

set -euo pipefail

OGID="${1:-osatest}"
DOCKER_IMAGE="${2:-cadarn/osa:11-native-arm64}"
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

TEST_DATA_IN="${3:-$PROJECT_ROOT/../integral_test_data}"
TEST_DATA_DIR="$(cd "$TEST_DATA_IN" && pwd)"

IC_DATA_IN="${4:-$PROJECT_ROOT/../integral_data_archive}"
IC_DATA_DIR="$(cd "$IC_DATA_IN" && pwd)"

WORK_IN="${5:-$PROJECT_ROOT/validation_runs/ibis}"
mkdir -p "$WORK_IN"
WORK_DIR="$(cd "$WORK_IN" && pwd)"

echo "===================================================================="
echo " INTEGRAL OSA Modern: IBIS/ISGRI Official Testdata Validation"
echo "===================================================================="
echo "Observation Group ID : ${OGID}"
echo "Container Image      : ${DOCKER_IMAGE}"
echo "Test Data Directory  : ${TEST_DATA_DIR}"
echo "IC / Catalog Dir     : ${IC_DATA_DIR}"
echo "Working Directory    : ${WORK_DIR}"
echo "===================================================================="

if [[ ! -d "${TEST_DATA_DIR}/testdata/scw" ]]; then
    echo "ERROR: Test data directory '${TEST_DATA_DIR}/testdata/scw' not found." >&2
    exit 1
fi

if [[ ! -d "${IC_DATA_DIR}/cat" ]]; then
    echo "ERROR: IC/Catalog directory '${IC_DATA_DIR}/cat' not found." >&2
    exit 1
fi

rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}/pfiles"

cat > "${WORK_DIR}/scw.lis" <<SCWLIST
/data/scw/1667/166700520010.001/swg.fits[1]
/data/scw/1667/166700530010.001/swg.fits[1]
/data/scw/1667/166700540010.001/swg.fits[1]
/data/scw/1667/166700550010.001/swg.fits[1]
SCWLIST

START_TIME=$(date +%s)

docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "${WORK_DIR}:/home/integral" \
    -v "${TEST_DATA_DIR}/testdata/scw:/data/scw:ro" \
    -v "${TEST_DATA_DIR}/testdata/aux:/data/aux:ro" \
    -v "${IC_DATA_DIR}/ic:/data/ic:ro" \
    -v "${IC_DATA_DIR}/idx:/data/idx:ro" \
    -v "${IC_DATA_DIR}/cat:/data/cat:ro" \
    "${DOCKER_IMAGE}" \
    bash -c "
        set -euo pipefail
        cd /home/integral

        export PFILES=\"/home/integral/pfiles;/opt/osa/pfiles\"
        export COMMONSCRIPT=1
        export COMMONLOGFILE=+/home/integral/common_log.txt
        export ISDC_REF_CAT=/data/cat/hec/gnrl_refr_cat_0043.fits[1]
        export ISDC_OMC_CAT=/data/cat/omc/omc_refr_cat_0005.fits[1]

        echo '[Stage 1/2] Creating observation group...'
        og_create \
            idxSwg=scw.lis \
            ogid='${OGID}' \
            baseDir=./ \
            instrument=IBIS \
            obs_id='' \
            purpose='' \
            versioning=0

        rm -f scw.lis

        echo '[Stage 2/2] Running analysis...'
        cd 'obs/${OGID}'

        ibis_science_analysis \
            ogDOL='og_ibis.fits' \
            startLevel='COR' \
            endLevel='IMA2' \
            SWITCH_disablePICsIT='YES' \
            SWITCH_disableIsgri='NO' \
            IBIS_nregions_ima=3 \
            IBIS_nbins_ima='1 1 1' \
            IBIS_energy_boundaries_ima='25 40 100 300' \
            IBIS_nregions_spe=1 \
            IBIS_nbins_spe='-5' \
            IBIS_energy_boundaries_spe='28 300'
    "

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo ">>>>> IBIS/ISGRI analysis finished in ${ELAPSED} seconds."
echo "Outputs stored in: ${WORK_DIR}/obs/${OGID}"
