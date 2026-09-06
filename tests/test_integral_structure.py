"""
Verify modern package structure (src/integral) and backwards compatibility shims.
"""

import subprocess
import sys


def test_integral_package_imports():
    """Verify clean imports from the new modular src/integral package hierarchy."""
    import integral
    from integral.cli.main import app
    from integral.core.config import config

    assert integral.__version__ == "0.1.0"
    assert config is not None
    assert app is not None


def test_legacy_integral_cli_shim_imports():
    """Verify backwards compatibility layer for code importing integral_cli."""
    from integral_cli.analysis import parse_energy_bands, run_ibis
    from integral_cli.config import config

    assert config is not None
    assert run_ibis is not None
    assert parse_energy_bands("18-60")[2] == 1


def test_standalone_script_shims():
    """Verify that standalone scripts in scripts/ and pipeline/ run --help cleanly."""
    res_val = subprocess.run(
        [sys.executable, "scripts/validate_science_products.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_val.returncode == 0
    assert "compare" in res_val.stdout or "Usage:" in res_val.stdout

    res_fetch = subprocess.run(
        [sys.executable, "scripts/fetch_integral_data.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_fetch.returncode == 0
    assert "Usage:" in res_fetch.stdout

    res_dist = subprocess.run(
        [sys.executable, "pipeline/scw_distributor.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_dist.returncode == 0
    assert "scw_file" in res_dist.stdout
