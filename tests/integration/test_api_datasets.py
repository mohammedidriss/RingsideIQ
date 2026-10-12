"""Datasets (ADR-013) — consent, takes, and capture into the take folder.

A take is recorded with the session engines but must never write to session
storage: its clips, pose, IMU and HR land in data/datasets/…/takes/{id}/.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from tests.integration.test_api_imu_ble import FAKE_RECORDER


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    from api.routes import capture_coord
    from api.services import dataset_store

    paths = {"datasets": tmp_path / "datasets", "sessions": tmp_path / "session_clips"}
    monkeypatch.setattr(dataset_store, "DATASETS_DIR", paths["datasets"])
    monkeypatch.setattr(capture_coord, "_VIDEO_DIR", paths["sessions"])
    yield paths


def _fighter(client: TestClient, name: str, **extra: object) -> str:
    return client.post("/fighters", json={"name": name, **extra}).json()["id"]


def _dataset(client: TestClient, name: str = "RQ2 punches") -> str:
    r = client.post("/v2/datasets", json={"name": name, "protocol": "rq2-v1"})
    assert r.status_code == 201
    return r.json()["id"]


def _participant(client: TestClient, did: str, fid: str, consent: str) -> object:
    return client.put(
        f"/v2/datasets/{did}/participants", json={"fighter_id": fid, "consent": consent}
    )


def _take(client: TestClient, did: str, fid: str) -> str:
    r = client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": fid})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _start_cameras(client: TestClient, tid: str) -> None:
    """A camera joins and the coach presses Start all cameras."""
    token = client.post(f"/takes/{tid}/multicam/join-info").json()["join_token"]
    client.post(f"/takes/{tid}/multicam/register", json={"token": token, "label": "laptop"})
    assert client.post(f"/takes/{tid}/multicam/start").status_code == 200


def test_takes_need_consent_and_only_one_researcher_records_himself(
    authed_client: TestClient, stores: dict[str, Path]
) -> None:
    did = _dataset(authed_client)
    me, other, third = (_fighter(authed_client, n) for n in ("Mohamad", "Ali", "Sam"))

    # not a participant yet
    assert (
        authed_client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": me}).status_code == 409
    )
    # pending consent → can't record
    _participant(authed_client, did, other, "pending")
    r = authed_client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": other})
    assert r.status_code == 409 and "consent" in r.json()["detail"]
    # the researcher records himself; a second "self" is refused
    assert _participant(authed_client, did, me, "self").status_code == 200
    assert _participant(authed_client, did, third, "self").status_code == 409
    # IRB signed → can record
    _participant(authed_client, did, other, "irb_signed")
    _start_cameras(authed_client, _take(authed_client, did, me))
    _start_cameras(authed_client, _take(authed_client, did, other))

    body = authed_client.get(f"/v2/datasets/{did}").json()
    consent = {p["name"]: (p["consent"], p["may_record"]) for p in body["participants"]}
    assert consent == {"Mohamad": ("self", True), "Ali": ("irb_signed", True)}
    assert len(body["takes"]) == 2
    listing = authed_client.get("/v2/datasets").json()
    assert listing[0]["participants"] == 2 and listing[0]["takes"] == 2


def test_record_take_adds_nothing_until_the_cameras_start(
    authed_client: TestClient, stores: dict[str, Path]
) -> None:
    """Opening "Record dataset session" is a draft the phones and sensors connect to; it joins
    the dataset only when Start all cameras is pressed."""
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad")
    _participant(authed_client, did, fid, "self")

    tid = _take(authed_client, did, fid)
    take = authed_client.get(f"/v2/takes/{tid}").json()
    assert take["status"] == "recording" and take["started"] is False
    assert authed_client.get(f"/v2/datasets/{did}").json()["takes"] == []
    assert authed_client.get("/v2/datasets").json()[0]["takes"] == 0
    # leaving and pressing Record dataset session again reopens the same draft — nothing piles up
    assert _take(authed_client, did, fid) == tid
    assert len(list((stores["datasets"] / did / "takes").iterdir())) == 1

    _start_cameras(authed_client, tid)
    take = authed_client.get(f"/v2/takes/{tid}").json()
    assert take["started"] is True
    listed = authed_client.get(f"/v2/datasets/{did}").json()["takes"]
    assert [t["id"] for t in listed] == [tid]
    assert authed_client.get("/v2/datasets").json()[0]["takes"] == 1
    # the next Record dataset session is a new take
    assert _take(authed_client, did, fid) != tid


def test_a_stopped_take_shows_uploading_until_it_is_saved(
    authed_client: TestClient, stores: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After Stop & save the cameras are still sending their video (a 12-minute clip
    is a few hundred MB): the take says "uploading", not "recording", until then."""
    from uuid import UUID

    from api.routes import capture_coord

    monkeypatch.setattr(capture_coord, "_STATE_DIR", stores["datasets"].parent / "capture")
    monkeypatch.setattr(capture_coord, "_START_DELAY_MS", 0)  # no 3-2-1 countdown here
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad")
    _participant(authed_client, did, fid, "self")
    tid = _take(authed_client, did, fid)
    token = authed_client.post(f"/takes/{tid}/multicam/join-info").json()["join_token"]
    devs = [
        authed_client.post(
            f"/takes/{tid}/multicam/register", json={"token": token, "label": n}
        ).json()["device_id"]
        for n in ("laptop", "iphone")
    ]
    authed_client.post(f"/takes/{tid}/multicam/start")
    assert authed_client.get(f"/v2/takes/{tid}").json()["status"] == "recording"
    time.sleep(0.05)

    authed_client.post(f"/takes/{tid}/multicam/stop")
    assert authed_client.get(f"/v2/takes/{tid}").json()["status"] == "uploading"
    listed = authed_client.get(f"/v2/datasets/{did}").json()["takes"]
    assert [t["status"] for t in listed] == ["uploading"]

    def upload(dev: str) -> None:
        r = authed_client.post(
            f"/takes/{tid}/multicam/upload",
            data={"token": token, "device_id": dev},
            files={"file": ("c.webm", b"\x1a\x45\xdf\xa3" + b"x" * 100, "video/webm")},
        )
        assert r.status_code == 200

    upload(devs[0])
    folder = stores["datasets"] / did / "takes" / tid
    # written under a temporary name, renamed only when complete
    assert sorted(p.name for p in (folder / "video").iterdir()) == [
        f"{devs[0]}.json",
        f"{devs[0]}.webm",
    ]

    # the API restarts before the coach's page finishes the save (page reloaded):
    # Start's t = 0 survives, and the length comes from Start → Stop
    capture_coord._coords.clear()
    assert capture_coord.last_started_at_ms(UUID(tid)) is not None
    done = authed_client.post(f"/takes/{tid}/multicam/complete", json={})
    assert done.status_code == 200
    take = authed_client.get(f"/v2/takes/{tid}").json()
    assert take["status"] == "completed" and take["duration_ms"] > 0
    meta = json.loads((folder / "take.json").read_text())
    assert meta["t0_ms"] is not None and len(meta["devices"]) == 1

    # the phone's clip lands after the save: it joins the take's clip list
    upload(devs[1])
    meta = json.loads((folder / "take.json").read_text())
    assert sorted(d["device_id"] for d in meta["devices"]) == sorted(devs)


