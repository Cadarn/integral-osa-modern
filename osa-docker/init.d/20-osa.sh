#!/bin/bash
export ISDC_ENV=${ISDC_ENV:-/opt/osa}
export REP_BASE_PROD=${REP_BASE_PROD:-/data}
export CURRENT_IC=${CURRENT_IC:-/data}

# Catalogs
if [ -z "$ISDC_REF_CAT" ]; then
    CAT_FILE=$(find /data/cat/hec -name "gnrl_refr_cat_*.fits" 2>/dev/null | sort -V | tail -n 1)
    export ISDC_REF_CAT=${CAT_FILE:-/data/cat/hec/gnrl_refr_cat_0043.fits}
fi

if [ -z "$ISDC_OMC_CAT" ]; then
    OMC_FILE=$(find /data/cat/omc -name "omc_refr_cat_*.fits" 2>/dev/null | sort -V | tail -n 1)
    export ISDC_OMC_CAT=${OMC_FILE:-/data/cat/omc/omc_refr_cat_0005.fits}
fi

if [ -f "$ISDC_ENV/bin/isdc_init_env.sh" ]; then
    source "$ISDC_ENV/bin/isdc_init_env.sh"
fi
