"""Train the camera punch counter on a dataset's export (analyze.punch_model).

Reads the dataset's export manifest (dataset_export: its takes, their pose and
labels, and the split — which stretches of which takes are for training and
which for testing), builds training rows from every camera's pose in the train
stretches, picks the probability threshold by cross-validation over takes (a
take is never split between fit and score), fits the final model on all train
stretches, and scores it on the test stretches — next to the current heuristic
counter (`punch_fusion.camera_punches`, as the browser cameras run it), the
baseline it has to beat. Labels come from the wrist sensors, so neither counter
is graded against itself.

Training takes a few minutes, so it runs in a background thread. The model and
its report land in data/ml/punch_counter/{dataset}/: v{n}.joblib and v{n}.json.
"""

from __future__ import annotations

import datetime
import json
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np

from analyze import punch_model
from analyze.punch_fusion import camera_punches
from api.services import dataset_store
from common import get_logger
from contracts import Hand, Landmark, PoseFrame, WorldLandmark
from studies import DetectedPunch, match_events
from studies.evaluation import GroundTruthPunch

log = get_logger(__name__)

MODELS_DIR = Path("data/ml/punch_counter")
THRESHOLDS = tuple(round(x, 2) for x in np.arange(0.3, 0.96, 0.05))
TOLERANCE_MS = 200.0
BACKGROUND = True  # tests switch the thread off and call run()

_lock = threading.Lock()
_status: dict[str, dict[str, Any]] = {}  # dataset id → {running, step, error}


@dataclass
class Pose:
    """One camera's pose as arrays: time, image landmarks (n, 33, 4) and world
    landmarks (n, 33, 4), NaN where a frame had none."""

    t: np.ndarray
    lm: np.ndarray
    wl: np.ndarray

    def frames(self, t_start: float, t_end: float, capture: UUID) -> list[PoseFrame]:
        """`PoseFrame`s for one stretch (the heuristic baseline needs them)."""
        out: list[PoseFrame] = []
        for i in np.flatnonzero((self.t >= t_start) & (self.t <= t_end)):
            world = None
            if not np.isnan(self.wl[i, 0, 0]):
                world = tuple(
                    WorldLandmark.model_construct(x=x, y=y, z=z, visibility=v)
                    for x, y, z, v in self.wl[i].tolist()
                )
            out.append(
                PoseFrame.model_construct(
                    session_id=capture,
                    frame_index=len(out),
                    t_ms=float(self.t[i]),
                    landmarks=tuple(
                        Landmark.model_construct(x=x, y=y, z=z, visibility=v)
                        for x, y, z, v in self.lm[i].tolist()
                    ),
                    world_landmarks=world,
                )
            )
        return out


def read_pose(path: Path) -> Pose:
    """A pose parquet straight into arrays (reading it as `PoseFrame`s takes far
    longer for a long take)."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    cols = set(table.column_names)

    def block(prefix: str) -> np.ndarray:
        n = table.num_rows
        out = np.full((n, 33, 4), np.nan)
        for i in range(33):
            for k, axis in enumerate("xyzv"):
                name = f"{prefix}{i:02d}_{axis}"
                if name in cols:
                    out[:, i, k] = table.column(name).to_numpy(zero_copy_only=False)
        return out

    t = table.column("t_ms").to_numpy(zero_copy_only=False).astype(float)
    return Pose(t=t, lm=block("lm"), wl=block("wl"))


@dataclass
class Stretch:
    """A labelled stretch of a take (one protocol block) and its cameras' pose."""

    take_id: str
    block: str
    t_start: float
    t_end: float
    labels: list[tuple[float, Hand]]
    tracks: dict[str, punch_model.Track]  # camera → smoothed pose, for the model
    poses: dict[str, Pose] = field(default_factory=dict)  # raw pose, for the baseline

    def truth(self) -> list[GroundTruthPunch]:
        return [GroundTruthPunch(t_ms=t, hand=h) for t, h in self.labels]


@dataclass
class Tally:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    runs: int = 0  # camera × stretch evaluations
    counted: int = 0
    truth: int = 0

    def add(self, truth: list[GroundTruthPunch], found: list[DetectedPunch]) -> None:
        m = match_events(truth, found, TOLERANCE_MS)
        self.tp += m.true_positives
        self.fp += m.false_positives
        self.fn += m.false_negatives
        self.runs += 1
        self.counted += len(found)
        self.truth += len(truth)

    def out(self) -> dict[str, Any]:
        p = self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0
        r = self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        return {
            "precision": round(p, 3),
            "recall": round(r, 3),
            "f1": round(f1, 3),
            # per camera, on average: what it counted vs the punches thrown
            "counted": round(self.counted / self.runs, 1) if self.runs else 0,
            "truth": round(self.truth / self.runs, 1) if self.runs else 0,
        }


