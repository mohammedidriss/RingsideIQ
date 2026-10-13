"""De-identified datasets (ADR-014): participant codes instead of names in the
dataset's files, an export that refuses to carry personal data, and withdrawing a
participant by code."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_api_protocol import (
    Clock,
    _block,
    _complete,
    _dataset,
    _folder,
    _imu_lines,
    _write_imu,
)


@pytest.fixture
def clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    """Takes in a temp folder on a faked take clock (as in test_api_protocol)."""
    from api.routes import capture_coord
    from api.services import dataset_store

    monkeypatch.setattr(dataset_store, "DATASETS_DIR", tmp_path / "datasets")
    c = Clock()
    monkeypatch.setattr(capture_coord, "timeline_now_ms", lambda _id: c.now)
    yield c


def _participant(client: TestClient, did: str, name: str, stance: str = "orthodox") -> str:
    fid = client.post("/fighters", json={"name": name, "stance": stance}).json()["id"]
    r = client.put(
        f"/v2/datasets/{did}/participants", json={"fighter_id": fid, "consent": "irb_signed"}
    )
    assert r.status_code == 200, r.text
    return fid


def _recorded_take(client: TestClient, clock: Clock, did: str, fid: str) -> str:
    r = client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": fid})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    _write_imu(tid, _imu_lines("left", 6, [1000, 2000]))
    _block(client, clock, tid, "jab", 0, 5000)
    _complete(client, tid, 6000)
    return tid


def _all_text(root: Path) -> str:
    return "\n".join(
        str(p.relative_to(root)) + "\n" + (p.read_text() if p.suffix in (".json", ".csv") else "")
        for p in root.rglob("*")
        if p.is_file()
    )


def test_participants_get_codes_and_the_take_files_carry_the_code_not_the_name(
    authed_client: TestClient, clock: Clock
) -> None:
    did = _dataset(authed_client)
    sami = _participant(authed_client, did, "Sami Haddad")
    _participant(authed_client, did, "Lina Khoury", "southpaw")
    people = authed_client.get(f"/v2/datasets/{did}").json()["participants"]
    assert [(p["code"], p["name"]) for p in people] == [
        ("F-0001", "Sami Haddad"),  # the researcher still sees the name…
        ("F-0002", "Lina Khoury"),
    ]
    tid = _recorded_take(authed_client, clock, did, sami)
    take = json.loads((_folder(tid) / "take.json").read_text())
    assert take["participant"] == "F-0001"  # …the take's files only the code
    assert "fighter" not in take and "fighter_id" not in take and "dataset" not in take
    assert "Sami" not in _all_text(_folder(tid))


def test_the_export_holds_no_name_and_refuses_one_that_would(
    authed_client: TestClient, clock: Clock
) -> None:
    did = _dataset(authed_client)
    tid = _recorded_take(authed_client, clock, did, _participant(authed_client, did, "Sami Haddad"))
    r = authed_client.post(f"/v2/datasets/{did}/export")
    assert r.status_code == 200, r.text
    export = Path(r.json()["path"]).parent
    assert "Sami Haddad" not in _all_text(export)
    assert (export / "takes" / tid / "take.json").exists()

    # A name sneaks into the labels (say, a reviewer's note) — the export is refused
    # and the previous one stays as it was.
    labels = _folder(tid) / "labels.json"
    data = json.loads(labels.read_text())
    data[0]["note"] = "checked with Sami Haddad"
    labels.write_text(json.dumps(data))
    before = (export / "manifest.json").read_text()
    r = authed_client.post(f"/v2/datasets/{did}/export")
    assert r.status_code == 409 and "personal data" in r.json()["detail"]
    assert (export / "manifest.json").read_text() == before
    assert not list(export.parent.glob("export.tmp-*"))  # nothing half-written left

    # So does a personal-data field, whatever it holds.
    data[0] = {**data[0], "note": "ok", "email": "x"}
    labels.write_text(json.dumps(data))
    r = authed_client.post(f"/v2/datasets/{did}/export")
    assert r.status_code == 409 and "email" in r.json()["detail"]


def test_withdrawing_a_participant_deletes_their_data_and_logs_counts_only(
    authed_client: TestClient, clock: Clock
) -> None:
    did = _dataset(authed_client)
    sami = _participant(authed_client, did, "Sami Haddad")
    lina = _participant(authed_client, did, "Lina Khoury", "southpaw")
    gone = [_recorded_take(authed_client, clock, did, sami) for _ in range(2)]
    kept = _recorded_take(authed_client, clock, did, lina)
    gone_folders = [_folder(t) for t in gone]
    export = Path(authed_client.post(f"/v2/datasets/{did}/export").json()["path"]).parent

    r = authed_client.post(f"/v2/datasets/{did}/participants/F-0001/withdraw")
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["participant"] == "F-0001" and entry["takes"] == 2
    assert entry["clips"] == 2 and entry["labels"] > 0 and entry["exported_takes"] == 2
    assert entry["trained_models"] == "unchanged"

    # Sami's takes are gone — folders, rows and their copies in the export…
    assert not any(f.exists() for f in gone_folders)
    d = authed_client.get(f"/v2/datasets/{did}").json()
    assert {t["id"] for t in d["takes"]} == {kept}
    manifest = json.loads((export / "manifest.json").read_text())
    assert [t["participant"] for t in manifest["takes"]] == ["F-0002"]
    assert all(s["take_id"] == kept for v in manifest["splits"].values() for s in v)
    assert not any((export / "takes" / t).exists() for t in gone)
    # …Lina's are untouched…
    assert _folder(kept).exists() and (export / "takes" / kept / "imu.csv").exists()
    # …Sami is withdrawn, so can't be recorded again; the code stays his.
    sami_row = next(p for p in d["participants"] if p["code"] == "F-0001")
    assert sami_row["consent"] == "withdrawn" and sami_row["may_record"] is False
    assert (
        authed_client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": sami}).status_code
        == 409
    )

    # The log names the code, the time and the counts — nothing else.
    log = authed_client.get(f"/v2/datasets/{did}/deletions").json()
    assert log == [entry]
    assert "Sami" not in json.dumps(log)


def test_withdrawing_an_unknown_code_changes_nothing(
    authed_client: TestClient, clock: Clock
) -> None:
    did = _dataset(authed_client)
    _participant(authed_client, did, "Sami Haddad")
    r = authed_client.post(f"/v2/datasets/{did}/participants/F-0099/withdraw")
    assert r.status_code == 404
    assert authed_client.get(f"/v2/datasets/{did}/deletions").json() == []


# --------------------------------------------------------------------------
# Face-blurred video in the export (ADR-014, step 2)
# --------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None, reason="needs ffmpeg"
)


@pytest.fixture
def blur(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Blurring runs inline, with a detector that always finds one face."""
    pytest.importorskip("cv2")
    from api.services import video_blur
    from tests.unit.test_face_blur import Fixed

    monkeypatch.setattr(video_blur, "BACKGROUND", False)
    monkeypatch.setattr(video_blur, "DETECTOR", lambda: Fixed([(0, 0, 100, 100)]))
    video_blur._status.clear()
    yield
    video_blur._status.clear()


