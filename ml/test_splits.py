import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from splits import class_weights, scan_dataset, split_by_session  # noqa: E402

CLASSES = ["clean", "dusty", "bird_drop"]


def make_tree(root: Path, sessions: dict[str, dict[str, int]]) -> None:
    for session, per_class in sessions.items():
        for cls, n in per_class.items():
            d = root / session / cls
            d.mkdir(parents=True)
            for i in range(n):
                (d / f"{i}.jpg").write_bytes(b"x")
            (d / "notes.txt").write_text("ignored")


def test_scan_finds_images_only(tmp_path):
    make_tree(tmp_path, {"s1": {"clean": 3, "dusty": 2}})
    items = scan_dataset(tmp_path, CLASSES)
    assert len(items) == 5
    assert {i[2] for i in items} == {"s1"}
    assert {i[1] for i in items} == {0, 1}


def test_split_keeps_sessions_apart(tmp_path):
    spec = {f"s{i}": {"clean": 4, "dusty": 4, "bird_drop": 2} for i in range(5)}
    spec["own"] = {"clean": 3, "dusty": 3, "bird_drop": 3}
    make_tree(tmp_path, spec)
    train, val, test, warns = split_by_session(scan_dataset(tmp_path, CLASSES), ["own"], seed=1)
    sessions = lambda part: {s for _, _, s in part}  # noqa: E731
    assert sessions(test) == {"own"}
    assert not sessions(train) & sessions(val)
    assert "own" not in sessions(train) | sessions(val)
    assert not warns


def test_split_is_deterministic(tmp_path):
    spec = {f"s{i}": {"clean": 2, "dusty": 2} for i in range(6)}
    spec["own"] = {"clean": 2, "dusty": 2}
    make_tree(tmp_path, spec)
    items = scan_dataset(tmp_path, CLASSES)
    a = split_by_session(items, ["own"], seed=7)
    b = split_by_session(items, ["own"], seed=7)
    assert [p for p, _, _ in a[1]] == [p for p, _, _ in b[1]]


def test_few_sessions_falls_back_with_warning(tmp_path):
    make_tree(tmp_path, {"a": {"clean": 6, "dusty": 6}, "own": {"clean": 2, "dusty": 2}})
    train, val, test, warns = split_by_session(scan_dataset(tmp_path, CLASSES), ["own"])
    assert warns and train and val and len(test) == 4


def test_unknown_or_empty_test_session_errors(tmp_path):
    make_tree(tmp_path, {"a": {"clean": 3}})
    items = scan_dataset(tmp_path, CLASSES)
    with pytest.raises(ValueError, match="not found"):
        split_by_session(items, ["nope"])
    with pytest.raises(ValueError, match="no test images"):
        split_by_session(items, [])


def test_class_weights_favour_rare_classes():
    w = class_weights([0] * 80 + [1] * 15 + [2] * 5, 3)
    assert w[2] > w[1] > w[0]
    assert sum(w) / 3 == pytest.approx(1.0)


def test_class_weights_missing_class_gets_zero():
    w = class_weights([0, 0, 1], 3)
    assert w[2] == 0.0 and w[1] > w[0]
