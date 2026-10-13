"""Nothing reaches an outside service (ADR-015): the dashboard loads no code or
models from a CDN, Next.js sends no usage data, and YOLO turns its usage
statistics off before it loads."""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "apps" / "dashboard"
LOCAL = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|www\.w3\.org)(:\d+)?$")


def test_the_dashboard_source_names_no_outside_host() -> None:
    hosts: set[str] = set()
    for folder in ("app", "components", "lib"):
        for path in (DASHBOARD / folder).rglob("*.ts*"):
            if path.name == "api-schema.ts":  # generated types
                continue
            hosts |= {
                f"{h} ({path.relative_to(DASHBOARD)})"
                for h in re.findall(r"https?://([\w.-]+(?::\d+)?)", path.read_text())
                if not LOCAL.match(h)
            }
    assert hosts == set(), "load these from the dashboard instead (public/)"


def test_every_next_command_runs_with_usage_data_off() -> None:
    scripts = json.loads((DASHBOARD / "package.json").read_text())["scripts"]
    nexts = {k: v for k, v in scripts.items() if re.search(r"\bnext (dev|build|start|lint)\b", v)}
    assert nexts, "no next scripts found"
    assert all(v.startswith("NEXT_TELEMETRY_DISABLED=1 ") for v in nexts.values()), nexts


def test_the_camera_pages_get_mediapipe_before_dev_and_build() -> None:
    scripts = json.loads((DASHBOARD / "package.json").read_text())["scripts"]
    for name in ("dev", "dev:lan", "dev:https", "build"):
        assert scripts.get(f"pre{name}") == "npm run mediapipe", name


def test_yolo_turns_its_usage_statistics_off(monkeypatch: pytest.MonkeyPatch) -> None:
    class Settings(dict[str, object]):
        pass

    settings = Settings(sync=True)
    fake = types.ModuleType("ultralytics")
    fake.settings = settings  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ultralytics", fake)

    from capture.cv.pose_yolo import _usage_data_off

    _usage_data_off()
    assert settings["sync"] is False
