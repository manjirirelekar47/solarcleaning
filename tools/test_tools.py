import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibrate_severity as cal  # noqa: E402
import capture  # noqa: E402

# ------------------------------------------------------------------ calibrate_severity


def test_isotonic_fit_is_monotone_and_pools_violations():
    xs, ys = cal.isotonic_fit([1, 2, 3, 4], [1.0, 3.0, 2.0, 5.0])
    assert list(xs) == [1, 2, 3, 4]
    assert list(ys) == [1.0, 2.5, 2.5, 5.0]
    assert all(np.diff(ys) >= 0)


def test_isotonic_fit_averages_duplicate_x():
    xs, ys = cal.isotonic_fit([10, 10, 20], [2.0, 4.0, 9.0])
    assert list(xs) == [10.0, 20.0] and list(ys) == [3.0, 9.0]


def test_isotonic_fit_rejects_bad_input():
    with pytest.raises(ValueError):
        cal.isotonic_fit([], [])
    with pytest.raises(ValueError):
        cal.isotonic_fit([1, 2], [1])


def test_pearson_edge_cases():
    assert cal.pearson([1, 2], [1, 2]) is None
    assert cal.pearson([1, 1, 1], [1, 2, 3]) is None
    assert cal.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)


T0 = datetime(2026, 9, 1, 12, 0, 0)


def _series(ratio, start=T0, n=30, step=10, ref_w=4.0):
    ts = [start + timedelta(seconds=step * i) for i in range(n)]
    return [(t, ratio * ref_w) for t in ts], [(t, ref_w) for t in ts]


def test_electrical_loss_uses_median_of_pair_ratios():
    test, ref = _series(0.9)
    test[5] = (test[5][0], 0.0)  # one glitch sample must not move the median
    loss = cal.electrical_loss_at(T0 + timedelta(seconds=100), test, ref, 120, 0.5, 1.0)
    assert loss == pytest.approx(10.0, abs=0.01)


def test_electrical_loss_respects_baseline_and_clips():
    test, ref = _series(0.95)
    at = T0 + timedelta(seconds=100)
    assert cal.electrical_loss_at(at, test, ref, 120, 0.5, 0.95) == pytest.approx(0.0, abs=0.01)
    test_hi, ref_hi = _series(1.2)
    assert cal.electrical_loss_at(at, test_hi, ref_hi, 120, 0.5, 1.0) == 0.0  # never negative


def test_electrical_loss_none_when_reference_is_dark_or_data_missing():
    test, ref = _series(0.9, ref_w=0.1)
    assert cal.electrical_loss_at(T0, test, ref, 120, 0.5, 1.0) is None
    assert cal.electrical_loss_at(T0, [], [], 120, 0.5, 1.0) is None
    far = T0 + timedelta(days=1)
    good_test, good_ref = _series(0.9)
    assert cal.electrical_loss_at(far, good_test, good_ref, 120, 0.5, 1.0) is None


def test_calibrate_requires_enough_samples():
    with pytest.raises(ValueError, match="need at least"):
        cal.calibrate([1.0] * 5, [0.0] * 5, [1.0] * 5, min_points=15)


def test_calibrate_rejects_constant_severity():
    with pytest.raises(ValueError, match="identical"):
        cal.calibrate([50.0] * 20, [50.0] * 20, list(np.linspace(0, 20, 20)))


def test_calibrated_beats_fixed_table_on_monotone_data():
    rng = np.random.default_rng(0)
    raw = list(rng.uniform(0, 100, 80))
    loss = [0.25 * r + rng.normal(0, 1.0) for r in raw]
    fixed = [0.0 if r < 33 else 50.0 if r < 66 else 60.0 for r in raw]
    out = cal.calibrate(raw, fixed, loss)
    assert out["corr_calibrated_cross_validated"] > out["corr_fixed_table"]
    xs = [p[0] for p in out["breakpoints"]]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)


def _make_db(path: Path, n_images=40):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE image_captures (id INTEGER PRIMARY KEY, timestamp TEXT, label TEXT,"
        " severity_score REAL, used BOOLEAN, model TEXT)"
    )
    con.execute(
        "CREATE TABLE panel_readings (id INTEGER PRIMARY KEY, timestamp TEXT, panel_type TEXT,"
        " power REAL)"
    )
    rng = np.random.default_rng(1)
    for i in range(n_images):
        t = T0 + timedelta(minutes=10 * i)
        raw = float(rng.uniform(0, 100))
        ratio = 1.0 - 0.002 * raw  # up to 20% loss
        con.execute(
            "INSERT INTO image_captures (timestamp,label,severity_score,used,model)"
            " VALUES (?,?,?,?,?)",
            (t.isoformat(), "dusty" if raw > 30 else "clean", raw, 1, "cnn"),
        )
        for s in range(0, 60, 10):
            ts = (t + timedelta(seconds=s)).isoformat()
            con.execute(
                "INSERT INTO panel_readings (timestamp,panel_type,power) VALUES (?,?,?)",
                (ts, "test", 4.0 * ratio),
            )
            con.execute(
                "INSERT INTO panel_readings (timestamp,panel_type,power) VALUES (?,?,?)",
                (ts, "reference", 4.0),
            )
    con.commit()
    con.close()


