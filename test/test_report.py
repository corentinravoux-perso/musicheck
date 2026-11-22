import pytest

from musicheck.report import filter_records, parse_sort, resolve_columns, sort_records

RECS = [
    {
        "verdict": "OK",
        "cutoff_khz": None,
        "artist": "Beta",
        "year": "1999",
        "path": "b",
    },
    {
        "verdict": "FAKE",
        "cutoff_khz": 16.1,
        "artist": "alpha",
        "year": "2004",
        "path": "a",
    },
    {
        "verdict": "SUSPECT",
        "cutoff_khz": 19.5,
        "artist": "Gamma",
        "year": "",
        "path": "c",
    },
]


def test_sort_verdict_severity_and_missing_last():
    out = sort_records(RECS, parse_sort("-verdict"))
    assert [r["verdict"] for r in out] == ["FAKE", "SUSPECT", "OK"]
    out = sort_records(RECS, parse_sort("cutoff_khz"))
    assert [r["path"] for r in out] == ["a", "c", "b"]
    out = sort_records(RECS, parse_sort("-cutoff_khz"))
    assert [r["path"] for r in out] == ["c", "a", "b"]


def test_sort_text_case_insensitive():
    assert [r["artist"] for r in sort_records(RECS, parse_sort("artist"))] == [
        "alpha",
        "Beta",
        "Gamma",
    ]


def test_filters():
    assert [r["path"] for r in filter_records(RECS, ["verdict!=OK"])] == ["a", "c"]
    assert [
        r["path"]
        for r in filter_records(RECS, ["verdict=fake|suspect", "cutoff_khz<17"])
    ] == ["a"]
    assert [r["path"] for r in filter_records(RECS, ["artist~ALP"])] == ["a"]
    assert [r["path"] for r in filter_records(RECS, ["year>=2000"])] == ["a"]


def test_bad_input():
    with pytest.raises(ValueError):
        parse_sort("nope")
    with pytest.raises(ValueError):
        filter_records(RECS, ["verdict"])
    assert resolve_columns("default,dr")[-1] == "path"