def model_dir(dataset_id: UUID) -> Path:
    return MODELS_DIR / str(dataset_id)


def manifest_path(dataset_id: UUID) -> Path:
    return dataset_store.dataset_dir(dataset_id) / "export" / "manifest.json"


def _step(dataset_id: UUID, text: str) -> None:
    with _lock:
        _status.setdefault(str(dataset_id), {})["step"] = text


def _stretches(dataset_id: UUID, manifest: dict[str, Any], split: str) -> list[Stretch]:
    """The split's stretches with their labels and every camera's pose (each
    camera's pose is read and smoothed once per take)."""
    root = dataset_store.dataset_dir(dataset_id)
    takes = {t["take_id"]: t for t in manifest.get("takes", [])}
    cache: dict[str, tuple[list[tuple[float, Hand]], dict[str, Any], dict[str, Pose]]] = {}
    out: list[Stretch] = []
    entries = manifest.get("splits", {}).get(split, [])
    for i, e in enumerate(entries):
        take = takes.get(e["take_id"])
        if take is None:
            continue
        if e["take_id"] not in cache:
            _step(dataset_id, f"reading {split} takes ({i + 1}/{len(entries)})")
            folder = root / take["folder"]
            raw = (
                json.loads((folder / take["labels"]["path"]).read_text())
                if take.get("labels")
                else []
            )
            labels = [
                (float(x["t_ms"]), x["hand"]) for x in raw if x.get("hand") in ("left", "right")
            ]
            poses, tracks = {}, {}
            for rel in take.get("pose", []):
                if not (folder / rel).exists():
                    continue
                pose = read_pose(folder / rel)
                if len(pose.t) > 30:
                    cam = Path(rel).stem
                    poses[cam] = pose
                    tracks[cam] = punch_model.track_arrays(
                        pose.t, pose.wl[:, :, :3], pose.wl[:, :, 3]
                    )
            cache[e["take_id"]] = (labels, tracks, poses)
        labels, tracks, poses = cache[e["take_id"]]
        lo, hi = float(e["t_start_ms"]), float(e["t_end_ms"])
        out.append(
            Stretch(
                take_id=e["take_id"],
                block=str(e.get("block") or ""),
                t_start=lo,
                t_end=hi,
                labels=[(t, h) for t, h in labels if lo <= t <= hi],
                tracks=tracks,
                poses=poses,
            )
        )
    return out


def _rows(stretches: list[Stretch]) -> list[punch_model.Sample]:
    rows = []
    for s in stretches:
        for tr in s.tracks.values():
            r = punch_model.samples(tr, s.labels, s.t_start, s.t_end, group=s.take_id)
            if r is not None:
                rows.append(r)
    return rows


def _found(evs: list[tuple[float, Hand, float]], s: Stretch) -> list[DetectedPunch]:
    return [
        DetectedPunch(t_ms=t, hand=h, punch_type=None, confidence=p)
        for t, h, p in evs
        if s.t_start <= t <= s.t_end
    ]


def _pick_threshold(dataset_id: UUID, train: list[Stretch]) -> tuple[float, dict[str, Any]]:
    """The threshold with the best F1 on takes the model didn't see (grouped CV)."""
    takes = sorted({s.take_id for s in train})
    folds = [takes[i::3] for i in range(min(3, len(takes)))]
    tallies = {th: Tally() for th in THRESHOLDS}
    for k, held in enumerate(folds):
        _step(dataset_id, f"choosing the threshold (fold {k + 1}/{len(folds)})")
        fit_on = [s for s in train if s.take_id not in held]
        if not fit_on:
            continue
        model = punch_model.fit(_rows(fit_on), seed=k)
        for s in (s for s in train if s.take_id in held):
            for tr in s.tracks.values():
                probs = punch_model.probabilities(model, tr)
                for th in THRESHOLDS:
                    tallies[th].add(s.truth(), _found(punch_model.events(probs, th), s))
    best = max(THRESHOLDS, key=lambda th: (tallies[th].out()["f1"], -th))
    return best, tallies[best].out()


