#!/bin/bash
if [ -d "/opt/heasoft" ]; then
    # Find headas init path dynamically
    HEADAS_DIR=$(find /opt/heasoft -maxdepth 2 -name "headas-init.sh" -exec dirname {} \; 2>/dev/null | head -n 1)
    if [ -n "$HEADAS_DIR" ]; then
        export HEADAS="$HEADAS_DIR"
        source "$HEADAS/headas-init.sh"
    fi
fi
