"""A learned punch counter for the cameras' pose, trained on wrist-sensor labels.

The heuristic detector (`punch_detector_heuristic`) counts speed peaks of the
wrist landmark. Browser pose jitters, so it over-counts 1.1–2×, and by rule it
can't tell a punch from a pull-back or a guard adjustment. Here a classifier
learns that from the dataset, whose labels come from the wrist IMUs — an
independent sensor, so the model isn't taught the heuristic's mistakes:

* every pose frame (smoothed to 30 Hz like `punch_fusion.smooth_pose`) gets, for each
  wrist, the arm's motion relative to its shoulder in metric world coordinates,
  sampled over ±300 ms around the frame (`window_features`);
* frames within 100 ms of a labelled punch of that hand are positives, frames
  more than 250 ms from any are negatives, the ones between are left out
  (`frame_targets`);
* counting is the classifier's probability over time: peaks above a threshold,
  at most one per 250 ms per hand (`detect`).

The right arm is mirrored onto the left (x flipped), so one model serves both
hands and learns from twice the punches. Pure numpy; scikit-learn is imported
when training (the "ml" extra).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from contracts import Hand, PoseFrame

HZ = 30.0
OFFSETS = tuple(range(-9, 10, 2))  # frames: ±300 ms at 30 Hz, every 66 ms
POSITIVE_MS = 100.0
IGNORE_MS = 250.0
MIN_GAP_MS = 250.0  # one punch per hand per this long

# MediaPipe landmark indices, per side: (wrist, elbow, shoulder, other shoulder)
_ARM = {"left": (15, 13, 11, 12), "right": (16, 14, 12, 11)}
_HIPS = (23, 24)


@dataclass(frozen=True)
class Track:
    """One camera's pose, resampled: time (ms) and world landmarks (n, 33, 3)."""

    t: np.ndarray
    world: np.ndarray  # NaN where the frame had no world landmarks
    vis: np.ndarray  # (n, 33)


SMOOTH_WIN_MS = 100.0


def track_arrays(t: np.ndarray, world: np.ndarray, vis: np.ndarray) -> Track:
    """Resample raw pose arrays to 30 Hz with a 100 ms moving average — the same
    smoothing as `punch_fusion.smooth_pose`, on arrays (a long take is tens of
    thousands of frames). `world` is (n, 33, 3), NaN where a frame had none."""
    if len(t) < 2:
        return Track(t=np.asarray(t, dtype=float), world=np.asarray(world), vis=np.asarray(vis))
    order = np.argsort(t, kind="stable")
    t, world, vis = t[order], world[order], vis[order]
    ok = ~np.isnan(world[:, 0, 0])
    w0 = np.where(ok[:, None, None], world, 0.0)
    v0 = np.where(ok[:, None], vis, 0.0)

    def csum(a: np.ndarray) -> np.ndarray:
        return np.concatenate((np.zeros((1, *a.shape[1:])), np.cumsum(a, axis=0)))

    cw, cv, cn = csum(w0), csum(v0), csum(ok.astype(float))
    grid = np.arange(t[0], t[-1] + 1e-9, 1000.0 / HZ)
    lo = np.searchsorted(t, grid - SMOOTH_WIN_MS / 2, side="left")
    hi = np.searchsorted(t, grid + SMOOTH_WIN_MS / 2, side="right")
    n = cn[hi] - cn[lo]
    has = n > 0
    out_w = np.full((len(grid), 33, 3), np.nan)
    out_v = np.zeros((len(grid), 33))
    out_w[has] = (cw[hi] - cw[lo])[has] / n[has, None, None]
    out_v[has] = (cv[hi] - cv[lo])[has] / n[has, None]
    return Track(t=grid, world=out_w, vis=out_v)


