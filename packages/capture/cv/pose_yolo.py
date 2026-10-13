"""YOLOv8-Pose wrapper — alternative pose backend to MediaPipe.

Produces the same `PoseFrame` contract so all downstream code
(punch detector, LSTM, overlay, parquet writer) works unchanged.

YOLOv8-Pose outputs 17 COCO keypoints. We map them into the 33-slot
MediaPipe layout, zero-filling the 16 landmarks that COCO doesn't have
(inner eye corners, mouth, hands, feet). The key boxing landmarks
(wrists, elbows, shoulders, hips) all have direct COCO equivalents.

Supports multi-person detection — when `num_poses > 1`, returns the
closest person (largest bounding box) by default, or all persons when
paired with the DeepSORT tracker.

Requires: `pip install ultralytics` (or `uv sync --extra yolo`).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from contracts import NUM_POSE_LANDMARKS, Landmark, PoseFrame, WorldLandmark

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    Frame = NDArray[np.uint8]
else:
    Frame = Any

# ---------------------------------------------------------------------------
# COCO 17 → MediaPipe 33 index mapping.
#
# COCO idx → MediaPipe idx (None = no direct mapping)
# 0  nose           → 0
# 1  left_eye       → 2
# 2  right_eye      → 5
# 3  left_ear       → 7
# 4  right_ear      → 8
# 5  left_shoulder  → 11
# 6  right_shoulder → 12
# 7  left_elbow     → 13
# 8  right_elbow    → 14
# 9  left_wrist     → 15
# 10 right_wrist    → 16
# 11 left_hip       → 23
# 12 right_hip      → 24
# 13 left_knee      → 25
# 14 right_knee     → 26
# 15 left_ankle     → 27
# 16 right_ankle    → 28
# ---------------------------------------------------------------------------
COCO_TO_MP: dict[int, int] = {
    0: 0,
    1: 2,
    2: 5,
    3: 7,
    4: 8,
    5: 11,
    6: 12,
    7: 13,
    8: 14,
    9: 15,
    10: 16,
    11: 23,
    12: 24,
    13: 25,
    14: 26,
    15: 27,
    16: 28,
}

# Reverse: MediaPipe idx → COCO idx (for quick lookup)
MP_TO_COCO: dict[int, int] = {v: k for k, v in COCO_TO_MP.items()}

# YOLOv8-pose model variants (ascending size/accuracy).
YOLO_MODEL_SIZES = ("yolov8n-pose", "yolov8s-pose", "yolov8m-pose", "yolov8l-pose", "yolov8x-pose")
DEFAULT_YOLO_MODEL = "yolov8n-pose"  # nano — fastest, good enough for single-cam gym


def _usage_data_off() -> None:
    """Ultralytics sends anonymous usage statistics unless its `sync` setting is
    off, and that setting lives per machine. Turn it off from here, before any
    model loads, so no install of this app reports to an outside service
    (ADR-015) whatever the machine's setting says."""
    try:
        from ultralytics import settings  # type: ignore
    except ImportError:  # older ultralytics
        import ultralytics.utils  # type: ignore

        settings = ultralytics.utils.SETTINGS
    if settings.get("sync", True):
        settings.update({"sync": False})


