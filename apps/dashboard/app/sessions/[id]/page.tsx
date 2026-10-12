"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { EvaluationCard } from "@/components/EvaluationCard";
import { FighterBackLink } from "@/components/FighterBackLink";
import { HrvPanel } from "@/components/HrvPanel";
import { IMUPanel } from "@/components/IMUPanel";
import { LiveReader } from "@/components/LiveReader";
import { getPairedDevice, PolarH10Card } from "@/components/PolarH10Card";
import { MulticamPanel } from "@/components/MulticamPanel";
import { JoinQrCard } from "@/components/JoinQrCard";
import { MulticamRecordings } from "@/components/MulticamRecordings";
import { DetectorComparisonCard } from "@/components/DetectorComparisonCard";
import { LiveAdviceCard } from "@/components/LiveAdviceCard";
import { RoundBreakdownCard } from "@/components/RoundBreakdownCard";
import { RQ1RaterCard } from "@/components/RQ1RaterCard";
import { SessionVideo } from "@/components/SessionVideo";
import { PunchChart } from "@/components/PunchChart";
import { PunchTimeline } from "@/components/PunchTimeline";
import {
  AttachmentsCard,
  RoundConfigCard,
  RoundTimer,
} from "@/components/SessionRounds";
import { VelocityHistogram } from "@/components/VelocityHistogram";
import {
  api,
  type Camera,
  type Capabilities,
  type CaptureStatus,
  type PoseBackend,
  type PunchEvent,
  type Session,
  type SessionSource,
  type StudyCondition,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";


export default function SessionPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const router = useRouter();
  const { user: authUser } = useAuth();

  // Admin cannot view session data — manages accounts only
  if (authUser?.role === "admin") {
    return (
      <div className="space-y-4 px-4 py-8 sm:px-8 sm:py-12">
        <div className="text-4xl">🔒</div>
        <h1 className="text-xl font-semibold">Access Restricted</h1>
        <p className="max-w-md text-sm text-neutral-400">
          Training session data is confidential. System administrators manage
          accounts and general information only.
        </p>
      </div>
    );
  }
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<CaptureStatus | null>(null);
  const [events, setEvents] = useState<PunchEvent[]>([]);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraIndex, setCameraIndex] = useState<number>(0);
  const [cameraReason, setCameraReason] = useState<string | null>(null);
  const [notesDraft, setNotesDraft] = useState<string>("");
  const [notesDirty, setNotesDirty] = useState(false);
  const [baselineUploading, setBaselineUploading] = useState(false);
  // ---- Pending-state setup UI ----
  const [setupSource, setSetupSource] = useState<SessionSource>("live_webcam");
  const [setupBackend, setSetupBackend] = useState<PoseBackend>("mediapipe");
  const [setupVideoFile, setSetupVideoFile] = useState<File | null>(null);
  const [setupHrvFile, setSetupHrvFile] = useState<File | null>(null);
  const [setupCondition, setSetupCondition] = useState<StudyCondition | null>(null);
  const [setupBusy, setSetupBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  // Manual-pause bookkeeping. Timer freezes only when the user hits the
  // pause button — not when auto-rest pauses the camera (the timer must
  // keep counting through rest so it can flip back to round).
  const [manualPauseStart, setManualPauseStart] = useState<number | null>(null);
  const [manualPauseAccumMs, setManualPauseAccumMs] = useState(0);
  // Same shape but for the auto rest→round 3s countdown: while a
  // `break_resume` countdown is ticking we want the round timer to
  // sit frozen at the rest-end instead of rolling into the next round
  // before the camera says GO.
  const [breakResumeStart, setBreakResumeStart] = useState<number | null>(null);
  const [breakResumeAccumMs, setBreakResumeAccumMs] = useState(0);
  // Wall-clock timestamp when capture actually started (after the initial
  // 3-2-1 countdown). The auto-phase timer uses this as its epoch so it
  // doesn't run ahead by the countdown duration.
  const [captureEpoch, setCaptureEpoch] = useState<number | null>(null);
  // Pre-action countdown. Three flavours:
  //  - "start"        → fires startCapture(...) when it hits 0
  //  - "resume"       → manual pause → resume; fires resumeCapture
  //  - "break_resume" → auto-rest break ended; fires resumeCapture
  // The fighter sees a 3 → 2 → 1 overlay with a beep each second so
  // they can get back on guard before frames start being captured.
  type CountdownKind = "start" | "resume" | "break_resume";
  const [countdown, setCountdown] = useState<{
    kind: CountdownKind;
    n: number;
  } | null>(null);

  useEffect(() => {
    if (session?.status !== "capturing") return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [session?.status]);

  // Auto-pause capture during rest, auto-resume during rounds. Skips
  // recording rest frames so the saved video stays small. Reads phase
  // from elapsed wall-clock since started_at.
  const lastPhaseRef = useRef<"round" | "rest" | "done" | null>(null);
  useEffect(() => {
    if (session?.status !== "capturing" || !captureEpoch) return;
    const livePauseMs =
      manualPauseStart != null ? now - manualPauseStart : 0;
    const liveBreakMs =
      breakResumeStart != null ? now - breakResumeStart : 0;
    const elapsedS =
      (now - captureEpoch -
        manualPauseAccumMs - livePauseMs -
        breakResumeAccumMs - liveBreakMs) /
      1000;
    const rounds = session.round_count ?? 3;
    const roundS = session.round_duration_s ?? 180;
    const restS = session.rest_duration_s ?? 60;
    const totalS = rounds * roundS + Math.max(0, rounds - 1) * restS;
    let phase: "round" | "rest" | "done" = "done";
    if (elapsedS < totalS) {
      let acc = 0;
      for (let i = 1; i <= rounds; i++) {
        if (elapsedS < acc + roundS) {
          phase = "round";
          break;
        }
        acc += roundS;
        if (i < rounds && elapsedS < acc + restS) {
          phase = "rest";
          break;
        }
        acc += restS;
      }
    }
    const prev = lastPhaseRef.current;
    if (prev !== phase) {
      lastPhaseRef.current = phase;
      if (prev === "round" && phase === "rest" && !status?.is_paused) {
        api.pauseCapture(id).catch(() => undefined);
      } else if (prev === "rest" && phase === "round" && status?.is_paused) {
        // Don't resume immediately — kick a 3-2-1 countdown with a
        // beep so the fighter knows the next round is starting.
        // Skip if a countdown is already running (e.g. user clicked
        // Resume manually right at the boundary). Mark the start so
        // the round timer freezes at the rest-end while the count
        // ticks down.
        setCountdown((c) => {
          if (c !== null) return c;
          setBreakResumeStart(Date.now());
          return { kind: "break_resume", n: 3 };
        });
      }
    }
    // Auto-stop the session once the planned rounds are done. Immediately
    // refresh so `isLive` flips to false and the camera preview stops.
    if (phase === "done" && session?.status === "capturing") {
      api.stopCapture(id).then(() => refresh()).catch(() => undefined);
    }
  }, [
    now,
    session?.status,
    captureEpoch,
    session?.round_count,
    session?.round_duration_s,
    session?.rest_duration_s,
    status?.is_paused,
    id,
    manualPauseAccumMs,
    manualPauseStart,
    breakResumeAccumMs,
    breakResumeStart,
  ]);

  const refresh = async () => {
    try {
      const [s, st, ev] = await Promise.all([
        api.getSession(id),
        api.captureStatus(id),
        api.listEvents(id),
      ]);
      setSession(s);
      setStatus(st);
      setEvents(ev);
      // Only sync notes from the server if the user hasn't typed unsaved
      // changes in the textarea.
      if (!notesDirty) setNotesDraft(s.notes ?? "");
      // NOTE: setup fields (source, backend, rounds) are seeded once
      // in a separate effect below — NOT here, because refresh() runs
      // every 1.5 s and would overwrite the user's local picks.
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    refresh();
    api.capabilities().then(setCaps).catch(() => setCaps(null));
    api
      .listCameras()
      .then((r) => {
        setCameras(r.cameras);
        setCameraReason(r.reason);
        if (r.cameras.length > 0) setCameraIndex(r.cameras[0].index);
      })
      .catch(() => setCameras([]));
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [id]);

  // One-shot: seed the setup form from the server's initial values.
  // Runs once on mount, then never again — so the 1.5 s poll can't
  // overwrite what the user picks in the UI.
  useEffect(() => {
    let cancelled = false;
    api.getSession(id).then((s) => {
      if (cancelled || s.status !== "pending") return;
      setSetupSource(s.source);
      setSetupBackend(s.pose_backend);
      setSetupCondition(s.study_condition ?? null);
      // Round config is seeded by RoundConfigCard internally.
    }).catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const upload = async (file: File) => {
    setUploading(true);
    try {
      await api.uploadVideo(id, file);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setUploading(false);
    }
  };

  const startSession = async () => {
    if (countdown !== null) return;
    setSetupBusy(true);
    setErr(null);
    try {
      // Round config is auto-saved by RoundConfigCard (debounced 600 ms),
      // so no need to patch it here. Just handle source-specific uploads.
      // Persist the RQ2 study condition before kicking off capture.
      if (setupCondition !== null) {
        await api.patchStudyCondition(id, setupCondition);
      }
      // Side-channel uploads when the source needs them.
      if (setupSource === "uploaded_video" && setupVideoFile) {
        await api.uploadVideo(id, setupVideoFile);
      }
      if (setupSource === "hrv_replay" && setupHrvFile) {
        await api.uploadHrvCsv(id, setupHrvFile);
        await api.loadHrvSync(id).catch(() => undefined);
      }
      await refresh();
      // Now kick the 3-2-1 countdown.
      setCountdown({ kind: "start", n: 3 });
    } catch (e) {
      setErr(String(e));
    } finally {
      setSetupBusy(false);
    }
  };

  const stop = async () => {
    try {
      // Stop capture and HRV stream simultaneously.
      await Promise.all([
        api.stopCapture(id),
        api.stopHrv(id).catch(() => undefined),
        api.stopImuBle(id).catch(() => undefined),
      ]);
      await refresh();
    } catch (e) {
      setErr(String(e));
    }
  };

  const pause = async () => {
    try {
      await api.pauseCapture(id);
      setManualPauseStart(Date.now());
      await refresh();
    } catch (e) {
      setErr(String(e));
    }
  };

  const resume = () => {
    if (countdown !== null) return;
    setCountdown({ kind: "resume", n: 3 });
  };

  // Beep using the Web Audio API. A short oscillator pulse — pitched
  // higher on the final "GO" tick (n=0) so the fighter can hear when
  // capture actually starts.
  const playTick = (kind: "tick" | "go") => {
    try {
      const Ctx =
        (window as unknown as { AudioContext?: typeof AudioContext })
          .AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = kind === "go" ? 1100 : 800;
      gain.gain.value = 0.0001;
      osc.connect(gain);
      gain.connect(ctx.destination);
      const t0 = ctx.currentTime;
      gain.gain.exponentialRampToValueAtTime(0.25, t0 + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.18);
      osc.start(t0);
      osc.stop(t0 + 0.2);
      setTimeout(() => ctx.close(), 250);
    } catch {
      // Audio is best-effort; silent failure is fine.
    }
  };

  // Tick the unified countdown each second. On hit 0, fire the
  // appropriate API action and close out the manual-pause window
  // when applicable.
  useEffect(() => {
    if (countdown === null) return;
    // Beep on entry to each tick (3, 2, 1) and on go (0).
    playTick(countdown.n === 0 ? "go" : "tick");
    if (countdown.n <= 0) {
      const kind = countdown.kind;
      (async () => {
        try {
          if (kind === "start") {
            // Start capture AND BLE streaming simultaneously.
            const captureP = api.startCapture(id, { camera_index: cameraIndex, pose_backend: setupBackend });
            const polar = getPairedDevice();
            const bleP = polar
              ? api.startHrvBle(id, polar.address).catch((e) =>
                  console.warn("BLE stream failed to start (capture continues):", e),
                )
              : Promise.resolve();
            await Promise.all([captureP, bleP]);
            setCaptureEpoch(Date.now());
          } else {
            await api.resumeCapture(id);
            if (kind === "resume" && manualPauseStart != null) {
              setManualPauseAccumMs((a) => a + (Date.now() - manualPauseStart));
              setManualPauseStart(null);
            }
            if (kind === "break_resume" && breakResumeStart != null) {
              setBreakResumeAccumMs(
                (a) => a + (Date.now() - breakResumeStart),
              );
              setBreakResumeStart(null);
            }
          }
          await refresh();
        } catch (e) {
          setErr(String(e));
        } finally {
          setCountdown(null);
        }
      })();
      return;
    }
    const t = setTimeout(
      () =>
        setCountdown((c) => (c === null ? null : { ...c, n: c.n - 1 })),
      1000,
    );
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countdown]);

  const reprocess = async () => {
    try {
      await api.reprocessCapture(id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    }
  };

  const uploadBaseline = async (file: File) => {
    setBaselineUploading(true);
    try {
      await api.uploadBaseline(id, file);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBaselineUploading(false);
    }
  };

  const saveNotes = async () => {
    try {
      await api.annotateSession(id, notesDraft);
      setNotesDirty(false);
      await refresh();
    } catch (e) {
      setErr(String(e));
    }
  };

  const doDelete = async () => {
    try {
      await api.deleteSession(id);
      router.push(session?.fighter_id ? `/fighters/${session.fighter_id}` : "/");
    } catch (e) {
      setErr(String(e));
      setConfirmDelete(false);
    }
  };

  if (!session) {
    return (
      <main className="p-8 text-sm text-neutral-400">
        {err ? <span className="text-red-400">{err}</span> : "Loading…"}
      </main>
    );
  }

  const counts = events.reduce(
    (acc, e) => ({ ...acc, [e.hand]: (acc[e.hand] ?? 0) + 1 }),
    {} as Record<string, number>,
  );

  const cvAvailable = caps?.cv_available ?? true; // assume yes if check failed
  // showStart is no longer used — the inline setup UI handles the
  // pending state. Keep a minimal flag only for the countdown overlay.
  const showStart = false;

  const isLive =
    session.status === "capturing" || session.status === "processing";
  const liveDurationMs = isLive
    ? session.status === "capturing" && captureEpoch
      ? Math.max(
          0,
          now -
            captureEpoch -
            manualPauseAccumMs -
            (manualPauseStart != null ? now - manualPauseStart : 0) -
            breakResumeAccumMs -
            (breakResumeStart != null ? now - breakResumeStart : 0),
        )
      : status?.duration_ms ?? 0
    : status?.duration_ms ?? 0;

  // Two distinct paused states to surface differently in the UI:
  // - manual: user explicitly hit Pause. Always shown in red.
  //   `manualPauseStart` is only set inside the `pause()` handler.
  // - break: auto-pause that fires when the round timer enters a rest
  //   phase between rounds. status.is_paused is true but the user
  //   didn't trigger it.
  const isManualPaused =
    !!status?.is_paused && manualPauseStart !== null;
  const isBreak = !!status?.is_paused && manualPauseStart === null;

  // How many rounds have fully finished (elapsed time past their end).
  // Used to trigger progressive AI advice after each round.
  const completedRounds = (() => {
    if (!isLive || !captureEpoch) return 0;
    const roundS = session.round_duration_s ?? 180;
    const elapsedS = liveDurationMs / 1000;
    const totalRounds = session.round_count ?? 3;
    return Math.min(totalRounds, Math.floor(elapsedS / roundS));
  })();

  return (
    <main className="space-y-6 p-8">
      <div className="flex items-center justify-between">
        {session.fighter_id ? (
          <FighterBackLink fighterId={session.fighter_id} />
        ) : (
          <Link
            href="/"
            className="inline-flex items-center gap-1 text-sm text-neutral-400 hover:text-neutral-100"
          >
            <span aria-hidden>←</span> Back home
          </Link>
        )}
        {authUser?.role !== "gym_manager" && (
          <button
            onClick={() => setConfirmDelete(true)}
            disabled={session.status === "capturing" || session.status === "processing"}
            title={
              session.status === "capturing" || session.status === "processing"
                ? "Stop the capture before deleting."
                : undefined
            }
            className="text-sm text-red-400 hover:text-red-300 disabled:cursor-not-allowed disabled:text-neutral-600"
          >
            Delete session
          </button>
        )}
      </div>

      {/* Three columns on wide screens (1400px+) — title + devices | join QR (top middle)
          | status — so the QR sits centred; narrower, it flows after the title. */}
      <header className="flex flex-wrap items-center justify-between gap-4 min-[1400px]:grid min-[1400px]:grid-cols-[1fr_auto_1fr]">
        {/* Wraps its own items rather than squeezing the title when space is tight. */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div>
            <h1 className="text-2xl font-semibold">{session.bout_id ? "Fight" : "Training session"}</h1>
            <p className="font-mono text-xs text-neutral-500" title={session.id}>
              {session.id.slice(0, 8)}
            </p>
          </div>
          <Link
            href={`/sessions/${id}/corner`}
            className="rounded bg-amber-600/20 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-600/30"
          >
            🥊 Gym Mode
          </Link>
          <PolarH10Card />
        </div>
        {/* Join QR — only while cameras can still be added. */}
        {(session.status === "pending" || session.status === "capturing") ? (
          <JoinQrCard sessionId={session.id} compact />
        ) : (
          <div className="hidden min-[1400px]:block" />
        )}
        {/* Status + backend badges — hidden for pending (the setup UI
            below already conveys both). */}
        {session.status !== "pending" && (
          <div className="flex items-center gap-2 min-[1400px]:justify-self-end">
            {(() => {
              let label: string = session.status;
              let cls = "bg-neutral-800 text-neutral-300";
              if (session.status === "completed") {
                cls = "bg-emerald-900 text-emerald-200";
              } else if (session.status === "failed") {
                cls = "bg-red-900 text-red-200";
              } else if (
                session.status === "capturing" ||
                session.status === "processing"
              ) {
                if (isManualPaused) {
                  label = "paused";
                  cls = "bg-red-700 text-red-100";
                } else if (isBreak) {
                  label = "break";
                  cls = "bg-amber-700 text-amber-100";
                } else {
                  cls = "bg-amber-900 text-amber-200";
                }
              }
              return (
                <span
                  className={`rounded-full px-3 py-1 text-xs font-medium ${cls}`}
                >
                  {label}
                </span>
              );
            })()}
            <span className={`rounded-full px-3 py-1 text-xs font-medium ${
              session.pose_backend === "yolov8"
                ? "bg-purple-900 text-purple-200"
                : "bg-blue-900 text-blue-200"
            }`}>
              {session.pose_backend === "yolov8" ? "YOLOv8" : "MediaPipe"}
            </span>
            {session.study_condition && (
              <span className="rounded-full bg-amber-900/60 px-3 py-1 text-xs font-medium text-amber-200">
                RQ2: {session.study_condition.replace(/_/g, " ")}
              </span>
            )}
          </div>
        )}
      </header>

      {err && <p className="text-sm text-red-400">{err}</p>}

      {/* Capture: live reader + round configuration (left) and the cameras panel
          (right). Only while the session can still capture — hidden once completed. */}
      {(session.status === "pending" || session.status === "capturing") && (
        <div className="grid items-start gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
          <div className="space-y-6">
            <LiveReader sessionId={session.id} fighterId={session.fighter_id} />
            <RoundConfigCard session={session} onChange={setSession} />
          </div>
          <MulticamPanel
            session={session}
            defaultLaptop
            onFinished={refresh}
            onDelete={async () => {
              // Delete for good, then a fresh session for the same fighter with the
              // same round setup — the linked phones follow it there.
              await api.deleteSession(session.id);
              const next = await api.createSession(
                session.fighter_id,
                session.source,
                session.pose_backend ?? "mediapipe",
              );
              if (session.round_count != null) {
                await api
                  .patchSessionConfig(next.id, {
                    round_count: session.round_count,
                    round_duration_s: session.round_duration_s ?? undefined,
                    rest_duration_s: session.rest_duration_s ?? undefined,
                  })
                  .catch(() => {});
              }
              router.replace(`/sessions/${next.id}`);
            }}
          />
        </div>
      )}

      {/* Recorded per-camera clips — disk-scanned, so they show right after Stop and
          for any past session. Renders nothing until a session has clips. */}
      {/* Keyed on status so the clip list reloads when Stop completes the session. */}
      <MulticamRecordings key={session.status} sessionId={session.id} />

      {session.status === "failed" && session.failure_reason && cvAvailable && (
        <div className="rounded-lg border border-red-700/60 bg-red-950/40 p-4 text-sm">
          <p className="font-medium text-red-200">Capture failed</p>
          <p className="mt-1 text-red-100/80">{session.failure_reason}</p>
        </div>
      )}

      {/* ── Pending: full setup UI — hidden when browser capture handles it ── */}
      {session.status === "pending" && (cvAvailable || session.source !== "live_webcam") && (
        <section className="space-y-5 rounded-lg border border-neutral-800 bg-neutral-950/60 p-5">
          <h2 className="text-lg font-semibold">Session setup</h2>

          {/* Camera permission warning — only shown when server-side CV is available.
              When cvAvailable is false the Cameras panel manages its own camera. */}
          {cvAvailable && setupSource === "live_webcam" && cameras.length === 0 && cameraReason && (
            <div className="rounded-lg border border-amber-700/60 bg-amber-950/40 p-4 text-sm">
              <p className="font-medium text-amber-200">⚠ Camera not available</p>
              <p className="mt-1 text-amber-100/80">{cameraReason}</p>
            </div>
          )}

          {/* Source + Pose model on the same row */}
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <label className="text-xs text-neutral-400">Source</label>
              <div className="mt-1 flex flex-wrap gap-2">
                {(
                  [
                    ["live_webcam", "Live webcam"],
                    ["uploaded_video", "Upload MP4"],
                    ["hrv_replay", "HRV replay"],
                  ] as const
                ).map(([val, lbl]) => (
                  <button
                    key={val}
                    onClick={() => setSetupSource(val)}
                    className={`rounded-full px-3 py-1 text-xs ${
                      setupSource === val
                        ? "bg-emerald-600 text-white"
                        : "bg-white/[0.04] text-neutral-400 hover:bg-white/[0.08]"
                    }`}
                  >
                    {lbl}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-xs text-neutral-400">Pose model</label>
              <div className="mt-1 flex flex-wrap gap-2">
                {(
                  [
                    ["mediapipe", "MediaPipe"],
                    ["yolov8", "YOLOv8"],
                  ] as const
                ).map(([val, lbl]) => (
                  <button
                    key={val}
                    onClick={() => setSetupBackend(val)}
                    className={`rounded-full px-3 py-1 text-xs ${
                      setupBackend === val
                        ? "bg-purple-600 text-white"
                        : "bg-white/[0.04] text-neutral-400 hover:bg-white/[0.08]"
                    }`}
                  >
                    {lbl}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* RQ2 study condition */}
          <div>
            <label className="text-xs text-neutral-400">
              Study condition <span className="text-neutral-600">(RQ2)</span>
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              {(
                [
                  [null, "None"],
                  ["cv_only", "CV only"],
                  ["imu_only", "IMU only"],
                  ["hrv_only", "HRV only"],
                  ["fused", "Fused"],
                  ["coach_only", "Coach only"],
                ] as [StudyCondition | null, string][]
              ).map(([val, lbl]) => (
                <button
                  key={String(val)}
                  onClick={() => setSetupCondition(val)}
                  className={`rounded-full px-3 py-1 text-xs ${
                    setupCondition === val
                      ? "bg-amber-600 text-white"
                      : "bg-white/[0.04] text-neutral-400 hover:bg-white/[0.08]"
                  }`}
                >
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          {/* Round structure — reuses RoundConfigCard which includes
              saved plans, presets (3×3 pro, 12×3 fight, 3×2 amateur),
              and auto-saves to the server after 600 ms. */}
          <RoundConfigCard session={session} onChange={setSession} />

          {/* Camera picker (live_webcam only) */}
          {setupSource === "live_webcam" && cameras.length > 1 && (
            <div>
              <label className="text-xs text-neutral-400">Camera</label>
              <select
                className="mt-1 w-full rounded bg-neutral-900 p-2 text-sm"
                value={cameraIndex}
                onChange={(e) => setCameraIndex(Number(e.target.value))}
              >
                {cameras.map((c) => (
                  <option key={c.index} value={c.index}>
                    Camera {c.index} ({c.width}×{c.height})
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* File upload for uploaded_video / hrv_replay */}
          {setupSource === "uploaded_video" && (
            <div>
              <label className="text-xs text-neutral-400">Video file</label>
              <input
                type="file"
                accept="video/*"
                onChange={(e) => setSetupVideoFile(e.target.files?.[0] ?? null)}
                className="mt-1 w-full text-sm"
              />
            </div>
          )}
          {setupSource === "hrv_replay" && (
            <div>
              <label className="text-xs text-neutral-400">HRV CSV file</label>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setSetupHrvFile(e.target.files?.[0] ?? null)}
                className="mt-1 w-full text-sm"
              />
            </div>
          )}

          {/* Start button */}
          <button
            onClick={startSession}
            disabled={
              setupBusy ||
              countdown !== null ||
              (setupSource === "uploaded_video" && !setupVideoFile) ||
              (setupSource === "hrv_replay" && !setupHrvFile)
            }
            className="w-full rounded-xl bg-emerald-500 px-5 py-3 text-sm font-semibold text-black shadow-lg shadow-emerald-900/30 hover:bg-emerald-400 disabled:opacity-50"
          >
            {setupBusy ? "Starting…" : countdown ? `${countdown.n}…` : "Start live capture"}
          </button>

          {/* Countdown overlay */}
          {countdown?.kind === "start" && (
            <div className="flex items-center justify-center py-8">
              <span className="text-[10rem] font-black leading-none text-emerald-400 drop-shadow-2xl">
                {countdown.n > 0 ? countdown.n : "GO"}
              </span>
            </div>
          )}
        </section>
      )}

      {/* Top action bar — controls + (when live) the camera preview.
          Sits above the main 3-column layout so the primary capture
          controls and the live skeleton overlay are always above the
          fold regardless of state. */}
      {isLive && (
        <section
          className={`relative space-y-4 rounded-lg border p-4 transition-colors ${
            isManualPaused
              ? "border-red-500 bg-red-700/30"
              : isBreak
                ? "border-amber-500 bg-amber-600/20"
                : "border-neutral-800 bg-neutral-950/60"
          }`}
        >
          {countdown?.kind === "start" && (
            <div className="absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-emerald-700/80">
              <span className="text-[14rem] font-black leading-none text-white drop-shadow-2xl">
                {countdown.n > 0 ? countdown.n : "GO"}
              </span>
            </div>
          )}
          {/* Round timer pinned at the top of the capture panel during
              live capture so the fighter can read elapsed/round-left
              while looking at the camera. */}
          {isLive && (
            <RoundTimer
              session={session}
              durationMs={liveDurationMs}
              isPaused={isManualPaused}
            />
          )}
          <div
            className={`grid grid-cols-1 gap-4 ${
              isLive ? "lg:grid-cols-[1fr_minmax(0,420px)]" : ""
            }`}
          >
            {/* Live preview: only visible during capture. Lives in the
                same panel as the capture controls so "what the camera sees"
                is co-located with start/pause/stop. */}
            {isLive && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-medium">Camera</h2>
                  <span
                    className={`flex items-center gap-2 text-xs ${
                      isManualPaused
                        ? "text-red-200"
                        : isBreak
                          ? "text-amber-200"
                          : "text-neutral-400"
                    }`}
                  >
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${
                        isManualPaused
                          ? "bg-red-500"
                          : isBreak
                            ? "bg-amber-400"
                            : "animate-pulse bg-red-500"
                      }`}
                    />
                    {isManualPaused ? "paused" : isBreak ? "break" : "live"}
                  </span>
                </div>
                <div className="relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    key={session.id}
                    src={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/sessions/${session.id}/preview`}
                    alt="live capture preview with pose overlay"
                    className="w-full rounded border border-neutral-800 bg-black"
                  />
                  {(isManualPaused || isBreak || countdown !== null) && (
                    <div
                      className={`absolute inset-0 flex items-center justify-center rounded ${
                        countdown?.kind === "break_resume"
                          ? "bg-amber-600/60"
                          : isBreak && countdown === null
                            ? "bg-amber-600/60"
                            : "bg-red-700/60"
                      }`}
                    >
                      {countdown !== null && countdown.n > 0 ? (
                        <span className="text-[12rem] font-black leading-none text-white drop-shadow-2xl">
                          {countdown.n}
                        </span>
                      ) : (
                        <span className="text-7xl font-black uppercase tracking-widest text-white drop-shadow-lg">
                          {isManualPaused ? "Paused" : "Break"}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <p className="text-[11px] text-neutral-500">
                  Skeleton overlay from MediaPipe; preview at ~15 fps,
                  capture at native rate.
                </p>
              </div>
            )}

            {/* Controls column — only live capture controls remain. */}
            <div className="flex flex-wrap items-end gap-3">
              {isLive && (
                <>
                  {status?.is_paused ? (
                    <button
                      onClick={resume}
                      disabled={countdown !== null}
                      className="rounded bg-emerald-600 px-4 py-2 font-medium hover:bg-emerald-500 disabled:cursor-wait disabled:bg-neutral-700"
                    >
                      {countdown?.kind === "resume" || countdown?.kind === "break_resume"
                        ? `Resuming in ${countdown.n}…`
                        : "Resume"}
                    </button>
                  ) : (
                    <button
                      onClick={pause}
                      className="rounded bg-amber-600 px-4 py-2 font-medium hover:bg-amber-500"
                    >
                      Pause
                    </button>
                  )}
                  <button
                    onClick={stop}
                    className="rounded bg-red-600 px-4 py-2 font-medium hover:bg-red-500"
                  >
                    Stop capture
                  </button>
                  {(isManualPaused || isBreak) && (
                    <span
                      className={`self-center text-xs ${
                        isManualPaused ? "text-red-300" : "text-amber-300"
                      }`}
                    >
                      {isManualPaused ? "paused" : "break"}
                    </span>
                  )}
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {/* 3-col layout: timer + round config (left), all metrics +
          analysis (middle), AI corner advice (right). Stacks on mobile.
          Hidden entirely for pending sessions — the setup UI above is
          all the user needs before capture starts. */}
      {session.status !== "pending" && (
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[280px_minmax(0,1fr)_320px]">
        <aside className="space-y-4 xl:sticky xl:top-4 xl:self-start">
          {/* ── Session stats panel (combined) ── */}
          {(() => {
            const durationMs = status?.duration_ms ?? 0;
            const hasData = events.length > 0 && durationMs > 0;
            let peakP90: number | null = null;
            let ppmVal: number | null = null;
            let score: number | null = null;
            const durationMin = durationMs / 60_000;
            if (hasData) {
              const sorted = [...events].map((e) => e.velocity_ms).sort((a, b) => a - b);
              const k = (sorted.length - 1) * 0.9;
              const lo = Math.floor(k);
              const hi = Math.min(lo + 1, sorted.length - 1);
              peakP90 = sorted[lo] * (1 - (k - lo)) + sorted[hi] * (k - lo);
              ppmVal = events.length / Math.max(durationMin, 1e-6);
              score = peakP90 * (ppmVal / 60) * durationMin;
            }
            const fmt = (v: number | null, d = 2) => (v === null ? "—" : v.toFixed(d));
            const hardest = events.length > 0
              ? events.reduce((m, e) => (e.velocity_ms > m.velocity_ms ? e : m), events[0])
              : null;
            const meanConf = events.length >= 5
              ? events.reduce((s, e) => s + e.confidence, 0) / events.length
              : 1;

            return (
              <section className="rounded-lg border border-neutral-800 bg-neutral-950/60 p-4 space-y-3">
                {/* Quick stats row */}
                <div className="grid grid-cols-2 gap-2">
                  <MiniStat label="Frames" value={status?.frame_count ?? 0} />
                  <MiniStat label="Duration" value={`${((durationMs) / 1000).toFixed(1)}s`} />
                  <MiniStat label="Punches" value={status?.punch_count ?? 0} />
                  <MiniStat label="PPM" value={ppmVal !== null ? ppmVal.toFixed(0) : "—"} />
                </div>

                {/* Performance metrics */}
                <div className="border-t border-white/5 pt-3">
                  <div className="flex items-baseline justify-between">
                    <h3 className="text-xs font-medium text-neutral-300">Performance</h3>
                    <span className="rounded bg-amber-950/40 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-amber-200">
                      Score {fmt(score)}
                    </span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-center text-xs">
                    <div>
                      <div className="text-[10px] text-neutral-500">Peak p90</div>
                      <div className="font-semibold tabular-nums">{fmt(peakP90)} <span className="text-[10px] text-neutral-500">m/s</span></div>
                    </div>
                    <div>
                      <div className="text-[10px] text-neutral-500">Throughput</div>
                      <div className="font-semibold tabular-nums">{fmt(ppmVal, 0)} <span className="text-[10px] text-neutral-500">ppm</span></div>
                    </div>
                    <div>
                      <div className="text-[10px] text-neutral-500">Duration</div>
                      <div className="font-semibold tabular-nums">{durationMin > 0 ? durationMin.toFixed(2) : "—"} <span className="text-[10px] text-neutral-500">min</span></div>
                    </div>
                  </div>
                </div>

                {/* Hardest punch */}
                <div className="border-t border-white/5 pt-3">
                  <div className="flex items-baseline justify-between">
                    <h3 className="text-xs font-medium text-amber-200">Hardest punch</h3>
                    <span className="text-[10px] text-neutral-500">
                      {hardest ? formatPunchTime(session.started_at, hardest.t_ms) : "—"}
                    </span>
                  </div>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="text-2xl font-bold tabular-nums text-amber-100">
                      {hardest ? hardest.velocity_ms.toFixed(2) : "—"}
                    </span>
                    <span className="text-xs text-neutral-400">m/s</span>
                  </div>
                  {hardest && (
                    <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-neutral-400">
                      <span>
                        <span className={hardest.hand === "left" ? "text-amber-300" : "text-sky-300"}>
                          {hardest.hand}
                        </span>
                      </span>
                      {hardest.punch_type && <span>{hardest.punch_type}</span>}
                      {hardest.lead_or_rear && <span>{hardest.lead_or_rear}</span>}
                      <span>conf {hardest.confidence.toFixed(2)}</span>
                    </div>
                  )}
                </div>

                {/* Low pose quality warning */}
                {events.length >= 5 && meanConf < 0.7 && (
                  <div className="border-t border-amber-700/40 pt-3">
                    <p className="text-[11px] font-medium text-amber-200">Low pose quality</p>
                    <p className="mt-0.5 text-[10px] text-amber-100/70">
                      Mean conf {meanConf.toFixed(2)} (threshold 0.70). Better
                      lighting or closer framing may help.
                    </p>
                  </div>
                )}

                {!hasData && (
                  <p className="text-[10px] text-neutral-500">
                    Waiting for first punch detection…
                  </p>
                )}
              </section>
            );
          })()}
        </aside>
        <div className="space-y-6">
      {/* HRV panel — above per-round breakdown so live HR is immediately visible.
          Gated by study condition; always shown when no condition is set so the
          CSV replay fallback is always accessible regardless of device pairing. */}
      {(!session.study_condition || ["hrv_only", "fused"].includes(session.study_condition)) && (
        <HrvPanel sessionId={session.id} hasPairedDevice={getPairedDevice() !== null} />
      )}

      {session.video_path && (
        <div className="card">
          <h2 className="mb-3 text-lg font-semibold">Recording</h2>
          <SessionVideo sessionId={session.id} />
        </div>
      )}

      <RoundBreakdownCard sessionId={session.id} status={session.status} />

      {/* Detector evaluation — labels-vs-detections accuracy.
          Shown for any completed session; the card itself handles the
          "no labels yet" empty state. */}
      {session.status === "completed" && events.length > 0 && (
        <EvaluationCard sessionId={id} />
      )}
      {(!session.study_condition || ["imu_only", "fused"].includes(session.study_condition)) && (
        <IMUPanel sessionId={session.id} punchEvents={events} />
      )}
      <RQ1RaterCard sessionId={session.id} />

      <PunchChart events={events} />

      {events.length > 0 && status?.duration_ms && status.duration_ms > 0 && (
        <section className="rounded-lg border border-neutral-800 p-4">
          <h2 className="font-medium">Timeline</h2>
          <p className="mt-1 text-xs text-neutral-500">
            Each dot is a detected punch; size scales with peak velocity.
            Hover for details.
          </p>
          <div className="mt-3">
            <PunchTimeline events={events} durationMs={status.duration_ms} />
          </div>
        </section>
      )}

      {events.length > 1 && (
        <section className="rounded-lg border border-neutral-800 p-4">
          <h2 className="font-medium">Velocity distribution</h2>
          <p className="mt-1 text-xs text-neutral-500">
            How your punches stack across the velocity range. Bars stack
            left + right counts within each bucket.
          </p>
          <div className="mt-3">
            <VelocityHistogram events={events} />
          </div>
        </section>
      )}

      <section className="rounded-lg border border-neutral-800 p-4">
        <h2 className="font-medium">Pre-session HRV baseline</h2>
        <p className="mt-1 text-xs text-neutral-500">
          Upload a 5-min resting RR-interval CSV (single column{" "}
          <code className="rounded bg-neutral-900 px-1 font-mono">rr_ms</code>{" "}
          or two columns{" "}
          <code className="rounded bg-neutral-900 px-1 font-mono">
            t_ms,rr_ms
          </code>
          ) recorded just before warmup. Used as the readiness signal in the
          fighter performance matrix.
        </p>
        {session.baseline_rmssd_ms != null ? (
          <div className="mt-3 flex flex-wrap gap-3 text-sm">
            <span className="rounded bg-emerald-900/40 px-2 py-1 text-emerald-200">
              RMSSD {session.baseline_rmssd_ms.toFixed(1)} ms
            </span>
            <span className="rounded bg-emerald-900/40 px-2 py-1 text-emerald-200">
              SDNN {session.baseline_sdnn_ms?.toFixed(1) ?? "—"} ms
            </span>
            <span className="rounded bg-emerald-900/40 px-2 py-1 text-emerald-200">
              Mean HR {session.baseline_mean_hr_bpm?.toFixed(0) ?? "—"} bpm
            </span>
            {session.baseline_recorded_at && (
              <span className="self-center text-xs text-neutral-500">
                recorded{" "}
                {new Date(session.baseline_recorded_at).toLocaleString()}
              </span>
            )}
          </div>
        ) : (
          <p className="mt-3 text-sm text-neutral-500">
            No baseline recorded for this session.
          </p>
        )}
      </section>

      <section className="rounded-lg border border-neutral-800 p-4">
        <h2 className="font-medium">Notes &amp; tags</h2>
        <p className="mt-1 text-xs text-neutral-500">
          Free-form notes for this session. Useful tags: shadowboxing, bag,
          mitts, sparring.
        </p>
        <textarea
          rows={3}
          value={notesDraft}
          onChange={(e) => {
            setNotesDraft(e.target.value);
            setNotesDirty(true);
          }}
          placeholder="e.g. shadowboxing — focus on jab footwork"
          className="mt-2 w-full rounded bg-neutral-900 p-2 text-sm"
        />
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={saveNotes}
            disabled={!notesDirty}
            className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium hover:bg-emerald-500 disabled:bg-neutral-700 disabled:text-neutral-400"
          >
            {notesDirty ? "Save notes" : "Saved"}
          </button>
          {notesDirty && (
            <button
              onClick={() => {
                setNotesDraft(session.notes ?? "");
                setNotesDirty(false);
              }}
              className="text-xs text-neutral-400 hover:text-neutral-100"
            >
              Discard
            </button>
          )}
        </div>
      </section>

      {/* Generic file uploads attached to this session — extra videos,
          sparring photos, coach notes PDFs, etc. */}
      <AttachmentsCard sessionId={id} />

      {events.length > 0 && (
        <section className="rounded-lg border border-neutral-800 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-medium">Export</h2>
            <a
              href={api.eventsCsvUrl(id)}
              download={`alion-${id}-events.csv`}
              className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700"
            >
              Download events CSV
            </a>
          </div>
          {session.source === "uploaded_video" && session.video_path && (
            <div className="mt-3 flex items-center gap-2">
              <button
                onClick={reprocess}
                disabled={
                  session.status === "capturing" ||
                  session.status === "processing"
                }
                className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700 disabled:cursor-not-allowed disabled:bg-neutral-900 disabled:text-neutral-600"
                title="Re-runs the pipeline on the uploaded video. Existing events are wiped first."
              >
                Re-process video
              </button>
              <span className="text-xs text-neutral-500">
                Re-runs detection on the uploaded video. Existing events are
                replaced.
              </span>
            </div>
          )}
        </section>
      )}

      <section className="rounded-lg border border-neutral-800 p-4">
        <h2 className="font-medium">
          Punches{" "}
          <span className="text-sm text-neutral-500">
            (L {counts.left ?? 0} · R {counts.right ?? 0})
          </span>
        </h2>
        {events.length === 0 ? (
          <p className="mt-2 text-sm text-neutral-500">No events yet.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead className="text-left text-xs uppercase text-neutral-500">
              <tr>
                <th className="py-1">#</th>
                <th>Hand</th>
                <th>Type</th>
                <th>Time</th>
                <th>Velocity (m/s)</th>
                <th>Conf</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i} className="border-t border-neutral-800">
                  <td className="py-1 font-mono text-xs text-neutral-500">{i + 1}</td>
                  <td className={e.hand === "left" ? "text-amber-300" : "text-sky-300"}>
                    {e.hand}
                  </td>
                  <td className="capitalize text-neutral-300">{e.punch_type || "—"}</td>
                  <td className="font-mono">{formatPunchTime(session.started_at, e.t_ms)}</td>
                  <td className="font-mono">{e.velocity_ms.toFixed(2)}</td>
                  <td className="font-mono">{e.confidence.toFixed(2)}</td>
                  <td className="text-neutral-500">
                    {e.detected_by === "custom_ml" ? "ML" : e.detected_by}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
        </div>
        <aside className="space-y-4 xl:sticky xl:top-4 xl:self-start">
          {session.study_condition !== "coach_only" && (
            <LiveAdviceCard
              sessionId={session.id}
              status={session.status}
              completedRounds={completedRounds}
            />
          )}
          <DetectorComparisonCard
            sessionId={session.id}
            status={session.status}
          />
        </aside>
      </div>
      )}

      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
          <div className="w-full max-w-md rounded-lg border border-neutral-800 bg-neutral-950 p-5 shadow-xl">
            <h3 className="font-medium">Delete this session?</h3>
            <p className="mt-2 text-sm text-neutral-400">
              This removes the session row, all detected punch events, captured
              pose data, and any uploaded video file. Cannot be undone.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setConfirmDelete(false)}
                className="rounded bg-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-600"
              >
                Cancel
              </button>
              <button
                onClick={doDelete}
                className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium hover:bg-red-500"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

function parseUtc(s: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z");
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-neutral-800 p-3">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded border border-white/5 bg-black/30 px-2 py-1.5">
      <div className="text-[10px] text-neutral-500">{label}</div>
      <div className="text-sm font-semibold tabular-nums">{value}</div>
    </div>
  );
}

/**
 * Convert (session start, offset ms) → "h:mm:ss AM/PM" wall-clock time.
 * Server returns started_at as an ISO timestamp in UTC; the browser's
 * `new Date(...)` parses it and `toLocaleTimeString` renders in the
 * user's local timezone with their locale's preferred 12/24-hour format.
 */
function formatPunchTime(sessionStartedAt: string, eventTMs: number): string {
  const startMs = new Date(sessionStartedAt).getTime();
  if (Number.isNaN(startMs)) return "—";
  const d = new Date(startMs + eventTMs);
  return d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}
