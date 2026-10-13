"""Dataset export — write the de-identified training copy (ADR-013, ADR-014).

    POST /v2/datasets/{dataset_id}/export   → {dataset}/export/ (manifest.json + takes/)

See `api.services.dataset_export` for what the export holds, how splits are
made, and the personal-data check every export passes before it's written.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from api.deps import db_session
from api.routes.auth import require_current_user
from api.routes.datasets import dataset_repo
from api.services import dataset_export
from common.privacy import PersonalDataError
from store import DatasetRepo, Fighter, User

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
    return {**manifest, "path": str(path)}
