"""Face blurring for exported video (ADR-014, step 2).

A clip is decoded frame by frame; every face is pixelated; the frames are
re-encoded with no sound and no metadata. A face is wherever either of two
sources puts one, and both are always blurred:

- **pose** — the clip's own MediaPipe landmarks: the face points (nose, eyes,
  ears, mouth) give the centre, the shoulders give the size and where the chin
  ends, so the box doesn't shrink when the head turns. Boxes from every pose
  frame within ±150 ms are joined, so a fast head move doesn't outrun the box.
  Pose finds the fighter even when the gloves hide the face.
- **YuNet** — a face detector (OpenCV's model zoo), for side-on faces, jaw and
  beard, and anyone else in view: a bystander, a face in a mirror.

**Fail closed**: a frame where neither finds a face is pixelated whole — a face
the detectors missed can't get through.

Each blurred clip comes with its exact frame times on the take timeline (the
recorded WebM has a variable frame rate, which the re-encode can't keep) and a
strip of blurred head close-ups for the researcher to check.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray

# Bump when the method changes: cached blurred copies are made again.
BLUR_VERSION = 1

Box = tuple[int, int, int, int]  # x0, y0, x1, y1 in pixels
Frame = NDArray[np.uint8]

_MODEL_DIR = Path("models/face")
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + YUNET_FILE
)
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

VISIBLE = 0.5  # pose landmark visibility that counts
SPREAD_MS = 150.0  # join the boxes of pose frames this close to the video frame
DETECT_SIDE = 800  # longest side YuNet sees
DETECT_SCORE = 0.5  # low on purpose: a fist taken for a face costs a moment of blur
CHECK_SHOTS = 16


class BlurError(RuntimeError):
    """The clip couldn't be blurred safely — it must not be exported."""


class FaceDetector(Protocol):
    def detect(self, frame: Frame) -> list[Box]: ...