def run(dataset_id: UUID) -> dict[str, Any]:
    """Train and score; returns (and saves) the report."""
    import joblib  # type: ignore[import-untyped, import-not-found, unused-ignore]

    manifest = json.loads(manifest_path(dataset_id).read_text())
    train = _stretches(dataset_id, manifest, "train")
    test = _stretches(dataset_id, manifest, "test")
    if not train or not any(s.labels for s in train):
        raise ValueError("the export has no labelled training stretches")

    threshold, cv = _pick_threshold(dataset_id, train)
    _step(dataset_id, "training the model")
    model = punch_model.fit(_rows(train))

    _step(dataset_id, "scoring it against the current counter")
    overall = {"model": Tally(), "baseline": Tally()}
    by_block: dict[str, dict[str, Tally]] = defaultdict(
        lambda: {"model": Tally(), "baseline": Tally()}
    )
    by_camera: dict[str, dict[str, Tally]] = defaultdict(
        lambda: {"model": Tally(), "baseline": Tally()}
    )
    for s in test:
        for cam, tr in s.tracks.items():
            mine = _found(punch_model.events(punch_model.probabilities(model, tr), threshold), s)
            # the current counter, as the browser cameras run it (a second either
            # side so the block's first and last punches aren't cut off)
            frames = s.poses[cam].frames(s.t_start - 1000, s.t_end + 1000, dataset_id)
            base = [
                DetectedPunch(t_ms=e.t_ms, hand=e.hand, punch_type=None)
                for e in (camera_punches(frames) if len(frames) > 10 else [])
                if s.t_start <= e.t_ms <= s.t_end
            ]
            for name, found in (("model", mine), ("baseline", base)):
                overall[name].add(s.truth(), found)
                by_block[s.block][name].add(s.truth(), found)
                by_camera[cam][name].add(s.truth(), found)

    out_dir = model_dir(dataset_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    version = 1 + max((int(p.stem[1:]) for p in out_dir.glob("v*.json")), default=0)
    model_file = out_dir / f"v{version}.joblib"
    joblib.dump(
        {
            "model": model,
            "threshold": threshold,
            "offsets": punch_model.OFFSETS,
            "hz": punch_model.HZ,
        },
        model_file,
    )
    report: dict[str, Any] = {
        "version": version,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "dataset_id": str(dataset_id),
        "export_at": manifest.get("exported_at"),
        "split_strategy": manifest.get("split_strategy"),
        "model_file": str(model_file),
        "threshold": threshold,
        "data": {
            "takes": len({s.take_id for s in train + test}),
            "fighters": len({t["fighter_id"] for t in manifest.get("takes", [])}),
            "train_stretches": len(train),
            "test_stretches": len(test),
            "train_punches": sum(len(s.labels) for s in train),
            "test_punches": sum(len(s.labels) for s in test),
            "reviewed_labels": all(
                (t.get("labels") or {}).get("reviewed") for t in manifest.get("takes", [])
            ),
        },
        "train_cv": cv,  # on takes held out while choosing the threshold
        "test": {name: t.out() for name, t in overall.items()},
        "by_block": [
            {"block": b, **{name: t.out() for name, t in v.items()}}
            for b, v in sorted(by_block.items())
        ],
        "by_camera": [
            {"camera": c, **{name: t.out() for name, t in v.items()}}
            for c, v in sorted(by_camera.items())
        ],
        "warnings": list(manifest.get("warnings") or []),
    }
    (out_dir / f"v{version}.json").write_text(json.dumps(report, indent=2))
    log.info(
        "training.done",
        extra={
            "_ctx_dataset_id": str(dataset_id),
            "_ctx_version": version,
            "_ctx_f1_model": report["test"]["model"]["f1"],
            "_ctx_f1_baseline": report["test"]["baseline"]["f1"],
        },
    )
    return report


def start(dataset_id: UUID) -> bool:
    """Train in the background; False if a run for this dataset is going already."""
    key = str(dataset_id)
    with _lock:
        if _status.get(key, {}).get("running"):
            return False
        _status[key] = {"running": True, "step": "starting", "error": None}
    if not BACKGROUND:
        return True

    def work() -> None:
        try:
            run(dataset_id)
            err = None
        except Exception as e:  # reported to the coach, not raised into a thread
            log.exception("training.failed", extra={"_ctx_dataset_id": key})
            err = str(e)[:300] or type(e).__name__
        with _lock:
            _status[key] = {"running": False, "step": None, "error": err}

    threading.Thread(target=work, daemon=True, name=f"train-{key[:8]}").start()
    return True


def status(dataset_id: UUID) -> dict[str, Any]:
    """{running, step, error, latest report, all runs}."""
    with _lock:
        st = dict(_status.get(str(dataset_id), {"running": False, "step": None, "error": None}))
    reports = []
    for p in sorted(model_dir(dataset_id).glob("v*.json"), key=lambda p: int(p.stem[1:])):
        try:
            reports.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    st["latest"] = reports[-1] if reports else None
    st["runs"] = [
        {
            "version": r["version"],
            "created_at": r["created_at"],
            "f1_model": r["test"]["model"]["f1"],
            "f1_baseline": r["test"]["baseline"]["f1"],
        }
        for r in reports
    ]
    return st
