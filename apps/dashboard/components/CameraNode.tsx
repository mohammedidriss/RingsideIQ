"use client";

/**
 * CameraNode (ADR-010) — one capture device. Registers to a session with the join
 * token, previews the feed, runs MediaPipe pose + the punch detector, records, and
 * on the coach's synchronized start flips to RECORDING and uploads its clip + pose.
 *
 * Used by the phone camera page (/sessions/[id]/camera) and embedded directly in
 * the coach's session page so the laptop can be a camera without opening a link.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MEDIAPIPE_WASM, poseModelUrl } from "@/lib/mediapipe";
import { PunchDetector } from "@/lib/punchDetector";
import { api, type CaptureRef } from "@/lib/api";

type Phase = "connecting" | "ready" | "countdown" | "recording" | "stopped" | "error";

const LABELS = ["front", "left", "right", "45-left", "45-right", "overhead"];

// ~2.5 Mbps: plenty for pose at 1280 px, and a 12-minute protocol take stays near
// 225 MB (Safari's default bitrate made an iPhone take's clip several times larger).
const VIDEO_BPS = 2_500_000;

/**
 * Open the camera, falling back to simpler requests when the preferred one fails.
 * A phone asks for its back camera at 4:3 (the full sensor, like the native camera
 * app); the laptop webcam keeps 16:9. Some Android phones refuse a specific size
 * with "Could not start video source", so each step asks for less, ending at "any
 * camera". Video only: sound is never captured (ADR-014) — the microphone isn't
 * even asked for, so no clip can carry voices.
 */
async function openCamera(laptop: boolean, deviceId?: string | null): Promise<MediaStream> {
  const back = { facingMode: { ideal: "environment" } };
  const pick = deviceId ? { deviceId: { exact: deviceId } } : {};
  const attempts: MediaStreamConstraints[] = laptop
    ? [
        {
          video: { ...pick, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } },
          audio: false,
        },
        { video: deviceId ? pick : true, audio: false },
      ]
    : [
        {
          video: { ...back, aspectRatio: { ideal: 4 / 3 }, width: { ideal: 1280 }, height: { ideal: 960 } },
          audio: false,
        },
        { video: back, audio: false },
        { video: true, audio: false },
      ];
  let last: unknown = null;
  for (const c of attempts) {
    try {
      return await navigator.mediaDevices.getUserMedia(c);
    } catch (e) {
      last = e;
      const name = e instanceof Error ? e.name : "";
      // A refused permission or insecure page won't change with a smaller request.
      if (name === "NotAllowedError" || name === "SecurityError") break;
    }
  }
  throw last;
}

// The laptop's camera choice, remembered in this browser. Without one, the browser
// default can be a camera that opens but shows nothing here: a virtual camera (black
// unless its app is running) or an iPhone through Continuity Camera (which also
// takes that iPhone away from its own camera page).
const LAPTOP_CAMERA_KEY = "alion.laptopCameraId";
const VIRTUAL_CAMERA = /virtual|vcam|obs|snap camera|manycam|camo|desk view|continuity|iphone|ipad/i;
const BUILT_IN_CAMERA = /facetime|built-?in|integrated/i;

function savedLaptopCamera(): string | null {
  try {
    return localStorage.getItem(LAPTOP_CAMERA_KEY);
  } catch {
    return null;
  }
}

/** The camera to use: the one picked before, else the built-in, else any real one. */
function preferredCamera(cams: MediaDeviceInfo[], saved: string | null): string | null {
  if (saved && cams.some((c) => c.deviceId === saved)) return saved;
  const real = cams.filter((c) => !VIRTUAL_CAMERA.test(c.label));
  return (real.find((c) => BUILT_IN_CAMERA.test(c.label)) ?? real[0] ?? cams[0])?.deviceId ?? null;
}

/** A camera failure in words a coach can act on. */
function cameraErrorText(e: unknown): string {
  const name = e instanceof Error ? e.name : "";
  if (name === "NotReadableError" || name === "TrackStartError")
    return "The camera is busy or couldn't start. Close the phone's camera app, video calls and any other tab with this page, then reload.";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Camera permission is blocked. Allow the camera for this site in the browser settings, then reload.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "No usable camera was found on this device.";
  return e instanceof Error ? `${e.name}: ${e.message}` : "camera error";
}