def track(frames: Sequence[PoseFrame]) -> Track:
    """A camera's pose (`PoseFrame`s), smoothed and resampled for the model."""
    t = np.array([f.t_ms for f in frames], dtype=float)
    world = np.full((len(frames), 33, 3), np.nan)
    vis = np.zeros((len(frames), 33))
    for i, f in enumerate(frames):
        if f.world_landmarks is not None:
            world[i] = [(p.x, p.y, p.z) for p in f.world_landmarks]
            vis[i] = [p.visibility for p in f.world_landmarks]
    return track_arrays(t, world, vis)


def _fill(a: np.ndarray) -> np.ndarray:
    """Carry the last good value over missing frames (then the first good back)."""
    out = a.copy()
    flat = out.reshape(len(out), -1)
    for j in range(flat.shape[1]):
        col = flat[:, j]
        ok = ~np.isnan(col)
        if not ok.any():
            col[:] = 0.0
            continue
        idx = np.where(ok, np.arange(len(col)), 0)
        np.maximum.accumulate(idx, out=idx)
        col[:] = col[idx]
        first = int(np.argmax(ok))
        col[:first] = col[first]
    return out


def arm_features(tr: Track, side: Hand) -> np.ndarray:
    """(n, F) per-frame features of one arm, mirrored so both arms look alike."""
    wi, ei, si, oi = _ARM[side]
    w = _fill(tr.world)
    if side == "right":
        w = w * np.array([-1.0, 1.0, 1.0])  # mirror the body's x axis
    dt = np.gradient(tr.t) / 1000.0 if len(tr.t) > 1 else np.ones(len(tr.t))
    dt = np.where(dt > 0, dt, 1.0 / HZ)
    wrist, elbow, shoulder, other = w[:, wi], w[:, ei], w[:, si], w[:, oi]
    rel = wrist - shoulder
    ext = np.linalg.norm(rel, axis=1)
    upper, fore = shoulder - elbow, wrist - elbow
    cosang = np.einsum("ij,ij->i", upper, fore) / (
        np.linalg.norm(upper, axis=1) * np.linalg.norm(fore, axis=1) + 1e-9
    )
    elbow_angle = np.arccos(np.clip(cosang, -1.0, 1.0))
    vel = np.gradient(rel, axis=0) / dt[:, None]
    speed = np.linalg.norm(np.gradient(wrist, axis=0) / dt[:, None], axis=1)
    other_wrist = w[:, _ARM["right" if side == "left" else "left"][0]]
    other_speed = np.linalg.norm(np.gradient(other_wrist, axis=0) / dt[:, None], axis=1)
    hips = w[:, list(_HIPS)].mean(axis=1)
    hip_speed = np.linalg.norm(np.gradient(hips, axis=0) / dt[:, None], axis=1)
    shoulder_width = np.linalg.norm(shoulder - other, axis=1) + 1e-6
    vis = tr.vis[:, [wi, ei, si]].min(axis=1)
    return np.column_stack(
        [
            rel,
            ext,
            ext / shoulder_width,
            elbow_angle,
            vel,
            np.gradient(ext) / dt,
            speed,
            other_speed,
            hip_speed,
            vis,
        ]
    )


def window_features(base: np.ndarray) -> np.ndarray:
    """Each frame's features over ±300 ms: the base features at every offset."""
    n = len(base)
    idx = np.arange(n)
    cols = [base[np.clip(idx + o, 0, n - 1)] for o in OFFSETS]
    return np.concatenate(cols, axis=1) if n else np.zeros((0, base.shape[1] * len(OFFSETS)))


def frame_targets(t: np.ndarray, punches: Sequence[float]) -> np.ndarray:
    """1 near a punch of this hand, 0 well away from any, -1 in between (unused)."""
    y = np.zeros(len(t), dtype=np.int8)
    if len(t) == 0 or not punches:
        return y
    p = np.sort(np.asarray(punches, dtype=float))
    k = np.searchsorted(p, t)
    near = np.minimum(
        np.abs(t - p[np.clip(k - 1, 0, len(p) - 1)]), np.abs(t - p[np.clip(k, 0, len(p) - 1)])
    )
    y[near <= IGNORE_MS] = -1
    y[near <= POSITIVE_MS] = 1
    return y


