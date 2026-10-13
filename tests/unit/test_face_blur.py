"""Face blurring for exported video (capture.cv.face_blur, ADR-014): every face
the pose or the detector finds is pixelated, a frame with no face found is
pixelated whole, and the copy keeps no sound and no metadata."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def make_clip(path: Path, seconds: float = 1.0) -> Path:
    """A detailed test picture, with a tone and a name in its metadata — what a
    phone clip carries that the blurred copy must not."""
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-vf", "noise=alls=40:allf=t",  # detail everywhere, so blurring shows
         "-metadata", "title=Sami Haddad", "-metadata", "comment=gym, 5th street",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip
    return path


class Fixed:
    """A detector that always finds the same faces."""

    def __init__(self, boxes: list[tuple[int, int, int, int]]) -> None:
        self.boxes = boxes

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        return list(self.boxes)


def detail(img: np.ndarray) -> float:
    return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def first_frame(path: Path) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    assert ok
    return frame


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format_tags",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


@needs_ffmpeg
def test_detected_faces_are_pixelated_and_the_copy_has_no_sound_or_metadata(
    tmp_path: Path,
) -> None:
    from capture.cv.face_blur import blur_clip

    src = make_clip(tmp_path / "cam.mp4")
    original = first_frame(src)
    out = tmp_path / "out" / "cam.mp4"
    stats = blur_clip(src, out, offset_ms=500.0, pose=None, detector=Fixed([(0, 0, 160, 240)]))

    frame = first_frame(out)
    assert detail(frame[:, :160]) < 0.1 * detail(original[:, :160])  # the "face": pixelated
    assert detail(frame[:, 170:]) > 0.5 * detail(original[:, 170:])  # the rest: untouched
    info = probe(out)
    assert [s["codec_type"] for s in info["streams"]] == ["video"]  # no sound
    tags = info.get("format", {}).get("tags", {})
    assert "title" not in tags and "comment" not in tags and "encoder" not in tags
    assert stats.frames == 30 and stats.face_from_detector == 30 and stats.whole_frame == 0
    times = out.with_suffix(".frames.csv").read_text().splitlines()
    assert times[0] == "frame,t_ms" and len(times) == 31
    assert float(times[1].split(",")[1]) == pytest.approx(500.0, abs=1)  # on the take timeline


@needs_ffmpeg
def test_a_frame_with_no_face_found_is_pixelated_whole(tmp_path: Path) -> None:
    from capture.cv.face_blur import blur_clip

    src = make_clip(tmp_path / "cam.mp4")
    original = first_frame(src)
    out = tmp_path / "cam_blurred.mp4"
    stats = blur_clip(src, out, offset_ms=0.0, pose=None, detector=Fixed([]))
    assert stats.whole_frame == stats.frames == 30
    assert detail(first_frame(out)) < 0.1 * detail(original)


@needs_ffmpeg
def test_the_pose_face_box_is_blurred_too(tmp_path: Path) -> None:
    from capture.cv.face_blur import blur_clip

    src = make_clip(tmp_path / "cam.mp4")
    original = first_frame(src)
    # A pose frame every 33 ms with the face points around (0.25, 0.3) of the
    # picture and shoulders below them.
    t = np.arange(0.0, 1100.0, 33.0)
    pts = np.zeros((t.size, 13, 3))
    face = [
        (0.25 + dx, 0.3 + dy)
        for dx, dy in [
            (0, 0),
            (-0.03, -0.03),
            (0.03, -0.03),
            (-0.06, 0),
            (0.06, 0),
            (-0.02, 0.05),
            (0.02, 0.05),
        ]
    ]
    for i, (x, y) in enumerate(face):
        pts[:, i] = (x, y, 0.9)
    pts[:, 11], pts[:, 12] = (0.12, 0.55, 0.9), (0.38, 0.55, 0.9)
    out = tmp_path / "cam_blurred.mp4"
    stats = blur_clip(
        src, out, offset_ms=0.0, pose=(t, pts), detector=Fixed([]), check=tmp_path / "check.jpg"
    )
    assert stats.face_from_pose == stats.frames and stats.whole_frame == 0
    frame = first_frame(out)
    fx, fy = int(0.25 * 320), int(0.3 * 240)
    assert detail(frame[fy - 15 : fy + 15, fx - 15 : fx + 15]) < 0.1 * detail(
        original[fy - 15 : fy + 15, fx - 15 : fx + 15]
    )
    assert detail(frame[:, 200:]) > 0.5 * detail(original[:, 200:])
    assert (tmp_path / "check.jpg").exists()


def test_the_pose_box_reaches_below_the_chin_and_keeps_its_size_side_on() -> None:
    from capture.cv.face_blur import pose_face_box

    pts = np.zeros((13, 3))
    pts[0] = (0.5, 0.3, 0.9)  # nose
    pts[2], pts[5] = (0.48, 0.28, 0.9), (0.52, 0.28, 0.9)  # eyes
    pts[9], pts[10] = (0.49, 0.33, 0.9), (0.51, 0.33, 0.9)  # mouth
    pts[11], pts[12] = (0.4, 0.5, 0.9), (0.6, 0.5, 0.9)  # shoulders
    box = pose_face_box(pts, 1000, 1000)
    assert box is not None
    x0, y0, x1, y1 = box
    assert y0 <= 0.25 * 1000  # above the eyes (the forehead)
    assert y1 >= 0.38 * 1000  # past the chin (between mouth and shoulders)
    assert x1 - x0 >= 0.15 * 1000  # head-sized, from the shoulders, though the points are close
    pts[:9, 2] = (
        0.1  # face points not visible: no box (the detector or the fail-closed rule covers it)
    )
    pts[9:11, 2] = 0.1
    assert pose_face_box(pts, 1000, 1000) is None


@needs_ffmpeg
def test_a_clip_that_cannot_be_read_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    from capture.cv.face_blur import BlurError, blur_clip

    src = tmp_path / "broken.webm"
    src.write_bytes(b"\x1a\x45\xdf\xa3not a video")
    out = tmp_path / "out.mp4"
    with pytest.raises(BlurError):
        blur_clip(src, out, offset_ms=0.0, pose=None, detector=Fixed([]))
    assert not out.exists() and not list(tmp_path.glob("out.mp4*"))
