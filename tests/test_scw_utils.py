"""Tests for the shared ScW pointing-selection helper (used by both local and remote resolution)."""

from integral_cli.scw_utils import filter_pointing_scws


def test_filters_to_pointing_scws():
    ids = ["006000010010", "006000010021", "006000020010", "006000020021"]
    assert filter_pointing_scws(ids, "0060") == ["006000010010", "006000020010"]


def test_falls_back_to_all_ids_when_none_are_pointing_scws():
    ids = ["006000010020", "006000020030"]
    assert filter_pointing_scws(ids, "0060") == sorted(ids)


def test_ignores_ids_from_other_revolutions():
    ids = ["006000010010", "007000010010"]
    assert filter_pointing_scws(ids, "0060") == ["006000010010"]


def test_empty_input_returns_empty():
    assert filter_pointing_scws([], "0060") == []


def test_validate_scws_have_data_ibis(tmp_path):
    from integral.core.scw import validate_scws_have_data

    archive = tmp_path / "archive"
    scw_dir = archive / "scw" / "0060"
    scw_dir.mkdir(parents=True)

    # ScW 1: valid with large isgri_events.fits
    s1 = scw_dir / "006000010010.001"
    s1.mkdir()
    (s1 / "isgri_events.fits").write_bytes(b"0" * 60000)

    # ScW 2: aborted/empty (<50KB)
    s2 = scw_dir / "006000020010.001"
    s2.mkdir()
    (s2 / "isgri_events.fits").write_bytes(b"0" * 1000)

    # ScW 3: non-existent directory
    s3_id = "006000030010"

    # ScW 4: valid with .gz event file
    s4 = scw_dir / "006000040010.001"
    s4.mkdir()
    (s4 / "isgri_events.fits.gz").write_bytes(b"0" * 2000)

    valid, dropped = validate_scws_have_data(
        ["006000010010", "006000020010", s3_id, "006000040010"],
        archive,
        instrument="IBIS",
    )
    assert valid == ["006000010010", "006000040010"]
    assert dropped == ["006000020010", s3_id]