def test_one_block_per_take_is_checked_and_counted(
    authed_client: TestClient, stores: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each protocol block is its own short take; right after it's saved, a check
    says whether it's good enough to move on — so a mistake costs one block."""
    from api.routes import capture_coord

    monkeypatch.setattr(capture_coord, "_STATE_DIR", stores["datasets"].parent / "capture")
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad", stance="orthodox")
    _participant(authed_client, did, fid, "self")
    bad = authed_client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": fid, "block": "nope"})
    assert bad.status_code == 422

    jab = authed_client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": fid, "block": "jab"})
    tid = jab.json()["id"]
    assert jab.json()["block"] == "jab"
    # a draft is reused for the same block only
    again = authed_client.post(
        f"/v2/datasets/{did}/takes", json={"fighter_id": fid, "block": "jab"}
    )
    cross = authed_client.post(
        f"/v2/datasets/{did}/takes", json={"fighter_id": fid, "block": "cross"}
    )
    assert again.json()["id"] == tid and cross.json()["id"] != tid

    token = authed_client.post(f"/takes/{tid}/multicam/join-info").json()["join_token"]
    devs = {}
    for name in ("laptop", "Samsung"):
        dev = authed_client.post(
            f"/takes/{tid}/multicam/register", json={"token": token, "label": name}
        ).json()["device_id"]
        devs[name] = dev
    authed_client.post(f"/takes/{tid}/multicam/start")
    for name, dev in devs.items():
        authed_client.post(
            f"/takes/{tid}/multicam/heartbeat",
            json={"token": token, "device_id": dev, "status": "recording", "label": name},
        )
    assert authed_client.get(f"/v2/takes/{tid}/check").json() == {
        "ready": False,
        "ok": False,
        "checks": [],
    }
    authed_client.post(f"/takes/{tid}/multicam/stop")

    folder = stores["datasets"] / did / "takes" / tid
    lines = [dataset_store_imu_header()]
    lines += [f"{i * 10},{h},0,0,1,0,0,0" for i in range(500) for h in ("left", "right")]
    (folder / "imu.csv").write_text("\n".join(lines) + "\n")
    (folder / "hr.csv").write_text("t_ms,rr_ms,hr_bpm\n100,800,75\n900,800,75\n")
    protocol = {
        "blocks": [
            {"key": "jab", "t_start_ms": 0, "t_end_ms": 47000, "detected": 30, "off_hand": 0}
        ]
    }
    (folder / "protocol.json").write_text(json.dumps(protocol))

    def upload(dev: str) -> None:
        authed_client.post(
            f"/takes/{tid}/multicam/upload",
            data={"token": token, "device_id": dev},
            files={"file": ("c.webm", b"\x1a\x45\xdf\xa3clip", "video/webm")},
        )

    upload(devs["laptop"])
    assert authed_client.post(f"/takes/{tid}/multicam/complete", json={}).status_code == 200
    check = authed_client.get(f"/v2/takes/{tid}/check").json()
    by = {c["key"]: c for c in check["checks"]}
    assert check["ready"] and not check["ok"]
    assert by["video"]["level"] == "fail" and "Samsung" in by["video"]["text"]
    assert by["wrists"]["level"] == "ok" and by["punches"]["text"] == "Jab: 30/30 punches"
    assert by["heart"]["level"] == "ok"

    upload(devs["Samsung"])  # the phone's clip lands late — the block is good now
    assert authed_client.get(f"/v2/takes/{tid}/check").json()["ok"] is True

    # sensors on the wrong wrists: the punches land on the other hand
    protocol["blocks"][0].update(detected=0, off_hand=29)
    (folder / "protocol.json").write_text(json.dumps(protocol))
    check = authed_client.get(f"/v2/takes/{tid}/check").json()
    assert not check["ok"]
    assert "swapped" in next(c for c in check["checks"] if c["key"] == "punches")["text"]

    # the participant's coverage: jab recorded once
    me = authed_client.get(f"/v2/datasets/{did}").json()["participants"][0]
    assert me["blocks"] == {"jab": 1}


def dataset_store_imu_header() -> str:
    from api.services import dataset_store

    return dataset_store.IMU_HEADER


def test_take_capture_lands_in_the_take_folder_never_in_session_storage(
    authed_client: TestClient, stores: dict[str, Path]
) -> None:
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad", stance="orthodox")
    _participant(authed_client, did, fid, "self")
    tid = _take(authed_client, did, fid)
    folder = stores["datasets"] / did / "takes" / tid
    assert json.loads((folder / "take.json").read_text())["stance"] == "orthodox"

    join = authed_client.post(f"/takes/{tid}/multicam/join-info").json()
    assert join["join_path"].startswith(f"/takes/{tid}/camera?token=")
    token = join["join_token"]
    dev = authed_client.post(
        f"/takes/{tid}/multicam/register", json={"token": token, "role": "camera", "label": "front"}
    ).json()["device_id"]
    assert authed_client.post(f"/takes/{tid}/multicam/start").status_code == 200
    from uuid import UUID

    from api.routes import capture_coord

    assert capture_coord.timeline_now_ms(UUID(tid)) is None  # 3 s countdown not over yet
    # takes can't be paused — a node would apply it up to a heartbeat late
    assert authed_client.post(f"/takes/{tid}/multicam/pause").status_code == 409
    authed_client.post(f"/takes/{tid}/multicam/stop")

    # nothing saved yet → can't complete
    assert authed_client.post(f"/takes/{tid}/multicam/complete", json={}).status_code == 409
    r = authed_client.post(
        f"/takes/{tid}/multicam/upload",
        data={"token": token, "device_id": dev, "start_offset_ms": "12.5"},
        files={"file": ("clip.webm", b"\x1a\x45\xdf\xa3fake-clip", "video/webm")},
    )
    assert r.status_code == 200
    assert (folder / "video" / f"{dev}.webm").exists()
    assert not stores["sessions"].exists() or not any(stores["sessions"].iterdir())

    clips = authed_client.get(f"/takes/{tid}/multicam/clips").json()
    assert clips == [{"device_id": dev, "ext": "webm", "bytes": 13, "start_offset_ms": 12.5}]
    assert authed_client.get(f"/takes/{tid}/multicam/clip/{dev}").content.startswith(b"\x1a\x45")

    done = authed_client.post(f"/takes/{tid}/multicam/complete", json={"duration_ms": 61_000})
    assert done.status_code == 200
    meta = json.loads((folder / "take.json").read_text())
    assert meta["status"] == "completed" and meta["duration_ms"] == 61_000
    assert meta["t0_ms"] is not None
    assert meta["devices"][0]["start_offset_ms"] == 12.5
    take = authed_client.get(f"/v2/takes/{tid}").json()
    assert take["status"] == "completed" and take["data"]["clips"][0]["device_id"] == dev
    # a finished take can't be re-joined; record a new take instead
    assert authed_client.post(f"/takes/{tid}/multicam/join-info").status_code == 409


def test_take_wrist_data_goes_to_imu_csv_not_the_session_table(
    authed_client: TestClient,
    session: Session,
    stores: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import imu_devices, imu_runner
    from store import IMUSampleRow

    devices = tmp_path / "devices.json"
    monkeypatch.setattr(imu_devices, "DEVICES_FILE", devices)
    fake = tmp_path / "fake_recorder.py"
    fake.write_text(FAKE_RECORDER)
    monkeypatch.setattr(imu_runner, "RECORDER_CMD", [sys.executable, str(fake)])

    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad")
    devices.write_text(
        json.dumps(
            {
                "AAA": {"wrist": "left", "fighter_id": fid},
                "BBB": {"wrist": "right", "fighter_id": fid},
            }
        )
    )
    _participant(authed_client, did, fid, "self")
    tid = _take(authed_client, did, fid)

    assert authed_client.post(f"/v2/takes/{tid}/imu/ble/start").json()["running"]
    deadline = time.time() + 10
    while time.time() < deadline:
        st = authed_client.get(f"/v2/takes/{tid}/imu/ble/status").json()
        if all(u["samples"] >= 250 for u in st["units"].values()):
            break
        time.sleep(0.1)
    authed_client.post(f"/v2/takes/{tid}/imu/ble/stop")

    lines = (stores["datasets"] / did / "takes" / tid / "imu.csv").read_text().splitlines()
    assert lines[0] == "t_ms,hand,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps"
    rows = [ln.split(",") for ln in lines[1:]]
    assert len(rows) >= 500 and {r[1] for r in rows} == {"left", "right"}
    assert all(float(r[0]) >= 0 for r in rows)
    assert session.exec(select(IMUSampleRow)).first() is None  # nothing in session storage
    assert authed_client.get(f"/v2/takes/{tid}").json()["data"]["imu_rows"] == len(rows)


def test_take_live_reader_reads_heart_rate_from_the_take(
    authed_client: TestClient, stores: dict[str, Path]
) -> None:
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad", dob="1990-01-01")
    _participant(authed_client, did, fid, "self")
    tid = _take(authed_client, did, fid)
    hr = stores["datasets"] / did / "takes" / tid / "hr.csv"
    hr.write_text("t_ms,rr_ms,hr_bpm\n" + "".join(f"{i * 500},500,{120 + i}\n" for i in range(70)))

    body = authed_client.get(f"/v2/takes/{tid}/live").json()
    assert body["heart"]["bpm_trace"] == [float(120 + i) for i in range(10, 70)]
    assert body["heart"]["bpm"] == 189.0 and body["heart"]["zone"] is not None
    assert body["imu"]["running"] is False


def test_discarded_take_is_excluded_but_kept_on_disk(
    authed_client: TestClient, stores: dict[str, Path]
) -> None:
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad")
    _participant(authed_client, did, fid, "self")
    tid = _take(authed_client, did, fid)
    assert authed_client.post(f"/v2/takes/{tid}/discard").json()["status"] == "discarded"
    folder = stores["datasets"] / did / "takes" / tid
    assert json.loads((folder / "take.json").read_text())["status"] == "discarded"
    assert authed_client.get("/v2/datasets").json()[0]["takes"] == 0


def test_a_new_take_takes_the_wrist_sensors_over_and_discard_releases_them(
    authed_client: TestClient,
    stores: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The units accept one connection: a take left streaming (discarded, tab closed)
    used to hold them, and the next take failed with "device not found"."""
    from api.services import imu_devices, imu_runner

    devices = tmp_path / "devices.json"
    monkeypatch.setattr(imu_devices, "DEVICES_FILE", devices)
    fake = tmp_path / "fake_recorder.py"
    fake.write_text(FAKE_RECORDER)
    monkeypatch.setattr(imu_runner, "RECORDER_CMD", [sys.executable, str(fake)])

    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad")
    devices.write_text(
        json.dumps(
            {
                "AAA": {"wrist": "left", "fighter_id": fid},
                "BBB": {"wrist": "right", "fighter_id": fid},
            }
        )
    )
    _participant(authed_client, did, fid, "self")
    first = _take(authed_client, did, fid)
    _start_cameras(authed_client, first)  # recording, so the next take is a new one
    second = _take(authed_client, did, fid)
    assert second != first

    assert authed_client.post(f"/v2/takes/{first}/imu/ble/start").json()["running"]
    # the next take starts while the first is still streaming → it takes the sensors over
    assert authed_client.post(f"/v2/takes/{second}/imu/ble/start").json()["running"]
    assert authed_client.get(f"/v2/takes/{first}/imu/ble/status").json()["running"] is False
    # discarding a take releases whatever it still held
    authed_client.post(f"/v2/takes/{second}/discard")
    assert authed_client.get(f"/v2/takes/{second}/imu/ble/status").json()["running"] is False
    assert not imu_runner.any_running()


def test_deleting_a_take_mid_recording_removes_it_and_frees_the_sensors(
    authed_client: TestClient,
    stores: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import imu_devices, imu_runner

    devices = tmp_path / "devices.json"
    monkeypatch.setattr(imu_devices, "DEVICES_FILE", devices)
    fake = tmp_path / "fake_recorder.py"
    fake.write_text(FAKE_RECORDER)
    monkeypatch.setattr(imu_runner, "RECORDER_CMD", [sys.executable, str(fake)])
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad")
    devices.write_text(
        json.dumps(
            {
                "AAA": {"wrist": "left", "fighter_id": fid},
                "BBB": {"wrist": "right", "fighter_id": fid},
            }
        )
    )
    _participant(authed_client, did, fid, "self")
    tid = _take(authed_client, did, fid)
    token = authed_client.post(f"/takes/{tid}/multicam/join-info").json()["join_token"]
    dev = authed_client.post(f"/takes/{tid}/multicam/register", json={"token": token}).json()[
        "device_id"
    ]
    authed_client.post(f"/takes/{tid}/multicam/start")
    assert authed_client.post(f"/v2/takes/{tid}/imu/ble/start").json()["running"]

    assert authed_client.delete(f"/v2/takes/{tid}").status_code == 204
    assert authed_client.get(f"/v2/takes/{tid}").status_code == 404
    assert not (stores["datasets"] / did / "takes" / tid).exists()
    assert not imu_runner.any_running()
    # the phones are told to drop their clip, not upload it
    hb = authed_client.post(
        f"/takes/{tid}/multicam/heartbeat", json={"token": token, "device_id": dev}
    ).json()
    assert hb["command"] == "discard"


STREAMING_RECORDER = """
import json, signal, sys, time
hands = [a.split("=", 1)[0] for p, a in zip(sys.argv, sys.argv[1:]) if p == "--unit"]
stop = False
def _stop(*_):
    global stop
    stop = True
signal.signal(signal.SIGTERM, _stop)
for h in hands:
    print(json.dumps({"k": "st", "h": h, "connected": True, "battery_v": 4.2}), flush=True)
while not stop:  # real-time 100 Hz, stamped with the wall clock like the real recorder
    t = time.time() * 1000
    for h in hands:
        print(json.dumps({"k": "s", "h": h, "t": t, "a": [0, 0, 1.0], "g": [0, 0, 0]}), flush=True)
    time.sleep(0.01)
"""


def test_armed_wrists_connect_early_and_store_from_the_cameras_start(
    authed_client: TestClient,
    stores: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sensors connect when the take opens (no Bluetooth delay at Start) and
    begin storing exactly at the cameras' t = 0, together with the video."""
    from api.routes import capture_coord
    from api.services import imu_devices, imu_runner

    devices = tmp_path / "devices.json"
    monkeypatch.setattr(imu_devices, "DEVICES_FILE", devices)
    rec = tmp_path / "streaming_recorder.py"
    rec.write_text(STREAMING_RECORDER)
    monkeypatch.setattr(imu_runner, "RECORDER_CMD", [sys.executable, str(rec)])
    monkeypatch.setattr(capture_coord, "_START_DELAY_MS", 0.0)
    did = _dataset(authed_client)
    fid = _fighter(authed_client, "Mohamad")
    devices.write_text(
        json.dumps(
            {
                "AAA": {"wrist": "left", "fighter_id": fid},
                "BBB": {"wrist": "right", "fighter_id": fid},
            }
        )
    )
    _participant(authed_client, did, fid, "self")
    tid = _take(authed_client, did, fid)

    # armed: live, connected, nothing stored
    assert authed_client.post(f"/v2/takes/{tid}/imu/ble/start?arm=true").json()["running"]
    time.sleep(0.6)
    st = authed_client.get(f"/v2/takes/{tid}/imu/ble/status").json()
    assert all(u["connected"] and u["samples"] == 0 for u in st["units"].values())

    # the cameras start → storage begins at their t = 0
    token = authed_client.post(f"/takes/{tid}/multicam/join-info").json()["join_token"]
    authed_client.post(f"/takes/{tid}/multicam/register", json={"token": token})
    authed_client.post(f"/takes/{tid}/multicam/start")
    time.sleep(0.6)
    authed_client.post(f"/v2/takes/{tid}/imu/ble/stop")
    rows = (stores["datasets"] / did / "takes" / tid / "imu.csv").read_text().splitlines()[1:]
    times = [float(r.split(",", 1)[0]) for r in rows]
    assert len(times) > 50 and min(times) >= 0.0 and min(times) < 50.0  # starts right at t = 0
