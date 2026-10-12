"""Datasets API (ADR-013) — datasets, participants' consent, and recorded takes.

A take is recorded with the same engines as a training session (camera
coordinator, wrist-IMU recorder, Polar stream) but everything it captures lands
in its own folder (`dataset_store`), never in session tables. Its routes mirror a
session's, under `/takes/{id}` instead of `/sessions/{id}`:

- `/v2/datasets…`, `/v2/takes/{id}`            — datasets, participants, takes
- `/takes/{id}/multicam/join-info|complete`    — the take-specific capture steps
  (device / start / stop / upload routes are shared: `capture_coord.take_*`)
- `/v2/takes/{id}/imu/ble/*`, `/hrv/*`, `/live` — sensors and the live reader
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session as DBSession

from api.deps import db_session, fighter_repo
from api.routes import capture_coord
from api.routes.auth import require_current_user
from api.routes.capture_coord import JoinInfo
from api.routes.imu_ble import ImuBleStatus, session_status
from api.routes.live import LiveHeart, LiveOut, hr_zone, max_hr_for
from api.routes.sessions import CrossCheckCamera, CrossCheckSummary
from api.services import (
    cross_check,
    dataset_protocol,
    dataset_store,
    hrv_runner,
    imu_devices,
    imu_runner,
    pose_backfill,
)
from store import (
    ConsentEnum,
    DatasetCreate,
    DatasetParticipantIn,
    DatasetRead,
    DatasetRepo,
    DatasetTake,
    FighterRepo,
    TakeStatusEnum,
)

_auth = [Depends(require_current_user)]
router = APIRouter(prefix="/v2", tags=["datasets"], dependencies=_auth)
capture = APIRouter(prefix="/takes", tags=["datasets"], dependencies=_auth)


def dataset_repo(db: DBSession = Depends(db_session)) -> DatasetRepo:
    return DatasetRepo(db)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class ParticipantOut(BaseModel):
    fighter_id: UUID
    name: str
    stance: str | None = None
    consent: str
    may_record: bool
    consent_date: str | None = None
    irb_ref: str | None = None
    notes: str | None = None
    takes: int = 0
    # Protocol block → how many completed takes recorded it (single-block takes, and
    # the blocks inside older full-protocol takes).
    blocks: dict[str, int] = Field(default_factory=dict)


class TakeOut(BaseModel):
    id: UUID
    dataset_id: UUID
    dataset_name: str | None = None
    fighter_id: UUID
    fighter_name: str | None = None
    # recording · uploading (stopped; the cameras' video is still arriving) ·
    # completed · discarded
    status: str
    # False for a draft: "Record dataset session" opened it, but the cameras haven't started.
    started: bool = True
    # The protocol block this take records (one block per take), or None for an
    # older take that ran the whole protocol / a free recording.
    block: str | None = None
    started_at: datetime  # when the cameras started (when it was opened, for a draft)
    ended_at: datetime | None = None
    duration_ms: float
    notes: str | None = None
    data: dict[str, Any] = {}  # dataset_store.summary(): clips, imu/hr rows, labels


class DatasetSummary(DatasetRead):
    participants: int = 0
    takes: int = 0
    completed_takes: int = 0


class DatasetOut(DatasetRead):
    participants: list[ParticipantOut]
    takes: list[TakeOut]


class TakeCreate(BaseModel):
    fighter_id: UUID
    block: str | None = None  # record just this protocol block (dataset_protocol.SPECS)


class CompleteBody(BaseModel):
    duration_ms: float | None = None


class HrStartBody(BaseModel):
    address: str
    window_ms: float = 60_000.0


class HrStatusOut(BaseModel):
    session_id: UUID  # the take id — same shape as a session's HRV status
    is_running: bool
    sample_count: int = 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _is_draft(take: DatasetTake) -> bool:
    """Opened with "Record dataset session" but the cameras never started: nothing recorded,
    so it isn't part of the dataset (not listed, not counted, reused next time)."""
    if take.status != TakeStatusEnum.RECORDING:
        return False
    folder = dataset_store.take_dir(take.id)
    return folder is None or not dataset_store.started(folder)


