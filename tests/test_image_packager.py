"""Tests for dedicated instrument Docker image packager."""

import tempfile
from pathlib import Path

from integral.core.image_packager import (
    INSTRUMENT_SPECS,
    compute_stage_content_digest,
    construct_image_tags,
    detect_ic_release_tag,
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


def test_detect_ic_release_tag():
    """Verify automatic resolution of IC release date tag."""
    tag = detect_ic_release_tag()
    assert tag.startswith("ic")
    assert len(tag) >= 8  # e.g. "ic202505"


def test_construct_image_tags():
    """Verify informative tag generation (cal_label, arch, instrument, digest)."""
    # 1. Detected IC release tag
    tags_latest = construct_image_tags(
        instrument="ibis",
        registry_prefix="cadarn/osa",
        cal_label="ic202505",
        target_arch="arm64",
        tag_version="11.2",
        date_tag=False,
        digest="89505374",
    )
    assert "cadarn/osa:11.2-ic202505-arm64-ibis" in tags_latest
    assert "cadarn/osa:11.2-ic-89505374-arm64-ibis" in tags_latest

    # 2. Legacy esa-2022 profile
    tags_legacy = construct_image_tags(
        instrument="jemx",
        registry_prefix="cadarn/osa",
        cal_label="esa-2022",
        target_arch="amd64",
        tag_version="11.2",
        date_tag=True,
    )
    assert "cadarn/osa:11.2-esa-2022-amd64-jemx" in tags_legacy
    assert any("202" in t for t in tags_legacy)  # Date tag present


def test_generate_instrument_dockerfile():
    """Verify Dockerfile syntax and environment variables."""
    content = generate_instrument_dockerfile(
        instrument="ibis",
        base_image="cadarn/osa:11-native-arm64",
        cal_label="ic202505",
        profile_name="latest",
    )
    assert "FROM cadarn/osa:11-native-arm64" in content
    assert "ENV CURRENT_IC=/opt/osa/caldb" in content
    assert "ENV ISDC_REF_CAT=/opt/osa/caldb/cat/hec/gnrl_refr_cat_0043.fits" in content
    assert "COPY ic/ /opt/osa/caldb/ic/" in content


def test_stage_instrument_calibration_tree_and_digest():
    """Verify selective staging of IC trees and content digest."""
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

        digest = compute_stage_content_digest(stage_path)
        assert len(digest) == 8