def ensure_face_model() -> Path:
    """YuNet's model, fetched once into models/face/ and checked against its known
    hash (a changed or partial file is refused, not used)."""
    path = _MODEL_DIR / YUNET_FILE
    if not path.exists():
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".partial")
        urllib.request.urlretrieve(YUNET_URL, tmp)
        tmp.rename(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != YUNET_SHA256:
        raise BlurError(f"{path} isn't the expected YuNet model (sha256 {digest[:12]}…)")
    return path


class YuNet:
    """OpenCV's YuNet face detector, with a margin for hairline, ears and chin."""

    def __init__(self, model: Path | None = None) -> None:
        import cv2

        self._cv2 = cv2
        self._net = cv2.FaceDetectorYN.create(
            str(model or ensure_face_model()), "", (320, 320), DETECT_SCORE, 0.3, 50
        )

    def detect(self, frame: Frame) -> list[Box]:
        h, w = frame.shape[:2]
        k = DETECT_SIDE / max(w, h)
        small = self._cv2.resize(frame, (int(w * k), int(h * k)))
        self._net.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self._net.detect(small)
        out: list[Box] = []
        for f in faces if faces is not None else []:
            x, y, fw, fh = f[0] / k, f[1] / k, f[2] / k, f[3] / k
            mx, my = 0.25 * fw, 0.2 * fh
            out.append(
                (
                    max(0, int(x - mx)),
                    max(0, int(y - my)),
                    min(w, int(x + fw + mx)),
                    min(h, int(y + fh + 1.5 * my)),
                )
            )
        return out


def pose_face_box(points: NDArray[np.float64], w: int, h: int) -> Box | None:
    """The face from one pose frame: `points` are landmarks 0–12 as (x, y, vis),
    normalised. None if fewer than three face points are visible."""
    face, shoulders = points[:11], points[11:13]
    ok = face[:, 2] >= VISIBLE
    if ok.sum() < 3:
        return None
    xy = face[ok, :2] * [w, h]
    cx, cy = xy.mean(axis=0)
    span = max(float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1])) * 1.2, 0.025 * w)
    if (shoulders[:, 2] >= VISIBLE).all():
        s = shoulders[:, :2] * [w, h]
        span = max(span, 0.42 * float(np.hypot(*(s[0] - s[1]))))  # a head ≈ 0.4 shoulders
        chin = cy + 0.55 * max(0.0, float(s[:, 1].mean()) - cy)
    else:
        chin = cy + 1.2 * span
    half = 0.95 * span
    x0, x1 = int(cx - half), int(cx + half)
    y0, y1 = int(cy - 1.25 * half), int(max(chin + 0.15 * span, cy + half))
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def pose_box_at(
    t_ms: float, pose_t: NDArray[np.float64], pose_pts: NDArray[np.float64], w: int, h: int
) -> Box | None:
    """The union of the face boxes of every pose frame within ±SPREAD_MS."""
    lo, hi = np.searchsorted(pose_t, [t_ms - SPREAD_MS, t_ms + SPREAD_MS])
    boxes = [b for b in (pose_face_box(pose_pts[j], w, h) for j in range(lo, hi)) if b]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def pixelate(frame: Frame, box: Box) -> None:
    """Coarse blocks, then a blur over them — nothing of the face survives."""
    import cv2

    x0, y0, x1, y1 = box
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    roi = frame[y0:y1, x0:x1]
    small = cv2.resize(
        roi, (max(1, (x1 - x0) // 12), max(1, (y1 - y0) // 12)), interpolation=cv2.INTER_AREA
    )
    big = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
    frame[y0:y1, x0:x1] = cv2.GaussianBlur(big, (0, 0), 3)


def frame_times_s(src: Path) -> list[float]:
    """Every video frame's time in the clip (s), read from the container — fast,
    no decoding. Recorded WebM has no usable frame rate or duration of its own."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if out.returncode != 0:
        raise BlurError(f"can't read {src.name}: {out.stderr.strip()[:200]}")
    return sorted(float(x) for x in out.stdout.split() if x and x != "N/A")


@dataclass
class ClipBlur:
    """What blurring one clip found — kept next to the blurred copy."""

    version: int
    frames: int
    fps: float
    face_from_pose: int  # frames with a pose face box
    face_from_detector: int  # frames where YuNet found at least one face
    whole_frame: int  # fail-closed frames: no face found, so all of it blurred
    faces_detected: int
    seconds: float


def blur_clip(
    src: Path,
    dst: Path,
    *,
    offset_ms: float,
    pose: tuple[NDArray[np.float64], NDArray[np.float64]] | None,
    detector: FaceDetector,
    check: Path | None = None,
    progress: Callable[[float], None] | None = None,
) -> ClipBlur:
    """Write a face-blurred, silent, metadata-free copy of `src` to `dst` (.mp4),
    its frame times to `dst` with `.frames.csv`, and a close-up strip to `check`.
    `pose` is (t_ms on the take timeline, landmarks 0–12 as x/y/vis); `offset_ms`
    puts clip time on that timeline. Raises BlurError if it can't be done safely —
    then nothing is left at `dst`."""
    import time

    import cv2

    started = time.time()
    times = frame_times_s(src)
    if len(times) < 2:
        raise BlurError(f"{src.name}: no video frames")
    fps = (len(times) - 1) / max(1e-3, times[-1] - times[0])
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise BlurError(f"{src.name}: can't be decoded")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    part = dst.with_name(dst.name + ".part")
    dst.parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
         "-an", "-sn", "-dn", "-map_metadata", "-1", "-fflags", "+bitexact",
         "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(part)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    stats = ClipBlur(BLUR_VERSION, 0, round(fps, 3), 0, 0, 0, 0, 0.0)
    rows: list[str] = ["frame,t_ms"]
    shots: list[Frame] = []
    n_expected = len(times)
    try:
        while True:
            ok, raw = cap.read()
            if not ok:
                break
            frame = cast(Frame, raw)
            t_take = cap.get(cv2.CAP_PROP_POS_MSEC) + offset_ms
            stats.frames += 1
            boxes: list[Box] = []
            pbox = pose_box_at(t_take, pose[0], pose[1], w, h) if pose else None
            if pbox:
                stats.face_from_pose += 1
                boxes.append(pbox)
            faces = detector.detect(frame)
            if faces:
                stats.face_from_detector += 1
                stats.faces_detected += len(faces)
                boxes += faces
            if not boxes:
                stats.whole_frame += 1
                boxes = [(0, 0, w, h)]
            for b in boxes:
                pixelate(frame, b)
            if check and pbox and stats.frames % max(1, n_expected // CHECK_SHOTS) == 0:
                shots.append(_close_up(frame, pbox))
            rows.append(f"{stats.frames - 1},{t_take:.1f}")
            assert enc.stdin is not None
            enc.stdin.write(frame.tobytes())
            if progress and stats.frames % 60 == 0:
                progress(stats.frames / n_expected)
        assert enc.stdin is not None
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace") if enc.stderr else ""
        if enc.wait() != 0:
            raise BlurError(f"{src.name}: encoding failed: {err.strip()[:200]}")
        if stats.frames == 0:
            raise BlurError(f"{src.name}: no frames decoded")
        _verify(part, stats.frames)
    except BaseException:
        enc.kill()
        part.unlink(missing_ok=True)
        raise
    finally:
        cap.release()
    os.replace(part, dst)
    dst.with_suffix(".frames.csv").write_text("\n".join(rows) + "\n")
    if check and shots:
        _write_strip(check, shots[:CHECK_SHOTS])
    stats.seconds = round(time.time() - started, 1)
    return stats


def _verify(path: Path, frames: int) -> None:
    """The output must hold one video stream, nothing else, with every frame."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,nb_read_packets:format_tags", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    info: dict[str, Any] = json.loads(out.stdout or "{}")
    streams = info.get("streams", [])
    if [s.get("codec_type") for s in streams] != ["video"]:
        raise BlurError(f"blurred copy has streams {[s.get('codec_type') for s in streams]}")
    if int(streams[0].get("nb_read_packets", 0)) != frames:
        raise BlurError("blurred copy is missing frames")
    tags = set((info.get("format") or {}).get("tags", {})) - {
        "major_brand",
        "minor_version",
        "compatible_brands",
    }
    if tags:
        raise BlurError(f"blurred copy kept metadata: {sorted(tags)}")


def _close_up(frame: Frame, box: Box) -> Frame:
    import cv2

    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    r = int(max(box[2] - box[0], box[3] - box[1]) * 1.1)
    crop = frame[max(0, cy - r) : cy + r, max(0, cx - r) : cx + r]
    return cast(Frame, cv2.resize(crop, (200, 200)))


def _write_strip(path: Path, shots: Sequence[Frame]) -> None:
    import cv2

    blank = np.zeros_like(shots[0])
    rows = [
        np.hstack(list(shots[i : i + 8]) + [blank] * (8 - len(shots[i : i + 8])))
        for i in range(0, len(shots), 8)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])


def stats_dict(s: ClipBlur) -> dict[str, Any]:
    return asdict(s)