def _take_with_video(client: TestClient, clock: Clock, did: str, fid: str) -> str:
    from tests.unit.test_face_blur import make_clip

    r = client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": fid})
    tid = r.json()["id"]
    _write_imu(tid, _imu_lines("left", 6, [1000, 2000]))
    _block(client, clock, tid, "jab", 0, 5000)
    make_clip(_folder(tid) / "video" / "cam1.mp4")
    r = client.post(f"/takes/{tid}/multicam/complete", json={"duration_ms": 6000})
    assert r.status_code == 200, r.text
    return tid


def _streams(path: Path) -> list[str]:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [s["codec_type"] for s in json.loads(out)["streams"]]


@needs_ffmpeg
def test_the_export_carries_video_only_as_its_face_blurred_copy(
    authed_client: TestClient, clock: Clock, blur: None
) -> None:
    did = _dataset(authed_client)
    tid = _take_with_video(
        authed_client, clock, did, _participant(authed_client, did, "Sami Haddad")
    )

    # First export: the clip isn't blurred yet — left out, never exported as
    # recorded — and the export starts blurring it.
    first = authed_client.post(f"/v2/datasets/{did}/export").json()
    take = first["takes"][0]
    assert take["video"] == [] and take["video_left_out"][0]["camera"] == "cam1"
    assert any("left out until they're face-blurred" in w for w in first["warnings"])
    export = Path(first["path"]).parent
    assert not list(export.rglob("*.mp4"))
    status = authed_client.get(f"/v2/datasets/{did}/blur").json()
    assert status["counts"] == {"ready": 1, "pending": 0, "failed": 0}

    # Export again: the blurred copy is in, silent, with its frame times.
    again = authed_client.post(f"/v2/datasets/{did}/export").json()
    video = again["takes"][0]["video"]
    assert [v["camera"] for v in video] == ["cam1"] and again["takes"][0]["video_left_out"] == []
    clip = export / "takes" / tid / video[0]["path"]
    assert _streams(clip) == ["video"]
    assert (export / "takes" / tid / video[0]["frame_times"]).read_text().startswith("frame,t_ms")
    assert video[0]["blur"]["frames"] == 30 and video[0]["blur"]["whole_frame"] == 0
    assert not any("face-blurred" in w for w in again["warnings"])


