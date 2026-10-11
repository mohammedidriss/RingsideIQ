"""Train a dataset's camera punch counter on its export (services.training).

POST /v2/datasets/{id}/train      start training on the latest export (202)
GET  /v2/datasets/{id}/training   running? which step, the latest report, every run
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.routes.auth import require_current_user
from api.routes.datasets import dataset_repo
from api.services import training
from store import DatasetRepo

router = APIRouter(prefix="/v2", tags=["training"], dependencies=[Depends(require_current_user)])


@router.post("/datasets/{dataset_id}/train", status_code=202, response_model=dict)
def train(dataset_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> dict[str, Any]:
    """Train on the dataset's latest export — export first so it has every take."""
    if repo.get(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if not training.manifest_path(dataset_id).exists():
        raise HTTPException(status_code=409, detail="Export the dataset for training first")
    if not training.start(dataset_id):
        raise HTTPException(status_code=409, detail="Already training — wait for it to finish")
    return training.status(dataset_id)


@router.get("/datasets/{dataset_id}/training", response_model=dict)
def training_status(dataset_id: UUID, repo: DatasetRepo = Depends(dataset_repo)) -> dict[str, Any]:
    if repo.get(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return training.status(dataset_id)
