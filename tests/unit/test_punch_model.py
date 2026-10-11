"""The learned camera punch counter (analyze.punch_model) on synthetic pose:
an arm that really extends at known times, and slow guard drift as decoys."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from contracts import Landmark, PoseFrame, WorldLandmark

pytest.importorskip("sklearn")

SID = uuid4()


def synthetic(
    seconds: float, punches: list[tuple[float, str]], seed: int, fps: float = 30.0
) -> list[PoseFrame]:
    """A boxer in guard; each (t_ms, hand) throws that hand straight out (120 ms
    out, 60 ms held, 200 ms back); the guard drifts slowly the rest of the time."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(int(seconds * fps)):
        t = i * 1000.0 / fps
        w = np.zeros((33, 3))
        w[11], w[12] = (0.2, -0.45, 0.0), (-0.2, -0.45, 0.0)  # shoulders
        w[23], w[24] = (0.1, 0.0, 0.0), (-0.1, 0.0, 0.0)  # hips
        drift = 0.05 * np.sin(t / 900.0)
        for side, (wi, ei, si) in {"left": (15, 13, 11), "right": (16, 14, 12)}.items():
            guard = w[si] + np.array([-0.08 * np.sign(w[si][0]), -0.1 + drift, -0.25])
            reach = 0.0
            for pt, ph in punches:
                if ph != side:
                    continue
                d = t - pt  # the label sits at full extension
                if -120 <= d <= 0:
                    reach = 1 + d / 120
                elif 0 < d <= 60:
                    reach = 1.0
                elif 60 < d <= 260:
                    reach = 1 - (d - 60) / 200
            extended = w[si] + np.array([0.0, 0.0, -0.62])
            w[wi] = guard + reach * (extended - guard)
            w[ei] = (w[si] + w[wi]) / 2 + np.array([0.0, 0.08 * (1 - reach), 0.0])
        w += rng.normal(0, 0.004, w.shape)
        world = tuple(WorldLandmark(x=x, y=y, z=z, visibility=0.95) for x, y, z in w.tolist())
        image = tuple(
            Landmark(x=0.5 + x / 2, y=0.5 + y / 2, z=z, visibility=0.95) for x, y, z in w.tolist()
        )
        out.append(
            PoseFrame(session_id=SID, frame_index=i, t_ms=t, landmarks=image, world_landmarks=world)
        )
    return out


def schedule(seconds: float, every_ms: float, start_ms: float = 1500.0) -> list[tuple[float, str]]:
    times = np.arange(start_ms, seconds * 1000 - 1000, every_ms)
    return [(float(t), "left" if k % 2 == 0 else "right") for k, t in enumerate(times)]


def test_the_model_learns_punches_from_labels_and_counts_them() -> None:
    from analyze import punch_model

    rows = []
    for seed in range(3):
        punches = schedule(40, 1500 + 100 * seed)
        tr = punch_model.track(synthetic(40, punches, seed))
        rows.append(punch_model.samples(tr, punches, 0, 40_000, group=f"take{seed}"))
    model = punch_model.fit([r for r in rows if r is not None])

    truth = schedule(40, 1700, start_ms=1200)
    found = punch_model.detect(model, synthetic(40, truth, seed=9), threshold=0.5)
    hits = sum(1 for t, h in truth if any(fh == h and abs(ft - t) <= 200 for ft, fh, _ in found))
    assert hits >= 0.9 * len(truth)
    assert len(found) <= 1.1 * len(truth)  # the guard drift isn't counted


def test_targets_mark_frames_near_a_punch_and_leave_the_edges_out() -> None:
    from analyze import punch_model

    t = np.arange(0, 1000, 50.0)
    y = punch_model.frame_targets(t, [500.0])
    assert y[t == 500][0] == 1 and y[t == 400][0] == 1  # within 100 ms
    assert y[t == 300][0] == -1  # 100–250 ms: left out
    assert y[t == 200][0] == 0 and y[t == 0][0] == 0


def test_peaks_keep_one_punch_per_250_ms() -> None:
    from analyze import punch_model

    t = np.arange(0, 1000, 33.3)
    p = np.zeros(len(t))
    p[10], p[13], p[25] = 0.9, 0.8, 0.7  # 100 ms apart, then a separate one
    found = punch_model.peaks(t, p, threshold=0.2)
    assert len(found) == 2
