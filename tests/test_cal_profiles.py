"""
Tests for calibration profile management and cross-run comparison.
"""

from integral_cli.cal_profiles import (
    CalibrationProfile,
    CalibrationRule,
    get_profile,
    list_profiles,
    save_user_profile,
)


def test_builtin_profiles():
    profiles = list_profiles()
    assert "latest" in profiles
    assert "esa-2022" in profiles

    esa = get_profile("esa-2022")
    assert esa.name == "esa-2022"
    assert len(esa.rules) == 4
    rule_indices = [r.index for r in esa.rules]
    assert "ISGR-RMF.-RSP-IDX.fits" in rule_indices
    assert "ISGR-BACK-BKG-IDX.fits" in rule_indices
    assert "JMX2-IMOD-GRP-IDX.fits" in rule_indices


def test_save_and_retrieve_user_profile(tmp_path, monkeypatch):
    import integral_cli.cal_profiles as cp_mod

    monkeypatch.setattr(cp_mod, "PROFILES_DIR", tmp_path / "profiles")

    custom = CalibrationProfile(
        name="custom-test",
        description="Test custom calibration profile",
        rules=[
            CalibrationRule(
                index="ISGR-RMF.-RSP-IDX.fits",
                max_version=1,
                description="Pins to v1",
            )
        ],
    )
    save_path = save_user_profile(custom)
    assert save_path.exists()

    retrieved = get_profile("custom-test")
    assert retrieved.name == "custom-test"
    assert len(retrieved.rules) == 1
    assert retrieved.rules[0].max_version == 1


def test_prune_and_restore_ic_master(tmp_path):
    import numpy as np
    from astropy.io import fits

    from integral.core.calibration import prune_ic_master, restore_ic_master

    master_file = tmp_path / "ic_master_file.fits"

    # Create synthetic master FITS file with MEMBER_LOCATION table
    c1 = fits.Column(
        name="MEMBER_LOCATION",
        format="64A",
        array=np.array(
            [
                "IBIS/cal/isgr_cal.fits",
                "ISGR/bkg/isgr_bkg.fits",
                "SPI/gain/spi_gain.fits",
                "JMX1/imod/jmx1_imod.fits",
                "GNRL/refr/gnrl_cat.fits",
            ]
        ),
    )
    hdu_table = fits.BinTableHDU.from_columns([c1])
    hdul = fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(), hdu_table])
    hdul.writeto(master_file)

    # Prune for IBIS only (should keep IBIS, ISGR, and GNRL = 3)
    kept, orig, target = prune_ic_master(master_file=master_file, instruments=["IBIS"])
    assert orig == 5
    assert kept == 3
    assert master_file.with_suffix(".fits.bak").exists()

    with fits.open(target) as h_after:
        locs = list(h_after[2].data["MEMBER_LOCATION"])
        assert len(locs) == 3
        assert "IBIS/cal/isgr_cal.fits" in locs
        assert "ISGR/bkg/isgr_bkg.fits" in locs
        assert "GNRL/refr/gnrl_cat.fits" in locs
        assert "SPI/gain/spi_gain.fits" not in locs

    # Restore from backup
    restored, _bak = restore_ic_master(master_file=master_file)
    assert restored.exists()
    with fits.open(restored) as h_restored:
        assert len(h_restored[2].data["MEMBER_LOCATION"]) == 5
