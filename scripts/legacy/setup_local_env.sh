#!/usr/bin/env bash
# ==============================================================================
# setup_local_env.sh
# Initialises local directory structure for INTEGRAL OSA data & IC trees
# ==============================================================================

set -euo pipefail

BASE_DATA_DIR="${1:-$HOME/integral_data}"

echo "============================================================"
echo "Initialising INTEGRAL local data workspace at: $BASE_DATA_DIR"
echo "============================================================"

mkdir -pv "$BASE_DATA_DIR/scw"
mkdir -pv "$BASE_DATA_DIR/aux"
mkdir -pv "$BASE_DATA_DIR/ic"
mkdir -pv "$BASE_DATA_DIR/idx"
mkdir -pv "$BASE_DATA_DIR/cat/hec"
mkdir -pv "$BASE_DATA_DIR/cat/omc"
mkdir -pv "$BASE_DATA_DIR/work"
mkdir -pv "$BASE_DATA_DIR/pfiles"

echo ""
echo "Directory layout created successfully:"
echo "  - SCW Data:      $BASE_DATA_DIR/scw"
echo "  - AUX Data:      $BASE_DATA_DIR/aux"
echo "  - IC Files:      $BASE_DATA_DIR/ic"
echo "  - Index Files:   $BASE_DATA_DIR/idx"
echo "  - Catalogs:      $BASE_DATA_DIR/cat"
echo "  - Work Directory:$BASE_DATA_DIR/work"
echo ""
echo "To use with OSA CLI or container:"
echo "  export REP_BASE_PROD=\"$BASE_DATA_DIR\""
echo "  export CURRENT_IC=\"$BASE_DATA_DIR\""