def _recorded_blocks(take_id: UUID) -> list[str]:
    """The protocol blocks a take recorded: marked, finished, not discarded."""
    folder = dataset_store.take_dir(take_id)
    if folder is None:
        return []
    try:
        data = json.loads((folder / "protocol.json").read_text())
    except (OSError, ValueError):
        return []
    return [
        str(b["key"])
        for b in data.get("blocks") or []
        if isinstance(b, dict)
        and b.get("key")
        and b.get("t_end_ms") is not None
        and not b.get("discarded")
    ]


def _take_out(take: DatasetTake, fighters: FighterRepo, dataset_name: str | None) -> TakeOut:
    fighter = fighters.get(take.fighter_id)
    folder = dataset_store.take_dir(take.id)
    meta = dataset_store.read_take_json(folder) if folder else {}
    t0 = meta.get("t0_ms")
    started = not _is_draft(take)
    status = str(take.status)
    if take.status == TakeStatusEnum.RECORDING and started:
        if capture_coord.last_command(take.id) == "stop":
            status = "uploading"  # Stop & save pressed; it completes when the clips land
    return TakeOut(
        id=take.id,
        dataset_id=take.dataset_id,
        dataset_name=dataset_name,
        fighter_id=take.fighter_id,
        fighter_name=fighter.name if fighter else None,
        status=status,
        started=started,
        block=meta.get("block"),
        started_at=datetime.fromtimestamp(t0 / 1000, UTC) if t0 else take.started_at,
        ended_at=take.ended_at,
        duration_ms=take.duration_ms,
        notes=take.notes,
        data=dataset_store.summary(folder) if folder else {},
    )


def _get_take(take_id: UUID, repo: DatasetRepo) -> DatasetTake:
    take = repo.get_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    return take


def _folder(take_id: UUID) -> Any:
    folder = dataset_store.take_dir(take_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="take folder missing on disk")
    return folder


def _release_capture(take_id: UUID, *, cameras: bool = False, discard: bool = False) -> None:
    """Free the wrist sensors and the Polar a take was using, so the next take can
    connect. `cameras` also stops its cameras (they upload); `discard` makes them
    drop the clip instead."""
    imu_runner.stop(take_id)
    hrv_runner.request_stop(take_id)
    if discard:
        capture_coord.discard_capture(take_id)
    elif cameras:
        capture_coord.stop_capture(take_id)


def _require_recording(take: DatasetTake) -> None:
    if take.status != TakeStatusEnum.RECORDING:
        raise HTTPException(status_code=409, detail=f"take is {take.status} — record a new take")


# --------------------------------------------------------------------------
# Datasets + participants
# --------------------------------------------------------------------------


@router.get("/datasets", response_model=list[DatasetSummary])
def list_datasets(repo: DatasetRepo = Depends(dataset_repo)) -> list[DatasetSummary]:
    out = []
    for d in repo.list_all():
        takes = [t for t in repo.takes(d.id) if not _is_draft(t)]
        out.append(
            DatasetSummary(
                **DatasetRead.model_validate(d, from_attributes=True).model_dump(),
                participants=len(repo.participants(d.id)),
                takes=len([t for t in takes if t.status != TakeStatusEnum.DISCARDED]),
                completed_takes=len([t for t in takes if t.status == TakeStatusEnum.COMPLETED]),
            )
        )
    return out