@dataclass(frozen=True)
class Sample:
    """Training rows from one camera over one labelled stretch of a take."""

    x: np.ndarray
    y: np.ndarray
    group: str  # the take, so cross-validation never splits one take


def samples(
    tr: Track,
    labels: Sequence[tuple[float, Hand]],
    t_start: float,
    t_end: float,
    group: str,
) -> Sample | None:
    """Rows for both arms of one camera's pose (`track`) within [t_start, t_end]."""
    keep = (tr.t >= t_start) & (tr.t <= t_end)
    if keep.sum() < len(OFFSETS):
        return None
    xs, ys = [], []
    for side in ("left", "right"):
        x = window_features(arm_features(tr, side))[keep]
        y = frame_targets(tr.t[keep], [t for t, h in labels if h == side])
        xs.append(x)
        ys.append(y)
    return Sample(x=np.concatenate(xs), y=np.concatenate(ys), group=group)


MAX_NEGATIVES = 200_000  # a random subset beyond this — training stays a few minutes


def fit(rows: Sequence[Sample], *, seed: int = 0) -> Any:
    """Train the frame classifier (gradient-boosted trees, positives up-weighted)."""
    import sklearn.ensemble  # type: ignore[import-untyped, import-not-found, unused-ignore]

    x = np.concatenate([r.x for r in rows])
    y = np.concatenate([r.y for r in rows])
    use = y >= 0
    neg = np.flatnonzero(use & (y == 0))
    if len(neg) > MAX_NEGATIVES:
        drop = np.random.default_rng(seed).choice(neg, len(neg) - MAX_NEGATIVES, replace=False)
        use[drop] = False
    x, y = x[use], y[use]
    pos = max(1, int(y.sum()))
    weight = np.where(y == 1, (len(y) - pos) / pos, 1.0)
    model = sklearn.ensemble.HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.08, max_leaf_nodes=31, random_state=seed
    )
    model.fit(x, y, sample_weight=weight)
    return model


def probabilities(model: Any, tr: Track) -> dict[Hand, tuple[np.ndarray, np.ndarray]]:
    """Per hand: (time, punch probability) over one camera's pose (`track`)."""
    out: dict[Hand, tuple[np.ndarray, np.ndarray]] = {}
    for side in ("left", "right"):
        x = window_features(arm_features(tr, side))
        p = model.predict_proba(x)[:, 1] if len(x) else np.zeros(0)
        out[side] = (tr.t, p)
    return out


def peaks(t: np.ndarray, p: np.ndarray, threshold: float) -> list[tuple[float, float]]:
    """(time, probability) of local maxima above threshold, ≥ MIN_GAP_MS apart
    (the taller one wins)."""
    if len(p) < 3:
        return []
    sm = np.convolve(p, np.ones(3) / 3, mode="same")
    cand = [
        i
        for i in range(1, len(sm) - 1)
        if sm[i] >= threshold and sm[i] >= sm[i - 1] and sm[i] > sm[i + 1]
    ]
    kept: list[int] = []
    for i in sorted(cand, key=lambda i: sm[i], reverse=True):
        if all(abs(t[i] - t[k]) >= MIN_GAP_MS for k in kept):
            kept.append(i)
    return sorted((float(t[i]), float(sm[i])) for i in kept)


def events(
    probs: dict[Hand, tuple[np.ndarray, np.ndarray]], threshold: float
) -> list[tuple[float, Hand, float]]:
    """Punches from `probabilities`: (t_ms, hand, probability), oldest first."""
    out: list[tuple[float, Hand, float]] = []
    for side, (t, p) in probs.items():
        out += [(tm, side, pr) for tm, pr in peaks(t, p, threshold)]
    return sorted(out)


def detect(
    model: Any, frames: Sequence[PoseFrame], threshold: float
) -> list[tuple[float, Hand, float]]:
    """The model's punches in one camera's pose: (t_ms, hand, probability)."""
    return events(probabilities(model, track(frames)), threshold)
