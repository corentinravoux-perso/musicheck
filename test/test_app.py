"""The desktop app's local server: API, security checks, audio, organize."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from musicheck.app.server import Library, make_server, track_id


@pytest.fixture()
def served(library, tmp_path):
    root, files = library
    lib = Library([root], cache_path=tmp_path / "cache.sqlite", jobs=1)
    server = make_server(lib)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    assert lib.start_scan()
    lib.wait(120)
    yield server, lib, files
    server.shutdown()
    server.server_close()


def _call(server, path, body=None, token=True, headers=None, method=None):
    req = urllib.request.Request(server.url.rstrip("/") + path, method=method)
    if token:
        req.add_header("X-Musicheck-Token", server.token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data) as res:
            raw = res.read()
            ctype = res.headers.get("Content-Type", "")
            return res.status, (json.loads(raw) if "json" in ctype else raw), res.headers
    except urllib.error.HTTPError as err:
        raw = err.read()
        return err.code, (json.loads(raw) if raw[:1] == b"{" else raw), err.headers


def test_index_carries_token(served):
    server, _, _ = served
    status, body, headers = _call(server, "/", token=False)
    assert status == 200 and server.token.encode() in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    status, body, _ = _call(server, "/static/app.js", token=False)
    assert status == 200 and body


def test_api_needs_token_and_host(served):
    server, _, _ = served
    assert _call(server, "/api/tracks", token=False)[0] == 403
    assert _call(server, "/api/tracks", headers={"Host": "evil.example:80"})[0] == 403
    assert _call(server, "/api/scan", body={}, token=False)[0] == 403
    assert _call(server, "/static/../server.py", token=False)[0] == 404


def test_tracks_and_detail(served):
    server, lib, files = served
    status, body, _ = _call(server, "/api/tracks")
    assert status == 200
    by_file = {t["file"]: t for t in body["tracks"]}
    assert by_file["fake128.flac"]["verdict"] == "FAKE"
    assert by_file["genuine.flac"]["verdict"] == "OK"
    tid = by_file["fake128.flac"]["id"]
    assert tid == track_id(files["fake128.flac"].resolve())
    status, d, _ = _call(server, f"/api/track/{tid}")
    assert status == 200
    assert any(i["code"] == "LOSSY_SOURCE" for i in d["issues"])
    assert len(d["spectrum"]["db"]) == 512 and d["spectrum"]["nyquist_hz"] == 22050
    assert _call(server, "/api/track/nope")[0] == 404
    state = _call(server, "/api/state")[1]
    assert state["count"] == len(body["tracks"]) and not state["scan"]["running"]


def test_audio_ranges(served):
    server, _, files = served
    tid = track_id(files["g128.mp3"].resolve())
    size = files["g128.mp3"].stat().st_size
    status, body, headers = _call(server, f"/api/track/{tid}/audio?t={server.token}",
                                  token=False, headers={"Range": "bytes=10-19"})  # fmt: skip
    assert status == 206 and len(body) == 10
    assert headers["Content-Range"] == f"bytes 10-19/{size}"
    assert headers["Content-Type"] == "audio/mpeg"
    status, body, _ = _call(server, f"/api/track/{tid}/audio")
    assert status == 200 and len(body) == size
    assert _call(server, f"/api/track/{tid}/audio", headers={"Range": f"bytes={size}-"})[0] == 416
    assert _call(server, f"/api/track/{tid}/cover")[0] == 404


def test_organize_plan_then_apply(served, tmp_path):
    server, lib, files = served
    fakes = [t["id"] for t in lib.tracks()["tracks"] if t["verdict"] == "FAKE"]
    dest = tmp_path / "sorted"
    req = {"ids": fakes, "dest": str(dest), "template": "{verdict}/{format}", "mode": "link"}
    status, plan, _ = _call(server, "/api/organize/plan", body=req)
    assert status == 200 and plan["todo"] == len(fakes) and not dest.exists()
    assert _call(server, "/api/organize/apply", body=req)[0] == 400  # no confirm
    status, res, _ = _call(server, "/api/organize/apply", body={**req, "confirm": True})
    assert status == 200 and res["done"] == len(fakes) and not res["errors"]
    assert (dest / "FAKE" / "MP3" / "fake320.mp3").is_symlink()
    lib.wait(120)
    bad = {**req, "template": "{nope}"}
    status, err, _ = _call(server, "/api/organize/plan", body=bad)
    assert status == 400 and "nope" in err["error"]
    assert _call(server, "/api/organize/plan", body={**req, "ids": []})[0] == 400


def test_open_folder_and_bad_path(served, tmp_path):
    server, lib, files = served
    status, err, _ = _call(server, "/api/open", body={"paths": [str(tmp_path / "missing")]})
    assert status == 400
    one = files["genuine.flac"].parent
    status, state, _ = _call(server, "/api/open", body={"paths": [str(one)]})
    assert status == 200 and state["roots"] == [str(one.resolve())]
    lib.wait(120)
    assert lib.state()["count"] > 0
    assert _call(server, "/api/pick-folder", body={})[0] == 501  # no window in tests


def test_spectra_fonts_and_reference(served):
    server, lib, files = served
    status, body, _ = _call(server, "/api/spectra")
    assert status == 200 and body["generation"] == lib.state()["generation"]
    one = body["spectra"][track_id(files["genuine.flac"].resolve())]
    assert len(one["db"]) == 128 and one["nyquist_hz"] == 22050
    status, font, headers = _call(server, "/static/fonts/archivo-latin.woff2", token=False)
    assert status == 200 and headers["Content-Type"] == "font/woff2" and font[:4] == b"wOF2"
    assert _call(server, "/static/fonts/../app.js", token=False)[0] == 404
    assert _call(server, "/static/other/app.js", token=False)[0] == 404
    d = _call(server, f"/api/track/{track_id(files['fake128.flac'].resolve())}")[1]
    assert d["spectrum"]["reference_khz"] == 20.6
    assert d["spectrum"]["reference_label"] == "lossless"
    d = _call(server, f"/api/track/{track_id(files['upsampled96.flac'].resolve())}")[1]
    assert d["track"]["verdict"] == "FAKE"
    assert d["spectrum"]["reference_khz"] == 28.3
    assert d["spectrum"]["reference_label"] == "hi-res"


def test_cache_carry(library, tmp_path):
    from musicheck.scan import Cache, measure

    _, files = library
    src = files["g128.mp3"]
    cache = Cache(tmp_path / "c.sqlite")
    cache.put(src, measure(src))
    copy = tmp_path / "copy.mp3"
    copy.write_bytes(src.read_bytes())
    assert cache.carry(src, copy)
    assert cache.get(copy) is not None and cache.get(src) is not None
    assert not cache.carry(tmp_path / "missing.mp3", copy)
    cache.close()


def test_common_root(tmp_path):
    from musicheck.scan import common_root

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    f = tmp_path / "a" / "x.flac"
    f.write_bytes(b"")
    assert common_root([tmp_path / "a"]) == (tmp_path / "a").resolve()
    assert common_root([f]) == (tmp_path / "a").resolve()
    assert common_root([tmp_path / "a", tmp_path / "b"]) == tmp_path.resolve()
    assert common_root([]) is None


def test_install_desktop(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from musicheck.cli import main

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    res = CliRunner().invoke(main, ["app", "--install-desktop"])
    assert res.exit_code == 0, res.output
    entry = tmp_path / "applications" / "musicheck.desktop"
    icon = tmp_path / "icons" / "hicolor" / "scalable" / "apps" / "musicheck.svg"
    text = entry.read_text()
    assert "-m musicheck app %F" in text and "Icon=musicheck" in text
    assert icon.read_text().startswith("<svg")
    res = CliRunner().invoke(main, ["app", "--uninstall-desktop"])
    assert res.exit_code == 0 and not entry.exists() and not icon.exists()


def test_last_roots(tmp_path, monkeypatch):
    from musicheck.app import window

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert window.last_roots() == []
    window._save_roots([tmp_path, tmp_path / "gone"])
    assert window.last_roots() == [str(tmp_path)]
