"""Withdraw a participant from a dataset (ADR-014).

Deletes everything the dataset holds for one participant code — every take's
folder (video, pose, wrist and heart data, labels), its face-blurred copies and
its copy in the export,
whose manifest and splits drop it — and appends one line to the dataset's
deletion log: the code, when, and how much went. The log holds counts only, no
data. Models already trained are not changed (the consent form says so); the
next training run simply doesn't see the participant.

    data/datasets/{dataset_id}/deletions.jsonl
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from api.services import dataset_export, dataset_store, video_blur

LOG = "deletions.jsonl"


def log_path(dataset_id: UUID) -> Path:
    return dataset_store.dataset_dir(dataset_id) / LOG


def delete_files(dataset_id: UUID, code: str, take_ids: list[UUID]) -> dict[str, Any]:
    """Delete the takes' folders and their exported copies; log and return the counts."""
    counts = {"takes": 0, "clips": 0, "blurred_clips": 0, "imu_rows": 0, "hr_rows": 0, "labels": 0}
    for tid in take_ids:
        counts["takes"] += 1
        folder = dataset_store.take_dir(tid)
        if folder is None:
            continue
        s = dataset_store.summary(folder)
        counts["clips"] += len(s["clips"])
        counts["imu_rows"] += s["imu_rows"]
        counts["hr_rows"] += s["hr_rows"]
        counts["labels"] += s["labels"] or 0
        counts["blurred_clips"] += video_blur.delete_take(folder)
        shutil.rmtree(folder)
    exported = dataset_export.drop_takes(dataset_id, {str(t) for t in take_ids})
    entry = {
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "action": "withdrawn",
        "participant": code,
        **counts,
        "exported_takes": exported,
        "trained_models": "unchanged",
    }
    path = log_path(dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_log(dataset_id: UUID) -> list[dict[str, Any]]:
    try:
        lines = log_path(dataset_id).read_text().splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