class YOLOPoseEstimator:
    """YOLOv8-based pose estimator. Same interface as `PoseEstimator`.

    Multi-person capable: when `num_poses > 1`, returns detections for all
    persons sorted by bounding-box area (largest first). The single-person
    path picks the largest detection automatically.
    """

    def __init__(
        self,
        session_id: UUID,
        fps: float,
        *,
        model_name: str = DEFAULT_YOLO_MODEL,
        confidence: float = 0.5,
        num_poses: int = 1,
    ) -> None:
        self.session_id = session_id
        self.fps = fps
        self._model_name = model_name
        self._confidence = confidence
        self._num_poses = num_poses
        self._frame_idx = 0
        self._model: Any = None

    @contextmanager
    def open(self) -> Iterator[YOLOPoseEstimator]:
        """Load the YOLO model. Downloads weights on first use (~6 MB for nano)."""
        from ultralytics import YOLO  # type: ignore

        _usage_data_off()
        self._model = YOLO(self._model_name + ".pt")
        try:
            yield self
        finally:
            self._model = None

    def process(self, bgr_frame: Frame) -> PoseFrame | None:
        """Run pose on one BGR frame. Returns None if no person detected."""
        if self._model is None:
            raise RuntimeError("YOLOPoseEstimator not opened — use `with estimator.open():`")

        idx = self._frame_idx
        self._frame_idx += 1
        t_ms = (idx / self.fps) * 1000.0

        results = self._model(bgr_frame, conf=self._confidence, verbose=False)
        if not results or len(results) == 0:
            return None

        result = results[0]
        if result.keypoints is None or len(result.keypoints) == 0:
            return None

        # Pick the largest bounding box (closest / most prominent person).
        kpts = result.keypoints
        if hasattr(kpts, "xy") and len(kpts.xy) == 0:
            return None

        # kpts.xy shape: (num_persons, 17, 2) — normalized [0,1] if using
        # stream mode, pixel coords otherwise.
        # kpts.conf shape: (num_persons, 17)
        import numpy as np

        xy = kpts.xy.cpu().numpy() if hasattr(kpts.xy, "cpu") else np.asarray(kpts.xy)
        conf = kpts.conf.cpu().numpy() if hasattr(kpts.conf, "cpu") else np.asarray(kpts.conf)

        if len(xy) == 0:
            return None

        # Select person by largest bbox area.
        if result.boxes is not None and len(result.boxes) > 0:
            boxes = (
                result.boxes.xyxy.cpu().numpy()
                if hasattr(result.boxes.xyxy, "cpu")
                else np.asarray(result.boxes.xyxy)
            )
            areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            person_idx = int(np.argmax(areas))
        else:
            person_idx = 0

        person_xy = xy[person_idx]  # (17, 2)
        person_conf = conf[person_idx]  # (17,)

        h, w = bgr_frame.shape[:2]

        return self._build_pose_frame(person_xy, person_conf, w, h, idx, t_ms)

    def process_multi(self, bgr_frame: Frame) -> list[tuple[PoseFrame, Any]]:
        """Run pose and return ALL detected persons with their bounding boxes.

        Returns list of (PoseFrame, bbox_xyxy) tuples, sorted by bbox area
        descending. Used by the DeepSORT tracker for multi-person sessions.
        """
        if self._model is None:
            raise RuntimeError("YOLOPoseEstimator not opened")

        import numpy as np

        idx = self._frame_idx
        self._frame_idx += 1
        t_ms = (idx / self.fps) * 1000.0

        results = self._model(bgr_frame, conf=self._confidence, verbose=False)
        if not results or len(results) == 0:
            return []

        result = results[0]
        if result.keypoints is None or len(result.keypoints) == 0:
            return []

        kpts = result.keypoints
        xy = kpts.xy.cpu().numpy() if hasattr(kpts.xy, "cpu") else np.asarray(kpts.xy)
        conf = kpts.conf.cpu().numpy() if hasattr(kpts.conf, "cpu") else np.asarray(kpts.conf)

        boxes_raw = None
        if result.boxes is not None and len(result.boxes) > 0:
            boxes_raw = (
                result.boxes.xyxy.cpu().numpy()
                if hasattr(result.boxes.xyxy, "cpu")
                else np.asarray(result.boxes.xyxy)
            )

        h, w = bgr_frame.shape[:2]
        detections: list[tuple[PoseFrame, Any]] = []

        for i in range(len(xy)):
            pf = self._build_pose_frame(xy[i], conf[i], w, h, idx, t_ms)
            if pf is not None:
                bbox = boxes_raw[i] if boxes_raw is not None and i < len(boxes_raw) else None
                detections.append((pf, bbox))

        # Sort by bbox area descending.
        if boxes_raw is not None:
            detections.sort(
                key=lambda d: (
                    float((d[1][2] - d[1][0]) * (d[1][3] - d[1][1])) if d[1] is not None else 0
                ),
                reverse=True,
            )

        return detections[: self._num_poses]

    def _build_pose_frame(
        self,
        xy: Any,  # (17, 2)
        conf: Any,  # (17,)
        w: int,
        h: int,
        frame_idx: int,
        t_ms: float,
    ) -> PoseFrame | None:
        """Convert YOLO 17-keypoint detection → 33-slot PoseFrame."""
        # Build 33 landmarks, mapping COCO → MediaPipe indices.
        landmarks: list[Landmark] = []
        for mp_idx in range(NUM_POSE_LANDMARKS):
            coco_idx = MP_TO_COCO.get(mp_idx)
            if coco_idx is not None:
                px, py = float(xy[coco_idx][0]), float(xy[coco_idx][1])
                vis = float(conf[coco_idx])
                # Normalize to [0, 1].
                nx = px / w if w > 0 else 0.0
                ny = py / h if h > 0 else 0.0
                landmarks.append(Landmark(x=nx, y=ny, z=0.0, visibility=min(1.0, max(0.0, vis))))
            else:
                landmarks.append(Landmark(x=0.0, y=0.0, z=0.0, visibility=0.0))

        # Require at least shoulders + one wrist visible.
        key_indices = [11, 12, 15, 16]  # L/R shoulder, L/R wrist
        visible_key = sum(1 for i in key_indices if landmarks[i].visibility >= 0.3)
        if visible_key < 2:
            return None

        # YOLO doesn't provide world landmarks (no metric 3D), so we
        # estimate pseudo-world coords using shoulder width as scale ref.
        # This is approximate but gives the punch detector something to
        # work with for velocity calculations.
        world_lms = self._estimate_world_landmarks(landmarks)

        return PoseFrame(
            session_id=self.session_id,
            frame_index=frame_idx,
            t_ms=t_ms,
            landmarks=tuple(landmarks),
            world_landmarks=world_lms,
        )

    @staticmethod
    def _estimate_world_landmarks(
        landmarks: list[Landmark],
    ) -> tuple[WorldLandmark, ...] | None:
        """Estimate pseudo-world landmarks from normalized image coords.

        Uses shoulder width as a scale reference (~40 cm average). This is
        crude but lets the velocity-based punch detector work without
        real depth data. Returns None if shoulders aren't visible.
        """
        import math

        ls = landmarks[11]  # left shoulder
        rs = landmarks[12]  # right shoulder
        if ls.visibility < 0.3 or rs.visibility < 0.3:
            return None

        shoulder_px = math.hypot(ls.x - rs.x, ls.y - rs.y)
        if shoulder_px < 1e-4:
            return None

        # Assume ~0.40 m shoulder width.
        scale = 0.40 / shoulder_px
        # Hip center as origin (avg of hips, or avg of shoulders if hips absent).
        lh, rh = landmarks[23], landmarks[24]
        if lh.visibility >= 0.3 and rh.visibility >= 0.3:
            ox = (lh.x + rh.x) / 2.0
            oy = (lh.y + rh.y) / 2.0
        else:
            ox = (ls.x + rs.x) / 2.0
            oy = (ls.y + rs.y) / 2.0

        world: list[WorldLandmark] = []
        for lm in landmarks:
            wx = (lm.x - ox) * scale
            wy = (lm.y - oy) * scale
            wz = lm.z * scale  # z is 0 for most YOLO points
            world.append(WorldLandmark(x=wx, y=wy, z=wz, visibility=lm.visibility))

        return tuple(world)
