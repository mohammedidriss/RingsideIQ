"""Training the camera punch counter on a dataset's export (services.training):
train stretches teach it, test stretches score it next to the current counter."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_punch_model import schedule, synthetic

pytest.importorskip("sklearn")


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from api.services import dataset_store, training

    monkeypatch.setattr(dataset_store, "DATASETS_DIR", tmp_path / "datasets")
    monkeypatch.setattr(training, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(training, "BACKGROUND", False)
    yield tmp_path


def _export(did: str, fid: str) -> None:
    """A dataset export: three takes to train on, one to test on, each one camera
    of synthetic pose with labelled punches."""
    from api.services import dataset_store
    from capture.cv.writer import write_pose_parquet

    root = dataset_store.dataset_dir(UUID(did))
    takes, splits = [], {"train": [], "val": [], "test": []}
    for k in range(4):
        tid = str(uuid4())
        folder = root / "takes" / tid
        (folder / "pose").mkdir(parents=True)
        punches = schedule(40, 1500 + 100 * k)
        write_pose_parquet(folder / "pose" / "cam1.parquet", synthetic(40, punches, seed=k))
        (folder / "labels.json").write_text(
            json.dumps([{"t_ms": t, "hand": h, "punch_type": None} for t, h in punches])
        )
        takes.append(
            {
                "take_id": tid,
                "fighter_id": fid,
                "folder": f"takes/{tid}",
                "pose": ["pose/cam1.parquet"],
                "labels": {"path": "labels.json", "count": len(punches), "reviewed": False},
            }
        )
        split = "test" if k == 3 else "train"
        splits[split].append({"take_id": tid, "t_start_ms": 0, "t_end_ms": 40_000, "block": "jab"})
    (root / "export").mkdir(parents=True)
    (root / "export" / "manifest.json").write_text(
        json.dumps(
            {
                "exported_at": "2026-10-07T10:00:00+00:00",
                "split_strategy": "pilot",
                "takes": takes,
                "splits": splits,
                "warnings": ["pilot split: 1 fighter(s)"],
            }
        )
    )


def test_training_scores_the_new_counter_against_the_current_one(
    authed_client: TestClient, stores: Path
) -> None:
    from api.services import training

    did = authed_client.post("/v2/datasets", json={"name": "RQ2", "protocol": "rq2-v1"}).json()[
        "id"
    ]
    fid = authed_client.post("/fighters", json={"name": "Mohamad"}).json()["id"]
    # nothing exported yet
    assert authed_client.post(f"/v2/datasets/{did}/train").status_code == 409

    _export(did, fid)
    started = authed_client.post(f"/v2/datasets/{did}/train")
    assert started.status_code == 202 and started.json()["running"] is True
    # (the background thread is off in tests: run it here, as the thread would)
    training.run(UUID(did))
    training._status.pop(did, None)

    st = authed_client.get(f"/v2/datasets/{did}/training").json()
    report = st["latest"]
    assert st["running"] is False and report["version"] == 1
    assert report["data"]["train_stretches"] == 3 and report["data"]["test_stretches"] == 1
    assert report["data"]["reviewed_labels"] is False
    assert report["test"]["model"]["f1"] >= 0.85  # it learnt the punches…
    assert {"model", "baseline"} <= set(report["test"])  # …scored next to today's counter
    assert report["by_block"][0]["block"] == "jab"
    assert Path(report["model_file"]).exists()
    assert st["runs"] == [
        {
            "version": 1,
            "created_at": report["created_at"],
            "f1_model": report["test"]["model"]["f1"],
            "f1_baseline": report["test"]["baseline"]["f1"],
        }
    ]
