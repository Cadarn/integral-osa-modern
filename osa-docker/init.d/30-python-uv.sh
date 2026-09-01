#!/bin/bash
# Python and uv environment activation
if [ -d "/opt/venv" ]; then
    export VIRTUAL_ENV="/opt/venv"
    export PATH="/opt/venv/bin:$PATH"
fi
