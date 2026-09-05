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
