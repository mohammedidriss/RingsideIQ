"""Dataset protocol on takes — blocks marked during a take become labels, and the
dataset export turns labeled takes into a training manifest with splits.

The take clock is faked (`capture_coord.timeline_now_ms`) and the wrists are a
synthetic imu.csv with punches at known times, so the tests pin exactly which
bursts become which labels.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

IMU_HEADER = "t_ms,hand,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps"


class Clock:
    now: float | None = 0.0


@pytest.fixture
def clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    from api.routes import capture_coord
    from api.services import dataset_store

    monkeypatch.setattr(dataset_store, "DATASETS_DIR", tmp_path / "datasets")
    c = Clock()
    monkeypatch.setattr(capture_coord, "timeline_now_ms", lambda _id: c.now)
    yield c


def _dataset(client: TestClient) -> str:
    r = client.post("/v2/datasets", json={"name": "RQ2 punches", "protocol": "rq2-v1"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _take(client: TestClient, did: str, stance: str, consent: str = "irb_signed") -> str:
    fid = client.post("/fighters", json={"name": f"F-{stance}", "stance": stance}).json()["id"]
    r = client.put(f"/v2/datasets/{did}/participants", json={"fighter_id": fid, "consent": consent})
    assert r.status_code == 200, r.text
    r = client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": fid})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _folder(take_id: str) -> Path:
    from uuid import UUID

    from api.services import dataset_store

    folder = dataset_store.take_dir(UUID(take_id))
    assert folder is not None
    return folder


def _imu_lines(hand: str, seconds: float, punches_ms: list[float]) -> list[str]:
    """100 Hz of a resting wrist with a launch / lock-out / retraction burst per punch."""
    t = np.arange(0.0, seconds * 1000.0, 10.0)
    ax = np.zeros(t.size)
    for p in punches_ms:
        for offset, g, width in ((-120.0, 3.6, 25.0), (0.0, 6.0, 30.0), (180.0, 2.4, 40.0)):
            ax += g * np.exp(-0.5 * ((t - (p + offset)) / width) ** 2)
    return [
        f"{ti:.2f},{hand},{a:.4f},0.0000,1.0000,0.00,0.00,0.00" for ti, a in zip(t, ax, strict=True)
    ]


def _write_imu(take_id: str, lines: list[str], *, append: bool = False) -> None:
    path = _folder(take_id) / "imu.csv"
    if append and path.exists():
        with path.open("a") as f:
            f.write("\n".join(lines) + "\n")
    else:
        path.write_text(IMU_HEADER + "\n" + "\n".join(lines) + "\n")


def _block(
    client: TestClient, clock: Clock, take_id: str, key: str, start: float, end: float
) -> dict:
    clock.now = start
    r = client.post(f"/v2/takes/{take_id}/protocol/start", json={"key": key})
    assert r.status_code == 200, r.text
    clock.now = end
    r = client.post(f"/v2/takes/{take_id}/protocol/end")
    assert r.status_code == 200, r.text
    return r.json()


def _complete(client: TestClient, take_id: str, duration_ms: float) -> None:
    (_folder(take_id) / "video" / "dev1.webm").write_bytes(b"\x1a\x45\xdf\xa3clip")
    r = client.post(f"/takes/{take_id}/multicam/complete", json={"duration_ms": duration_ms})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


def test_blocks_become_typed_labels_on_the_right_wrist(
    authed_client: TestClient, clock: Clock
) -> None:
    tid = _take(authed_client, _dataset(authed_client), "southpaw")  # lead = right, rear = left
    _write_imu(
        tid,
        _imu_lines("right", 16, [1000, 2000, 3000, 13500])  # jabs + a free punch
        + _imu_lines("left", 16, [2500, 6000, 7000, 11000, 13000]),  # twitch, crosses, …
    )
    _block(authed_client, clock, tid, "jab", 0, 5000)
    _block(authed_client, clock, tid, "cross", 5000, 10000)
    _block(authed_client, clock, tid, "negatives", 10000, 12000)
    body = _block(authed_client, clock, tid, "free", 12000, 15000)

    labels = json.loads((_folder(tid) / "labels.json").read_text())
    got = [(round(lab["t_ms"], -2), lab["hand"], lab["punch_type"]) for lab in labels]
    assert got == [
        (1000, "right", "jab"),
        (2000, "right", "jab"),
        (3000, "right", "jab"),
        (6000, "left", "cross"),
        (7000, "left", "cross"),
        (13000, "left", None),  # free shadowboxing: timed, type left for review
        (13500, "right", None),
    ]
    by_key = {b["key"]: b for b in body["blocks"]}
    assert by_key["jab"]["detected"] == 3 and by_key["jab"]["off_hand"] == 1  # the 2500 twitch
    assert by_key["negatives"]["detected"] == 0 and by_key["negatives"]["off_hand"] == 1
    assert body["lead_hand"] == "right" and body["labels"]["count"] == 7
    # Provenance lives in the protocol side's own file, never in take.json.
    prov = json.loads((_folder(tid) / "protocol.json").read_text())["labels"]
    assert prov["detector"]["name"] == "analyze.imu_punches" and prov["stance"] == "southpaw"
    assert "labels" not in json.loads((_folder(tid) / "take.json").read_text())


def test_open_block_live_count_reads_only_rows_written_since_it_began(
    authed_client: TestClient, clock: Clock
) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    _write_imu(tid, _imu_lines("left", 3, [500, 1500]))  # warm-up jabs before the block
    clock.now = 3000
    authed_client.post(f"/v2/takes/{tid}/protocol/start", json={"key": "jab"})
    # The runner keeps appending: two jabs inside the block so far.
    lines = [ln for ln in _imu_lines("left", 6, [3500, 4500]) if float(ln.split(",")[0]) >= 3000]
    _write_imu(tid, lines, append=True)
    clock.now = 5500
    body = authed_client.get(f"/v2/takes/{tid}/protocol").json()
    assert body["active"] == 0 and body["blocks"][0]["detected"] == 2 and body["recording"]


def test_reviewed_labels_are_never_silently_replaced(
    authed_client: TestClient, clock: Clock
) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    _write_imu(tid, _imu_lines("left", 6, [1000, 2000]))
    _block(authed_client, clock, tid, "jab", 0, 5000)

    path = _folder(tid) / "labels.json"
    reviewed = json.loads(path.read_text())[:1]  # the reviewer removed one
    path.write_text(json.dumps(reviewed))

    assert authed_client.post(f"/v2/takes/{tid}/protocol/labels").status_code == 409
    assert json.loads(path.read_text()) == reviewed
    assert authed_client.get(f"/v2/takes/{tid}/protocol").json()["labels_edited"] is True
    _block(authed_client, clock, tid, "cross", 5000, 6000)  # a new block leaves it alone…
    assert json.loads(path.read_text()) == reviewed
    r = authed_client.post(f"/v2/takes/{tid}/protocol/labels?overwrite=true")  # …until asked
    assert r.status_code == 200 and len(json.loads(path.read_text())) == 2


def test_discarded_block_drops_out_of_the_labels(authed_client: TestClient, clock: Clock) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    _write_imu(tid, _imu_lines("left", 12, [1000, 2000, 6000, 7000]))
    _block(authed_client, clock, tid, "jab", 0, 5000)
    _block(authed_client, clock, tid, "jab", 5000, 10000)  # the redo
    r = authed_client.post(f"/v2/takes/{tid}/protocol/blocks/0/discard")
    assert r.status_code == 200 and r.json()["blocks"][0]["discarded"] is True
    labels = json.loads((_folder(tid) / "labels.json").read_text())
    assert [round(lab["t_ms"], -2) for lab in labels] == [6000, 7000]


def test_a_block_left_open_at_stop_closes_at_the_take_end(
    authed_client: TestClient, clock: Clock
) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    _write_imu(tid, _imu_lines("left", 6, [1000, 2000]))
    clock.now = 0
    authed_client.post(f"/v2/takes/{tid}/protocol/start", json={"key": "jab"})
    clock.now = None  # Stop & save pressed with the block still open
    _complete(authed_client, tid, 4000)
    body = authed_client.post(f"/v2/takes/{tid}/protocol/end").json()
    assert body["blocks"][0]["t_end_ms"] == 4000 and body["labels"]["count"] == 2


def test_blocks_need_the_cameras_and_a_real_take(authed_client: TestClient, clock: Clock) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    clock.now = None
    r = authed_client.post(f"/v2/takes/{tid}/protocol/start", json={"key": "jab"})
    assert r.status_code == 409 and "cameras" in r.json()["detail"]
    clock.now = 0
    assert (
        authed_client.post(f"/v2/takes/{tid}/protocol/start", json={"key": "nope"}).status_code
        == 409
    )
    missing = "00000000-0000-0000-0000-000000000000"
    assert authed_client.get(f"/v2/takes/{missing}/protocol").status_code == 404


def test_plan_lists_the_standard_blocks(authed_client: TestClient) -> None:
    plan = authed_client.get("/v2/protocol/plan").json()
    assert [b["key"] for b in plan][:2] == ["jab", "cross"]
    assert {b["kind"] for b in plan} == {"typed", "combo", "negative", "free"}
    combos = {b["key"]: b for b in plan if b["kind"] == "combo"}
    assert set(combos) == {"combo_12", "combo_123", "combo_112", "combo_32"}
    assert combos["combo_123"]["sequence"] == [["jab", "lead"], ["cross", "rear"], ["hook", "lead"]]
    assert all(c["reps"] == 15 and c["pace_s"] == 3.0 and c["callout"] for c in combos.values())


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def test_pilot_export_trains_on_scripted_blocks_and_tests_on_free(
    authed_client: TestClient, clock: Clock
) -> None:
    did = _dataset(authed_client)
    tid = _take(authed_client, did, "orthodox", consent="self")
    _write_imu(tid, _imu_lines("left", 16, [1000, 2000]) + _imu_lines("right", 16, [13000]))
    _block(authed_client, clock, tid, "jab", 0, 5000)
    _block(authed_client, clock, tid, "negatives", 5000, 10000)
    _block(authed_client, clock, tid, "free", 12000, 15000)
    _complete(authed_client, tid, 16000)
    unfinished = _take(authed_client, did, "southpaw")  # cameras started, never finished
    from api.services import dataset_store

    dataset_store.update_take_json(_folder(unfinished), t0_ms=1.0)

    r = authed_client.post(f"/v2/datasets/{did}/export")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["split_strategy"] == "pilot" and m["counts"] == {
        "takes": 1,
        "fighters": 1,
        "labels": 3,
    }
    assert [s["block"] for s in m["splits"]["train"]] == ["jab", "negatives"]
    assert [s["block"] for s in m["splits"]["test"]] == ["free"]
    take = m["takes"][0]
    assert take["participant"] == "F-0001" and "fighter_id" not in take
    assert take["cameras"] == [{"camera": "dev1", "start_offset_ms": None}]
    assert take["video"] == [] and take["imu"] == "imu.csv"
    assert take["labels"]["count"] == 3 and take["labels"]["reviewed"] is False
    assert {"take_id": unfinished, "reason": "recording"} in m["excluded"]
    assert any("pilot split" in w for w in m["warnings"])
    assert any("unreviewed" in w for w in m["warnings"])
    assert json.loads(Path(m["path"]).read_text())["takes"] == m["takes"]
    # The export is a self-contained, de-identified copy next to the manifest:
    # the take's data under its code, no video (faces) and no name anywhere.
    copy = Path(m["path"]).parent / take["folder"]
    assert (copy / "imu.csv").exists() and (copy / "labels.json").exists()
    assert json.loads((copy / "take.json").read_text())["participant"] == "F-0001"
    assert not [p for p in copy.rglob("*") if p.suffix in (".webm", ".mp4")]
    text = "".join(p.read_text() for p in copy.parent.parent.rglob("*.json"))
    assert "F-orthodox" not in text  # the fighter's name


def test_three_fighters_split_by_person_so_nobody_is_in_train_and_test(
    authed_client: TestClient, clock: Clock
) -> None:
    did = _dataset(authed_client)
    takes = {}
    for stance in ("orthodox", "southpaw", "switch", "orthodox"):
        tid = _take(authed_client, did, stance)
        _write_imu(tid, _imu_lines("left", 6, [1000]))
        _block(authed_client, clock, tid, "jab", 0, 5000)
        _complete(authed_client, tid, 6000)
        takes[tid] = stance

    m = authed_client.post(f"/v2/datasets/{did}/export").json()
    assert m["split_strategy"] == "by_fighter" and m["counts"]["fighters"] == 4
    fighter_of = {t["take_id"]: t["participant"] for t in m["takes"]}
    people = {name: {fighter_of[s["take_id"]] for s in segs} for name, segs in m["splits"].items()}
    assert people["test"] and people["val"] and people["train"]
    assert not (people["train"] & people["test"]) and not (people["train"] & people["val"])
    # Stable: the same dataset exports the same split.
    again = authed_client.post(f"/v2/datasets/{did}/export").json()
    assert again["splits"] == m["splits"]


def test_uppercut_block_labels_the_punch_not_the_return(
    authed_client: TestClient, clock: Clock
) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    lines = _imu_lines("left", 8, [1000, 2500, 4000])
    # Each uppercut's return to guard: an opposite burst ~430 ms later (first real take).
    t = np.arange(0.0, 8000.0, 10.0)
    ret = sum(-5.0 * np.exp(-0.5 * ((t - (p + 430.0)) / 35.0) ** 2) for p in (1000, 2500, 4000))
    lines = [
        f"{ln.split(',')[0]},left,{float(ln.split(',')[2]) + r:.4f},0.0000,1.0000,0.00,0.00,0.00"
        for ln, r in zip(lines, ret, strict=True)
    ]
    _write_imu(tid, lines)
    body = _block(authed_client, clock, tid, "lead_uppercut", 0, 6000)
    labels = json.loads((_folder(tid) / "labels.json").read_text())
    assert [(round(lab["t_ms"], -2), lab["punch_type"]) for lab in labels] == [
        (1000, "uppercut"),
        (2500, "uppercut"),
        (4000, "uppercut"),
    ]
    assert body["blocks"][0]["detected"] == 3
    prov = json.loads((_folder(tid) / "protocol.json").read_text())["labels"]["detector"]
    assert prov["profiles"]["uppercut"]["mode"] == "first"


# --------------------------------------------------------------------------
# Sensors worn on swapped wrists
# --------------------------------------------------------------------------


def _swapped_take(client: TestClient, clock: Clock) -> tuple[str, str]:
    """An orthodox fighter wearing the two (identical-looking) units swapped: the
    left hand's jabs land in imu.csv as "right", the right hand's crosses as "left"."""
    did = _dataset(client)
    tid = _take(client, did, "orthodox")
    jabs = [1000, 2000, 3000, 4000, 4500, 4900]
    crosses = [6000, 6800, 7600, 8400, 9000, 9600]
    _write_imu(tid, _imu_lines("right", 16, jabs) + _imu_lines("left", 16, crosses))
    _block(client, clock, tid, "jab", 0, 5000)
    _block(client, clock, tid, "cross", 5000, 10000)
    return did, tid


def test_swapped_sensors_are_flagged_and_fixed_without_touching_imu_csv(
    authed_client: TestClient, clock: Clock
) -> None:
    did, tid = _swapped_take(authed_client, clock)
    body = authed_client.get(f"/v2/takes/{tid}/protocol").json()
    assert [b["detected"] for b in body["blocks"]] == [0, 0]
    assert [b["off_hand"] for b in body["blocks"]] == [6, 6]
    assert body["swap_evidence"] and body["imu_hands_swapped"] is False
    raw = (_folder(tid) / "imu.csv").read_text()

    body = authed_client.post(
        f"/v2/takes/{tid}/protocol/swap-wrists", json={"swapped": True}
    ).json()
    assert body["imu_hands_swapped"] is True and body["swap_evidence"] == []
    assert [b["detected"] for b in body["blocks"]] == [6, 6]
    labels = json.loads((_folder(tid) / "labels.json").read_text())
    assert {(lab["hand"], lab["punch_type"]) for lab in labels} == {
        ("left", "jab"),
        ("right", "cross"),
    }
    assert (_folder(tid) / "imu.csv").read_text() == raw  # the raw stream is never rewritten

    _complete(authed_client, tid, 16000)
    m = authed_client.post(f"/v2/datasets/{did}/export").json()
    assert m["takes"][0]["imu_hands_swapped"] is True
    assert any("swapped wrists" in w for w in m["warnings"])

    body = authed_client.post(
        f"/v2/takes/{tid}/protocol/swap-wrists", json={"swapped": False}
    ).json()  # undo
    assert [b["detected"] for b in body["blocks"]] == [0, 0] and body["swap_evidence"]


def test_cameras_flag_a_swap_and_reviewed_labels_stay_protected(
    authed_client: TestClient, clock: Clock
) -> None:
    _, tid = _swapped_take(authed_client, clock)
    (_folder(tid) / "crosscheck.json").write_text(json.dumps({"hands": "swapped"}))
    body = authed_client.get(f"/v2/takes/{tid}/protocol").json()
    assert any("cameras" in why for why in body["swap_evidence"])

    path = _folder(tid) / "labels.json"
    path.write_text(json.dumps([{"t_ms": 1000.0, "hand": "left", "punch_type": "jab"}]))
    r = authed_client.post(f"/v2/takes/{tid}/protocol/swap-wrists", json={"swapped": True})
    assert r.status_code == 409  # someone reviewed these labels
    assert authed_client.get(f"/v2/takes/{tid}/protocol").json()["imu_hands_swapped"] is False
    r = authed_client.post(
        f"/v2/takes/{tid}/protocol/swap-wrists?overwrite=true", json={"swapped": True}
    )
    assert r.status_code == 200 and r.json()["imu_hands_swapped"] is True


# --------------------------------------------------------------------------
# Combos
# --------------------------------------------------------------------------


def test_combo_blocks_label_every_punch_in_order(authed_client: TestClient, clock: Clock) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")  # lead = left
    left, right = [], []
    bases = [1000.0 + 3000.0 * i for i in range(3)]
    for b in bases:  # 1-2-3, with the jab's return to guard landing just before the cross
        left += [b, b + 400.0, b + 900.0]  # jab, (return), lead hook
        right += [b + 450.0]  # cross
    _write_imu(tid, _imu_lines("left", 12, left) + _imu_lines("right", 12, right))
    body = _block(authed_client, clock, tid, "combo_123", 0, 10000)

    labels = json.loads((_folder(tid) / "labels.json").read_text())
    got = [
        (round(lab["t_ms"] - b, -1), lab["hand"], lab["punch_type"])
        for b in bases
        for lab in labels
        if b - 200 <= lab["t_ms"] < b + 1500
    ]
    assert got == [(0.0, "left", "jab"), (450.0, "right", "cross"), (900.0, "left", "hook")] * 3
    assert body["blocks"][0]["detected"] == 3 and body["blocks"][0]["off_hand"] == 0


def test_double_jab_and_lead_hook_combos(authed_client: TestClient, clock: Clock) -> None:
    tid = _take(authed_client, _dataset(authed_client), "southpaw")  # lead = right
    _write_imu(
        tid,
        _imu_lines("right", 20, [1000, 1350, 10000])  # 1-1-2: jab, jab | 3-2: hook
        + _imu_lines("left", 20, [1700, 10350]),  # 1-1-2: cross | 3-2: cross
    )
    _block(authed_client, clock, tid, "combo_112", 0, 5000)
    _block(authed_client, clock, tid, "combo_32", 9000, 14000)
    labels = json.loads((_folder(tid) / "labels.json").read_text())
    assert [(round(lab["t_ms"], -1), lab["hand"], lab["punch_type"]) for lab in labels] == [
        (1000.0, "right", "jab"),
        (1350.0, "right", "jab"),
        (1700.0, "left", "cross"),
        (10000.0, "right", "hook"),
        (10350.0, "left", "cross"),
    ]


def test_incomplete_combos_are_counted_not_labeled(authed_client: TestClient, clock: Clock) -> None:
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    # Three 1-2s; the second one's cross never came.
    _write_imu(
        tid, _imu_lines("left", 12, [1000, 4000, 7000]) + _imu_lines("right", 12, [1300, 7300])
    )
    body = _block(authed_client, clock, tid, "combo_12", 0, 10000)
    assert body["blocks"][0]["detected"] == 2 and body["blocks"][0]["off_hand"] == 1
    labels = json.loads((_folder(tid) / "labels.json").read_text())
    assert [round(lab["t_ms"], -2) for lab in labels] == [1000, 1300, 7000, 7300]


def test_pilot_split_tests_on_combos(authed_client: TestClient, clock: Clock) -> None:
    did = _dataset(authed_client)
    tid = _take(authed_client, did, "orthodox", consent="self")
    _write_imu(tid, _imu_lines("left", 12, [1000, 6000]) + _imu_lines("right", 12, [6300]))
    _block(authed_client, clock, tid, "jab", 0, 5000)
    _block(authed_client, clock, tid, "combo_12", 5000, 9000)
    _complete(authed_client, tid, 12000)
    m = authed_client.post(f"/v2/datasets/{did}/export").json()
    assert [s["block"] for s in m["splits"]["train"]] == ["jab"]
    assert [s["block"] for s in m["splits"]["test"]] == ["combo_12"]


def test_a_slow_three_two_stays_one_combo(authed_client: TestClient, clock: Clock) -> None:
    """The first combo take: a 3-2 thrown as hook, return to guard, then the cross
    ~1.1 s after the hook. A fixed 1 s lull rule split those in half."""
    tid = _take(authed_client, _dataset(authed_client), "orthodox")
    bases = [1000.0 + 3000.0 * i for i in range(4)]
    left = [x for b in bases for x in (b, b + 400.0)]  # lead hook, its return to guard
    right = [b + 1150.0 for b in bases]  # the cross
    _write_imu(tid, _imu_lines("left", 15, left) + _imu_lines("right", 15, right))
    body = _block(authed_client, clock, tid, "combo_32", 0, 13500)
    assert body["blocks"][0]["detected"] == 4 and body["blocks"][0]["off_hand"] == 0
    labels = json.loads((_folder(tid) / "labels.json").read_text())
    got = [
        (round(lab["t_ms"] - b, -1), lab["punch_type"])
        for b in bases
        for lab in labels
        if b - 100 <= lab["t_ms"] < b + 2000
    ]
    assert got == [(0.0, "hook"), (1150.0, "cross")] * 4


def test_combo_break_uses_the_expected_number_of_long_pauses() -> None:
    from api.services.dataset_protocol import combo_break_ms

    inside = [300.0, 1100.0] * 15  # hook → return → cross, 15 times
    between = [2000.0 + 50 * i for i in range(14)]
    assert 1100.0 < combo_break_ms(inside + between, 15) < 2000.0
    # Without a usable count, the biggest jump among pauses still finds it.
    assert 1100.0 < combo_break_ms(inside + between, None) < 2000.0


def test_export_skips_drafts_that_never_started(authed_client: TestClient, clock: Clock) -> None:
    did = _dataset(authed_client)
    _take(authed_client, did, "orthodox")  # "Record take" pressed, cameras never started
    m = authed_client.post(f"/v2/datasets/{did}/export").json()
    assert m["excluded"] == [] and m["counts"]["takes"] == 0


def test_a_one_block_take_labels_and_exports_just_its_block(
    authed_client: TestClient, clock: Clock
) -> None:
    """Recording by category: a take made for one block (take.json "block")."""
    did = _dataset(authed_client)
    fid = authed_client.post("/fighters", json={"name": "Category", "stance": "orthodox"}).json()[
        "id"
    ]
    authed_client.put(
        f"/v2/datasets/{did}/participants", json={"fighter_id": fid, "consent": "self"}
    )
    r = authed_client.post(f"/v2/datasets/{did}/takes", json={"fighter_id": fid, "block": "cross"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    _write_imu(tid, _imu_lines("right", 8, [1000, 2500, 4000]))
    body = _block(authed_client, clock, tid, "cross", 0, 6000)
    assert body["take_block"] == "cross" and body["blocks"][0]["detected"] == 3
    labels = json.loads((_folder(tid) / "labels.json").read_text())
    assert {(lab["hand"], lab["punch_type"]) for lab in labels} == {("right", "cross")}
    _complete(authed_client, tid, 8000)
    m = authed_client.post(f"/v2/datasets/{did}/export").json()
    assert [s["block"] for s in m["splits"]["train"]] == ["cross"]
