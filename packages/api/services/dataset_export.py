"""Dataset export — a de-identified copy of the dataset for training (ADR-013, ADR-014).

`build_manifest` turns a dataset's takes into one JSON document:

- **takes**: every completed take, named by its participant code (F-0001), with
  its files (pose, imu.csv, hr.csv, labels.json) and its protocol blocks.
- **splits**: train / val / test as time segments of takes.
- **warnings**: what a reviewer should fix before trusting the numbers.

`write_export` then copies those files into `{dataset}/export/` next to the
manifest, so the export stands on its own and holds nothing but the data:

    export/manifest.json
    export/takes/{take_id}/take.json      code, stance, block, timing — no names
    export/takes/{take_id}/labels.json
    export/takes/{take_id}/imu.csv, hr.csv
    export/takes/{take_id}/pose/{camera}.parquet

Video isn't copied: it shows faces, and the face-blurred copies come with the
next step of ADR-014. Before the copy replaces the previous export, every file
is checked for personal data (`common.privacy`) — field names like `name` or
`email`, and the participants' own names and emails anywhere in the text. If
anything is found the export is refused and the previous one stays.

Splits follow docs/studies/RQ2_DATASET_PROTOCOL.md §7:

- **by_fighter** (3+ participants): whole participants are held out, so no
  person is in both train and test. A seeded shuffle keeps the split stable.
- **pilot** (1–2 participants): there's no one to hold out, so each take is
  split by block — the single-punch blocks (and the no-punch block, for
  negatives) train; combos and free shadowboxing test. Fine for proving the
  pipeline; it can't support a claim that the model generalizes to other people.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import random
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from api.services import dataset_protocol as proto
from api.services import dataset_store
from common.privacy import PersonalDataError, find_personal_data

BY_FIGHTER_MIN = 3
EXPORT = "export"

# take.json fields the export keeps — everything else (the dataset's name, device
# labels a phone may carry its owner's name in, …) stays behind.
TAKE_FIELDS = ("take_id", "participant", "protocol", "block", "stance", "duration_ms", "timeline")


@dataclass(frozen=True)
class TakeRef:
    take_id: UUID
    participant: str  # the participant's code, e.g. "F-0001"
    status: str
    duration_ms: float | None


def export_dir(dataset_id: UUID) -> Path:
    return dataset_store.dataset_dir(dataset_id) / EXPORT


def manifest_path(dataset_id: UUID) -> Path:
    return export_dir(dataset_id) / "manifest.json"


def _labels_version(path: Path) -> str:
    """The labels file's content hash — changes whenever the labels do."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _take_entry(ref: TakeRef, folder: Path) -> dict[str, Any]:
    data = proto.load(folder)
    take = dataset_store.read_take_json(folder)
    labels: dict[str, Any] | None = None
    if proto.labels_path(folder).exists():
        try:
            count = len(json.loads(proto.labels_path(folder).read_text()))
        except ValueError:
            count = 0
        labels = {
            "path": "labels.json",
            "count": count,
            "reviewed": proto.labels_edited(folder, data),
            "version": _labels_version(proto.labels_path(folder)),
        }
    blocks = [
        {k: b.get(k) for k in ("key", "t_start_ms", "t_end_ms", "detected", "off_hand")}
        for b in data["blocks"]
        if not b.get("discarded") and b.get("t_end_ms") is not None
    ]
    return {
        "take_id": str(ref.take_id),
        "participant": ref.participant,
        "stance": take.get("stance"),
        "duration_ms": ref.duration_ms,
        "folder": f"takes/{ref.take_id}",
        # Each camera's clip start on the take timeline (its pose file is already
        # on that timeline). Videos are left out until they're face-blurred.
        "cameras": [
            {"camera": c["device_id"], "start_offset_ms": c["start_offset_ms"]}
            for c in dataset_store.clips(folder)
        ],
        "video": [],
        "pose": sorted(f"pose/{p.name}" for p in (folder / "pose").glob("*.parquet")),
        "imu": "imu.csv" if (folder / "imu.csv").exists() else None,
        "hr": "hr.csv" if (folder / "hr.csv").exists() else None,
        "labels": labels,
        "blocks": blocks,
        # imu.csv is raw: when True its "left" rows are the right wrist and vice
        # versa (sensors worn swapped); labels.json is already corrected.
        "imu_hands_swapped": proto.hands_swapped(data),
        "swap_evidence": proto.swap_evidence(data, proto.camera_hands(folder)),
    }


