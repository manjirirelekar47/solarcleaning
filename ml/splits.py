"""Dataset scanning and session-aware splitting (pure Python: no torch needed).

Expected layout:  data/<session>/<class>/<image>.jpg
A "session" is one photo-taking occasion (a day, a location, a camera). Splitting by session
stops near-identical frames from landing in both train and test, which inflates scores.
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def scan_dataset(root: Path, classes: list[str]) -> list[tuple[Path, int, str]]:
    """Return (path, class_index, session) for every image under root/<session>/<class>/."""
    items: list[tuple[Path, int, str]] = []
    for session_dir in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        for idx, cls in enumerate(classes):
            cls_dir = session_dir / cls
            if not cls_dir.is_dir():
                continue
            for f in sorted(cls_dir.iterdir()):
                if f.suffix.lower() in IMAGE_EXT:
                    items.append((f, idx, session_dir.name))
    return items


def split_by_session(
    items: list[tuple[Path, int, str]],
    test_sessions: list[str],
    val_fraction: float = 0.2,
    seed: int = 42,
) -> tuple[list, list, list, list[str]]:
    """Return (train, val, test, warnings).

    test_sessions are held out completely. Of the remaining sessions, about val_fraction go to
    validation. With fewer than 3 remaining sessions the split falls back to a stratified
    random split and says so in warnings (scores will then be optimistic).
    """
    warnings: list[str] = []
    known = {s for _, _, s in items}
    missing = [s for s in test_sessions if s not in known]
    if missing:
        raise ValueError(f"test sessions not found in data: {missing}")
    test = [it for it in items if it[2] in test_sessions]
    rest = [it for it in items if it[2] not in test_sessions]
    if not test:
        raise ValueError("no test images: pass --test-sessions with your own-panel session")
    sessions = sorted({s for _, _, s in rest})
    rng = random.Random(seed)

    if len(sessions) >= 3:
        n_val = max(1, round(len(sessions) * val_fraction))
        val_sessions = set(rng.sample(sessions, n_val))
        val = [it for it in rest if it[2] in val_sessions]
        train = [it for it in rest if it[2] not in val_sessions]
    else:
        warnings.append(
            "Fewer than 3 non-test sessions: validation is a random split, not by session."
        )
        by_class: dict[int, list] = {}
        for it in rest:
            by_class.setdefault(it[1], []).append(it)
        train, val = [], []
        for group in by_class.values():
            group = group[:]
            rng.shuffle(group)
            k = max(1, round(len(group) * val_fraction)) if len(group) > 1 else 0
            val.extend(group[:k])
            train.extend(group[k:])
    if not train or not val:
        raise ValueError("train or validation split is empty: add more images/sessions")
    return train, val, test, warnings


def class_weights(labels: list[int], num_classes: int) -> list[float]:
    """Inverse-frequency weights, normalised so the mean weight is 1. Missing class -> 0."""
    counts = Counter(labels)
    present = [c for c in range(num_classes) if counts.get(c, 0) > 0]
    if not present:
        raise ValueError("no labels")
    total = sum(counts.values())
    raw = [total / (len(present) * counts[c]) if c in present else 0.0 for c in range(num_classes)]
    mean = sum(raw[c] for c in present) / len(present)
    return [w / mean for w in raw]