def test_cli_end_to_end_writes_a_map_vision_can_read(tmp_path, capsys):
    db, out = tmp_path / "t.db", tmp_path / "map.json"
    _make_db(db)
    code = cal.main(["--db", f"sqlite:///{db}", "--out", str(out), "--window-s", "60"])
    assert code == 0
    data = json.loads(out.read_text())
    assert len(data["breakpoints"]) >= 2 and data["n_samples"] >= 15
    assert "5-fold CV" in capsys.readouterr().out
    y_low = np.interp(5, *zip(*data["breakpoints"]))
    y_high = np.interp(95, *zip(*data["breakpoints"]))
    assert y_high > y_low


def test_cli_reports_too_little_data_without_writing(tmp_path, capsys):
    db, out = tmp_path / "t.db", tmp_path / "map.json"
    _make_db(db, n_images=3)
    assert cal.main(["--db", f"sqlite:///{db}", "--out", str(out)]) == 1
    assert not out.exists() and "need at least" in capsys.readouterr().err


def test_cli_requires_db(monkeypatch, capsys):
    monkeypatch.delenv("DB_URL", raising=False)
    assert cal.main([]) == 2


# ------------------------------------------------------------------ capture


def test_parse_status_variants():
    assert capture.parse_status(b'{"status": "DONE"}') == "done"
    assert capture.parse_status('{"state": "spraying"}') == "spraying"
    assert capture.parse_status(b"Done\n") == "done"
    assert capture.parse_status(b"") is None
    assert capture.parse_status(b'{"other": 1}') is None


def test_folder_source_cycles_and_rejects_empty(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"A")
    (tmp_path / "b.png").write_bytes(b"B")
    (tmp_path / "c.txt").write_bytes(b"ignored")
    src = capture.FolderSource(tmp_path)
    assert [src.grab() for _ in range(3)] == [b"A", b"B", b"A"]
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        capture.FolderSource(empty)


class FakeResp:
    def __init__(self, code, body=None):
        self.status_code, self._body, self.text = code, body or {}, "x"

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_post_image_sends_context_and_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)
    sess = FakeSession(requests.ConnectionError("down"), FakeResp(503), FakeResp(201, {"id": 7}))
    out = capture.post_image("http://api/", b"jpg", "post_clean", session=sess)
    assert out == {"id": 7} and len(sess.calls) == 3
    url, kw = sess.calls[0]
    assert url == "http://api/images" and kw["data"] == {"context": "post_clean"}


def test_post_image_does_not_retry_client_errors_and_gives_up(monkeypatch):
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)
    sess = FakeSession(FakeResp(422))
    with pytest.raises(ValueError, match="rejected"):
        capture.post_image("http://api", b"jpg", "routine", session=sess)
    assert len(sess.calls) == 1
    sess = FakeSession(FakeResp(500), FakeResp(500), FakeResp(500))
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        capture.post_image("http://api", b"jpg", "routine", session=sess)


def test_post_image_refuses_oversize_frames():
    with pytest.raises(ValueError, match="5 MB"):
        capture.post_image(
            "http://api", b"x" * (5 * 1024 * 1024 + 1), "routine", session=FakeSession()
        )


def test_scheduler_posts_one_image_per_done_and_ignores_other_status():
    fired = []
    sched = capture.PostCleanScheduler(lambda: fired.append(time.monotonic()), settle_s=0.05)
    assert sched.on_status(b"spraying") is False
    assert sched.on_status(b'{"status": "done"}') is True
    assert sched.on_status(b"done") is False  # duplicate while waiting
    sched.wait(2)
    assert len(fired) == 1
    assert sched.on_status(b"done") is True  # a later cycle schedules again
    sched.wait(2)
    assert len(fired) == 2


def test_scheduler_survives_a_failing_capture():
    def boom():
        raise RuntimeError("camera unplugged")

    sched = capture.PostCleanScheduler(boom, settle_s=0)
    assert sched.on_status(b"done") is True
    sched.wait(2)  # must not raise


def test_parse_args_validation(tmp_path):
    with pytest.raises(SystemExit):
        capture.parse_args(["--folder", str(tmp_path), "--interval", "0"])
    with pytest.raises(SystemExit):
        capture.parse_args([])  # a source is required
    args = capture.parse_args(["--folder", str(tmp_path)])
    assert args.interval == 300.0 and args.settle_s == 30.0


def test_main_once_posts_a_routine_frame(tmp_path, monkeypatch):
    (tmp_path / "f.jpg").write_bytes(b"JPEGDATA")
    sent = []
    monkeypatch.setattr(
        capture,
        "post_image",
        lambda api, data, context, **kw: sent.append((api, data, context)) or {"id": 1},
    )
    assert capture.main(["--folder", str(tmp_path), "--once", "--api", "http://x"]) == 0
    assert sent == [("http://x", b"JPEGDATA", "routine")]
