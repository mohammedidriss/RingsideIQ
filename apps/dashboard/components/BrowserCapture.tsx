"use client";

/**
 * BrowserCapture — runs MediaPipe PoseLandmarker entirely in the browser (WASM/GPU),
 * detects punches with the same heuristic as the Python backend, and posts events
 * to the Railway API via POST /sessions/{id}/events/bulk when stopped.
 *
 * Used when caps.cv_available === false (Railway server has no opencv/mediapipe).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { MEDIAPIPE_WASM, poseModelUrl, type PoseModel } from "@/lib/mediapipe";
import { PunchDetector, classifyPunchType, type PunchEvent } from "@/lib/punchDetector";
import { api } from "@/lib/api";

interface Props {
  sessionId: string;
  stance?: string | null;
  onDone?: () => void;
}

type Phase = "idle" | "loading" | "ready" | "countdown" | "recording" | "uploading" | "done" | "error";

// Pose model accuracy: lite < full < heavy. `lite` (the old default) is the
// least accurate; every gate depends on landmark quality, so we use `full` for
// a clear accuracy gain that still keeps ~30 fps on a GPU. Switch to `heavy`
// for maximum accuracy if the machine can sustain the frame rate (heavy is
// slower, and fewer frames/second hurts fast-punch sampling — measure first).
const POSE_MODEL: PoseModel = "full";
const MODEL_URL = poseModelUrl(POSE_MODEL); // served by the dashboard itself (ADR-015)

export function BrowserCapture({ sessionId, stance, onDone }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const landmarkerRef = useRef<unknown>(null);
  const detectorRef = useRef<PunchDetector>(new PunchDetector(stance ?? null));
  const eventsRef = useRef<PunchEvent[]>([]);
  // Full per-frame landmarks, uploaded on stop so the session saves pose data
  // (offline detector evaluation + RQ2). 33 × [x, y, z, visibility].
  const poseRef = useRef<
    { t_ms: number; landmarks: number[][]; world_landmarks: number[][] | null }[]
  >([]);
  const rafRef = useRef<number>(0);
  const startEpochRef = useRef<number>(0);
  const streamRef = useRef<MediaStream | null>(null);
  // Downscaled webcam recording, uploaded on stop so the count can be reviewed.
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const [phase, setPhase] = useState<Phase>("idle");
  const [countdown, setCountdown] = useState(3);
  const [punchCount, setPunchCount] = useState(0);
  const [lastHand, setLastHand] = useState<"left" | "right" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Load MediaPipe lazily — only when user clicks Start
  const loadModel = useCallback(async () => {
    setPhase("loading");
    setErr(null);
    try {
      const { PoseLandmarker, FilesetResolver } = await import("@mediapipe/tasks-vision");
      const vision = await FilesetResolver.forVisionTasks(
        MEDIAPIPE_WASM
      );
      const landmarker = await PoseLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" },
        runningMode: "VIDEO",
        numPoses: 1,
      });
      landmarkerRef.current = landmarker;

      // Open webcam — navigator.mediaDevices can be undefined in non-secure
      // contexts or before iOS grants camera permission.
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error(
          "Camera not accessible. On iPhone, make sure you opened the app natively (not via Safari) and granted camera permission in Settings → Alion."
        );
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setPhase("ready");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load camera or model";
      setErr(msg);
      setPhase("error");
    }
  }, []);

  const stopStream = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  // Countdown then start recording
  const startCountdown = useCallback(() => {
    setCountdown(3);
    setPhase("countdown");
  }, []);

  useEffect(() => {
    if (phase !== "countdown") return;
    if (countdown <= 0) {
      // Reset detector and event buffer
      detectorRef.current = new PunchDetector(stance ?? null);
      eventsRef.current = [];
      poseRef.current = [];
      setPunchCount(0);
      startEpochRef.current = performance.now();
      // Start recording the clip (downscaled ~1 Mbps). Best-effort; capture
      // continues even if MediaRecorder isn't supported.
      chunksRef.current = [];
      recorderRef.current = null;
      if (streamRef.current) {
        try {
          const rec = new MediaRecorder(streamRef.current, { videoBitsPerSecond: 1_000_000 });
          rec.ondataavailable = (e) => {
            if (e.data.size) chunksRef.current.push(e.data);
          };
          rec.start(1000);
          recorderRef.current = rec;
        } catch {
          recorderRef.current = null; // recording unsupported — pose/events still saved
        }
      }
      setPhase("recording");
      return;
    }
    const t = setTimeout(() => setCountdown((n) => n - 1), 1000);
    return () => clearTimeout(t);
  }, [phase, countdown, stance]);

  // Detection loop
  useEffect(() => {
    if (phase !== "recording") return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !landmarkerRef.current) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const landmarker = landmarkerRef.current as { detectForVideo: (v: HTMLVideoElement, t: number) => { landmarks?: {x:number;y:number;z:number;visibility?:number}[][], worldLandmarks?: {x:number;y:number;z:number;visibility?:number}[][] } };

    let lastVideoTime = -1;

    const loop = () => {
      rafRef.current = requestAnimationFrame(loop);
      if (video.readyState < 2) return;
      if (video.currentTime === lastVideoTime) return;
      lastVideoTime = video.currentTime;

      const tMs = performance.now() - startEpochRef.current;
      const result = landmarker.detectForVideo(video, performance.now());

      // Draw video frame
      canvas.width = video.videoWidth || 320;
      canvas.height = video.videoHeight || 240;
      ctx.drawImage(video, 0, 0);

      if (result.landmarks?.length) {
        const lms = result.landmarks[0];
        const worldLms = result.worldLandmarks?.[0] ?? null;

        // Draw skeleton
        drawSkeleton(ctx, lms, canvas.width, canvas.height);

        // Record the full landmark frame for server-side pose saving.
        poseRef.current.push({
          t_ms: Math.round(tMs),
          landmarks: lms.map((p) => [p.x, p.y, p.z, p.visibility ?? 1]),
          world_landmarks: worldLms ? worldLms.map((p) => [p.x, p.y, p.z, p.visibility ?? 1]) : null,
        });

        // Detect punches
        const punches = detectorRef.current.feed(lms, worldLms, tMs);
        if (punches.length) {
          punches.forEach((p) => {
            // Label the punch type from the wrist trajectory in the pose buffer
            // (poseRef already includes this frame). Mirrors the server path.
            p.punch_type = classifyPunchType(poseRef.current, p.hand, stance ?? null);
            eventsRef.current.push(p);
            setLastHand(p.hand);
            setTimeout(() => setLastHand(null), 300);
          });
          setPunchCount((n) => n + punches.length);
        }
      }
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [phase]);

  const stop = useCallback(async () => {
    cancelAnimationFrame(rafRef.current);
    setPhase("uploading");
    const durationMs = performance.now() - startEpochRef.current;

    // Finalise the recording and collect the clip (if recording was supported).
    let videoBlob: Blob | null = null;
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") {
      videoBlob = await new Promise<Blob>((resolve) => {
        rec.onstop = () => resolve(new Blob(chunksRef.current, { type: "video/webm" }));
        rec.stop();
      });
    }

    try {
      await api.bulkAddEvents(sessionId, eventsRef.current, { duration_ms: durationMs });
      // Persist the pose stream so the session saves its landmarks (offline
      // evaluation + RQ2). Best-effort: never fail the capture on this.
      try {
        if (poseRef.current.length) {
          await api.bulkAddPose(sessionId, poseRef.current, { duration_ms: durationMs });
        }
      } catch {
        /* pose saving is non-critical — events are already stored */
      }
      // Upload the recorded clip for visual review. Best-effort.
      try {
        if (videoBlob && videoBlob.size) {
          await api.uploadSessionVideo(sessionId, videoBlob);
        }
      } catch {
        /* video is non-critical — events + pose are already stored */
      }
      stopStream();
      setPhase("done");
      onDone?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Upload failed");
      setPhase("error");
    }
  }, [sessionId, stopStream, onDone]);

  // Cleanup on unmount
  useEffect(() => () => { stopStream(); }, [stopStream]);

  return (
    <div className="space-y-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">Browser Capture</h3>
        <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
          runs on device
        </span>
      </div>
      <p className="text-xs text-neutral-500">
        Pose detection runs locally in your browser — no server needed.
      </p>

      {err && (
        <p className="rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-3 text-sm text-red-300">{err}</p>
      )}

      {/* Video / canvas preview */}
      <div className="relative overflow-hidden rounded-xl bg-black aspect-video w-full max-w-sm mx-auto">
        <video ref={videoRef} className="absolute inset-0 w-full h-full object-cover" muted playsInline />
        <canvas ref={canvasRef} className="absolute inset-0 w-full h-full object-cover" />

        {/* Punch flash overlay */}
        {lastHand && (
          <div className={`absolute inset-0 flex items-center justify-center pointer-events-none`}>
            <span className={`text-4xl font-black ${lastHand === "left" ? "text-cyan-300" : "text-amber-300"}`}>
              {lastHand.toUpperCase()}
            </span>
          </div>
        )}

        {/* Countdown overlay */}
        {phase === "countdown" && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60">
            <span className="text-7xl font-black text-white">{countdown}</span>
          </div>
        )}

        {/* Idle placeholder */}
        {(phase === "idle" || phase === "loading") && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/80">
            <div className="text-4xl">📷</div>
            <p className="text-sm text-neutral-400">
              {phase === "loading" ? "Loading model…" : "Camera off"}
            </p>
          </div>
        )}
      </div>

      {/* Punch counter */}
      {(phase === "recording" || phase === "uploading") && (
        <div className="flex items-center justify-center gap-8">
          <div className="text-center">
            <p className="text-4xl font-black text-emerald-400">{punchCount}</p>
            <p className="text-xs text-neutral-500 mt-1">punches detected</p>
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="flex flex-wrap gap-3 justify-center">
        {phase === "idle" && (
          <button
            onClick={loadModel}
            className="rounded-xl bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-black hover:bg-emerald-400"
          >
            Start capture
          </button>
        )}
        {phase === "loading" && (
          <button disabled className="rounded-xl bg-emerald-500/40 px-5 py-2.5 text-sm font-semibold text-black/60">
            Loading model…
          </button>
        )}
        {phase === "ready" && (
          <button
            onClick={startCountdown}
            className="rounded-xl bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-black hover:bg-emerald-400"
          >
            Begin recording
          </button>
        )}
        {phase === "countdown" && (
          <button disabled className="rounded-xl bg-neutral-700 px-5 py-2.5 text-sm text-neutral-400">
            Get ready…
          </button>
        )}
        {phase === "recording" && (
          <button
            onClick={stop}
            className="rounded-xl bg-red-500 px-5 py-2.5 text-sm font-semibold text-white hover:bg-red-400"
          >
            Stop & save
          </button>
        )}
        {phase === "uploading" && (
          <button disabled className="rounded-xl bg-neutral-700 px-5 py-2.5 text-sm text-neutral-400">
            Saving…
          </button>
        )}
        {phase === "done" && (
          <p className="text-sm text-emerald-400 font-medium">
            ✓ {punchCount} punches saved
          </p>
        )}
        {phase === "error" && (
          <button
            onClick={() => { setPhase("idle"); setErr(null); }}
            className="rounded-xl border border-white/10 px-5 py-2.5 text-sm text-neutral-300 hover:bg-white/[0.04]"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

// ── Skeleton drawing ────────────────────────────────────────────────────────

const CONNECTIONS = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
  [11, 23], [12, 24], [23, 24], [23, 25], [24, 26],
];

function drawSkeleton(
  ctx: CanvasRenderingContext2D,
  lms: { x: number; y: number; visibility?: number }[],
  w: number,
  h: number,
) {
  ctx.strokeStyle = "rgba(0,255,150,0.8)";
  ctx.lineWidth = 2;
  for (const [a, b] of CONNECTIONS) {
    const la = lms[a], lb = lms[b];
    if ((la?.visibility ?? 1) < 0.4 || (lb?.visibility ?? 1) < 0.4) continue;
    ctx.beginPath();
    ctx.moveTo(la.x * w, la.y * h);
    ctx.lineTo(lb.x * w, lb.y * h);
    ctx.stroke();
  }
  ctx.fillStyle = "rgba(0,255,150,0.9)";
  for (const lm of lms) {
    if ((lm?.visibility ?? 1) < 0.4) continue;
    ctx.beginPath();
    ctx.arc(lm.x * w, lm.y * h, 3, 0, Math.PI * 2);
    ctx.fill();
  }
}
