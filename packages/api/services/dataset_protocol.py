"""Dataset recording protocol — self-labeling takes for the RQ2 punch dataset.

The fighter works through fixed blocks (30 jabs, 30 crosses, …) while the coach
marks each block's start and end on the take screen. Every punch inside a block
has a known type, and the wrist IMUs give each one's exact time, so a take
comes out labeled with no hand-tagging:

    block (type + lead/rear)  ×  IMU bursts on that wrist  →  {take}/labels.json

Everything lives in the take's folder (ADR-013); this side owns two files:

    protocol.json   block markers, per-block QA counts, label provenance
    labels.json     [{t_ms, hand, punch_type}] — what studies.evaluation reads

and reads `imu.csv`, which `imu_runner` appends to while the take records.
Block times are on the take timeline (ms since the cameras' synchronized start;
takes can't pause), the same axis as imu.csv, pose and the clips. The
methodology is in docs/studies/RQ2_DATASET_PROTOCOL.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import os
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from analyze.imu_punches import MIN_GAP_MS, PROFILES, SMOOTH_MS, THRESHOLD_G, detect_punches

Side = Literal["lead", "rear", "both"]
Kind = Literal["typed", "combo", "negative", "free"]
HANDS = ("left", "right")


@dataclass(frozen=True)
class BlockSpec:
    key: str
    title: str
    kind: Kind
    punch_type: str | None = None  # "jab" | "cross" | "hook" | "uppercut"
    side: Side | None = None
    reps: int | None = None
    duration_s: int | None = None
    hint: str = ""
    # Combo blocks: the punches thrown on each beep, in order, and the beep spacing.
    sequence: tuple[tuple[str, Side], ...] | None = None
    pace_s: float | None = None
    callout: str = ""  # what automatic mode says for the combo, e.g. "One-two"


# Six typed blocks give the 6-class type classifier (H2b) its classes; combos put
# the same punches in fight-pace sequences with every punch still labeled; the
# negatives teach the detector what isn't a punch; free shadowboxing is the
# realistic held-out test material.
PLAN: list[BlockSpec] = [
    BlockSpec("jab", "Jab", "typed", "jab", "lead", reps=30, hint="Return to guard each time"),
    BlockSpec("cross", "Cross", "typed", "cross", "rear", reps=30),
    BlockSpec("lead_hook", "Lead hook", "typed", "hook", "lead", reps=30),
    BlockSpec("rear_hook", "Rear hook", "typed", "hook", "rear", reps=30),
    BlockSpec("lead_uppercut", "Lead uppercut", "typed", "uppercut", "lead", reps=30),
    BlockSpec("rear_uppercut", "Rear uppercut", "typed", "uppercut", "rear", reps=30),
    BlockSpec(
        "combo_12",
        "1-2",
        "combo",
        side="both",
        reps=15,
        pace_s=3.0,
        sequence=(("jab", "lead"), ("cross", "rear")),
        callout="One-two",
        hint="Jab, cross at natural speed, then back to guard",
    ),
    BlockSpec(
        "combo_123",
        "1-2-3",
        "combo",
        side="both",
        reps=15,
        pace_s=3.0,
        sequence=(("jab", "lead"), ("cross", "rear"), ("hook", "lead")),
        callout="One-two-three",
        hint="Jab, cross, lead hook",
    ),
    BlockSpec(
        "combo_112",
        "1-1-2",
        "combo",
        side="both",
        reps=15,
        pace_s=3.0,
        sequence=(("jab", "lead"), ("jab", "lead"), ("cross", "rear")),
        callout="One-one-two",
        hint="Double jab, cross",
    ),
    BlockSpec(
        "combo_32",
        "3-2",
        "combo",
        side="both",
        reps=15,
        pace_s=3.0,
        sequence=(("hook", "lead"), ("cross", "rear")),
        callout="Three-two",
        hint="Lead hook, cross",
    ),
    BlockSpec(
        "negatives",
        "No punches",
        "negative",
        duration_s=120,
        hint="Guard, footwork, slips, rolls, feints — anything but a punch",
    ),
    BlockSpec(
        "free",
        "Free shadowboxing",
        "free",
        side="both",
        duration_s=120,
        hint="Combinations at your own pace — kept for testing",
    ),
]
SPECS = {s.key: s for s in PLAN}


class ProtocolError(ValueError):
    """A request that doesn't fit the protocol state (→ 409)."""


# --------------------------------------------------------------------------
# protocol.json
# --------------------------------------------------------------------------

_lock = threading.Lock()


def protocol_path(folder: Path) -> Path:
    return folder / "protocol.json"


