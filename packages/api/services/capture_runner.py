"""Capture runner — orchestrates a CV capture job in a background thread.

Lives in `api/` (the composition root) because it pulls together `capture`,
`analyze`, and `store`. Feature modules themselves never cross-import.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlmodel import Session as DBSession

from analyze import (
    ExtensionCyclePunchDetector,
    HeuristicPunchDetector,
    classify_punch_type,
    refine_peak_velocity,
)
from capture.cv import CapturePipeline, FileSource, WebcamSource
from capture.cv.overlay import draw_pose
from capture.cv.sources import FrameSource
from common import SessionClock, get_logger
from contracts import PoseFrame
from store import (
    DetectionSourceEnum,
    HandEnum,
    LeadOrRearEnum,
    PunchEventRepo,
    PunchEventRow,
    PunchTypeEnum,
    SessionRepo,
    SessionStatus,
    VelocitySourceEnum,
)

DBFactory = Callable[[], AbstractContextManager[DBSession]]

log = get_logger(__name__)

_active_jobs: dict[UUID, threading.Thread] = {}
_stop_events: dict[UUID, threading.Event] = {}
_pause_events: dict[UUID, threading.Event] = {}
_preview_frames: dict[UUID, bytes] = {}  # latest JPEG, written by capture thread
# Per-session T_0 reference. Other modalities (HRV BLE driver, IMU when wired)
# fetch this via clock_for(session_id) so every stream tags samples with
# offsets relative to the same instant. See common/time_utils.SessionClock.
_session_clocks: dict[UUID, SessionClock] = {}
_active_lock = threading.Lock()

_PREVIEW_MAX_WIDTH = 480
_PREVIEW_JPEG_QUALITY = 70


def latest_preview(session_id: UUID) -> bytes | None:
    with _active_lock:
        return _preview_frames.get(session_id)


def clock_for(session_id: UUID) -> SessionClock | None:
    """Return the SessionClock anchored when this session started capturing.

    Returns None if capture hasn't started or has already ended. Used by the
    HRV/IMU runners to convert their externally-timestamped samples into
    offsets relative to the same T_0 the CV pipeline uses.
    """
    with _active_lock:
        return _session_clocks.get(session_id)


def _data_dir() -> Path:
    p = Path("data/processed")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hand_to_enum(h: str) -> HandEnum:
    return HandEnum.LEFT if h == "left" else HandEnum.RIGHT


def _refined_peak_for_event(pose_history: list[PoseFrame], hand: str) -> float | None:
    """Build (t_ms, x, y, z) samples for the punching wrist and run sub-frame refinement."""
    if len(pose_history) < 4:
        return None
    wrist_idx = 15 if hand == "left" else 16
    use_world = all(f.world_landmarks is not None for f in pose_history)
    samples: list[tuple[float, float, float, float]] = []
    for f in pose_history:
        if use_world and f.world_landmarks is not None:
            wlm = f.world_landmarks[wrist_idx]
            if wlm.visibility < 0.4:
                continue
            samples.append((f.t_ms, wlm.x, wlm.y, wlm.z))
        else:
            lm = f.landmarks[wrist_idx]
            if lm.visibility < 0.4:
                continue
            # Image-plane mode: scale by an assumed body width so the result
            # is comparable to the detector's m/s number. Crude but matches
            # the legacy fallback path.
            samples.append((f.t_ms, lm.x * 0.45, lm.y * 0.45, lm.z * 0.45))
    if len(samples) < 4:
        return None
    return refine_peak_velocity(samples)


def is_running(session_id: UUID) -> bool:
    with _active_lock:
        t = _active_jobs.get(session_id)
        return t is not None and t.is_alive()


def request_stop(session_id: UUID) -> bool:
    """Signal a running capture to stop at the next frame boundary."""
    with _active_lock:
        ev = _stop_events.get(session_id)
        if ev is None:
            return False
        ev.set()
        # If we were paused, unpause first so the loop can see the stop flag
        # and exit cleanly instead of sleeping forever.
        pause = _pause_events.get(session_id)
        if pause is not None and not pause.is_set():
            pause.set()
        return True


def request_pause(session_id: UUID) -> bool:
    """Request the running capture to pause at the next frame boundary."""
    with _active_lock:
        ev = _pause_events.get(session_id)
        if ev is None:
            return False
        ev.clear()  # clear = paused (the run-loop blocks on .wait())
        return True


def request_resume(session_id: UUID) -> bool:
    """Resume a paused capture."""
    with _active_lock:
        ev = _pause_events.get(session_id)
        if ev is None:
            return False
        ev.set()  # set = running
        return True


def is_paused(session_id: UUID) -> bool:
    with _active_lock:
        ev = _pause_events.get(session_id)
        return ev is not None and not ev.is_set()


def _run_capture(
    session_id: UUID,
    source_kind: str,
    *,
    video_path: str | None,
    db_factory: DBFactory,
    max_frames: int | None,
    stop_event: threading.Event,
    pause_event: threading.Event,
    stance: str | None = None,
    camera_index: int = 0,
    pose_backend: str = "mediapipe",
) -> None:
    log.info(
        "capture.start",
        extra={
            "_ctx_session_id": str(session_id),
            "_ctx_source": source_kind,
            "_ctx_camera_index": camera_index,
        },
    )
    # Punch detector selection (ADR 009). The extension-cycle detector is the
    # default: on labeled ground truth it cut false positives from 84 to a
    # handful (precision 0.05 → 0.5) where the velocity-peak heuristic counted
    # every wrist blip. Set ALION_PUNCH_DETECTOR=heuristic to fall back.
    _detector_kind = os.environ.get("ALION_PUNCH_DETECTOR", "extension").strip().lower()
    detector: HeuristicPunchDetector | ExtensionCyclePunchDetector
    if _detector_kind == "heuristic":
        detector = HeuristicPunchDetector(stance=stance)
    else:
        detector = ExtensionCyclePunchDetector(stance=stance)
    buffered_events: list[PunchEventRow] = []
    # Rolling pose history feeds the punch-type classifier (~last 8 frames).
    pose_history: list[PoseFrame] = []
    history_len = 8
    # T_0 alignment: when the very first PoseFrame lands, reset
    # session.started_at to "now". Without this the dashboard timer
    # uses the *session-creation* time as T_0 and the round counter
    # is already several seconds in by the time the camera first
    # produces a frame.
    first_frame_seen = [False]

    # Gym Mode: optional "gloves up" gesture before detection starts.
    # Disabled by default — it silently swallowed every detection when
    # the fighter forgot the gesture (2026-05-09 bug). Re-enable per
    # session via env var if/when we want it back.
    import os as _os

    waiting_for_gesture = (
        source_kind == "live_webcam" and _os.environ.get("ALION_REQUIRE_GLOVES_UP") == "1"
    )
    gesture_frames = 0

    # Try to load custom ML model
    ml_model = None
    try:
        from pathlib import Path

        import joblib  # type: ignore[import-untyped, import-not-found, unused-ignore]

        model_path = Path("data/ml/punch_classifier_v1.pkl")
        if model_path.exists():
            ml_model = joblib.load(model_path)
            log.info("Loaded custom ML punch classifier.")
    except Exception as e:
        log.warning(f"Could not load ML classifier: {e}")

    def on_frame(pose: PoseFrame) -> None:
        nonlocal waiting_for_gesture, gesture_frames

        if waiting_for_gesture:
            if pose.landmarks:
                try:
                    nose = pose.landmarks[0]
                    l_sh = pose.landmarks[11]
                    r_sh = pose.landmarks[12]
                    l_wr = pose.landmarks[15]
                    r_wr = pose.landmarks[16]

                    if (
                        l_wr.y < l_sh.y
                        and r_wr.y < r_sh.y
                        and abs(l_wr.x - r_wr.x) < 0.2
                        and abs(l_wr.y - nose.y) < 0.25
                        and abs(r_wr.y - nose.y) < 0.25
                    ):
                        gesture_frames += 1
                        if gesture_frames > 20:  # roughly ~0.6 seconds at 30fps
                            waiting_for_gesture = False
                            with _active_lock:
                                _session_clocks[session_id] = SessionClock.start()
                            log.info(
                                "capture.gesture_detected",
                                extra={"_ctx_session_id": str(session_id)},
                            )
                    else:
                        gesture_frames = 0
                except IndexError:
                    pass
            if waiting_for_gesture:
                return

        # First frame after capture actually started: reset T_0 on the
        # session row so the dashboard timer ticks from 0 when the
        # camera shows its first usable frame, not from session-
        # creation time.
        if not first_frame_seen[0]:
            first_frame_seen[0] = True
            from datetime import UTC, datetime

            try:
                with db_factory() as db:
                    row = SessionRepo(db).get(session_id)
                    if row is not None:
                        row.started_at = datetime.now(UTC)
                        db.add(row)
                        db.commit()
            except Exception:
                # Non-fatal — the capture loop continues either way.
                pass

        pose_history.append(pose)
        if len(pose_history) > history_len:
            pose_history.pop(0)
        for ev in detector.feed(pose):
            ptype = None
            detected_by = ev.detected_by

            if ml_model is not None and pose.world_landmarks:
                try:
                    import pandas as pd  # type: ignore[import-untyped]

                    ls = pose.world_landmarks[11]
                    rs = pose.world_landmarks[12]
                    lw = pose.world_landmarks[15]
                    rw = pose.world_landmarks[16]
                    is_left_hand = 1 if ev.hand == "left" else 0

                    features = pd.DataFrame(
                        [
                            {
                                "velocity": ev.velocity_ms,
                                "is_left_hand": is_left_hand,
                                "ls_x": ls.x,
                                "ls_y": ls.y,
                                "ls_z": ls.z,
                                "rs_x": rs.x,
                                "rs_y": rs.y,
                                "rs_z": rs.z,
                                "lw_x": lw.x,
                                "lw_y": lw.y,
                                "lw_z": lw.z,
                                "rw_x": rw.x,
                                "rw_y": rw.y,
                                "rw_z": rw.z,
                            }
                        ]
                    )

                    ptype = ml_model.predict(features)[0].lower()
                    detected_by = "custom_ml"
                except Exception as e:
                    log.error(f"ML classification failed: {e}")

            if not ptype:
                ptype = classify_punch_type(pose_history, ev.hand, stance)

            # Refine the velocity using sub-frame interpolation across the
            # recent pose history. We pull the wrist's world-coord trajectory
            # (or image-plane fallback) and feed it to the refiner. If we get
            # a higher peak than the detector reported, we use the refined
            # value; otherwise stick with the detector's value.
            refined_v = _refined_peak_for_event(pose_history, ev.hand)
            final_v = max(ev.velocity_ms, refined_v) if refined_v is not None else ev.velocity_ms
            buffered_events.append(
                PunchEventRow(
                    session_id=ev.session_id,
                    t_ms=ev.t_ms,
                    hand=_hand_to_enum(ev.hand),
                    lead_or_rear=LeadOrRearEnum(ev.lead_or_rear) if ev.lead_or_rear else None,
                    velocity_ms=round(final_v, 2),
                    velocity_source=VelocitySourceEnum(ev.velocity_source),
                    punch_type=PunchTypeEnum(ptype) if ptype else None,
                    detected_by=DetectionSourceEnum(detected_by),
                    confidence=ev.confidence,
                )
            )

    def on_raw_frame(raw: Any, pose: PoseFrame | None) -> None:
        """Encode a downscaled BGR frame with skeleton overlay for the preview MJPEG stream."""
        import cv2

        # Copy so we don't mutate the source frame the pipeline still cares about.
        frame = raw.copy()
        h, w = frame.shape[:2]
        if w > _PREVIEW_MAX_WIDTH:
            scale = _PREVIEW_MAX_WIDTH / w
            frame = cv2.resize(frame, (_PREVIEW_MAX_WIDTH, int(h * scale)))
            # Landmark coords are in normalized [0,1], so they still map
            # correctly to the resized frame — no adjustment needed in draw_pose.
        draw_pose(frame, pose)
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), _PREVIEW_JPEG_QUALITY]
        )
        if not ok:
            return
        with _active_lock:
            _preview_frames[session_id] = buf.tobytes()

    parquet_path = _data_dir() / f"{session_id}.pose.parquet"
    source: FrameSource
    if source_kind == "live_webcam":
        source = WebcamSource(index=camera_index)
    elif source_kind == "uploaded_video":
        if not video_path:
            raise ValueError("uploaded_video requires video_path")
        source = FileSource(video_path)
    else:
        raise ValueError(f"unsupported source: {source_kind}")

    try:
        with db_factory() as db:
            SessionRepo(db).update_status(session_id, SessionStatus.CAPTURING)

        # If we are not waiting for a gesture, start clock immediately.
        # Otherwise, the gesture detector will start it.
        if not waiting_for_gesture:
            with _active_lock:
                _session_clocks[session_id] = SessionClock.start()

        # The pipeline checks `should_stop` each frame; we use it as a
        # combined "block-while-paused, return-true-to-quit" signal so the
        # capture thread can pause without spinning.
        def should_stop_or_pause() -> bool:
            # Block here while paused. Returns immediately if the pause
            # event is set (running) or wakes up when set/closed.
            pause_event.wait()
            return stop_event.is_set()

        pipeline = CapturePipeline(
            session_id=session_id,
            source=source,
            parquet_path=parquet_path,
            on_frame=on_frame,
            on_raw_frame=on_raw_frame,
            max_frames=max_frames,
            should_stop=should_stop_or_pause,
            pose_backend=pose_backend,  # type: ignore[arg-type]
        )
        result = pipeline.run()

        with db_factory() as db:
            PunchEventRepo(db).add_many(buffered_events)
            SessionRepo(db).attach_artifacts(
                session_id,
                pose_parquet_path=str(result.parquet_path),
                frame_count=result.frame_count,
                duration_ms=result.duration_ms,
            )
            SessionRepo(db).update_status(session_id, SessionStatus.COMPLETED, end=True)

        # Summarise near-misses so we can see WHY rejected peaks didn't fire.
        # Tuning the heuristic without this is guesswork.
        nm_counts: dict[str, int] = {}
        for nm in detector.near_misses:
            r = str(nm["reason"])
            nm_counts[r] = nm_counts.get(r, 0) + 1
        log.info(
            "capture.done",
            extra={
                "_ctx_session_id": str(session_id),
                "_ctx_frames": result.frame_count,
                "_ctx_punches": len(buffered_events),
                "_ctx_near_misses": len(detector.near_misses),
                "_ctx_near_miss_breakdown": nm_counts,
            },
        )
    except ModuleNotFoundError as e:
        # CV deps not installed (typical in the default Docker image — MediaPipe
        # has no linux/aarch64 wheel). Map to a user-friendly reason.
        reason = (
            "Video capture isn't available on this server. "
            "MediaPipe and OpenCV aren't installed in this environment "
            "(likely the default Docker image). Run capture on the host with "
            "`uv run python scripts/record_live.py --fighter <id>` instead."
        )
        log.exception(
            "capture.failed (cv unavailable): %s", e, extra={"_ctx_session_id": str(session_id)}
        )
        with db_factory() as db:
            SessionRepo(db).update_status(
                session_id, SessionStatus.FAILED, end=True, failure_reason=reason
            )
    except Exception as e:
        log.exception("capture.failed: %s", e, extra={"_ctx_session_id": str(session_id)})
        # Translate the most common macOS-permission failure into
        # actionable instructions instead of the cryptic OpenCV error.
        msg = str(e)
        import sys as _sys

        if "cannot open webcam" in msg.lower() and _sys.platform == "darwin":
            msg = (
                "Camera permission denied. macOS hasn't granted camera "
                "access to the terminal that launched the API. Open "
                "System Settings → Privacy & Security → Camera and "
                "enable access for Terminal (or iTerm/Warp/whatever you "
                "use), then restart the API. Original error: " + msg
            )
        with db_factory() as db:
            SessionRepo(db).update_status(
                session_id, SessionStatus.FAILED, end=True, failure_reason=msg
            )
    finally:
        with _active_lock:
            _active_jobs.pop(session_id, None)
            _stop_events.pop(session_id, None)
            _pause_events.pop(session_id, None)
            _preview_frames.pop(session_id, None)
            _session_clocks.pop(session_id, None)


def start_capture(
    session_id: UUID,
    source_kind: str,
    db_factory: DBFactory,
    *,
    video_path: str | None = None,
    max_frames: int | None = None,
    stance: str | None = None,
    camera_index: int = 0,
    pose_backend: str = "mediapipe",
) -> bool:
    """Spawn the capture in a background thread. Returns False if already running."""
    with _active_lock:
        if session_id in _active_jobs and _active_jobs[session_id].is_alive():
            return False
        stop_event = threading.Event()
        pause_event = threading.Event()
        pause_event.set()  # set = running; clear = paused (the loop blocks on .wait())
        _stop_events[session_id] = stop_event
        _pause_events[session_id] = pause_event
        t = threading.Thread(
            target=_run_capture,
            args=(session_id, source_kind),
            kwargs={
                "video_path": video_path,
                "db_factory": db_factory,
                "max_frames": max_frames,
                "stop_event": stop_event,
                "pause_event": pause_event,
                "stance": stance,
                "camera_index": camera_index,
                "pose_backend": pose_backend,
            },
            daemon=True,
            name=f"capture-{session_id}",
        )
        _active_jobs[session_id] = t
        t.start()
    return True


def run_capture_sync(
    db: DBSession,
    session_id: UUID,
    source_kind: str,
    *,
    video_path: str | None = None,
    max_frames: int | None = None,
) -> None:
    """Synchronous variant — used by CLI scripts where blocking is fine."""
    from collections.abc import Iterator
    from contextlib import contextmanager

    @contextmanager
    def factory() -> Iterator[DBSession]:
        yield db

    pause = threading.Event()
    pause.set()  # never paused in sync mode
    _run_capture(
        session_id,
        source_kind,
        video_path=video_path,
        db_factory=factory,
        max_frames=max_frames,
        stop_event=threading.Event(),
        pause_event=pause,
    )