@needs_ffmpeg
def test_a_clip_that_cannot_be_blurred_is_left_out_and_named(
    authed_client: TestClient, clock: Clock, blur: None
) -> None:
    did = _dataset(authed_client)
    _recorded_take(authed_client, clock, did, _participant(authed_client, did, "Sami Haddad"))
    authed_client.post(f"/v2/datasets/{did}/export")  # starts blurring: the clip is broken
    status = authed_client.get(f"/v2/datasets/{did}/blur").json()
    assert status["counts"]["failed"] == 1
    m = authed_client.post(f"/v2/datasets/{did}/export").json()
    assert m["takes"][0]["video"] == []
    assert any("couldn't be face-blurred safely" in w for w in m["warnings"])


@needs_ffmpeg
def test_withdrawing_or_deleting_takes_removes_their_blurred_copies(
    authed_client: TestClient, clock: Clock, blur: None
) -> None:
    from api.services import video_blur

    did = _dataset(authed_client)
    sami = _participant(authed_client, did, "Sami Haddad")
    lina = _participant(authed_client, did, "Lina Khoury", "southpaw")
    gone = _take_with_video(authed_client, clock, did, sami)
    deleted = _take_with_video(authed_client, clock, did, lina)
    authed_client.post(f"/v2/datasets/{did}/export")
    export = Path(authed_client.post(f"/v2/datasets/{did}/export").json()["path"]).parent
    gone_blur = video_blur.take_blur_dir(_folder(gone))
    deleted_blur = video_blur.take_blur_dir(_folder(deleted))
    assert (gone_blur / "cam1.mp4").exists() and (deleted_blur / "cam1.mp4").exists()

    entry = authed_client.post(f"/v2/datasets/{did}/participants/F-0001/withdraw").json()
    assert entry["blurred_clips"] == 1 and not gone_blur.exists()

    assert authed_client.delete(f"/v2/takes/{deleted}").status_code == 204
    assert not deleted_blur.exists() and not (export / "takes" / deleted).exists()
    manifest = json.loads((export / "manifest.json").read_text())
    assert manifest["takes"] == []


@needs_ffmpeg
def test_trainings_export_does_not_start_blurring(
    authed_client: TestClient, clock: Clock, blur: None
) -> None:
    did = _dataset(authed_client)
    _take_with_video(authed_client, clock, did, _participant(authed_client, did, "Sami Haddad"))
    m = authed_client.post(f"/v2/datasets/{did}/export?blur=false").json()
    assert m["blur"]["counts"] == {"ready": 0, "pending": 1, "failed": 0}
    assert authed_client.get(f"/v2/datasets/{did}/blur").json()["running"] is False