def labels_path(folder: Path) -> Path:
    return folder / "labels.json"


def load(folder: Path) -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(protocol_path(folder).read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("blocks", [])
    return data


def _write(path: Path, text: str) -> None:
    """Atomic replace, so the take screen's poll never reads half a file."""
    with _lock:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)


def save(folder: Path, data: dict[str, Any]) -> None:
    _write(protocol_path(folder), json.dumps(data, indent=2) + "\n")


def active_index(data: dict[str, Any]) -> int | None:
    for i, b in enumerate(data["blocks"]):
        if b.get("t_end_ms") is None and not b.get("discarded"):
            return i
    return None


def start_block(data: dict[str, Any], key: str, now_ms: float, imu_byte: int = 0) -> dict[str, Any]:
    """Open block `key` at `now_ms`, closing any block still open. `imu_byte` is
    imu.csv's size at that moment, so a live count reads only the block's rows."""
    if key not in SPECS:
        raise ProtocolError(f"unknown block {key!r}")
    end_block(data, now_ms, missing_ok=True)
    data["blocks"].append(
        {"key": key, "t_start_ms": max(0.0, now_ms), "t_end_ms": None, "imu_byte": imu_byte}
    )
    return data


def end_block(data: dict[str, Any], now_ms: float, *, missing_ok: bool = False) -> dict[str, Any]:
    i = active_index(data)
    if i is None:
        if missing_ok:
            return data
        raise ProtocolError("no block is running")
    block = data["blocks"][i]
    block["t_end_ms"] = max(block["t_start_ms"], now_ms)
    return data


def discard_block(data: dict[str, Any], index: int) -> dict[str, Any]:
    """Drop a block from the labels (a redo); it stays in the file for the record."""
    if not 0 <= index < len(data["blocks"]):
        raise ProtocolError(f"no block #{index}")
    data["blocks"][index]["discarded"] = True
    return data


# --------------------------------------------------------------------------
# imu.csv
# --------------------------------------------------------------------------

Wrists = dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]


def imu_size(folder: Path) -> int:
    try:
        return (folder / "imu.csv").stat().st_size
    except OSError:
        return 0


def read_imu(folder: Path, start_byte: int = 0, *, swapped: bool = False) -> Wrists:
    """{hand: (t_ms, ax, ay, az)} from imu.csv, starting at `start_byte`.

    The runner appends while the take records, so a trailing line without its
    newline (mid-write) is skipped rather than half-parsed. `swapped` reads the
    file's "left" as the right wrist and vice versa — for a take where the two
    (identical-looking) units were worn on the wrong wrists; imu.csv stays raw.
    """
    cols: dict[str, list[list[float]]] = {h: [] for h in HANDS}
    try:
        with (folder / "imu.csv").open("rb") as f:
            f.seek(start_byte)
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        raw = ""
    for line in raw.split("\n")[:-1]:  # the last piece is "" or an unfinished line
        parts = line.split(",")
        if len(parts) < 5 or parts[1] not in cols:
            continue  # header or a malformed line
        try:
            cols[parts[1]].append(
                [float(parts[0]), float(parts[2]), float(parts[3]), float(parts[4])]
            )
        except ValueError:
            continue
    if swapped:
        cols = {"left": cols["right"], "right": cols["left"]}
    out: Wrists = {}
    for hand, rows in cols.items():
        if rows:
            a = np.asarray(rows)
            out[hand] = (a[:, 0], a[:, 1], a[:, 2], a[:, 3])
    return out


# --------------------------------------------------------------------------
# Labeling
# --------------------------------------------------------------------------


def lead_hand(stance: str | None) -> str:
    """Orthodox leads with the left. Switch fighters are labeled as orthodox — record
    each stance as its own take."""
    return "right" if stance == "southpaw" else "left"


def expected_hands(spec: BlockSpec, stance: str | None) -> set[str]:
    if spec.side == "both":
        return set(HANDS)
    if spec.side is None:
        return set()
    lead = lead_hand(stance)
    rear = "right" if lead == "left" else "left"
    return {lead if spec.side == "lead" else rear}


@dataclass
class BlockResult:
    index: int
    key: str
    detected: int  # punches on the expected wrist(s)
    off_hand: int  # bursts on the other wrist (or any burst in the no-punch block)
    labels: list[dict[str, Any]]


# Combos. Inside a combo punches come 0.2–1.3 s apart, so detection uses a
# shorter gap and a threshold between the hook and default settings. Checked on
# the first combo take (8c87402d): 15, 15, 15 and 14/15 complete.
COMBO_DETECT: dict[str, Any] = {"threshold_g": 1.2, "min_gap_ms": 250.0}
COMBO_MIN_STEP_MS = 120.0  # the next punch of a combo comes at least this much later
COMBO_BREAK_RANGE_MS = (900.0, 2500.0)  # where the between-combos pause can sit


