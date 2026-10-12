"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FighterDashboard } from "@/components/FighterDashboard";
import { PolarH10Card } from "@/components/PolarH10Card";
import { ProfileAvatar } from "@/components/ProfileAvatar";
import { Sparkline } from "@/components/Sparkline";
import { useAuth } from "@/lib/auth";
import {
  api,
  type Allergy,
  type Fighter,
  type FighterOptions,
  type FighterPatch,
  type Hand,
  type MedicalCondition,
  type Medication,
  type PunchEvent,
  type Session,
  type SkillLevel,
  type Stance,
  type WeighIn,
  type WeightAnalysis,
} from "@/lib/api";

interface SessionWithStats {
  session: Session;
  events: PunchEvent[];
  totalPunches: number;
  peakVelocity: number;
  meanVelocity: number;
}

const PRO_LEVELS: SkillLevel[] = [
  "recreational",
  "amateur_novice",
  "amateur_open",
  "amateur_elite",
  "semi_pro",
  "professional",
  "coach",
];


export default function FighterPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const router = useRouter();
  const { user } = useAuth();
  const canEdit = !!user && user.role !== "fighter";
  const isAdmin = user?.role === "admin";
  const canCreateSession =
    (canEdit && user?.role !== "gym_manager" && !isAdmin) ||
    (user?.role === "fighter" && user?.profile_id === id);
  const [fighter, setFighter] = useState<Fighter | null>(null);
  const [options, setOptions] = useState<FighterOptions | null>(null);
  const [sessions, setSessions] = useState<SessionWithStats[]>([]);
  const [weighIns, setWeighIns] = useState<WeighIn[]>([]);
  const [editing, setEditing] = useState(false);
  const [confirmDeleteFighter, setConfirmDeleteFighter] = useState(false);
  const [confirmDeleteSession, setConfirmDeleteSession] = useState<string | null>(null);
  const [newWeight, setNewWeight] = useState("");
  const [newWeightNotes, setNewWeightNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [severeAllergies, setSevereAllergies] = useState<Allergy[]>([]);
  const [activeConds, setActiveConds] = useState<MedicalCondition[]>([]);
  const [activeMeds, setActiveMeds] = useState<Medication[]>([]);
  const [weightAnalysis, setWeightAnalysis] = useState<WeightAnalysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisErr, setAnalysisErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      // Admin only loads general fighter info — no sessions, no medical data
      const isAdminRole = user?.role === "admin";
      const [f, sList, wList, opts] = await Promise.all([
        api.getFighter(id),
        isAdminRole ? Promise.resolve([]) : api.listSessions(id, { recorded: true }),
        isAdminRole ? Promise.resolve([]) : api.listWeighIns(id),
        api.fighterOptions().catch(() => null),
      ]);
      setFighter(f);
      setOptions(opts);
      setWeighIns(wList);
      if (!isAdminRole) {
      const withStats = await Promise.all(
        sList
          .slice()
          .sort((a, b) => a.started_at.localeCompare(b.started_at))
          .map(async (s) => {
            const evs = await api.listEvents(s.id).catch(() => [] as PunchEvent[]);
            const vels = evs.map((e) => e.velocity_ms);
            return {
              session: s,
              events: evs,
              totalPunches: evs.length,
              peakVelocity: vels.length ? Math.max(...vels) : 0,
              meanVelocity: vels.length ? vels.reduce((s, v) => s + v, 0) / vels.length : 0,
            };
          }),
      );
      setSessions(withStats);
      }
      // Medical alerts for the dashboard banner (HIPAA: skip for gym managers and admins)
      if (user?.role !== "gym_manager" && user?.role !== "admin") {
        const [allergies, meds, conds] = await Promise.all([
          api.listAllergies(id).catch(() => [] as Allergy[]),
          api.listMedications(id).catch(() => [] as Medication[]),
          api.listConditions(id).catch(() => [] as MedicalCondition[]),
        ]);
        setSevereAllergies(
          allergies.filter(
            (a) => a.severity === "severe" || a.severity === "anaphylactic",
          ),
        );
        setActiveConds(conds.filter((c) => c.status === "active"));
        setActiveMeds(meds.filter((m) => m.is_active));
      }
    } catch (e) {
      setErr(String(e));
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const aggregate = useMemo(() => {
    const completed = sessions.filter((x) => x.session.status === "completed");
    const all = completed.flatMap((x) => x.events);
    const allVels = all.map((e) => e.velocity_ms);
    const totalDurationMs = completed.reduce((s, x) => s + x.session.duration_ms, 0);
    const punchesPerSession = completed.map((x) => x.totalPunches);
    const peakPerSession = completed.map((x) => x.peakVelocity);
    return {
      totalSessions: sessions.length,
      completedSessions: completed.length,
      totalPunches: all.length,
      totalDurationS: totalDurationMs / 1000,
      peakVelocity: allVels.length ? Math.max(...allVels) : 0,
      meanVelocity: allVels.length ? allVels.reduce((s, v) => s + v, 0) / allVels.length : 0,
      punchesPerSession,
      peakPerSession,
      bestByPunches: completed
        .slice()
        .sort((a, b) => b.totalPunches - a.totalPunches)
        .slice(0, 3),
      bestByPeak: completed
        .slice()
        .filter((x) => x.peakVelocity > 0)
        .sort((a, b) => b.peakVelocity - a.peakVelocity)
        .slice(0, 3),
    };
  }, [sessions]);

  if (!fighter) {
    return (
      <div className="text-sm text-neutral-400">
        {err ? <span className="text-red-400">{err}</span> : "Loading…"}
      </div>
    );
  }

  const age = fighter.dob
    ? Math.floor(
        (Date.now() - new Date(fighter.dob).getTime()) / (365.25 * 24 * 60 * 60 * 1000),
      )
    : null;

  const addWeighIn = async () => {
    const w = parseFloat(newWeight);
    if (!w || w <= 0) return;
    try {
      await api.createWeighIn(id, w, newWeightNotes.trim() || undefined);
      setNewWeight("");
      setNewWeightNotes("");
      await load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const deleteWeighIn = async (weighInId: number) => {
    try {
      await api.deleteWeighIn(id, weighInId);
      await load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const deleteFighter = async () => {
    try {
      await api.deleteFighter(id);
      router.push("/");
    } catch (e) {
      setErr(String(e));
      setConfirmDeleteFighter(false);
    }
  };

  const deleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      setConfirmDeleteSession(null);
      await load();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div className="space-y-8 px-4 py-5 sm:px-8 sm:py-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-baseline gap-3">
            <h1 className="truncate text-3xl font-semibold">{fighter.name}</h1>
            {fighter.nickname && (
              <span className="text-lg text-neutral-400">&ldquo;{fighter.nickname}&rdquo;</span>
            )}
          </div>
          <p className="mt-1 text-sm text-neutral-400">
            {fighter.stance}
            {fighter.skill_level ? ` · ${labelForSkill(fighter.skill_level)}` : ""}
            {fighter.weight_class ? ` · ${labelForWeightClass(fighter.weight_class)}` : ""}
            {fighter.nationality ? ` · ${fighter.nationality}` : ""}
            {age != null ? ` · ${age} yrs` : ""}
          </p>
          <p className="mt-1 text-xs text-neutral-600">
            System ID: <code className="rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-neutral-500 select-all">{fighter.id}</code>
          </p>
          {canCreateSession && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={async (e) => {
                  const btn = e.currentTarget;
                  btn.disabled = true;
                  try {
                    const s = await api.createSession(fighter.id, "live_webcam", "mediapipe");
                    router.push(`/sessions/${s.id}`);
                  } catch { btn.disabled = false; }
                }}
                className="rounded-xl bg-emerald-500 px-3 py-1.5 text-sm font-medium text-black hover:bg-emerald-400 disabled:opacity-50"
              >
                Training session
              </button>
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {!isAdmin && <PolarH10Card />}
          {canEdit && (
            <>
              <button
                onClick={() => setEditing(true)}
                className="rounded-xl border border-white/5 bg-white/[0.03] px-3 py-1.5 text-sm hover:bg-white/[0.07]"
              >
                Edit profile
              </button>
              <button
                onClick={() => setConfirmDeleteFighter(true)}
                className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/20"
              >
                Delete profile
              </button>
            </>
          )}
        </div>
      </header>

      {err && (
        <p className="rounded-2xl border border-red-500/30 bg-red-950/30 p-3 text-sm text-red-200">
          {err}
        </p>
      )}

      {/* Medical alert banner — surfaces ringside-critical info (HIPAA: hidden from gym managers & admins) */}
      {user?.role !== "gym_manager" && user?.role !== "admin" &&
       (severeAllergies.length > 0 ||
        activeConds.length > 0 ||
        activeMeds.length > 0) && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-red-200">
              Medical Alert
            </h2>
            <Link
              href={`/fighters/${id}/medical`}
              className="text-[10px] uppercase tracking-wider text-red-300/60 hover:text-red-200"
            >
              full medical record →
            </Link>
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {severeAllergies.map((a) => (
              <span
                key={a.id}
                className={`rounded-full px-2 py-0.5 font-medium ${
                  a.severity === "anaphylactic"
                    ? "bg-red-600/30 text-red-200"
                    : "bg-red-500/15 text-red-300"
                }`}
              >
                {a.severity}: {a.substance}
              </span>
            ))}
            {activeConds.map((c) => (
              <span
                key={c.id}
                className="rounded-full bg-amber-500/15 px-2 py-0.5 font-medium text-amber-300"
              >
                {c.name}
              </span>
            ))}
            {activeMeds.map((m) => (
              <span
                key={m.id}
                className="rounded-full bg-blue-500/15 px-2 py-0.5 font-medium text-blue-300"
              >
                {m.name} {m.dose ?? ""}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Session/performance data hidden from admin — admin manages accounts, not training data */}
      {!isAdmin && <FighterDashboard
        fighterId={fighter.id}
        sessionsWithEvents={sessions.map((s) => ({
          session: s.session,
          events: s.events,
        }))}
      />}

      {/* RECORD */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="md:col-span-2" />

        <aside className="rounded-2xl border border-white/5 bg-[#13131a] p-4">
          <h2 className="text-sm font-medium text-neutral-300">Record</h2>
          <p className="mt-2 text-2xl font-semibold">
            {fighter.record_wins}
            <span className="text-neutral-500"> · </span>
            {fighter.record_losses}
            <span className="text-neutral-500"> · </span>
            {fighter.record_draws}
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            wins · losses · draws · {fighter.record_kos} KO
            {fighter.record_kos === 1 ? "" : "s"}
          </p>
          {fighter.years_training != null && (
            <p className="mt-3 text-xs text-neutral-400">
              {fighter.years_training} yr{fighter.years_training === 1 ? "" : "s"} training
            </p>
          )}
        </aside>
      </section>

      {/* PHYSICAL + PROFESSIONAL */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-neutral-800 p-4">
          <h2 className="font-medium">Physical</h2>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
            <Field label="Height" value={fighter.height_cm ? `${fighter.height_cm} cm` : null} />
            <Field label="Reach" value={fighter.reach_cm ? `${fighter.reach_cm} cm` : null} />
            <Field
              label="Weight"
              value={fighter.weight_kg ? `${fighter.weight_kg.toFixed(1)} kg` : null}
            />
            <Field
              label="Shoulder width"
              value={fighter.shoulder_width_cm ? `${fighter.shoulder_width_cm} cm` : null}
            />
            <Field
              label="Dominant hand"
              value={fighter.dominant_hand}
            />
            <Field label="Sex" value={fighter.sex} />
          </dl>
        </div>

        <div className="rounded-lg border border-neutral-800 p-4">
          <h2 className="font-medium">Professional</h2>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
            <Field
              label="Skill level"
              value={fighter.skill_level ? labelForSkill(fighter.skill_level) : null}
            />
            <Field
              label="Weight class"
              value={
                fighter.weight_class ? labelForWeightClass(fighter.weight_class) : null
              }
            />
            <Field label="Gym" value={fighter.gym} />
            <Field label="Trainer" value={fighter.trainer} />
            <Field label="BoxRec ID" value={fighter.boxrec_id} mono />
            <Field label="USA Boxing ID" value={fighter.usa_boxing_id} mono />
          </dl>
          {fighter.notes && (
            <p className="mt-3 whitespace-pre-wrap rounded bg-neutral-900/50 p-3 text-xs text-neutral-300">
              {fighter.notes}
            </p>
          )}
        </div>
      </section>

      {/* WEIGHT TRACKER — hidden from admin */}
      {!isAdmin && <section className="rounded-lg border border-neutral-800 p-4">
        <div className="flex items-baseline justify-between">
          <h2 className="font-medium">Weight tracker</h2>
          <span className="text-xs text-neutral-500">
            {weighIns.length} weigh-in{weighIns.length === 1 ? "" : "s"}
          </span>
        </div>

        <div className="mt-3">
          <Sparkline
            values={weighIns.map((w) => w.weight_kg)}
            ariaLabel="Weight over time"
            color="#fbbf24"
          />
        </div>

        <div className="mt-4 flex flex-wrap gap-2 border-t border-neutral-800 pt-4">
          <input
            type="number"
            step="0.1"
            placeholder="Weight (kg)"
            value={newWeight}
            onChange={(e) => setNewWeight(e.target.value)}
            className="w-32 rounded bg-neutral-900 p-2 text-sm"
          />
          <input
            placeholder="Notes (optional)"
            value={newWeightNotes}
            onChange={(e) => setNewWeightNotes(e.target.value)}
            className="flex-1 rounded bg-neutral-900 p-2 text-sm"
          />
          <button
            onClick={addWeighIn}
            disabled={!newWeight}
            className="rounded bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
          >
            Log weigh-in
          </button>
        </div>

        {weighIns.length > 0 && (
          <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto text-sm">
            {weighIns
              .slice()
              .reverse()
              .map((w) => (
                <li
                  key={w.id}
                  className="flex items-baseline gap-3 border-t border-neutral-900 py-1.5 first:border-t-0"
                >
                  <span className="font-mono">{w.weight_kg.toFixed(1)} kg</span>
                  <span className="text-xs text-neutral-500">
                    {new Date(w.recorded_at).toLocaleString()}
                  </span>
                  {w.notes && (
                    <span className="flex-1 text-xs text-neutral-400">— {w.notes}</span>
                  )}
                  <button
                    onClick={() => deleteWeighIn(w.id)}
                    className="text-xs text-red-400 hover:text-red-300"
                  >
                    delete
                  </button>
                </li>
              ))}
          </ul>
        )}

        {/* Weight Analysis */}
        <div className="mt-4 border-t border-neutral-800 pt-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-neutral-300">AI Weight Analysis</h3>
            <button
              onClick={async () => {
                setAnalysisLoading(true);
                setAnalysisErr(null);
                try {
                  const result = await api.weightAnalysis(id);
                  setWeightAnalysis(result);
                } catch (e) {
                  setAnalysisErr(e instanceof Error ? e.message : "Analysis failed");
                } finally {
                  setAnalysisLoading(false);
                }
              }}
              disabled={analysisLoading || weighIns.length < 2}
              className="rounded-lg bg-amber-500/15 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-500/25 disabled:opacity-40"
            >
              {analysisLoading ? "Analysing..." : weighIns.length < 2 ? "Need 2+ weigh-ins" : "Analyse weight"}
            </button>
          </div>

          {analysisErr && (
            <p className="mt-2 rounded-lg border border-red-500/30 bg-red-950/30 px-3 py-2 text-xs text-red-300">
              {analysisErr}
            </p>
          )}

          {weightAnalysis && (
            <div className="mt-3 space-y-3">
              {/* Stats grid */}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <MiniStat label="Current" value={weightAnalysis.current_kg != null ? `${weightAnalysis.current_kg.toFixed(1)} kg` : "—"} />
                <MiniStat label="Range" value={weightAnalysis.range_kg != null ? `${weightAnalysis.range_kg.toFixed(1)} kg` : "—"} />
                <MiniStat label="Trend" value={
                  weightAnalysis.trend_direction === "gaining" ? `↑ ${Math.abs(weightAnalysis.trend_kg_per_week ?? 0).toFixed(2)} kg/wk` :
                  weightAnalysis.trend_direction === "losing" ? `↓ ${Math.abs(weightAnalysis.trend_kg_per_week ?? 0).toFixed(2)} kg/wk` :
                  "Stable"
                } />
                <MiniStat label="Variability" value={weightAnalysis.cv_pct != null ? `${weightAnalysis.cv_pct.toFixed(1)}%` : "—"} />
              </div>

              {/* Instability warning */}
              {weightAnalysis.instability_flag && (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
                  <p className="text-xs font-medium text-amber-300">⚠ Weight instability detected</p>
                  <p className="mt-0.5 text-[11px] text-amber-400/70">
                    Significant fluctuations found in your weigh-in history. See AI recommendations below.
                  </p>
                </div>
              )}

              {/* AI Summary */}
              <div className="rounded-lg border border-white/5 bg-white/[0.02] px-4 py-3">
                <p className="text-xs font-medium text-neutral-300">Summary</p>
                <p className="mt-1 text-sm text-neutral-400">{weightAnalysis.ai_summary}</p>
              </div>

              {/* AI Recommendations */}
              {weightAnalysis.ai_recommendations.length > 0 && (
                <div className="rounded-lg border border-white/5 bg-white/[0.02] px-4 py-3">
                  <p className="text-xs font-medium text-neutral-300">Recommendations</p>
                  <ul className="mt-2 space-y-1.5">
                    {weightAnalysis.ai_recommendations.map((rec, i) => (
                      <li key={i} className="flex gap-2 text-sm text-neutral-400">
                        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-500/20 text-[10px] font-bold text-amber-300">
                          {i + 1}
                        </span>
                        {rec}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </section>}

      {/* PERFORMANCE — hidden from admin */}
      {!isAdmin && <>
      <section className="rounded-lg border border-neutral-800 p-4">
        <h2 className="font-medium">Performance — career totals</h2>
        <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3 md:grid-cols-6">
          <Cell label="Sessions" value={aggregate.totalSessions} />
          <Cell label="Completed" value={aggregate.completedSessions} />
          <Cell label="Total punches" value={aggregate.totalPunches} />
          <Cell
            label="Total duration"
            value={`${(aggregate.totalDurationS / 60).toFixed(1)}m`}
          />
          <Cell label="Peak velocity" value={`${aggregate.peakVelocity.toFixed(2)} m/s`} />
          <Cell label="Mean velocity" value={`${aggregate.meanVelocity.toFixed(2)} m/s`} />
        </dl>
      </section>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-neutral-800 p-4">
          <h2 className="font-medium">Punches per session</h2>
          <p className="mt-1 text-xs text-neutral-500">
            Across {aggregate.completedSessions} completed session
            {aggregate.completedSessions === 1 ? "" : "s"}, oldest → newest.
          </p>
          <div className="mt-3">
            <Sparkline values={aggregate.punchesPerSession} color="#34d399" />
          </div>
        </div>
        <div className="rounded-lg border border-neutral-800 p-4">
          <h2 className="font-medium">Peak velocity per session</h2>
          <p className="mt-1 text-xs text-neutral-500">m/s, oldest → newest.</p>
          <div className="mt-3">
            <Sparkline values={aggregate.peakPerSession} color="#60a5fa" />
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <BestList
          title="Most punches"
          rows={aggregate.bestByPunches}
          metric={(s) => `${s.totalPunches}`}
          metricLabel="punches"
        />
        <BestList
          title="Hardest punch (peak velocity)"
          rows={aggregate.bestByPeak}
          metric={(s) => `${s.peakVelocity.toFixed(2)} m/s`}
          metricLabel="peak"
        />
      </section>
      </>}

      {editing && (
        <EditModal
          fighter={fighter}
          options={options}
          onCancel={() => setEditing(false)}
          onSave={async (patch) => {
            try {
              await api.updateFighter(id, patch);
              setEditing(false);
              await load();
            } catch (e) {
              setErr(String(e));
            }
          }}
        />
      )}

      {confirmDeleteFighter && (
        <ConfirmModal
          title="Delete this fighter?"
          body={`This also deletes all of ${fighter.name}'s sessions, captured pose data, uploaded videos, weigh-ins, and detected events. Cannot be undone.`}
          requireTyped={fighter.name}
          onCancel={() => setConfirmDeleteFighter(false)}
          onConfirm={deleteFighter}
        />
      )}
      {confirmDeleteSession && (
        <ConfirmModal
          title="Delete this session?"
          body="Removes the session row, detected punch events, captured pose data, and any uploaded video. Cannot be undone."
          onCancel={() => setConfirmDeleteSession(null)}
          onConfirm={() => deleteSession(confirmDeleteSession)}
        />
      )}
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | number | null | undefined;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd
        className={`mt-0.5 ${mono ? "font-mono text-xs" : "text-sm"} ${
          value ? "text-neutral-100" : "text-neutral-600"
        }`}
      >
        {value ?? "—"}
      </dd>
    </div>
  );
}

function Cell({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold">{value}</dd>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-3 py-2 text-center">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-neutral-200">{value}</p>
    </div>
  );
}

function BestList({
  title,
  rows,
  metric,
  metricLabel,
}: {
  title: string;
  rows: SessionWithStats[];
  metric: (s: SessionWithStats) => string;
  metricLabel: string;
}) {
  return (
    <div className="rounded-lg border border-neutral-800 p-4">
      <h2 className="font-medium">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-neutral-500">Not enough data yet.</p>
      ) : (
        <ol className="mt-3 space-y-1 text-sm">
          {rows.map((x, i) => (
            <li key={x.session.id} className="flex items-center gap-3">
              <span className="font-mono text-xs text-neutral-500">{i + 1}.</span>
              <Link
                href={`/sessions/${x.session.id}`}
                className="flex-1 hover:underline"
              >
                <span className="font-mono text-xs text-neutral-500">
                  {x.session.id.slice(0, 8)}
                </span>{" "}
                · {new Date(x.session.started_at).toLocaleDateString()}
              </Link>
              <span className="font-mono">
                {metric(x)}{" "}
                <span className="text-xs text-neutral-500">{metricLabel}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ConfirmModal({
  title,
  body,
  requireTyped,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: string;
  requireTyped?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const enabled = !requireTyped || typed === requireTyped;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-md rounded-2xl border border-white/5 bg-[#13131a] p-5 shadow-xl">
        <h3 className="font-medium">{title}</h3>
        <p className="mt-2 text-sm text-neutral-400">{body}</p>
        {requireTyped && (
          <div className="mt-3">
            <label className="text-xs text-neutral-400">
              Type <span className="font-mono text-red-300">{requireTyped}</span> to
              confirm
            </label>
            <input
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && enabled) onConfirm();
              }}
              className="mt-1 w-full rounded-xl border border-white/5 bg-black/30 px-3 py-2 text-sm focus:border-red-500/50 focus:outline-none"
              placeholder={requireTyped}
            />
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-xl bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={!enabled}
            className="rounded-xl bg-red-600 px-3 py-1.5 text-sm font-medium hover:bg-red-500 disabled:cursor-not-allowed disabled:bg-red-900/40 disabled:text-red-300/50"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

function EditModal({
  fighter,
  options,
  onCancel,
  onSave,
}: {
  fighter: Fighter;
  options: FighterOptions | null;
  onCancel: () => void;
  onSave: (patch: FighterPatch) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Fighter>({ ...fighter });
  const [saving, setSaving] = useState(false);
  const [photoPath, setPhotoPath] = useState<string | null>(fighter.photo_path);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoErr, setPhotoErr] = useState<string | null>(null);
  const set = <K extends keyof Fighter>(k: K, v: Fighter[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const submit = async () => {
    setSaving(true);
    const patch: FighterPatch = {};
    (Object.keys(draft) as (keyof Fighter)[]).forEach((k) => {
      if (k === "id" || k === "created_at") return;
      if (draft[k] !== fighter[k]) (patch as any)[k] = draft[k];
    });
    await onSave(patch);
    setSaving(false);
  };

  const onPickPhoto = async (file: File) => {
    setPhotoBusy(true);
    setPhotoErr(null);
    try {
      const updated = await api.uploadFighterPhoto(fighter.id, file);
      setPhotoPath(updated.photo_path);
    } catch (e) {
      setPhotoErr(String(e));
    } finally {
      setPhotoBusy(false);
    }
  };

  const num = (v: string) => (v === "" ? null : Number(v));

  return (
    <div className="fixed inset-0 z-50 flex justify-center overflow-y-auto bg-black/70 p-4">
      <div className="my-8 h-fit w-full max-w-2xl rounded-lg border border-neutral-800 bg-neutral-950 p-5 shadow-xl">
        <h3 className="text-lg font-medium">Edit profile</h3>

        <div className="mt-4 space-y-5 text-sm">
          <Group title="Photo">
            <div className="flex items-center gap-4">
              <ProfileAvatar
                name={draft.name || fighter.name}
                photo_path={photoPath}
                size={80}
              />
              <div className="flex-1">
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  disabled={photoBusy}
                  onChange={(e) =>
                    e.target.files?.[0] && onPickPhoto(e.target.files[0])
                  }
                  className="text-xs"
                />
                <p className="mt-1 text-[11px] text-neutral-500">
                  {photoBusy
                    ? "Uploading…"
                    : "JPG / PNG / WebP, max 5 MB. Saves immediately."}
                </p>
                {photoErr && (
                  <p className="mt-1 text-xs text-red-300">{photoErr}</p>
                )}
              </div>
            </div>
          </Group>

          <Group title="Identity">
            <Row>
              <Lbl>Name</Lbl>
              <input
                className="flex-1 rounded bg-neutral-900 p-2"
                value={draft.name}
                onChange={(e) => set("name", e.target.value)}
              />
            </Row>
            <Row>
              <Lbl>Nickname</Lbl>
              <input
                className="flex-1 rounded bg-neutral-900 p-2"
                value={draft.nickname ?? ""}
                onChange={(e) => set("nickname", e.target.value || null)}
              />
            </Row>
            <Row>
              <Lbl>DOB</Lbl>
              <input
                type="date"
                className="rounded bg-neutral-900 p-2"
                value={draft.dob ?? ""}
                onChange={(e) => set("dob", e.target.value || null)}
              />
              <Lbl>Sex</Lbl>
              <select
                className="rounded bg-neutral-900 p-2"
                value={draft.sex ?? ""}
                onChange={(e) => set("sex", e.target.value || null)}
              >
                <option value="">—</option>
                {(options?.sexes ?? ["male", "female", "other"]).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Row>
            <Row>
              <Lbl>Nationality</Lbl>
              <input
                className="flex-1 rounded bg-neutral-900 p-2"
                value={draft.nationality ?? ""}
                onChange={(e) => set("nationality", e.target.value || null)}
                placeholder="e.g. KSA"
              />
            </Row>
          </Group>

          <Group title="Boxing">
            <Row>
              <Lbl>Stance</Lbl>
              <select
                className="rounded bg-neutral-900 p-2"
                value={draft.stance}
                onChange={(e) => set("stance", e.target.value as Stance)}
              >
                {(options?.stances ?? ["orthodox", "southpaw", "switch"]).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <Lbl>Dominant hand</Lbl>
              <select
                className="rounded bg-neutral-900 p-2"
                value={draft.dominant_hand ?? ""}
                onChange={(e) =>
                  set("dominant_hand", (e.target.value || null) as Hand | null)
                }
              >
                <option value="">—</option>
                {(options?.hands ?? ["left", "right"]).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Row>
            <Row>
              <Lbl>Skill level</Lbl>
              <select
                className="flex-1 rounded bg-neutral-900 p-2"
                value={draft.skill_level ?? ""}
                onChange={(e) =>
                  set(
                    "skill_level",
                    (e.target.value || null) as SkillLevel | null,
                  )
                }
              >
                <option value="">—</option>
                {(options?.skill_levels ?? PRO_LEVELS).map((s) => (
                  <option key={s} value={s}>
                    {labelForSkill(s as SkillLevel)}
                  </option>
                ))}
              </select>
            </Row>
            <Row>
              <Lbl>Weight class</Lbl>
              <select
                className="flex-1 rounded bg-neutral-900 p-2"
                value={draft.weight_class ?? ""}
                onChange={(e) => set("weight_class", e.target.value || null)}
              >
                <option value="">—</option>
                {(options?.weight_classes ?? []).map((s) => (
                  <option key={s} value={s}>
                    {labelForWeightClass(s)}
                  </option>
                ))}
              </select>
            </Row>
            <Row>
              <Lbl>Years training</Lbl>
              <input
                type="number"
                min={0}
                max={80}
                className="w-24 rounded bg-neutral-900 p-2"
                value={draft.years_training ?? ""}
                onChange={(e) => set("years_training", num(e.target.value))}
              />
              <Lbl>Gym</Lbl>
              <input
                className="flex-1 rounded bg-neutral-900 p-2"
                value={draft.gym ?? ""}
                onChange={(e) => set("gym", e.target.value || null)}
              />
            </Row>
            <Row>
              <Lbl>Trainer</Lbl>
              <input
                className="flex-1 rounded bg-neutral-900 p-2"
                value={draft.trainer ?? ""}
                onChange={(e) => set("trainer", e.target.value || null)}
              />
            </Row>
          </Group>

          <Group title="Physical">
            <Row>
              <Lbl>Height (cm)</Lbl>
              <input
                type="number"
                step="0.5"
                className="w-24 rounded bg-neutral-900 p-2"
                value={draft.height_cm ?? ""}
                onChange={(e) => set("height_cm", num(e.target.value))}
              />
              <Lbl>Reach (cm)</Lbl>
              <input
                type="number"
                step="0.5"
                className="w-24 rounded bg-neutral-900 p-2"
                value={draft.reach_cm ?? ""}
                onChange={(e) => set("reach_cm", num(e.target.value))}
              />
              <Lbl>Shoulder (cm)</Lbl>
              <input
                type="number"
                step="0.5"
                className="w-24 rounded bg-neutral-900 p-2"
                value={draft.shoulder_width_cm ?? ""}
                onChange={(e) =>
                  set("shoulder_width_cm", num(e.target.value))
                }
              />
            </Row>
          </Group>

          <Group title="Record">
            <Row>
              <Lbl>Wins</Lbl>
              <input
                type="number"
                min={0}
                className="w-20 rounded bg-neutral-900 p-2"
                value={draft.record_wins}
                onChange={(e) => set("record_wins", Number(e.target.value || 0))}
              />
              <Lbl>Losses</Lbl>
              <input
                type="number"
                min={0}
                className="w-20 rounded bg-neutral-900 p-2"
                value={draft.record_losses}
                onChange={(e) => set("record_losses", Number(e.target.value || 0))}
              />
              <Lbl>Draws</Lbl>
              <input
                type="number"
                min={0}
                className="w-20 rounded bg-neutral-900 p-2"
                value={draft.record_draws}
                onChange={(e) => set("record_draws", Number(e.target.value || 0))}
              />
              <Lbl>KOs</Lbl>
              <input
                type="number"
                min={0}
                className="w-20 rounded bg-neutral-900 p-2"
                value={draft.record_kos}
                onChange={(e) => set("record_kos", Number(e.target.value || 0))}
              />
            </Row>
          </Group>

          <Group title="External IDs">
            <Row>
              <Lbl>BoxRec ID</Lbl>
              <input
                className="flex-1 rounded bg-neutral-900 p-2 font-mono"
                value={draft.boxrec_id ?? ""}
                onChange={(e) => set("boxrec_id", e.target.value || null)}
              />
            </Row>
            <Row>
              <Lbl>USA Boxing ID</Lbl>
              <input
                className="flex-1 rounded bg-neutral-900 p-2 font-mono"
                value={draft.usa_boxing_id ?? ""}
                onChange={(e) => set("usa_boxing_id", e.target.value || null)}
              />
            </Row>
          </Group>

          <Group title="Notes">
            <textarea
              className="w-full rounded bg-neutral-900 p-2"
              rows={3}
              value={draft.notes ?? ""}
              onChange={(e) => set("notes", e.target.value || null)}
            />
          </Group>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded bg-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-600"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={saving}
            className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="rounded border border-neutral-800 p-3">
      <legend className="px-1 text-xs uppercase tracking-wider text-neutral-500">
        {title}
      </legend>
      <div className="mt-2 space-y-2">{children}</div>
    </fieldset>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2">{children}</div>;
}

function Lbl({ children }: { children: React.ReactNode }) {
  return <label className="text-xs text-neutral-400">{children}</label>;
}

function labelForSkill(s: SkillLevel): string {
  return s.replace(/_/g, " ");
}

function labelForWeightClass(s: string): string {
  return s.replace(/_/g, " ");
}