export function CameraNode({
  sessionId,
  takeId,
  token,
  defaultLabel = "front",
  tile = false,
  onDeviceId,
  onBusy,
  onLabelChange,
}: {
  /** The training session this camera records into — or `takeId` for a dataset take. */
  sessionId?: string;
  takeId?: string;
  token: string;
  defaultLabel?: string;
  /** Render as a single grid tile (video + overlay only) — used for the laptop
   *  inside the coach's 2×2 cameras grid. */
  tile?: boolean;
  onDeviceId?: (deviceId: string) => void;
  /** True while counting down, recording or uploading — a linked phone mustn't
   *  switch to another capture until this goes false. */
  onBusy?: (busy: boolean) => void;
  /** The camera was renamed on this device (a linked phone keeps its studio name in sync). */
  onLabelChange?: (label: string) => void;
}) {
  // Where the clip, pose and heartbeats go — a session, or a dataset take (ADR-013).
  const cap = useMemo<CaptureRef>(
    () => (takeId ? { kind: "take", id: takeId } : (sessionId ?? "")),
    [takeId, sessionId],
  );
  const videoRef = useRef<HTMLVideoElement>(null);
  const deviceIdRef = useRef<string | null>(null);
  const offsetRef = useRef(0); // server clock − local clock (ms)
  const recStartRef = useRef<number | null>(null); // scheduled start (t = 0), local clock
  const startTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startOffsetRef = useRef<number | null>(null); // recorder began this long after t = 0
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const landmarkerRef = useRef<unknown>(null);
  const detectorRef = useRef<PunchDetector>(new PunchDetector(null));
  const rafRef = useRef<number>(0);
  const punchCountRef = useRef(0);
  const pausedRef = useRef(false); // gates the pose loop while the coach has paused
  const pauseStartRef = useRef<number | null>(null); // when the current pause began
  const pausedAccumRef = useRef(0); // total paused ms, subtracted from elapsed
  const poseRef = useRef<
    { t_ms: number; landmarks: number[][]; world_landmarks: number[][] | null }[]
  >([]);

  const [phase, setPhase] = useState<Phase>("connecting");
  const [noCamera, setNoCamera] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);
  const [recInfo, setRecInfo] = useState<string | null>(null);
  const [label, setLabel] = useState(defaultLabel);
  const labelRef = useRef(label); // read by the heartbeat loop without restarting it
  labelRef.current = label;
  const [countdown, setCountdown] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [uploaded, setUploaded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const uploadingRef = useRef(false); // read by the heartbeat: the coach sees "uploading"
  // The pose model loads in the background (a download plus GPU set-up, a few
  // seconds). Until it's in, the camera reports "loading" so the coach can wait;
  // if recording starts first, pose tracking starts the moment it arrives.
  const [poseReady, setPoseReady] = useState(false);
  const poseReadyRef = useRef(false);
  // A page that isn't on screen (phone locked, app switched, tab hidden) keeps
  // recording video, but the browser pauses pose tracking — count that time.
  const hiddenSinceRef = useRef<number | null>(null);
  const hiddenMsRef = useRef(0);
  const [punchCount, setPunchCount] = useState(0);
  const [paused, setPaused] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Laptop only: the cameras this computer has, and the one in use.
  const [cams, setCams] = useState<MediaDeviceInfo[]>([]);
  const [camId, setCamId] = useState<string | null>(null);

  const busy = phase === "countdown" || phase === "recording" || uploading;
  useEffect(() => {
    onBusy?.(busy);
  }, [busy, onBusy]);

  // Open the camera + register as a device.
  useEffect(() => {
    if (!token) {
      setErr("Missing join token.");
      setPhase("error");
      return;
    }
    let stream: MediaStream | null = null;
    let cancelled = false;
    (async () => {
      try {
        // Camera preview is best-effort: if it fails (no camera, or not a secure
        // context) the device still registers and joins the sync.
        try {
          if (!navigator.mediaDevices?.getUserMedia) throw new Error("no getUserMedia");
          const saved = tile ? savedLaptopCamera() : null;
          const videoInputs = async () =>
            (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
          try {
            stream = await openCamera(tile, saved).catch(() => openCamera(tile)); // saved one unplugged
          } catch (first) {
            // The browser's default camera wouldn't start (e.g. an iPhone busy as a
            // phone camera): a laptop tries its preferred camera before giving up.
            const want = tile ? preferredCamera(await videoInputs(), saved) : null;
            if (!want) throw first;
            stream = await openCamera(true, want);
          }
          if (tile) {
            // Labels are readable once the camera is allowed: switch to the preferred
            // camera if the browser opened a different one.
            const list = await videoInputs();
            const want = preferredCamera(list, saved);
            const got = stream.getVideoTracks()[0]?.getSettings().deviceId ?? null;
            if (want && want !== got) {
              stream.getTracks().forEach((t) => t.stop());
              stream = await openCamera(true, want).catch(() => openCamera(true));
            }
            if (!cancelled) {
              setCams(list);
              setCamId(stream.getVideoTracks()[0]?.getSettings().deviceId ?? want);
            }
          }
          if (cancelled) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }
          streamRef.current = stream;
          if (!cancelled && videoRef.current) {
            videoRef.current.srcObject = stream;
            videoRef.current.play().catch(() => {}); // iOS Safari needs an explicit play()
          }
        } catch (camErr) {
          if (!cancelled) {
            setNoCamera(true);
            setCamError(cameraErrorText(camErr));
            if (tile) {
              // Still offer the laptop's cameras, so another one can be picked.
              navigator.mediaDevices
                ?.enumerateDevices()
                .then((d) => !cancelled && setCams(d.filter((x) => x.kind === "videoinput")))
                .catch(() => {});
            }
          }
        }
        if (cancelled) return;
        const { device_id } = await api.multicamRegister(cap, token, "camera", defaultLabel);
        deviceIdRef.current = device_id;
        onDeviceId?.(device_id);
        setPhase("ready");
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Could not register with the session.");
        setPhase("error");
      }
    })();
    return () => {
      cancelled = true;
      stream?.getTracks().forEach((t) => t.stop());
      streamRef.current?.getTracks().forEach((t) => t.stop()); // a camera switched to since
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cap, token]);

  /** Laptop: use another camera (remembered for next time). Not while recording —
   *  the recorder holds the current stream. */
  const switchCamera = useCallback(async (id: string) => {
    try {
      localStorage.setItem(LAPTOP_CAMERA_KEY, id);
    } catch {
      /* not remembered — still switches */
    }
    try {
      const next = await openCamera(true, id);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = next;
      if (videoRef.current) {
        videoRef.current.srcObject = next;
        videoRef.current.play().catch(() => {});
      }
      setCamId(id);
      setNoCamera(false);
      setCamError(null);
    } catch (e) {
      setCamError(cameraErrorText(e));
    }
  }, []);

  // Heartbeat + poll the coach's command; drive the synchronized start.
  useEffect(() => {
    if (phase === "connecting" || phase === "error") return;
    let alive = true;
    const tick = async () => {
      const did = deviceIdRef.current;
      if (!did) return;
      try {
        const hidden = typeof document !== "undefined" && document.visibilityState === "hidden";
        const status = uploadingRef.current
          ? "uploading"
          : pausedRef.current
            ? "paused"
            : phase === "recording"
              ? hidden
                ? "hidden"
                : "recording"
              : poseReadyRef.current
                ? "ready"
                : "loading";
        const st = await api.multicamHeartbeat(cap, token, did, status, punchCountRef.current, labelRef.current);
        offsetRef.current = st.server_now_ms - Date.now();
        if (st.command === "start" && st.start_at_ms != null) {
          const localStart = st.start_at_ms - offsetRef.current;
          const msLeft = localStart - Date.now();
          if (msLeft > 0) {
            setPhase("countdown");
            setCountdown(Math.ceil(msLeft / 1000));
            // Begin exactly at the scheduled instant, not on the next 400 ms tick:
            // every camera's clip and the wrist IMUs then share t = 0 (dataset
            // labels are matched to video within ±200 ms).
            if (startTimerRef.current == null && !recStartRef.current) {
              startTimerRef.current = setTimeout(() => {
                startTimerRef.current = null;
                if (!recStartRef.current) {
                  recStartRef.current = localStart;
                  setPhase("recording");
                }
              }, msLeft);
            }
          } else if (!recStartRef.current) {
            recStartRef.current = localStart;
            setPhase("recording");
          }
          // Pause / resume — the coach toggles st.paused. Hold or continue the SAME
          // clip and freeze the punch loop, without ending the recording.
          const wantPaused = !!st.paused;
          if (recStartRef.current && wantPaused !== pausedRef.current) {
            pausedRef.current = wantPaused;
            setPaused(wantPaused);
            const rec = recorderRef.current;
            if (wantPaused) {
              pauseStartRef.current = Date.now();
              if (rec && rec.state === "recording") rec.pause();
            } else {
              if (pauseStartRef.current != null) {
                pausedAccumRef.current += Date.now() - pauseStartRef.current;
                pauseStartRef.current = null;
              }
              if (rec && rec.state === "paused") rec.resume();
            }
          }
        } else if (st.command === "stop") {
          if (startTimerRef.current != null) {
            clearTimeout(startTimerRef.current);
            startTimerRef.current = null;
          }
          recStartRef.current = null;
          setPhase("stopped");
        } else if (st.command === "discard" && (phase === "countdown" || phase === "recording")) {
          // The coach deleted this recording: stop, drop the clip, don't upload.
          if (startTimerRef.current != null) {
            clearTimeout(startTimerRef.current);
            startTimerRef.current = null;
          }
          const rec = recorderRef.current;
          recorderRef.current = null;
          if (rec) {
            rec.onstop = null;
            try {
              rec.stop();
            } catch {
              /* already stopped */
            }
          }
          chunksRef.current = [];
          recStartRef.current = null;
          pausedRef.current = false;
          setPaused(false);
          pauseStartRef.current = null;
          pausedAccumRef.current = 0;
          setRecInfo("recording deleted by the coach");
          setPhase("ready");
        }
      } catch {
        if (alive) setErr("Lost the connection to the session.");
      }
    };
    const id = setInterval(tick, 400);
    tick();
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [phase, cap, token]);

  useEffect(
    () => () => {
      if (startTimerRef.current != null) clearTimeout(startTimerRef.current);
    },
    [],
  );

  // Recording elapsed timer — paused time is subtracted so it freezes on Pause.
  useEffect(() => {
    if (phase !== "recording") return;
    const id = setInterval(() => {
      if (!recStartRef.current) return;
      const inPause = pauseStartRef.current != null ? Date.now() - pauseStartRef.current : 0;
      setElapsed(
        (Date.now() - recStartRef.current - pausedAccumRef.current - inPause) / 1000,
      );
    }, 200);
    return () => clearInterval(id);
  }, [phase]);

  // Real capture: start MediaRecorder on the synchronized start, upload on stop.
  useEffect(() => {
    if (phase === "recording" && streamRef.current && !recorderRef.current) {
      // Fresh recording — clear any pause bookkeeping from a prior run.
      pausedRef.current = false;
      pauseStartRef.current = null;
      pausedAccumRef.current = 0;
      startOffsetRef.current = null;
      setPaused(false);
      try {
        if (typeof MediaRecorder === "undefined") {
          setRecInfo("MediaRecorder not supported on this browser");
        } else {
          const mime = ["video/webm;codecs=vp9", "video/webm", "video/mp4"].find((m) =>
            MediaRecorder.isTypeSupported?.(m),
          );
          // Record the video track alone, so a clip can never carry sound (ADR-014).
          const video = new MediaStream(streamRef.current.getVideoTracks());
          const rec = mime
            ? new MediaRecorder(video, { mimeType: mime, videoBitsPerSecond: VIDEO_BPS })
            : new MediaRecorder(video, { videoBitsPerSecond: VIDEO_BPS });
          chunksRef.current = [];
          rec.ondataavailable = (e) => {
            if (e.data.size) chunksRef.current.push(e.data);
          };
          rec.onerror = () => setRecInfo("recorder error");
          // How late the clip's first frame is relative to t = 0 — uploaded with the
          // clip so the dataset tools can line video up with the IMU exactly.
          const markStart = () => {
            if (recStartRef.current != null) startOffsetRef.current = Date.now() - recStartRef.current;
          };
          rec.onstart = markStart;
          rec.start(1000); // timeslice — iOS Safari is more reliable emitting chunks
          markStart(); // fallback if onstart never fires; onstart refines it
          recorderRef.current = rec;
          setRecInfo(`recording (${mime || "default"})`);
        }
      } catch (e) {
        setRecInfo(e instanceof Error ? `recorder failed: ${e.name}` : "recorder failed");
      }
    }
    if (phase === "stopped" && recorderRef.current) {
      const rec = recorderRef.current;
      recorderRef.current = null;
      rec.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "video/webm" });
        const did = deviceIdRef.current;
        if (!blob.size) {
          setRecInfo("no video data captured");
          return;
        }
        setRecInfo(`uploading ${(blob.size / 1_000_000).toFixed(1)} MB…`);
        setUploading(true);
        uploadingRef.current = true;
        try {
          if (did) {
            await api.multicamUpload(cap, token, did, blob, startOffsetRef.current ?? undefined);
          }
          setUploaded(true);
          setRecInfo(null);
        } catch (e) {
          setRecInfo(e instanceof Error ? `upload failed: ${e.message}` : "upload failed");
        } finally {
          setUploading(false);
          uploadingRef.current = false;
        }
      };
      rec.stop();
    }
  }, [phase, cap, token]);

  // Post a small preview frame ~1×/s so the coach's live grid shows this camera.
  useEffect(() => {
    if (!["ready", "countdown", "recording"].includes(phase)) return;
    const canvas = document.createElement("canvas");
    const post = async () => {
      const v = videoRef.current;
      const did = deviceIdRef.current;
      if (!v || !did || !v.videoWidth) return;
      const w = 320;
      const h = Math.round((v.videoHeight / v.videoWidth) * w) || 240;
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(v, 0, 0, w, h);
      const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.5));
      if (blob) await api.multicamFrame(cap, token, did, blob);
    };
    const id = setInterval(post, 1000);
    return () => clearInterval(id);
  }, [phase, cap, token]);

  // Load MediaPipe pose as soon as the page opens, alongside the camera — loading it
  // only once the camera was ready left too little time when Start came quickly,
  // and the whole take then had no pose.
  useEffect(() => {
    if (landmarkerRef.current) return;
    let cancelled = false;
    (async () => {
      try {
        const { PoseLandmarker, FilesetResolver } = await import("@mediapipe/tasks-vision");
        const vision = await FilesetResolver.forVisionTasks(
          MEDIAPIPE_WASM, // served by the dashboard itself, not a CDN (ADR-015)
        );
        const lm = await PoseLandmarker.createFromOptions(vision, {
          baseOptions: {
            modelAssetPath: poseModelUrl("full"),
            delegate: "GPU",
          },
          runningMode: "VIDEO",
          numPoses: 1,
        });
        if (!cancelled) {
          landmarkerRef.current = lm;
          poseReadyRef.current = true;
          setPoseReady(true);
        }
      } catch {
        /* model load failed — recording still works, just no live count or pose */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Recording while off screen: note it, and say so when the page comes back.
  useEffect(() => {
    if (phase !== "recording") return;
    hiddenMsRef.current = 0;
    hiddenSinceRef.current = document.visibilityState === "hidden" ? Date.now() : null;
    const onVis = () => {
      if (document.visibilityState === "hidden") {
        hiddenSinceRef.current = Date.now();
      } else if (hiddenSinceRef.current != null) {
        hiddenMsRef.current += Date.now() - hiddenSinceRef.current;
        hiddenSinceRef.current = null;
        setRecInfo(
          `This page was off screen for ${Math.round(hiddenMsRef.current / 1000)} s — the video kept recording, but pose stopped. Keep this page in front with the screen on.`,
        );
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [phase]);

  // Count punches live during recording, using the shared PunchDetector.
  useEffect(() => {
    if (phase !== "recording") return;
    const video = videoRef.current;
    const lm = landmarkerRef.current as {
      detectForVideo: (
        v: HTMLVideoElement,
        t: number,
      ) => {
        landmarks?: { x: number; y: number; z: number; visibility?: number }[][];
        worldLandmarks?: { x: number; y: number; z: number; visibility?: number }[][];
      };
    } | null;
    if (!video || !lm) return; // runs again when the pose model arrives (poseReady)
    detectorRef.current = new PunchDetector(null);
    punchCountRef.current = 0;
    setPunchCount(0);
    poseRef.current = [];
    // Pose frames are stamped on the session timeline (ms since t = 0, paused time
    // removed) — the same axis as the clip, the IMU samples and the labels. A
    // monotonic clock anchored once to t = 0 so a wall-clock adjustment can't jump it.
    const perfAtT0 = performance.now() - (Date.now() - (recStartRef.current ?? Date.now()));
    let lastFrame = -1;
    const loop = () => {
      rafRef.current = requestAnimationFrame(loop);
      if (pausedRef.current) return; // frozen while paused — no detection or pose
      if (video.readyState < 2 || video.currentTime === lastFrame) return;
      lastFrame = video.currentTime;
      let res;
      try {
        res = lm.detectForVideo(video, performance.now());
      } catch {
        return;
      }
      const lms = res.landmarks?.[0];
      if (!lms) return;
      const world = res.worldLandmarks?.[0] ?? null;
      const tMs = performance.now() - perfAtT0 - pausedAccumRef.current;
      poseRef.current.push({
        t_ms: Math.round(tMs),
        landmarks: lms.map((p) => [p.x, p.y, p.z, p.visibility ?? 1]),
        world_landmarks: world ? world.map((p) => [p.x, p.y, p.z, p.visibility ?? 1]) : null,
      });
      const events = detectorRef.current.feed(lms, world, tMs);
      if (events.length) {
        punchCountRef.current += events.length;
        setPunchCount(punchCountRef.current);
      }
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [phase, poseReady]);

  // On stop, upload this device's pose stream for offline analysis (per-device parquet).
  useEffect(() => {
    if (phase !== "stopped") return;
    const did = deviceIdRef.current;
    const frames = poseRef.current;
    if (!did || frames.length === 0) return;
    poseRef.current = [];
    api.multicamPose(cap, token, did, frames).catch(() => {});
  }, [phase, cap, token]);

  const relabel = useCallback(
    async (next: string) => {
      setLabel(next);
      labelRef.current = next;
      onLabelChange?.(next);
      const did = deviceIdRef.current;
      // The heartbeat carries the new name to the coach's camera list.
      if (did) await api.multicamHeartbeat(cap, token, did, "ready", 0, next).catch(() => {});
    },
    [cap, token, onLabelChange],
  );
  // The device's own name (a phone's model) first, then the angle names.
  const labelOptions = LABELS.includes(defaultLabel) ? LABELS : [defaultLabel, ...LABELS];

  // Tile mode: just the video filling a grid cell, with a bottom overlay — matches
  // the phone frame-tiles so the laptop sits in the coach's 2×2 grid at the same size.
  if (tile) {
    return (
      <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-white/10 bg-black">
        <video ref={videoRef} autoPlay playsInline muted className="h-full w-full object-contain" />
        {cams.length > 1 && (phase === "ready" || phase === "connecting" || phase === "stopped") && (
          <select
            value={camId ?? ""}
            onChange={(e) => switchCamera(e.target.value)}
            aria-label="Laptop camera"
            title="Which camera this laptop records with"
            className="absolute right-1 top-1 max-w-[60%] truncate rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-neutral-200"
          >
            {cams.map((c, i) => (
              <option key={c.deviceId} value={c.deviceId}>
                {c.label || `Camera ${i + 1}`}
              </option>
            ))}
          </select>
        )}
        {noCamera && (
          <div className="absolute inset-0 grid place-items-center p-2 text-center text-[10px] leading-tight text-neutral-400">
            camera unavailable
          </div>
        )}
        {phase === "countdown" && countdown != null && (
          <div className="absolute inset-0 grid place-items-center bg-black/50">
            <span className="text-5xl font-bold text-white tabular-nums">{countdown}</span>
          </div>
        )}
        {recInfo && (
          <div className="absolute left-1 top-1 max-w-[92%] truncate rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-amber-300">
            {recInfo}
          </div>
        )}
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-black/60 px-2 py-1 text-[11px]">
          <span className="truncate">💻 {label}</span>
          <span className="flex items-center gap-2">
            {(phase === "recording" || phase === "stopped") && punchCount > 0 && (
              <span className="font-semibold text-emerald-400">{punchCount}👊</span>
            )}
            {phase === "recording" ? (
              paused ? (
                <span className="font-semibold text-amber-300">❚❚ paused</span>
              ) : (
                <span className="flex items-center gap-1 font-semibold text-red-300">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
                  {elapsed.toFixed(0)}s
                </span>
              )
            ) : (
              <StatusBadge phase={phase} />
            )}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">Camera · {label}</h3>
        <StatusBadge phase={phase} />
      </div>

      <div className="relative min-h-[220px] overflow-hidden rounded-2xl border border-white/10 bg-black">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className="mx-auto max-h-[70vh] w-full object-contain"
        />
        {noCamera && (
          <div className="absolute inset-0 grid place-items-center p-3 text-center text-xs leading-relaxed">
            <span className="text-neutral-400">
              camera unavailable
              <br />
              {camError ? (
                <span className="text-red-400">{camError}</span>
              ) : (
                <span className="text-neutral-500">(sync still active)</span>
              )}
              <br />
              <button
                onClick={() => window.location.reload()}
                className="mt-3 rounded-lg bg-white/10 px-4 py-2 text-sm font-medium text-white hover:bg-white/20"
              >
                Try again
              </button>
            </span>
          </div>
        )}
        {phase === "countdown" && countdown != null && (
          <div className="absolute inset-0 grid place-items-center bg-black/50">
            <span className="text-7xl font-bold text-white tabular-nums">{countdown}</span>
          </div>
        )}
        {phase === "recording" && (
          <div
            className={`absolute left-3 top-3 flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold text-white ${
              paused ? "bg-amber-600/90" : "bg-red-600/90"
            }`}
          >
            {paused ? (
              <>❚❚ PAUSED {elapsed.toFixed(1)}s</>
            ) : (
              <>
                <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-white" /> REC{" "}
                {elapsed.toFixed(1)}s
              </>
            )}
          </div>
        )}
      </div>

      {recInfo && <p className="text-xs text-amber-400">{recInfo}</p>}

      {(phase === "recording" || phase === "stopped") && (
        <p className="text-center text-neutral-300">
          <span className="text-3xl font-bold tabular-nums text-emerald-400">{punchCount}</span>{" "}
          <span className="text-sm">punches</span>
        </p>
      )}

      {phase === "ready" && (
        <div className="space-y-2">
          <p className="text-sm text-neutral-400">
            Connected. Name this camera (its phone name or an angle), then wait for the coach to
            start — all cameras start together.
          </p>
          <div className="flex flex-wrap gap-2">
            {labelOptions.map((l) => (
              <button
                key={l}
                onClick={() => relabel(l)}
                className={`rounded-lg border px-3 py-1 text-xs ${
                  l === label
                    ? "border-emerald-400 bg-emerald-500/20 text-emerald-300"
                    : "border-white/10 text-neutral-400 hover:bg-white/5"
                }`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
      )}
      {phase === "stopped" && (
        <p className="text-sm text-neutral-400">
          {uploaded ? "✓ Clip uploaded. " : ""}Session stopped.
        </p>
      )}
      {phase === "error" && <p className="text-sm text-red-400">{err}</p>}
    </div>
  );
}

function StatusBadge({ phase }: { phase: Phase }) {
  const map: Record<Phase, [string, string]> = {
    connecting: ["Connecting…", "bg-neutral-500"],
    ready: ["Ready", "bg-emerald-500"],
    countdown: ["Starting…", "bg-amber-500"],
    recording: ["Recording", "bg-red-500"],
    stopped: ["Stopped", "bg-neutral-500"],
    error: ["Error", "bg-red-500"],
  };
  const [text, color] = map[phase];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-neutral-300">
      <span className={`h-2 w-2 rounded-full ${color}`} /> {text}
    </span>
  );
}
