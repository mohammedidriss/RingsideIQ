"""Dataset export — write the de-identified training copy (ADR-013, ADR-014).

    POST /v2/datasets/{dataset_id}/export   → {dataset}/export/ (manifest.json + takes/)
    GET  /v2/datasets/{dataset_id}/blur     face blurring: progress and every clip's state
    POST /v2/datasets/{dataset_id}/blur     start blurring what's pending
    GET  /v2/datasets/{dataset_id}/blur/{take_id}/{camera}.jpg   a clip's blurred close-ups

See `api.services.dataset_export` for what the export holds, how splits are
made, and the personal-data check every export passes before it's written; and
`api.services.video_blur` for the face-blurred video it includes. An export
starts blurring any clip that isn't yet, in the background — export again when
it's done to include those videos.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from api.deps import db_session
from api.routes.auth import require_current_user
from api.routes.datasets import dataset_repo
from api.services import dataset_export, video_blur
from common.privacy import PersonalDataError
from store import DatasetRepo, Fighter, TakeStatusEnum, User

router = APIRouter(
    prefix="/datasets", tags=["datasets"], dependencies=[Depends(require_current_user)]
)


def personal_strings(db: DBSession, fighter_ids: Iterable[UUID]) -> list[str]:
    """What identifies these participants — full names, nicknames, birth dates,
    IDs and their accounts' names and emails — for the export's personal-data
    check to look for. (Not each part of a name on its own: "Test" or "Free"
    would match the export's own words; the export keeps only known fields, so
    a name could only reach it whole.)"""
    ids = list(fighter_ids)
    out: list[str | None] = []
    for f in db.exec(select(Fighter).where(col(Fighter.id).in_(ids))).all():
        out += [f.name, f.nickname, f.boxrec_id, f.usa_boxing_id]
        out.append(f.dob.isoformat() if f.dob else None)
    for u in db.exec(select(User).where(col(User.profile_id).in_(ids))).all():
        out += [u.email, u.name]
    return [s for s in out if s]


@router.post("/{dataset_id}/export")
def export_dataset(
    dataset_id: UUID,
    blur: bool = True,  # start blurring videos that aren't yet (Train passes false)
    repo: DatasetRepo = Depends(dataset_repo),
    db: DBSession = Depends(db_session),
) -> dict[str, Any]:
    dataset = repo.get(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    code = {p.fighter_id: p.code or "" for p in repo.ensure_codes(dataset_id)}
    takes = repo.takes(dataset_id)
    refs = [
        dataset_export.TakeRef(
            take_id=t.id,
            participant=code.get(t.fighter_id, ""),
            status=str(t.status),
            duration_ms=t.duration_ms or None,
        )
        for t in takes
    ]
    manifest = dataset_export.build_manifest(dataset_id, dataset.protocol, refs)
    known = personal_strings(db, {t.fighter_id for t in takes})
    try:
        path = dataset_export.write_export(dataset_id, manifest, known)
    except PersonalDataError as e:
        raise HTTPException(
            status_code=409,
            detail="Export stopped — it would contain personal data: " + "; ".join(e.findings[:5]),
        ) from e
    completed = _completed(repo, dataset_id)
    if blur and any(
        v["reason"] == "not blurred yet" for t in manifest["takes"] for v in t["video_left_out"]
    ):
        video_blur.start(dataset_id, completed)
    job = video_blur.status(dataset_id, completed)
    return {
        **manifest,
        "path": str(path),
        "blur": {k: job[k] for k in ("running", "done", "total", "counts", "error") if k in job},
    }


def _completed(repo: DatasetRepo, dataset_id: UUID) -> list[UUID]:
    return [t.id for t in repo.takes(dataset_id) if t.status == TakeStatusEnum.COMPLETED]


@router.get("/{dataset_id}/blur")
def blur_status(dataset_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> dict[str, Any]:
    if repo.get(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return video_blur.status(dataset_id, _completed(repo, dataset_id))


@router.post("/{dataset_id}/blur")
def start_blur(dataset_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> dict[str, Any]:
    if repo.get(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    completed = _completed(repo, dataset_id)
    video_blur.start(dataset_id, completed)
    return video_blur.status(dataset_id, completed)


@router.get("/{dataset_id}/blur/{take_id}/{camera}.jpg")
def blur_check(
    dataset_id: UUID, take_id: UUID, camera: str, repo: DatasetRepo = Depends(dataset_repo)
) -> FileResponse:
    take = repo.get_take(take_id)
    path = (
        video_blur.check_image(take_id, camera) if take and take.dataset_id == dataset_id else None
    )
    if path is None:
        raise HTTPException(status_code=404, detail="no close-ups for this clip yet")
    return FileResponse(path, media_type="image/jpeg")