def combo_break_ms(gaps_ms: Sequence[float], expected: int | None) -> float:
    """The pause length that separates one combo from the next, found per block —
    a fixed value can't work: the first combo take had 1.3 s pauses *inside* a
    3-2 (hook, return to guard, cross) and 1.36 s pauses *between* 1-1-2s.

    With `expected` combos there are expected − 1 long pauses; if they clearly
    stand out (≥1.2× the next-longest), the break sits halfway between the two.
    Otherwise it sits at the biggest relative jump among pauses ≥ 0.4 s."""
    lo, hi = COMBO_BREAK_RANGE_MS
    g = sorted((float(x) for x in gaps_ms), reverse=True)
    k = (expected or 0) - 1
    if k >= 1 and len(g) > k and g[k - 1] >= 1.2 * g[k]:
        return float(np.clip((g[k - 1] + g[k]) / 2, lo, hi))
    asc = sorted(x for x in g if x >= 400.0)
    if len(asc) < 2:
        return 1500.0
    j = int(np.argmax([asc[i + 1] / asc[i] for i in range(len(asc) - 1)]))
    return float(np.clip((asc[j] + asc[j + 1]) / 2, lo, hi))


def _label_combos(
    wrists: Wrists, index: int, spec: BlockSpec, t0: float, t1: float, stance: str | None
) -> BlockResult:
    """Group the block's bursts (both wrists) into combos and walk each combo's
    sequence in order: every punch takes the next burst on its own wrist. Only
    complete combos are labeled; `detected` counts them, `off_hand` counts the
    incomplete ones for review."""
    lead = lead_hand(stance)
    rear = "right" if lead == "left" else "left"
    bursts: list[tuple[float, str]] = []
    for hand, (t, ax, ay, az) in wrists.items():
        inside = (t >= t0) & (t <= t1)
        bursts += [
            (e.t_ms, hand)
            for e in detect_punches(t[inside], ax[inside], ay[inside], az[inside], **COMBO_DETECT)
        ]
    bursts.sort()
    brk = combo_break_ms([b[0] - a[0] for a, b in itertools.pairwise(bursts)], spec.reps)
    groups: list[list[tuple[float, str]]] = []
    for b in bursts:
        if groups and b[0] - groups[-1][-1][0] < brk:
            groups[-1].append(b)
        else:
            groups.append([b])
    complete, incomplete = 0, 0
    labels: list[dict[str, Any]] = []
    for group in groups:
        combo: list[dict[str, Any]] = []
        used: set[int] = set()
        prev = float("-inf")
        for punch_type, side in spec.sequence or ():
            hand = lead if side == "lead" else rear
            pick = next(
                (
                    k
                    for k, (tk, hk) in enumerate(group)
                    if hk == hand and k not in used and tk >= prev + COMBO_MIN_STEP_MS
                ),
                None,
            )
            if pick is None:
                break
            used.add(pick)
            prev = group[pick][0]
            combo.append({"t_ms": round(prev, 1), "hand": hand, "punch_type": punch_type})
        if len(combo) == len(spec.sequence or ()):
            complete += 1
            labels += combo
        else:
            incomplete += 1
    return BlockResult(index, spec.key, complete, incomplete, labels)


def label_block(
    wrists: Wrists,
    index: int,
    block: dict[str, Any],
    stance: str | None,
    *,
    until_ms: float | None = None,
) -> BlockResult:
    """Punches inside one block, labeled with its type and hand. `until_ms` closes
    a still-running block for a live count."""
    spec = SPECS[block["key"]]
    t_end = block["t_end_ms"] if block.get("t_end_ms") is not None else until_ms
    if t_end is None:
        return BlockResult(index, spec.key, 0, 0, [])
    if spec.kind == "combo":
        return _label_combos(wrists, index, spec, block["t_start_ms"], t_end, stance)
    wanted = expected_hands(spec, stance)
    detected, off_hand = 0, 0
    labels: list[dict[str, Any]] = []
    for hand, (t, ax, ay, az) in wrists.items():
        inside = (t >= block["t_start_ms"]) & (t <= t_end)
        # In a typed block the punch type is known, so use its timing profile
        # (e.g. an uppercut's return to guard isn't a second punch).
        profile = PROFILES.get(spec.punch_type or "", {})
        events = detect_punches(t[inside], ax[inside], ay[inside], az[inside], **profile)
        if spec.kind == "negative" or hand not in wanted:
            off_hand += len(events)  # a QA count, never a label
            continue
        detected += len(events)
        labels += [
            {"t_ms": round(e.t_ms, 1), "hand": hand, "punch_type": spec.punch_type} for e in events
        ]
    return BlockResult(index, spec.key, detected, off_hand, labels)


