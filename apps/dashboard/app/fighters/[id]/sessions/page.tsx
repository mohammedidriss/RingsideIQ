"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  api,
  type PunchEvent,
  type Session,
  type SessionSource,
  type SessionStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { SessionCameraPreview } from "@/components/SessionCameraPreview";

const STATUS_TINT: Record<SessionStatus, string> = {
  completed: "bg-emerald-500/15 text-emerald-300",
  failed: "bg-red-500/15 text-red-300",
  capturing: "bg-amber-500/15 text-amber-300",
  processing: "bg-amber-500/15 text-amber-300",
  pending: "bg-neutral-700/40 text-neutral-300",
};

const SOURCE_LABEL: Record<SessionSource, string> = {
  live_webcam: "Live webcam",
  uploaded_video: "Uploaded video",
  live_iphone: "Live iPhone",
  polar_h10_only: "Polar H10",
  hrv_replay: "HRV replay",
};

interface Row {
  session: Session;
  punches: number;
  peakVelocity: number;
}


export default function SessionsTab({ params }: { params: { id: string } }) {
  const router = useRouter();
  const { user } = useAuth();
  const [rows, setRows] = useState<Row[]>([]);

  // Admin cannot view session data — admin manages accounts, not training data
  if (user?.role === "admin") {
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
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | SessionStatus>("all");

  useEffect(() => {
    api
      .listSessions(params.id, { recorded: true })
      .then(async (sList) => {
        const enriched = await Promise.all(
          sList.map(async (s) => {
            const evs = await api.listEvents(s.id).catch(() => [] as PunchEvent[]);
            const peak = evs.length ? Math.max(...evs.map((e) => e.velocity_ms)) : 0;
            return { session: s, punches: evs.length, peakVelocity: peak };
          }),
        );
        enriched.sort((a, b) =>
          b.session.started_at.localeCompare(a.session.started_at),
        );
        setRows(enriched);
      })
      .catch((e) => setErr(String(e)));
  }, [params.id]);

  const filtered = useMemo(
    () => (filter === "all" ? rows : rows.filter((r) => r.session.status === filter)),
    [rows, filter],
  );

  const totals = useMemo(() => {
    const completed = rows.filter((r) => r.session.status === "completed");
    const totalPunches = completed.reduce((s, r) => s + r.punches, 0);
    const totalDurationMin =
      completed.reduce((s, r) => s + r.session.duration_ms, 0) / 60000;
    return {
      total: rows.length,
      completed: completed.length,
      totalPunches,
      totalDurationMin,
    };
  }, [rows]);

  return (
    <div className="space-y-6 px-4 py-5 sm:px-8 sm:py-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Training sessions</h1>
          <p className="text-sm text-neutral-400">
            Capture history, uploaded videos, and per-session metrics.
          </p>
        </div>
        {user?.role !== "gym_manager" && (
          <button
            onClick={async (e) => {
              const btn = e.currentTarget;
              btn.disabled = true;
              try {
                const s = await api.createSession(params.id, "live_webcam", "mediapipe");
                router.push(`/sessions/${s.id}`);
              } catch { btn.disabled = false; }
            }}
            className="rounded-xl bg-emerald-500 px-4 py-2 text-sm font-medium text-black hover:bg-emerald-400 disabled:opacity-50"
          >
            + Training session
          </button>
        )}
      </header>

      {err && (
        <p className="rounded-2xl border border-red-500/30 bg-red-950/30 p-3 text-sm text-red-200">
          {err}
        </p>
      )}

      {/* HERO: most recent session (the one a coach typically opens for) */}
      {rows.length > 0 && (
        <Link
          href={`/sessions/${rows[0].session.id}`}
          className="card flex flex-wrap items-center gap-x-6 gap-y-3 transition-colors hover:border-white/15"
        >
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-wide text-neutral-500">
              Most recent session
            </div>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-xl font-semibold">
                {new Date(rows[0].session.started_at).toLocaleString()}
              </span>
              <span className={`pill ${STATUS_TINT[rows[0].session.status]}`}>
                {rows[0].session.status}
              </span>
              <span className="pill bg-white/[0.04] text-neutral-300">
                {SOURCE_LABEL[rows[0].session.source] ?? rows[0].session.source}
              </span>
            </div>
            <div className="mt-1 text-sm text-neutral-400">
              {(rows[0].session.duration_ms / 1000).toFixed(1)}s ·{" "}
              {rows[0].punches} punch{rows[0].punches === 1 ? "" : "es"}
              {rows[0].peakVelocity > 0 &&
                ` · peak ${rows[0].peakVelocity.toFixed(1)} m/s`}
            </div>
          </div>
          <span className="text-emerald-400">open detail →</span>
        </Link>
      )}

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Total" value={totals.total} />
        <Stat label="Completed" value={totals.completed} />
        <Stat label="Punches" value={totals.totalPunches.toLocaleString()} />
        <Stat
          label="Active mins"
          value={
            totals.totalDurationMin >= 1
              ? totals.totalDurationMin.toFixed(0)
              : totals.totalDurationMin.toFixed(1)
          }
        />
      </section>

      <div className="flex flex-wrap gap-2">
        {(["all", "completed", "capturing", "pending", "failed"] as const).map(
          (k) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              className={`rounded-full px-3 py-1 text-xs ${
                filter === k
                  ? "bg-white/10 text-white"
                  : "bg-white/[0.03] text-neutral-400 hover:bg-white/[0.07]"
              }`}
            >
              {k}
            </button>
          ),
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-neutral-500">No training sessions match this filter.</p>
      ) : (
        <ul className="space-y-3">
          {filtered.map(({ session: s, punches, peakVelocity }) => (
            <li key={s.id} className="card flex flex-wrap gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <Link
                    href={`/sessions/${s.id}`}
                    className="text-base font-semibold text-emerald-400 hover:underline"
                  >
                    {new Date(s.started_at).toLocaleString()}
                  </Link>
                  <span className={`pill ${STATUS_TINT[s.status]}`}>{s.status}</span>
                  <span className="pill bg-white/[0.04] text-neutral-300">
                    {SOURCE_LABEL[s.source] ?? s.source}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-400">
                  <span>
                    {(s.duration_ms / 1000).toFixed(1)}s ·{" "}
                    {s.frame_count.toLocaleString()} frames
                  </span>
                  {punches > 0 && <span>{punches} punches</span>}
                  {peakVelocity > 0 && (
                    <span>peak {peakVelocity.toFixed(1)} m/s</span>
                  )}
                  {s.baseline_rmssd_ms != null && (
                    <span className="text-emerald-300">
                      RMSSD {s.baseline_rmssd_ms.toFixed(1)} ms
                    </span>
                  )}
                </div>
                {s.notes && (
                  <p className="mt-2 line-clamp-2 text-sm text-neutral-300">
                    {s.notes}
                  </p>
                )}
                {s.failure_reason && (
                  <p className="mt-2 text-sm text-red-300">
                    {s.failure_reason}
                  </p>
                )}
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  <Link
                    href={`/sessions/${s.id}`}
                    className="rounded-lg border border-white/5 bg-white/[0.03] px-2.5 py-1 hover:bg-white/[0.07]"
                  >
                    Open detail
                  </Link>
                  {punches > 0 && (
                    <a
                      href={api.eventsCsvUrl(s.id)}
                      download={`alion-${s.id}-events.csv`}
                      className="rounded-lg border border-white/5 bg-white/[0.03] px-2.5 py-1 hover:bg-white/[0.07]"
                    >
                      Events CSV
                    </a>
                  )}
                </div>
              </div>
              {s.video_path ? (
                <video
                  src={`${process.env.NEXT_PUBLIC_API_URL}/${s.video_path}`}
                  controls
                  preload="metadata"
                  className="h-32 w-56 shrink-0 rounded-xl bg-black object-cover"
                />
              ) : (
                <SessionCameraPreview sessionId={s.id} />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-2xl border border-white/5 bg-white/[0.02] p-3">
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}
