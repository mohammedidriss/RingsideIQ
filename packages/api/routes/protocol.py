"""Dataset recording protocol — mark blocks during a take, get labels out (ADR-013).

    GET  /v2/protocol/plan                                 the standard block list
    GET  /v2/takes/{take_id}/protocol                      blocks so far, live count
    POST /v2/takes/{take_id}/protocol/start   {key}        open a block (closes any open one)
    POST /v2/takes/{take_id}/protocol/end                  close it → labels regenerate
    POST /v2/takes/{take_id}/protocol/blocks/{i}/discard   redo: drop a block from the labels
    POST /v2/takes/{take_id}/protocol/labels?overwrite=    regenerate labels.json

Blocks are timed on the take timeline (`capture_coord.timeline_now_ms`), so
they line up with imu.csv, pose and the clips. Logic lives in
`api.services.dataset_protocol`; state lives in the take folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.routes import capture_coord
from api.routes.auth import require_current_user
from api.services import dataset_protocol as proto
from api.services import dataset_store, imu_runner

plan_router = APIRouter(
    prefix="/protocol", tags=["protocol"], dependencies=[Depends(require_current_user)]
)
router = APIRouter(prefix="/takes", tags=["protocol"], dependencies=[Depends(require_current_user)])


class BlockSpecOut(BaseModel):
    key: str
    title: str
    kind: str
    punch_type: str | None
    side: str | None
    reps: int | None
    duration_s: int | None
    hint: str
    sequence: list[tuple[str, str]] | None = None  # combo: punches in order (type, side)
    pace_s: float | None = None  # combo: seconds between combos
    callout: str = ""  # combo: what automatic mode says, e.g. "One-two"


class BlockOut(BaseModel):
    index: int
    key: str
    title: str
    t_start_ms: float
    t_end_ms: float | None
    discarded: bool = False
    detected: int | None = None  # punches on the expected wrist(s); live for the open block
    off_hand: int | None = None


class LabelsInfo(BaseModel):
    generated_at: str
    count: int
    stance: str | None = None


class ProtocolOut(BaseModel):
    plan: list[BlockSpecOut]
    stance: str | None
    lead_hand: str
    blocks: list[BlockOut]
    active: int | None
    labels: LabelsInfo | None
    labels_edited: bool  # labels.json was reviewed/changed since it was generated
    imu_running: bool
    recording: bool  # the cameras are rolling — blocks can be started
    imu_hands_swapped: bool  # labels read imu.csv with left/right reversed
    swap_evidence: list[str]  # why the sensors look swapped; empty if they don't
    take_block: str | None = None  # a one-block take (take.json "block"): only this block


class StartIn(BaseModel):
    key: str


class SwapIn(BaseModel):
    swapped: bool


def _take(take_id: UUID) -> tuple[Path, str | None]:
    """The take's folder and the stance it was recorded in (from take.json)."""
    folder = dataset_store.take_dir(take_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="take not found")
    stance = dataset_store.read_take_json(folder).get("stance")
    return folder, str(stance) if stance else None


def _now(take_id: UUID) -> float:
    t = capture_coord.timeline_now_ms(take_id)
    if t is None:
        raise HTTPException(
            status_code=409,
            detail="Start the cameras first — blocks are timed from the recording.",
        )
    return t


def _block_of(folder: Path) -> str | None:
    """The protocol block a one-block take was created for, if any."""
    block = dataset_store.read_take_json(folder).get("block")
    return str(block) if block in proto.SPECS else None


def _out(take_id: UUID, folder: Path, data: dict[str, Any], stance: str | None) -> ProtocolOut:
    active = proto.active_index(data)
    now = capture_coord.timeline_now_ms(take_id)
    live = proto.live_count(folder, data, stance, now) if now is not None else None
    blocks: list[BlockOut] = []
    for i, b in enumerate(data["blocks"]):
        out = BlockOut(
            index=i,
            key=b["key"],
            title=proto.SPECS[b["key"]].title,
            t_start_ms=b["t_start_ms"],
            t_end_ms=b.get("t_end_ms"),
            discarded=bool(b.get("discarded")),
            detected=b.get("detected"),
            off_hand=b.get("off_hand"),
        )
        if live is not None and i == live.index:
            out.detected, out.off_hand = live.detected, live.off_hand
        blocks.append(out)
    info = data.get("labels")
    return ProtocolOut(
        plan=[BlockSpecOut(**s) for s in proto.plan_out()],
        stance=stance,
        lead_hand=proto.lead_hand(stance),
        blocks=blocks,
        active=active,
        labels=LabelsInfo(**{k: info[k] for k in ("generated_at", "count", "stance")})
        if info
        else None,
        labels_edited=proto.labels_edited(folder, data),
        imu_running=imu_runner.is_running(take_id),
        recording=now is not None,
        imu_hands_swapped=proto.hands_swapped(data),
        swap_evidence=proto.swap_evidence(data, proto.camera_hands(folder)),
        take_block=_block_of(folder),
    )


