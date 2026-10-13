/**
 * Where the camera pages load MediaPipe from: the dashboard itself, never a CDN
 * (ADR-015). `scripts/mediapipe-assets.mjs` copies the engine from the installed
 * @mediapipe/tasks-vision (so its version matches this JS) and the models from
 * models/mediapipe/ into public/mediapipe/ before every dev or build.
 */

export const MEDIAPIPE_WASM = "/mediapipe/wasm";

export type PoseModel = "lite" | "full" | "heavy";

export function poseModelUrl(model: PoseModel): string {
  return `/mediapipe/models/pose_landmarker_${model}.task`;
}