@router.post("/datasets", response_model=DatasetRead, status_code=201)
def create_dataset(body: DatasetCreate, repo: DatasetRepo = Depends(dataset_repo)) -> DatasetRead:
    row = repo.create(body)
    dataset_store.dataset_dir(row.id).mkdir(parents=True, exist_ok=True)
    return DatasetRead.model_validate(row, from_attributes=True)


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(
    dataset_id: UUID,
    repo: DatasetRepo = Depends(dataset_repo),
    fighters: FighterRepo = Depends(fighter_repo),
) -> DatasetOut:
    d = repo.get(dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    takes = [t for t in repo.takes(dataset_id) if not _is_draft(t)]
    covered: dict[UUID, dict[str, int]] = {}
    for t in takes:
        if t.status == TakeStatusEnum.COMPLETED:
            per = covered.setdefault(t.fighter_id, {})
            for key in _recorded_blocks(t.id):
                per[key] = per.get(key, 0) + 1
    participants = []
    for p in repo.participants(dataset_id):
        f = fighters.get(p.fighter_id)
        participants.append(
            ParticipantOut(
                fighter_id=p.fighter_id,
                name=f.name if f else "(deleted fighter)",
                stance=str(f.stance) if f and f.stance else None,
                consent=str(p.consent),
                may_record=p.consent in ("self", "irb_signed"),
                consent_date=p.consent_date.isoformat() if p.consent_date else None,
                irb_ref=p.irb_ref,
                notes=p.notes,
                blocks=covered.get(p.fighter_id, {}),
                takes=len(
                    [
                        t
                        for t in takes
                        if t.fighter_id == p.fighter_id and t.status == TakeStatusEnum.COMPLETED
                    ]
                ),
            )
        )
    return DatasetOut(
        **DatasetRead.model_validate(d, from_attributes=True).model_dump(),
        participants=participants,
        takes=[_take_out(t, fighters, d.name) for t in takes],
    )


@router.put("/datasets/{dataset_id}/participants", response_model=DatasetOut)
def set_participant(
    dataset_id: UUID,
    body: DatasetParticipantIn,
    repo: DatasetRepo = Depends(dataset_repo),
    fighters: FighterRepo = Depends(fighter_repo),
) -> DatasetOut:
    """Add a fighter to the dataset, or change their consent."""
    if repo.get(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        repo.set_participant(dataset_id, body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return get_dataset(dataset_id, repo, fighters)


# --------------------------------------------------------------------------
# Takes
# --------------------------------------------------------------------------


@router.post("/datasets/{dataset_id}/takes", response_model=TakeOut, status_code=201)
def create_take(
    dataset_id: UUID,
    body: TakeCreate,
    repo: DatasetRepo = Depends(dataset_repo),
    fighters: FighterRepo = Depends(fighter_repo),
) -> TakeOut:
    """Open a take for a participant — refused unless their consent allows it.

    It's a draft until the cameras start: the phones and sensors connect to it, but
    it isn't in the dataset yet. The participant's unstarted draft is reused, so
    opening "Record dataset session" and leaving adds nothing."""
    d = repo.get(dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if body.block is not None and body.block not in dataset_protocol.SPECS:
        raise HTTPException(status_code=422, detail=f"unknown protocol block: {body.block}")
    p = repo.participant(dataset_id, body.fighter_id)
    if p is not None and ConsentEnum(p.consent).may_record:
        for t in repo.takes(dataset_id):
            folder = dataset_store.take_dir(t.id)
            if (
                t.fighter_id == body.fighter_id
                and _is_draft(t)
                and folder is not None
                and dataset_store.read_take_json(folder).get("block") == body.block
            ):
                return _take_out(t, fighters, d.name)
    try:
        take = repo.create_take(dataset_id, body.fighter_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    fighter = fighters.get(take.fighter_id)
    dataset_store.create_take_dir(
        dataset_id,
        take.id,
        {
            "take_id": str(take.id),
            "dataset_id": str(dataset_id),
            "dataset": d.name,
            "protocol": d.protocol,
            "fighter_id": str(take.fighter_id),
            "fighter": fighter.name if fighter else None,
            "stance": str(fighter.stance) if fighter and fighter.stance else None,
            "status": "recording",
            "created_at": take.started_at.isoformat(),
            "timeline": "t_ms = ms since the cameras' synchronized start (t0_ms, server "
            "wall clock); takes are never paused",
            "t0_ms": None,
            "duration_ms": None,
            "devices": [],
            "block": body.block,
        },
    )
    return _take_out(take, fighters, d.name)


@router.get("/takes/{take_id}", response_model=TakeOut)
def get_take(
    take_id: UUID,
    repo: DatasetRepo = Depends(dataset_repo),
    fighters: FighterRepo = Depends(fighter_repo),
) -> TakeOut:
    take = _get_take(take_id, repo)
    d = repo.get(take.dataset_id)
    return _take_out(take, fighters, d.name if d else None)


@router.delete("/takes/{take_id}", status_code=204)
def delete_take(take_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> None:
    """Delete a take for good — the coach's "Delete" on a recording that went wrong:
    the cameras drop their clips, the sensors stop, and the row and folder go."""
    take = _get_take(take_id, repo)
    _release_capture(take_id, discard=True)
    folder = dataset_store.take_dir(take_id)
    if folder is not None:
        shutil.rmtree(folder, ignore_errors=True)
    repo.delete_take(take.id)


@router.post("/takes/{take_id}/discard", response_model=TakeOut)
def discard_take(
    take_id: UUID,
    repo: DatasetRepo = Depends(dataset_repo),
    fighters: FighterRepo = Depends(fighter_repo),
) -> TakeOut:
    """Exclude a take from the dataset. Its files stay on disk. Anything still
    recording into it stops — sensors, Polar and the cameras."""
    take = _get_take(take_id, repo)
    _release_capture(take_id, cameras=True)
    repo.finish_take(take.id, TakeStatusEnum.DISCARDED)
    folder = dataset_store.take_dir(take_id)
    if folder is not None:
        dataset_store.update_take_json(folder, status="discarded")
    return get_take(take_id, repo, fighters)


# --------------------------------------------------------------------------
# Capture — the take-specific steps (shared device/start/stop routes live in
# capture_coord.take_master / take_slave)
# --------------------------------------------------------------------------


@capture.post("/{take_id}/multicam/join-info", response_model=JoinInfo)
def take_join_info(take_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> JoinInfo:
    take = _get_take(take_id, repo)
    _require_recording(take)
    _folder(take_id)
    return capture_coord.join(take_id, "/takes")


@capture.post("/{take_id}/multicam/complete", response_model=dict)
def take_complete(
    take_id: UUID, body: CompleteBody, repo: DatasetRepo = Depends(dataset_repo)
) -> dict[str, object]:
    """Stop's clips have landed: record the devices and timing, mark it completed."""
    take = _get_take(take_id, repo)
    folder = _folder(take_id)
    clips = dataset_store.clips(folder)
    if not clips:
        raise HTTPException(status_code=409, detail="no video was saved for this take")
    # t = 0 was written at Start; keep it if the coordinator forgot it (API restart).
    t0 = capture_coord.last_started_at_ms(take_id) or dataset_store.read_take_json(folder).get(
        "t0_ms"
    )
    duration = body.duration_ms
    stopped = capture_coord.stopped_at_ms(take_id)
    if duration is None and t0 and stopped and stopped > t0:
        duration = stopped - t0  # finished from a reloaded page: Start → Stop (no pauses)
    dataset_store.update_take_json(
        folder,
        status="completed",
        t0_ms=t0,
        duration_ms=duration,
        ended_at=datetime.now(UTC).isoformat(),
        devices=clips,
    )
    repo.finish_take(take.id, TakeStatusEnum.COMPLETED, duration)
    _release_capture(take_id)  # the client stops them too; this makes sure
    # A camera whose pose didn't arrive (model not loaded at Start, page off
    # screen) gets it recovered from its clip once the uploads have settled.
    pose_backfill.schedule_take(take_id)
    return {"status": "completed", "clips": len(clips)}


# --------------------------------------------------------------------------
# Sensors + live reader
# --------------------------------------------------------------------------


@router.post("/takes/{take_id}/imu/ble/start", response_model=ImuBleStatus)
def take_imu_start(
    take_id: UUID, arm: bool = False, repo: DatasetRepo = Depends(dataset_repo)
) -> ImuBleStatus:
    """Stream both wrists into the take. `arm`: connect before the cameras start and
    store from their t = 0 (the coordinator sets it at Start)."""
    take = _get_take(take_id, repo)
    _require_recording(take)
    folder = _folder(take_id)
    units = imu_devices.units_by_hand()
    if not units:
        raise HTTPException(
            status_code=409,
            detail="No IMU units configured — run scripts/imu_tool.py assign first.",
        )
    if imu_devices.owner_id() != str(take.fighter_id):
        raise HTTPException(
            status_code=409, detail="The IMU sensors aren't assigned to this fighter."
        )
    if not imu_runner.is_running(take_id):
        t0 = capture_coord.started_at_ms(take_id) or (
            imu_runner.ARMED if arm else time.time() * 1000.0
        )
        imu_runner.start(take_id, units, t0, dataset_store.imu_writer(folder))
    return session_status(take_id)


@router.post("/takes/{take_id}/imu/ble/stop", response_model=ImuBleStatus)
def take_imu_stop(take_id: UUID) -> ImuBleStatus:
    imu_runner.stop(take_id)
    return session_status(take_id)


@router.get("/takes/{take_id}/imu/ble/status", response_model=ImuBleStatus)
def take_imu_status(take_id: UUID) -> ImuBleStatus:
    return session_status(take_id)


@router.post("/takes/{take_id}/hrv/ble/start", response_model=HrStatusOut)
def take_hr_start(
    take_id: UUID, body: HrStartBody, repo: DatasetRepo = Depends(dataset_repo)
) -> HrStatusOut:
    take = _get_take(take_id, repo)
    _require_recording(take)
    folder = _folder(take_id)
    if not hrv_runner.is_running(take_id):
        # Connects whenever asked (often before the cameras start); beats are stored
        # from the cameras' t = 0, stamped by arrival on the take's timeline.
        hrv_runner.start_ble(
            take_id,
            body.address,
            None,
            window_ms=body.window_ms,
            write_rows=dataset_store.hr_writer(
                folder, lambda: capture_coord.last_started_at_ms(take_id)
            ),
            wall_clock=True,
        )
    return HrStatusOut(session_id=take_id, is_running=True)


@router.post("/takes/{take_id}/hrv/stop", response_model=HrStatusOut)
def take_hr_stop(take_id: UUID) -> HrStatusOut:
    hrv_runner.request_stop(take_id)
    return HrStatusOut(session_id=take_id, is_running=False)


@router.get("/takes/{take_id}/live", response_model=LiveOut)
def take_live(
    take_id: UUID,
    repo: DatasetRepo = Depends(dataset_repo),
    fighters: FighterRepo = Depends(fighter_repo),
) -> LiveOut:
    """The live reader for a take: same shape as a session's."""
    take = _get_take(take_id, repo)
    folder = dataset_store.take_dir(take_id)
    # Live beats from the strap (incl. before t = 0, so a pre-connected strap shows),
    # else what the take stored.
    live = hrv_runner.recent_bpm(take_id)
    stored = dataset_store.recent_bpm(folder) if folder else []
    trace = [round(b, 1) for b in (live or stored)]
    metrics = hrv_runner.latest_metrics(take_id)
    fighter = fighters.get(take.fighter_id)
    max_hr = max_hr_for(fighter.dob if fighter else None)
    bpm = trace[-1] if trace else None
    heart = LiveHeart(
        streaming=hrv_runner.is_running(take_id),
        bpm=bpm,
        bpm_trace=trace,
        rmssd_ms=round(metrics.rmssd_ms, 1) if metrics else None,
        max_hr=max_hr,
        zone=hr_zone(bpm, max_hr) if bpm is not None and max_hr else None,
        error=hrv_runner.last_error(take_id),
    )
    return LiveOut(heart=heart, imu=session_status(take_id))


# --------------------------------------------------------------------------
# Wrist ↔ camera cross-check
# --------------------------------------------------------------------------


class TakeCrossCheckBlock(BaseModel):
    key: str  # the protocol block (jab, cross, …)
    counted: int
    confirmed: int  # felt by the wrists and seen by the cameras
    unconfirmed: int  # camera-only while the wrists streamed


class TakeCrossCheckOut(CrossCheckSummary):
    blocks: list[TakeCrossCheckBlock] = Field(default_factory=list)
    # The take's labels were already relabeled with the wrists swapped (protocol
    # card → Swap wrists); `hands` stays the verdict on the raw imu.csv.
    labels_swapped: bool = False


@router.get("/takes/{take_id}/cross-check", response_model=TakeCrossCheckOut | None)
def take_cross_check(
    take_id: UUID, repo: DatasetRepo = Depends(dataset_repo)
) -> TakeCrossCheckOut | None:
    """How the take's wrist sensors and cameras agree — overall, per camera and per
    protocol block. None when no camera pose was uploaded; status "running" while
    it's being worked out (it starts when the cameras' pose lands)."""
    _get_take(take_id, repo)
    status, xc = cross_check.result("take", take_id)
    if xc is None:
        return TakeCrossCheckOut(status="running") if status == "running" else None
    blocks: list[TakeCrossCheckBlock] = []
    folder = dataset_store.take_dir(take_id)
    try:
        protocol = json.loads((folder / "protocol.json").read_text()) if folder else {}
    except (OSError, ValueError):
        protocol = {}
    for b in protocol.get("blocks") or []:
        try:
            lo, hi, key = float(b["t_start_ms"]), float(b["t_end_ms"]), str(b["key"])
        except (KeyError, TypeError, ValueError):
            continue
        ps = [p for p in xc["punches"] if lo <= p["t_ms"] <= hi]
        blocks.append(
            TakeCrossCheckBlock(
                key=key,
                counted=sum(1 for p in ps if p["counted"]),
                confirmed=sum(1 for p in ps if p["source"] == "both"),
                unconfirmed=sum(1 for p in ps if not p["counted"]),
            )
        )
    t = xc["totals"]
    return TakeCrossCheckOut(
        status="ready",
        wrist=bool(xc["wrist"]),
        hands=xc["hands"],
        hands_swapped=bool(xc["hands_swapped"]),
        agreement=xc["agreement"],
        counted=t["counted"],
        confirmed=t["confirmed"],
        wrist_only=t["wrist_only"],
        camera_only=t["camera_only"],
        unconfirmed=t["unconfirmed"],
        cameras=[CrossCheckCamera(**c) for c in xc["cameras"]],
        blocks=blocks,
        labels_swapped=dataset_protocol.hands_swapped(protocol),
    )


# --------------------------------------------------------------------------
# Block check — is this take good enough to move on?
# --------------------------------------------------------------------------


class CheckItem(BaseModel):
    key: Literal["video", "pose", "wrists", "punches", "heart"]
    level: Literal["ok", "warn", "fail"]  # fail: redo the block; warn: worth knowing
    text: str


class TakeCheckOut(BaseModel):
    ready: bool  # saved (all clips in) — before that there's nothing to check
    ok: bool  # nothing failed: safe to move on to the next block
    checks: list[CheckItem]


def _imu_rows_by_hand(folder: Any) -> dict[str, int]:
    out = {"left": 0, "right": 0}
    try:
        with (folder / "imu.csv").open() as f:
            next(f, None)  # header
            for line in f:
                hand = line.split(",", 2)[1] if line.count(",") >= 2 else ""
                if hand in out:
                    out[hand] += 1
    except OSError:
        pass
    return out


def _rows(path: Any) -> int:
    try:
        with path.open() as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


@router.get("/takes/{take_id}/check", response_model=TakeCheckOut)
def take_check(take_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> TakeCheckOut:
    """Right after a block is saved: did every camera's video arrive, did both wrist
    sensors record (on the right wrists), were the punches there, and the heart rate.
    A "fail" means record the block again — it costs a minute, not a whole sitting."""
    take = _get_take(take_id, repo)
    folder = _folder(take_id)
    if take.status != TakeStatusEnum.COMPLETED:
        return TakeCheckOut(ready=False, ok=False, checks=[])
    checks: list[CheckItem] = []

    # Every camera that was recording at Stop owes a clip.
    have = {c["device_id"] for c in dataset_store.clips(folder)}
    owed = capture_coord.cameras_at_stop(take_id)
    missing = [label or dev for dev, label in owed.items() if dev not in have]
    if missing:
        checks.append(
            CheckItem(
                key="video",
                level="fail",
                text=f"No video from {', '.join(missing)} — keep that phone's page open until it's saved",
            )
        )
    else:
        n = len(have)
        checks.append(
            CheckItem(
                key="video", level="ok" if n else "fail", text=f"{n} camera{'s' if n != 1 else ''}"
            )
        )

    # Pose from every camera — missing pose is recovered from the clip, so it's a
    # warning, not a redo.
    take_ms = dataset_store.read_take_json(folder).get("duration_ms")
    names = {**owed}
    for m in (folder / "video").glob("*.json"):
        try:
            names.setdefault(m.stem, json.loads(m.read_text()).get("label") or m.stem)
        except (OSError, ValueError):
            pass
    short: list[str] = []
    for c in dataset_store.clips(folder):
        dev = str(c["device_id"])
        length = float(take_ms) - (c.get("start_offset_ms") or 0.0) if take_ms else None
        pose = dataset_store.pose_path(folder, dev)
        span = pose_backfill.pose_span_ms(pose) if pose.exists() else 0.0
        if length and span < pose_backfill.MIN_COVERAGE * length:
            short.append(names.get(dev) or dev)
    if short:
        checks.append(
            CheckItem(
                key="pose",
                level="warn",
                text=f"No pose from {', '.join(short)} (its page's pose model wasn't ready, or it "
                "went off screen) — recovering it from the video, about a minute",
            )
        )
    elif have:
        checks.append(CheckItem(key="pose", level="ok", text=f"pose from {len(have)} cameras"))

    # Both wrists streamed.
    rows = _imu_rows_by_hand(folder)
    most = max(rows.values())
    if most == 0:
        checks.append(CheckItem(key="wrists", level="fail", text="No wrist-sensor data"))
    else:
        quiet = [h for h, n in rows.items() if n < 0.3 * most]
        checks.append(
            CheckItem(
                key="wrists",
                level="fail" if quiet else "ok",
                text=f"{' and '.join(quiet).capitalize()} wrist sensor barely recorded"
                if quiet
                else "both wrists recorded",
            )
        )

    # The block's punches, from the labeller's counts (protocol.json, read-only).
    try:
        proto = json.loads((folder / "protocol.json").read_text())
    except (OSError, ValueError):
        proto = {}
    blocks = [
        b
        for b in proto.get("blocks") or []
        if b.get("t_end_ms") is not None and not b.get("discarded")
    ]
    block_key = dataset_store.read_take_json(folder).get("block")
    if block_key and not any(b.get("key") == block_key for b in blocks):
        checks.append(
            CheckItem(key="punches", level="fail", text="The block wasn't marked — record it again")
        )
    for b in blocks:
        spec = dataset_protocol.SPECS.get(str(b.get("key")))
        if spec is None or not spec.reps or spec.kind not in ("typed", "combo"):
            continue
        reps, got, other = spec.reps, b.get("detected"), b.get("off_hand")
        unit = "combos" if spec.kind == "combo" else "punches"
        if got is None:
            continue
        level: Literal["ok", "warn", "fail"]
        if other is not None and other > got and other >= 0.5 * reps:
            level, text = (
                "fail",
                f"{spec.title}: {other} {unit} on the other wrist, {got} on the expected one — "
                "the sensors look swapped (Swap wrists, then run the wrist check)",
            )
        elif got < 0.8 * reps:
            level, text = "fail", f"{spec.title}: only {got} of {reps} {unit} found"
        elif got > 1.25 * reps:
            level, text = "warn", f"{spec.title}: {got} {unit} for {reps} cues"
        else:
            level, text = "ok", f"{spec.title}: {got}/{reps} {unit}"
        checks.append(CheckItem(key="punches", level=level, text=text))

    beats = _rows(folder / "hr.csv")
    checks.append(
        CheckItem(
            key="heart",
            level="ok" if beats else "warn",
            text=f"{beats} heartbeats" if beats else "No heart rate — the strap wasn't connected",
        )
    )
    return TakeCheckOut(ready=True, ok=not any(c.level == "fail" for c in checks), checks=checks)


@router.post("/takes/{take_id}/pose/recover", response_model=dict)
def take_pose_recover(take_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> dict[str, Any]:
    """Recover the take's missing camera pose from its clips now (in the background)."""
    take = _get_take(take_id, repo)
    if take.status != TakeStatusEnum.COMPLETED:
        raise HTTPException(status_code=409, detail="the take isn't saved yet")
    gaps = pose_backfill.take_gaps(_folder(take_id))
    if gaps:
        pose_backfill.schedule_take(take_id, delay_s=0.0)
    return {"recovering": [g.device_id for g in gaps]}
