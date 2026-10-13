"""Face-blurred copies of a dataset's clips, for the export (ADR-014, step 2).

    data/datasets/{dataset}/blurred/{take}/{camera}.mp4         faces blurred, no sound, no metadata
    data/datasets/{dataset}/blurred/{take}/{camera}.frames.csv  each frame's t_ms on the take timeline
    data/datasets/{dataset}/blurred/{take}/{camera}.json        what blurring found + the source it came from
    data/datasets/{dataset}/blurred/{take}/{camera}.check.jpg   blurred close-ups, for the researcher to check

Each clip is blurred once and kept; it's redone only when its recording changes
or the method does (`face_blur.BLUR_VERSION`). The export links the ready copies
in and leaves out the rest, listing them. Blurring runs in the background (about
an hour for 2.4 h of video); `start` launches it, `status` reports progress.
"""

from __future__ import annotations

import json
import shutil
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np

from api.services import dataset_store
from capture.cv import face_blur

BLURRED = "blurred"
BACKGROUND = True  # tests run the job inline
DETECTOR: Callable[[], face_blur.FaceDetector] = face_blur.YuNet

_lock = threading.Lock()
_status: dict[str, dict[str, Any]] = {}


def take_blur_dir(folder: Path) -> Path:
    """A take folder's blurred copies: {dataset}/blurred/{take}/."""
    return folder.parent.parent / BLURRED / folder.name


def _fingerprint(clip: Path) -> dict[str, int]:
    st = clip.stat()
    return {"bytes": st.st_size, "mtime_ns": st.st_mtime_ns}


def clip_state(folder: Path, clip: dict[str, Any]) -> dict[str, Any]:
    """{camera, status: ready | pending | failed, stats?, error?} for one recorded clip."""
    cam = clip["device_id"]
    src = dataset_store.clip_path(folder, cam, f".{clip['ext']}")
    out = take_blur_dir(folder)
    meta_path = out / f"{cam}.json"
    state: dict[str, Any] = {"camera": cam, "status": "pending"}
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return state
    current = meta.get("version") == face_blur.BLUR_VERSION and meta.get("source") == _fingerprint(
        src
    )
    if not current:
        return state
    if meta.get("error"):
        return {**state, "status": "failed", "error": meta["error"]}
    if (out / f"{cam}.mp4").exists() and (out / f"{cam}.frames.csv").exists():
        return {**state, "status": "ready", "stats": meta.get("stats", {})}
    return state


def _pose(folder: Path, cam: str) -> tuple[np.ndarray, np.ndarray] | None:
    """The camera's pose as (t_ms, landmarks 0–12 as x/y/vis), sorted by time."""
    path = dataset_store.pose_path(folder, cam)
    if not path.exists():
        return None
    import pyarrow.parquet as pq

    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    if table.num_rows == 0:
        return None
    t = np.asarray(table.column("t_ms").to_numpy(), float)
    pts = np.stack(
        [
            np.stack(
                [np.asarray(table.column(f"lm{i:02d}_{a}").to_numpy(), float) for a in "xyv"],
                axis=1,
            )
            for i in range(13)
        ],
        axis=1,
    )
    pts = np.nan_to_num(pts, nan=0.0)
    order = np.argsort(t)
    return t[order], pts[order]


def blur_one(
    folder: Path, clip: dict[str, Any], detector: face_blur.FaceDetector
) -> dict[str, Any]:
    """Blur one clip into the take's blurred folder; record the outcome either way."""
    cam = clip["device_id"]
    src = dataset_store.clip_path(folder, cam, f".{clip['ext']}")
    out = take_blur_dir(folder)
    out.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {"version": face_blur.BLUR_VERSION, "source": _fingerprint(src)}
    try:
        stats = face_blur.blur_clip(
            src,
            out / f"{cam}.mp4",
            offset_ms=float(clip.get("start_offset_ms") or 0.0),
            pose=_pose(folder, cam),
            detector=detector,
            check=out / f"{cam}.check.jpg",
        )
        meta["stats"] = face_blur.stats_dict(stats)
    except face_blur.BlurError as e:
        meta["error"] = str(e)
        for p in out.glob(f"{cam}.*"):
            if p.suffix != ".json":
                p.unlink(missing_ok=True)
    (out / f"{cam}.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def _todo(take_ids: list[UUID]) -> list[tuple[Path, dict[str, Any]]]:
    todo = []
    for tid in take_ids:
        folder = dataset_store.take_dir(tid)
        if folder is None:
            continue
        for clip in dataset_store.clips(folder):
            if clip_state(folder, clip)["status"] == "pending":
                todo.append((folder, clip))
    return todo


def run(dataset_id: UUID, take_ids: list[UUID]) -> None:
    """Blur every pending clip of these takes (the background job's body)."""
    key = str(dataset_id)
    todo = _todo(take_ids)
    with _lock:
        _status[key] = {
            "running": True,
            "done": 0,
            "total": len(todo),
            "current": None,
            "error": None,
        }
    try:
        detector = DETECTOR() if todo else None
        for i, (folder, clip) in enumerate(todo):
            with _lock:
                _status[key].update(current=f"{folder.name[:8]} · {clip['device_id']}", done=i)
            assert detector is not None
            blur_one(folder, clip, detector)
        with _lock:
            _status[key].update(running=False, done=len(todo), current=None)
    except Exception as e:
        traceback.print_exc()
        with _lock:
            _status[key].update(running=False, current=None, error=str(e))


def start(dataset_id: UUID, take_ids: list[UUID]) -> bool:
    """Start blurring whatever's pending, unless it's already running. True if started."""
    key = str(dataset_id)
    with _lock:
        if _status.get(key, {}).get("running"):
            return False
        _status[key] = {"running": True, "done": 0, "total": 0, "current": None, "error": None}
    if BACKGROUND:
        threading.Thread(target=run, args=(dataset_id, take_ids), daemon=True).start()
    else:
        run(dataset_id, take_ids)
    return True


def status(dataset_id: UUID, take_ids: list[UUID]) -> dict[str, Any]:
    """The job's progress and every clip's state."""
    with _lock:
        job = dict(_status.get(str(dataset_id), {"running": False, "done": 0, "total": 0}))
    clips = []
    for tid in take_ids:
        folder = dataset_store.take_dir(tid)
        if folder is None:
            continue
        for clip in dataset_store.clips(folder):
            s = clip_state(folder, clip)
            s["take_id"] = str(tid)
            s["has_check"] = (take_blur_dir(folder) / f"{clip['device_id']}.check.jpg").exists()
            clips.append(s)
    counts = {k: sum(1 for c in clips if c["status"] == k) for k in ("ready", "pending", "failed")}
    return {**job, "counts": counts, "clips": clips}


def check_image(take_id: UUID, camera: str) -> Path | None:
    folder = dataset_store.take_dir(take_id)
    if folder is None or "/" in camera or camera.startswith("."):
        return None
    path = take_blur_dir(folder) / f"{camera}.check.jpg"
    return path if path.exists() else None


def delete_take(folder: Path) -> int:
    """Remove a take's blurred copies (the take is deleted or withdrawn). Clips removed."""
    out = take_blur_dir(folder)
    if not out.exists():
        return 0
    n = len(list(out.glob("*.mp4")))
    shutil.rmtree(out)
    return n