def _whole(entry: dict[str, Any]) -> dict[str, Any]:
    return {"take_id": entry["take_id"], "t_start_ms": 0.0, "t_end_ms": entry["duration_ms"]}


def _split_by_participant(
    entries: list[dict[str, Any]], seed: str, test_frac: float, val_frac: float
) -> dict[str, list[dict[str, Any]]]:
    people = sorted({e["participant"] for e in entries})
    random.Random(seed).shuffle(people)
    n_test = max(1, math.ceil(len(people) * test_frac))
    n_val = max(1, math.ceil(len(people) * val_frac))
    test, val = set(people[:n_test]), set(people[n_test : n_test + n_val])
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}
    for e in entries:
        who = e["participant"]
        splits["test" if who in test else "val" if who in val else "train"].append(_whole(e))
    return splits


def _split_pilot(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}
    for e in entries:
        for b in e["blocks"]:
            kind = proto.SPECS[b["key"]].kind
            splits["test" if kind in ("free", "combo") else "train"].append(
                {
                    "take_id": e["take_id"],
                    "t_start_ms": b["t_start_ms"],
                    "t_end_ms": b["t_end_ms"],
                    "block": b["key"],
                }
            )
    return splits


def build_manifest(
    dataset_id: UUID,
    protocol: str | None,
    takes: list[TakeRef],
    *,
    test_frac: float = 0.2,
    val_frac: float = 0.2,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    warnings: list[str] = []
    for ref in takes:
        folder = dataset_store.take_dir(ref.take_id)
        if ref.status != "completed":
            excluded.append({"take_id": str(ref.take_id), "reason": ref.status})
            continue
        if folder is None:
            excluded.append({"take_id": str(ref.take_id), "reason": "folder missing"})
            continue
        entries.append(_take_entry(ref, folder))

    for e in entries:
        tid = e["take_id"][:8]
        if not e["cameras"]:
            warnings.append(f"take {tid}: no video")
        if e["imu"] is None:
            warnings.append(f"take {tid}: no wrist-sensor data — nothing to label from")
        if e["labels"] is None:
            warnings.append(f"take {tid}: no labels — run the protocol or regenerate")
        elif not e["labels"]["reviewed"]:
            warnings.append(f"take {tid}: labels are unreviewed auto-labels")
        if e["imu_hands_swapped"]:
            warnings.append(
                f"take {tid}: sensors were worn on swapped wrists — imu.csv's hand column "
                "is reversed (see imu_hands_swapped); labels are corrected"
            )
        if e["swap_evidence"]:
            warnings.append(
                f"take {tid}: wrist sensors look swapped ({'; '.join(e['swap_evidence'])}) "
                "— check it and use Swap wrists on the take"
            )

    n_people = len({e["participant"] for e in entries})
    if n_people >= BY_FIGHTER_MIN:
        strategy = "by_fighter"
        splits = _split_by_participant(entries, str(dataset_id), test_frac, val_frac)
    else:
        strategy = "pilot"
        splits = _split_pilot(entries)
        if entries:
            warnings.append(
                f"pilot split: {n_people} participant(s), so nobody is held out — single-punch "
                "blocks train, combos and free shadowboxing test; results can't claim "
                f"generalization to other people (needs {BY_FIGHTER_MIN}+ participants)"
            )

    return {
        "dataset_id": str(dataset_id),
        "protocol": protocol,
        "exported_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "split_strategy": strategy,
        "counts": {
            "takes": len(entries),
            "fighters": n_people,
            "labels": sum((e["labels"] or {}).get("count", 0) for e in entries),
        },
        "takes": entries,
        "splits": splits,
        "excluded": excluded,
        "warnings": warnings,
    }


def _copy_take(entry: dict[str, Any], src: Path, dst: Path) -> None:
    (dst / "pose").mkdir(parents=True)
    raw = dataset_store.read_take_json(src)
    take = {k: raw.get(k) for k in TAKE_FIELDS if k in raw}
    take["participant"] = entry["participant"]
    (dst / "take.json").write_text(json.dumps(take, indent=2, default=str) + "\n")
    labels = (entry["labels"] or {}).get("path")
    if labels and (src / labels).exists():
        shutil.copy2(src / labels, dst / labels)
    # The data files are hard-linked, not copied: the export costs no extra disk
    # (pose alone is ~800 MB for 19 takes). Only completed takes are exported, and
    # nothing appends to their sensor files after that; these files carry numbers
    # only, so sharing them with the take folder can't bring a name into the export.
    for rel in [entry["imu"], entry["hr"], *entry["pose"]]:
        if rel and (src / rel).exists():
            try:
                os.link(src / rel, dst / rel)
            except OSError:
                shutil.copy2(src / rel, dst / rel)


def _check(staging: Path, known: list[str]) -> list[str]:
    """Personal data anywhere in the staged export: JSON content, CSV headers and
    every file and folder name."""
    found: list[str] = []
    for path in sorted(staging.rglob("*")):
        rel = str(path.relative_to(staging))
        found += find_personal_data(rel, known, where=f"path {rel}")
        if path.suffix == ".json":
            found += find_personal_data(json.loads(path.read_text()), known, where=rel)
        elif path.suffix == ".csv":
            with path.open() as f:
                found += find_personal_data(f.readline().strip().split(","), known, where=rel)
    return found


def write_export(dataset_id: UUID, manifest: dict[str, Any], known: Iterable[str | None]) -> Path:
    """Copy the manifest's takes into `{dataset}/export/` and write the manifest.
    `known` are the participants' names and emails to make sure none of it carries.
    Raises PersonalDataError (leaving the previous export as it was) if any does."""
    out = export_dir(dataset_id)
    staging = out.with_name(f"{EXPORT}.tmp-{uuid.uuid4().hex[:8]}")
    staging.mkdir(parents=True)
    try:
        for entry in manifest["takes"]:
            folder = dataset_store.take_dir(UUID(entry["take_id"]))
            if folder is not None:
                _copy_take(entry, folder, staging / entry["folder"])
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        found = _check(staging, [k for k in known if k])
        if found:
            raise PersonalDataError(found)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    old = out.with_name(f"{EXPORT}.old-{uuid.uuid4().hex[:8]}")
    if out.exists():
        os.replace(out, old)
    os.replace(staging, out)
    shutil.rmtree(old, ignore_errors=True)
    return out / "manifest.json"


def drop_takes(dataset_id: UUID, take_ids: set[str]) -> int:
    """Remove takes from the current export — their copied files and every manifest
    entry and split segment that points at them. How many takes were removed."""
    out = export_dir(dataset_id)
    removed = 0
    for tid in take_ids:
        folder = out / "takes" / tid
        if folder.exists():
            shutil.rmtree(folder)
            removed += 1
    path = manifest_path(dataset_id)
    if path.exists():
        m = json.loads(path.read_text())
        m["takes"] = [t for t in m.get("takes", []) if t["take_id"] not in take_ids]
        m["splits"] = {
            k: [s for s in v if s["take_id"] not in take_ids]
            for k, v in (m.get("splits") or {}).items()
        }
        m["excluded"] = [x for x in m.get("excluded", []) if x["take_id"] not in take_ids]
        m["counts"] = {
            "takes": len(m["takes"]),
            "fighters": len({t["participant"] for t in m["takes"]}),
            "labels": sum((t.get("labels") or {}).get("count", 0) for t in m["takes"]),
        }
        tmp = path.with_name("manifest.json.tmp")
        tmp.write_text(json.dumps(m, indent=2) + "\n")
        os.replace(tmp, path)
    return removed