def live_count(
    folder: Path, data: dict[str, Any], stance: str | None, now_ms: float
) -> BlockResult | None:
    """The open block's count so far, reading only imu.csv rows written since it began."""
    i = active_index(data)
    if i is None:
        return None
    block = data["blocks"][i]
    wrists = read_imu(folder, block.get("imu_byte", 0), swapped=hands_swapped(data))
    return label_block(wrists, i, block, stance, until_ms=now_ms)


# --------------------------------------------------------------------------
# Swapped wrists
# --------------------------------------------------------------------------


def camera_hands(folder: Path) -> str | None:
    """The wrist × camera cross-check's left/right verdict on the raw imu.csv
    ("swapped" / "consistent" / "unclear"), if it has run for this take."""
    try:
        hands = json.loads((folder / "crosscheck.json").read_text()).get("hands")
    except (OSError, ValueError):
        return None
    return str(hands) if hands else None


def hands_swapped(data: dict[str, Any]) -> bool:
    """The coach marked this take's sensors as worn on the wrong wrists."""
    return bool(data.get("imu_hands_swapped"))


def swap_evidence(data: dict[str, Any], camera_hands: str | None) -> list[str]:
    """Why the take's wrist sensors look swapped, given the current setting —
    empty if nothing suggests it. `camera_hands` is the cross-check's verdict on
    the raw imu.csv ("swapped" / "consistent" / "unclear")."""
    why: list[str] = []
    typed = [
        b
        for b in data["blocks"]
        if not b.get("discarded")
        and b.get("t_end_ms") is not None
        and SPECS[b["key"]].kind == "typed"
        and b.get("detected") is not None
    ]
    detected = sum(b["detected"] for b in typed)
    off = sum(b.get("off_hand") or 0 for b in typed)
    mirrored = sum(1 for b in typed if (b.get("off_hand") or 0) > b["detected"])
    if off >= 10 and off > 2 * detected and mirrored >= 2:
        why.append(
            f"typed blocks found {off} punches on the other wrist and {detected} on the expected one"
        )
    swapped = hands_swapped(data)
    if camera_hands == "swapped" and not swapped:
        why.append("the cameras see the sensor labeled left moving with the right hand")
    if camera_hands == "consistent" and swapped:
        why.append("the cameras see the sensors on the right wrists — the swap may be wrong")
    return why


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def labels_edited(folder: Path, data: dict[str, Any]) -> bool:
    """True if labels.json exists and isn't what we last wrote — someone reviewed
    or hand-made it, so regenerating would throw their work away."""
    path = labels_path(folder)
    if not path.exists():
        return False
    written = (data.get("labels") or {}).get("sha256")
    return written != _digest(path.read_text())


def generate_labels(
    folder: Path, data: dict[str, Any], stance: str | None, *, overwrite: bool = False
) -> list[BlockResult]:
    """Label every finished, non-discarded block into labels.json, and record the
    per-block counts and provenance in `data` (the caller saves it).

    Refuses (ProtocolError) to replace labels someone edited, unless `overwrite`.
    """
    if labels_edited(folder, data) and not overwrite:
        raise ProtocolError(
            "The labels were edited after they were generated — regenerating would "
            "replace that review. Pass overwrite to do it anyway."
        )
    wrists = read_imu(folder, swapped=hands_swapped(data))
    results = [
        label_block(wrists, i, b, stance)
        for i, b in enumerate(data["blocks"])
        if not b.get("discarded") and b.get("t_end_ms") is not None
    ]
    labels = sorted((lab for r in results for lab in r.labels), key=lambda lab: lab["t_ms"])
    for r in results:
        data["blocks"][r.index]["detected"] = r.detected
        data["blocks"][r.index]["off_hand"] = r.off_hand
    text = json.dumps(labels, indent=2)
    _write(labels_path(folder), text)
    data["labels"] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "count": len(labels),
        "stance": stance,
        "imu_hands_swapped": hands_swapped(data),
        "sha256": _digest(text),
        "detector": {
            "name": "analyze.imu_punches",
            "threshold_g": THRESHOLD_G,
            "min_gap_ms": MIN_GAP_MS,
            "smooth_ms": SMOOTH_MS,
            "profiles": PROFILES,  # per punch type, inside typed blocks
        },
    }
    return results


def plan_out() -> list[dict[str, Any]]:
    return [asdict(s) for s in PLAN]