def _relabel(folder: Path, data: dict[str, Any], stance: str | None) -> None:
    """Keep labels.json in step with the blocks — unless someone has edited it."""
    if not proto.labels_edited(folder, data):
        proto.generate_labels(folder, data, stance)


@plan_router.get("/plan", response_model=list[BlockSpecOut])
def get_plan() -> list[BlockSpecOut]:
    return [BlockSpecOut(**s) for s in proto.plan_out()]


@router.get("/{take_id}/protocol", response_model=ProtocolOut)
def get_protocol(take_id: UUID) -> ProtocolOut:
    folder, stance = _take(take_id)
    return _out(take_id, folder, proto.load(folder), stance)


@router.post("/{take_id}/protocol/start", response_model=ProtocolOut)
def start_block(take_id: UUID, body: StartIn) -> ProtocolOut:
    folder, stance = _take(take_id)
    data = proto.load(folder)
    had_open = proto.active_index(data) is not None
    try:
        proto.start_block(data, body.key, _now(take_id), proto.imu_size(folder))
    except proto.ProtocolError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if had_open:
        _relabel(folder, data, stance)
    proto.save(folder, data)
    return _out(take_id, folder, data, stance)


@router.post("/{take_id}/protocol/end", response_model=ProtocolOut)
def end_block(take_id: UUID) -> ProtocolOut:
    folder, stance = _take(take_id)
    data = proto.load(folder)
    now = capture_coord.timeline_now_ms(take_id)
    if now is None:
        # Stopped with a block still open: close it at the end of the recording.
        now = dataset_store.read_take_json(folder).get("duration_ms")
        if now is None:
            raise HTTPException(status_code=409, detail="The take isn't recording.")
    try:
        proto.end_block(data, float(now))
    except proto.ProtocolError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _relabel(folder, data, stance)
    proto.save(folder, data)
    return _out(take_id, folder, data, stance)


@router.post("/{take_id}/protocol/blocks/{index}/discard", response_model=ProtocolOut)
def discard_block(take_id: UUID, index: int) -> ProtocolOut:
    folder, stance = _take(take_id)
    data = proto.load(folder)
    try:
        proto.discard_block(data, index)
    except proto.ProtocolError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    _relabel(folder, data, stance)
    proto.save(folder, data)
    return _out(take_id, folder, data, stance)


@router.post("/{take_id}/protocol/swap-wrists", response_model=ProtocolOut)
def swap_wrists(take_id: UUID, body: SwapIn, overwrite: bool = False) -> ProtocolOut:
    """Mark the take's sensors as worn on swapped wrists (or undo it) and relabel.
    imu.csv is never rewritten; the setting is read wherever it's used."""
    folder, stance = _take(take_id)
    data = proto.load(folder)
    previous = proto.hands_swapped(data)
    data["imu_hands_swapped"] = body.swapped
    if any(b.get("t_end_ms") is not None for b in data["blocks"]):
        try:
            proto.generate_labels(folder, data, stance, overwrite=overwrite)
        except proto.ProtocolError as e:
            data["imu_hands_swapped"] = previous
            raise HTTPException(status_code=409, detail=str(e)) from e
    proto.save(folder, data)
    return _out(take_id, folder, data, stance)


@router.post("/{take_id}/protocol/labels", response_model=ProtocolOut)
def regenerate_labels(take_id: UUID, overwrite: bool = False) -> ProtocolOut:
    folder, stance = _take(take_id)
    data = proto.load(folder)
    if not data["blocks"]:
        raise HTTPException(status_code=409, detail="No protocol blocks recorded yet.")
    try:
        proto.generate_labels(folder, data, stance, overwrite=overwrite)
    except proto.ProtocolError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    proto.save(folder, data)
    return _out(take_id, folder, data, stance)
