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
