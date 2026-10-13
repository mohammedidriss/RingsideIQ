#!/usr/bin/env node
/**
 * Serve MediaPipe from the dashboard itself (ADR-015): copy the pose engine (wasm,
 * from the installed @mediapipe/tasks-vision, so its version always matches the
 * JS) and the pose models (from the repo's models/mediapipe/) into
 * public/mediapipe/. Camera pages load them from there — no request to a CDN or
 * Google storage when a camera opens, and recording works without internet.
 *
 * Runs before `dev`, `dev:lan`, `dev:https` and `build`; copies only what changed.
 * A missing model is reported, not fatal: the camera still records, only live
 * pose is off (the server can recover pose from the clip afterwards).
 */

import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, "..");
const out = join(app, "public", "mediapipe");
const MODELS = ["full", "lite"]; // CameraNode and BrowserCapture use "full"; "lite" is the fallback

function copyIfChanged(src, dst) {
  if (existsSync(dst)) {
    const a = statSync(src);
    const b = statSync(dst);
    if (a.size === b.size && a.mtimeMs <= b.mtimeMs) return false;
  }
  mkdirSync(dirname(dst), { recursive: true });
  copyFileSync(src, dst);
  return true;
}

let copied = 0;
const wasm = join(app, "node_modules", "@mediapipe", "tasks-vision", "wasm");
if (!existsSync(wasm)) {
  console.error("[mediapipe] @mediapipe/tasks-vision isn't installed — run the package install first");
  process.exit(1);
}
for (const f of readdirSync(wasm)) {
  if (copyIfChanged(join(wasm, f), join(out, "wasm", f))) copied++;
}

const missing = [];
for (const m of MODELS) {
  const name = `pose_landmarker_${m}.task`;
  const src = join(app, "..", "..", "models", "mediapipe", name);
  if (!existsSync(src)) {
    missing.push(name);
    continue;
  }
  if (copyIfChanged(src, join(out, "models", name))) copied++;
}

if (missing.length) {
  console.warn(
    `[mediapipe] missing in models/mediapipe/: ${missing.join(", ")} — live pose stays off until ` +
      `you fetch them once, from the repo root:\n` +
      `  .venv/bin/python -c "from capture.cv.pose import ensure_pose_model as e; e('full'); e('lite')"`,
  );
}
console.log(`[mediapipe] public/mediapipe ready (${copied} file(s) updated)`);
