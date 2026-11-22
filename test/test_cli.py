import csv
import json

from click.testing import CliRunner

from musicheck.cli import main


def test_scan_sort_filter_export(library, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root, _ = library
    out_csv, out_json = tmp_path / "r.csv", tmp_path / "r.json"
    runner = CliRunner()
    res = runner.invoke(
        main,
        [
            "scan",
            str(root),
            "-j",
            "2",
            "-s",
            "-verdict,path",
            "-w",
            "verdict=FAKE",
            "--csv",
            str(out_csv),
            "--json",
            str(out_json),
            "-c",
            "quality",
        ],
    )
    assert res.exit_code == 0, res.output
    rows = list(csv.DictReader(out_csv.open()))
    assert rows and all(r["verdict"] == "FAKE" for r in rows)
    assert {r["file"] for r in rows} >= {"fake128.flac", "fake320.mp3"}
    assert json.loads(out_json.read_text())[0]["verdict"] == "FAKE"
    # second run hits the cache; --fail-on reports the fakes
    res = runner.invoke(main, ["scan", str(root), "-q", "--fail-on", "fake"])
    assert res.exit_code == 1


def test_organize_dry_run_then_link(library, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root, _ = library
    dest = tmp_path / "sorted"
    runner = CliRunner()
    res = runner.invoke(
        main,
        ["organize", str(root), "-o", str(dest), "-b", "{verdict}/{format}", "-j", "1"],
    )
    assert res.exit_code == 0, res.output
    assert "dry run" in res.output and not dest.exists()
    res = runner.invoke(
        main,
        [
            "organize",
            str(root),
            "-o",
            str(dest),
            "-b",
            "{verdict}/{format}",
            "-w",
            "verdict=FAKE",
            "--link",
            "--apply",
        ],
    )
    assert res.exit_code == 0, res.output
    assert (dest / "FAKE" / "MP3" / "fake320.mp3").is_symlink()
    assert not (dest / "OK").exists()
    # never overwrites
    res = runner.invoke(
        main,
        [
            "organize",
            str(root),
            "-o",
            str(dest),
            "-b",
            "{verdict}/{format}",
            "-w",
            "verdict=FAKE",
            "--link",
            "--apply",
        ],
    )
    assert "destination exists" in res.output


def test_info_and_fields(library):
    _, files = library
    runner = CliRunner()
    res = runner.invoke(main, ["info", str(files["fake320.mp3"])])
    assert res.exit_code == 0 and "FAKE" in res.output
    res = runner.invoke(main, ["info", "--json", str(files["genuine.flac"])])
    assert json.loads(res.output)["record"]["verdict"] == "OK"
    res = runner.invoke(main, ["fields"])
    assert "cutoff_khz" in res.output


def test_bad_template(library, tmp_path):
    root, _ = library
    res = CliRunner().invoke(
        main, ["organize", str(root), "-o", str(tmp_path), "-b", "{nope}", "--no-cache"]
    )
    assert res.exit_code != 0
