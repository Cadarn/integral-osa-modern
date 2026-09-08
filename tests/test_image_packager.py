"""Tests for dedicated instrument Docker image packager."""

import tempfile
from pathlib import Path

from integral.core.image_packager import (
    INSTRUMENT_SPECS,
    construct_image_tags,
    generate_instrument_dockerfile,
    stage_instrument_calibration_tree,
)


def test_instrument_specs_coverage():
    """Verify all 4 primary instruments are defined with expected paths."""
    for inst in ["ibis", "jemx", "omc", "spi"]:
        assert inst in INSTRUMENT_SPECS
        spec = INSTRUMENT_SPECS[inst]
        assert len(spec.ic_dirs) > 0
        assert len(spec.idx_patterns) > 0
        assert len(spec.cat_dirs) > 0


def test_construct_image_tags():
    """Verify informative tag generation (profile, arch, instrument)."""
    # 1. Latest profile
    tags_latest = construct_image_tags(
        instrument="ibis",
        registry_prefix="cadarn/osa",
        profile_name="latest",
        target_arch="arm64",
        tag_version="11.2",
        date_tag=False,
    )
    assert "cadarn/osa:11.2-latest-arm64-ibis" in tags_latest
    assert "cadarn/osa:11.2-arm64-ibis" in tags_latest

    # 2. Legacy esa-2022 profile
    tags_legacy = construct_image_tags(
        instrument="jemx",
        registry_prefix="cadarn/osa",
        profile_name="esa-2022",
        target_arch="amd64",
        tag_version="11.2",
        date_tag=True,
    )
    assert "cadarn/osa:11.2-esa2022-amd64-jemx" in tags_legacy
    assert any("202" in t for t in tags_legacy)  # Date tag present


def test_generate_instrument_dockerfile():
    """Verify Dockerfile syntax and environment variables."""
    content = generate_instrument_dockerfile(
        instrument="ibis",
        base_image="cadarn/osa:11-native-arm64",
        profile_name="latest",
    )
    assert "FROM cadarn/osa:11-native-arm64" in content
    assert "ENV CURRENT_IC=/opt/osa/caldb" in content
    assert "ENV ISDC_REF_CAT=/opt/osa/caldb/cat/hec/gnrl_refr_cat_0043.fits" in content
    assert "COPY ic/ /opt/osa/caldb/ic/" in content


def test_stage_instrument_calibration_tree():
    """Verify selective staging of IC trees."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        stage_path = Path(tmp_dir)
        counts = stage_instrument_calibration_tree(
            instrument="omc",
            target_stage_dir=stage_path,
            profile_name="latest",
        )
        assert (stage_path / "ic" / "omc").exists()
        assert (stage_path / "idx" / "ic" / "ic_master_file.fits").exists()
        assert counts["ic_files"] > 0
