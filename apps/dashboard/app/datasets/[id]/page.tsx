"use client";

/**
 * One dataset (ADR-013): its participants with their consent, and every recorded
 * take with what it captured. "Record take" is only offered when consent allows
 * it — the researcher recording himself (`self`) or a signed IRB consent. A take
 * is listed once its cameras have started; "Delete…" removes the takes ticked.
 *
 * With a protocol, a participant is recorded one category per take — jab, lead
 * hook, 1-2, free shadowboxing, … — picked from the category grid, each showing
 * how often it's been recorded. A take records that category only.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { TrainingCard } from "@/components/TrainingCard";
import { categoryLength } from "@/lib/categories";
import {
  api,
  type ProtocolBlockSpec,
  type Consent,
  type DatasetDetail,
  type DatasetExport,
  type DatasetParticipant,
  type Fighter,
  type Take,
} from "@/lib/api";

const CONSENT: Record<Consent, { label: string; cls: string }> = {
  self: { label: "Self (researcher)", cls: "bg-sky-900/60 text-sky-200" },
  irb_signed: { label: "IRB signed", cls: "bg-emerald-900/60 text-emerald-200" },
  pending: { label: "Pending", cls: "bg-amber-900/60 text-amber-200" },
  withdrawn: { label: "Withdrawn", cls: "bg-red-900/60 text-red-200" },
};

function errText(e: unknown): string {
  const m = String(e instanceof Error ? e.message : e).match(/^\d{3} (.*)$/s);
  if (m) {
    try {
      const d = JSON.parse(m[1])?.detail;
      if (typeof d === "string") return d;
    } catch {
      /* fall through */
    }
  }
  return String(e);
}

function duration(ms: number): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function DatasetPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const [ds, setDs] = useState<DatasetDetail | null>(null);
  const [fighters, setFighters] = useState<Fighter[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [plan, setPlan] = useState<ProtocolBlockSpec[]>([]);
  // Delete: null = off; a set = picking takes (empty to start).
  const [picked, setPicked] = useState<Set<string> | null>(null);

  const load = () =>
    api
      .getDataset(params.id)
      .then(setDs)
      .catch((e) => setErr(errText(e)));

  useEffect(() => {
    load();
    api.listFighters().then(setFighters).catch(() => {});
    api.protocolPlan().then(setPlan).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.id]);

  /** Open a take — with a protocol, for one category. */
  const record = async (fighterId: string, block?: string) => {
    setBusy(fighterId);
    setErr(null);
    try {
      const take = await api.createTake(params.id, fighterId, block);
      router.push(`/datasets/${params.id}/takes/${take.id}`);
    } catch (e) {
      setErr(errText(e));
      setBusy(null);
    }
  };

  const setConsent = async (p: DatasetParticipant, consent: Consent) => {
    setErr(null);
    try {
      setDs(
        await api.setParticipant(params.id, {
          fighter_id: p.fighter_id,
          consent,
          consent_date: p.consent_date,
          irb_ref: p.irb_ref,
          notes: p.notes,
        }),
      );
    } catch (e) {
      setErr(errText(e));
    }
  };

  if (!ds) {
    return (
      <div className="px-4 py-5 sm:px-8 sm:py-6 text-sm text-neutral-400">
        {err ?? "Loading…"}
      </div>
    );
  }

  const takes = ds.takes;
  const kept = takes.filter((t) => t.status !== "discarded");
  // A protocol dataset is recorded one block per take.
  const blocked = !!ds.protocol && plan.length > 0;

  return (
    <div className="space-y-6 px-4 py-5 sm:px-8 sm:py-6">
      <Link href="/datasets" className="text-sm text-neutral-400 hover:text-neutral-200">
        ← Datasets
      </Link>
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">{ds.name}</h1>
          {ds.protocol && <span className="pill bg-violet-900/50 text-violet-200">{ds.protocol}</span>}
        </div>
        {ds.description && <p className="mt-1 text-sm text-neutral-400">{ds.description}</p>}
      </header>

      {err && (
        <p className="rounded-xl border border-red-500/30 bg-red-950/30 p-3 text-sm text-red-200">
          {err}
        </p>
      )}

      {/* Participants */}
      <section className="card space-y-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-base font-semibold">Participants</h2>
          <span className="text-xs text-neutral-500">
            Record only with self or IRB consent (ADR-002)
          </span>
        </div>
        {ds.participants.length === 0 ? (
          <p className="text-sm text-neutral-500">
            No participants yet. Add yourself as the self-recording researcher to start.
          </p>
        ) : (
          <div className="divide-y divide-white/5">
            {ds.participants.map((p) => (
              <div key={p.fighter_id} className="flex flex-wrap items-center gap-3 py-2.5">
                <div className="min-w-[10rem] flex-1">
                  <div className="font-medium">{p.name}</div>
                  <div className="text-xs text-neutral-500">
                    {p.stance ?? "stance unknown"} · {p.takes} take{p.takes === 1 ? "" : "s"}
                    {p.irb_ref && <> · IRB {p.irb_ref}</>}
                  </div>
                </div>
                <select
                  value={p.consent}
                  onChange={(e) => setConsent(p, e.target.value as Consent)}
                  className={`rounded-full border-0 px-3 py-1 text-xs font-medium ${CONSENT[p.consent].cls}`}
                  aria-label={`Consent for ${p.name}`}
                >
                  {(Object.keys(CONSENT) as Consent[]).map((c) => (
                    <option key={c} value={c}>
                      {CONSENT[c].label}
                    </option>
                  ))}
                </select>
                {!blocked && (
                  <button
                    onClick={() => record(p.fighter_id)}
                    disabled={!p.may_record || busy !== null}
                    title={p.may_record ? undefined : "Needs self or IRB-signed consent"}
                    className="rounded-xl bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
                  >
                    {busy === p.fighter_id ? "Opening…" : "● Record take"}
                  </button>
                )}
                {blocked && (
                  <div className="grid w-full gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {plan.map((b) => {
                      const n = p.blocks?.[b.key] ?? 0;
                      return (
                        <button
                          key={b.key}
                          onClick={() => record(p.fighter_id, b.key)}
                          disabled={!p.may_record || busy !== null}
                          title={p.may_record ? b.hint || undefined : "Needs self or IRB-signed consent"}
                          className="group flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-left hover:border-red-500/50 hover:bg-red-950/20 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <span className="min-w-0">
                            <span className="block text-sm font-medium text-neutral-100">{b.title}</span>
                            <span className="block text-[11px] text-neutral-500">{categoryLength(b)}</span>
                          </span>
                          <span className="flex shrink-0 items-center gap-2">
                            <span
                              className={`text-[11px] tabular-nums ${n ? "text-emerald-300" : "text-neutral-600"}`}
                            >
                              {n ? `✓ ${n}` : "none yet"}
                            </span>
                            <span className="rounded-lg bg-red-600 px-2 py-1 text-xs font-semibold text-white group-hover:bg-red-500">
                              {busy === p.fighter_id ? "…" : "● Record"}
                            </span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <AddParticipant
          fighters={fighters.filter((f) => !ds.participants.some((p) => p.fighter_id === f.id))}
          onAdd={async (fighterId, consent, irbRef) => {
            setErr(null);
            try {
              setDs(
                await api.setParticipant(params.id, {
                  fighter_id: fighterId,
                  consent,
                  irb_ref: irbRef || null,
                  consent_date: consent === "irb_signed" ? new Date().toISOString().slice(0, 10) : null,
                }),
              );
            } catch (e) {
              setErr(errText(e));
            }
          }}
        />
      </section>

      {/* Takes */}
      <section className="card space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex items-baseline gap-3">
            <h2 className="text-base font-semibold">Takes</h2>
            <span className="text-xs text-neutral-500">
              {kept.filter((t) => t.status === "completed").length} completed
            </span>
          </div>
          <div className="flex flex-wrap items-start gap-2">
            <ExportButton
              datasetId={ds.id}
              disabled={!kept.some((t) => t.status === "completed")}
            />
            <DeleteTakes
              takes={takes}
              picked={picked}
              setPicked={setPicked}
              onDeleted={async (failed) => {
                setErr(failed.length ? `Couldn't delete: ${failed.join("; ")}` : null);
                await load();
              }}
            />
          </div>
        </div>
        {takes.length === 0 ? (
          <p className="text-sm text-neutral-500">No takes recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-left text-xs text-neutral-500">
                <tr>
                  {picked && (
                    <th className="w-8 py-2 font-normal">
                      <input
                        type="checkbox"
                        aria-label="Select all takes"
                        checked={takes.length > 0 && picked.size === takes.length}
                        onChange={(e) =>
                          setPicked(new Set(e.target.checked ? takes.map((t) => t.id) : []))
                        }
                        className="h-4 w-4 accent-red-500"
                      />
                    </th>
                  )}
                  <th className="py-2 font-normal">Recorded</th>
                  <th className="font-normal">Fighter</th>
                  <th className="font-normal">Category</th>
                  <th className="font-normal">Status</th>
                  <th className="font-normal">Length</th>
                  <th className="font-normal">Video</th>
                  <th className="font-normal">Wrist IMU</th>
                  <th className="font-normal">Heart</th>
                  <th className="font-normal">Labels</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {takes.map((t) => (
                  <TakeRow
                    key={t.id}
                    take={t}
                    datasetId={ds.id}
                    category={
                      t.block
                        ? (plan.find((b) => b.key === t.block)?.title ?? t.block.replace(/_/g, " "))
                        : ds.protocol
                          ? "Full protocol"
                          : "—"
                    }
                    picked={picked ? picked.has(t.id) : null}
                    onPick={(on) =>
                      setPicked((prev) => {
                        const next = new Set(prev ?? []);
                        if (on) next.add(t.id);
                        else next.delete(t.id);
                        return next;
                      })
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Train the camera punch counter on the takes, and test it. */}
      {blocked && (
        <TrainingCard
          datasetId={ds.id}
          plan={plan}
          canTrain={kept.some((t) => t.status === "completed")}
        />
      )}
    </div>
  );
}

function TakeRow({
  take,
  datasetId,
  category,
  picked,
  onPick,
}: {
  take: Take;
  datasetId: string;
  category: string; // the take's recording category (Jab, Rear uppercut, …)
  picked: boolean | null; // null: not picking takes to delete
  onPick: (on: boolean) => void;
}) {
  const clips = take.data.clips ?? [];
  const dim = take.status === "discarded" ? "opacity-50" : "";
  const status = {
    recording: "bg-amber-900/60 text-amber-200",
    uploading: "bg-sky-900/60 text-sky-200",
    completed: "bg-emerald-900/60 text-emerald-200",
    discarded: "bg-neutral-800 text-neutral-400",
  }[take.status];
  return (
    <tr className={`${dim} ${picked ? "bg-red-950/30" : ""}`}>
      {picked !== null && (
        <td className="py-2">
          <input
            type="checkbox"
            aria-label={`Select the take from ${new Date(take.started_at).toLocaleString()}`}
            checked={picked}
            onChange={(e) => onPick(e.target.checked)}
            className="h-4 w-4 accent-red-500"
          />
        </td>
      )}
      <td className="py-2">
        <Link
          href={`/datasets/${datasetId}/takes/${take.id}`}
          className="text-sky-300 hover:text-sky-200"
        >
          {new Date(take.started_at).toLocaleString()}
        </Link>
      </td>
      <td>{take.fighter_name ?? "—"}</td>
      <td className={take.block ? "font-medium text-neutral-100" : "text-neutral-500"}>{category}</td>
      <td>
        <span className={`pill ${status}`}>{take.status}</span>
      </td>
      <td className="tabular-nums">{duration(take.duration_ms)}</td>
      <td>{clips.length ? `${clips.length} cam${clips.length > 1 ? "s" : ""}` : "—"}</td>
      <td className="tabular-nums">{take.data.imu_rows ? take.data.imu_rows.toLocaleString() : "—"}</td>
      <td className="tabular-nums">{take.data.hr_rows ? `${take.data.hr_rows} beats` : "—"}</td>
      <td className="tabular-nums">{take.data.labels != null ? take.data.labels : "—"}</td>
    </tr>
  );
}

function AddParticipant({
  fighters,
  onAdd,
}: {
  fighters: Fighter[];
  onAdd: (fighterId: string, consent: Consent, irbRef: string) => Promise<void>;
}) {
  const [fighterId, setFighterId] = useState("");
  const [consent, setConsent] = useState<Consent>("self");
  const [irbRef, setIrbRef] = useState("");
  if (fighters.length === 0) return null;
  return (
    <div className="flex flex-wrap items-end gap-2 border-t border-white/5 pt-4">
      <label className="text-sm">
        <span className="block text-xs text-neutral-500">Add fighter</span>
        <select
          value={fighterId}
          onChange={(e) => setFighterId(e.target.value)}
          className="mt-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2"
        >
          <option value="">Choose…</option>
          {fighters.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </label>
      <label className="text-sm">
        <span className="block text-xs text-neutral-500">Consent</span>
        <select
          value={consent}
          onChange={(e) => setConsent(e.target.value as Consent)}
          className="mt-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2"
        >
          {(Object.keys(CONSENT) as Consent[]).map((c) => (
            <option key={c} value={c}>
              {CONSENT[c].label}
            </option>
          ))}
        </select>
      </label>
      {consent === "irb_signed" && (
        <label className="text-sm">
          <span className="block text-xs text-neutral-500">IRB reference</span>
          <input
            value={irbRef}
            onChange={(e) => setIrbRef(e.target.value)}
            placeholder="e.g. IRB-2026-041"
            className="mt-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2"
          />
        </label>
      )}
      <button
        onClick={async () => {
          if (!fighterId) return;
          await onAdd(fighterId, consent, irbRef);
          setFighterId("");
          setIrbRef("");
        }}
        disabled={!fighterId}
        className="rounded-xl border border-white/15 px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-40"
      >
        Add
      </button>
    </div>
  );
}

/** Delete takes for good: pick them in the list, then confirm. Each one's video,
 *  sensor data and labels are removed from disk (DELETE /v2/takes/{id}). */
function DeleteTakes({
  takes,
  picked,
  setPicked,
  onDeleted,
}: {
  takes: Take[];
  picked: Set<string> | null;
  setPicked: (p: Set<string> | null) => void;
  onDeleted: (failed: string[]) => Promise<void>;
}) {
  const [confirm, setConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  if (picked === null) {
    return (
      <button
        onClick={() => setPicked(new Set())}
        disabled={takes.length === 0}
        className="rounded-xl border border-red-400/40 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/10 disabled:opacity-40"
      >
        🗑 Delete…
      </button>
    );
  }

  const n = picked.size;
  const live = takes.filter(
    (t) => picked.has(t.id) && (t.status === "recording" || t.status === "uploading"),
  ).length;
  const cancel = () => {
    setConfirm(false);
    setPicked(null);
  };
  const run = async () => {
    setDeleting(true);
    const failed: string[] = [];
    for (const t of takes.filter((t) => picked.has(t.id))) {
      try {
        await api.deleteTake(t.id);
      } catch (e) {
        failed.push(`${new Date(t.started_at).toLocaleString()} (${errText(e)})`);
      }
    }
    setDeleting(false);
    setConfirm(false);
    setPicked(null);
    await onDeleted(failed);
  };

  return (
    <div className="flex w-full flex-wrap items-center gap-2 rounded-xl border border-red-500/30 bg-red-950/20 px-3 py-2 text-sm sm:w-auto">
      {confirm ? (
        <>
          <span className="text-red-200">
            Delete {n} take{n === 1 ? "" : "s"} for good? Video, sensor data and labels are
            removed from disk.
            {live > 0 && ` ${live} still recording — its cameras stop and drop the clip.`}
          </span>
          <button
            onClick={run}
            disabled={deleting}
            className="rounded-lg bg-red-600 px-3 py-1 font-semibold text-white hover:bg-red-500 disabled:opacity-60"
          >
            {deleting ? "Deleting…" : "Delete for good"}
          </button>
          <button
            onClick={() => setConfirm(false)}
            disabled={deleting}
            className="rounded-lg border border-white/10 px-3 py-1 text-neutral-300"
          >
            Keep
          </button>
        </>
      ) : (
        <>
          <span className="text-neutral-300">
            {n ? `${n} selected` : "Tick the takes to delete"}
          </span>
          <button
            onClick={() => setConfirm(true)}
            disabled={n === 0}
            className="rounded-lg bg-red-600 px-3 py-1 font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
          >
            Delete {n || ""} take{n === 1 ? "" : "s"}
          </button>
          <button
            onClick={cancel}
            className="rounded-lg border border-white/10 px-3 py-1 text-neutral-300"
          >
            Cancel
          </button>
        </>
      )}
    </div>
  );
}

/** Writes the training manifest (data/datasets/{id}/export/manifest.json) and shows
 *  what went in: counts, the split strategy, and anything excluded or suspicious. */
function ExportButton({ datasetId, disabled }: { datasetId: string; disabled: boolean }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DatasetExport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setResult(await api.datasetExport(datasetId));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="w-full sm:w-auto">
      <button
        onClick={run}
        disabled={disabled || busy}
        title={disabled ? "Record and complete a take first" : undefined}
        className="rounded-xl border border-violet-400/40 px-3 py-1.5 text-sm text-violet-200 hover:bg-violet-500/10 disabled:opacity-40"
      >
        {busy ? "Exporting…" : "⇩ Export for training"}
      </button>
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
      {result && (
        <div className="mt-3 rounded-xl border border-white/10 bg-black/30 p-3 text-xs sm:w-96">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-neutral-300">
            <span>
              <strong className="text-neutral-100">{result.counts.takes}</strong> takes
            </span>
            <span>
              <strong className="text-neutral-100">{result.counts.fighters}</strong> fighters
            </span>
            <span>
              <strong className="text-neutral-100">{result.counts.labels}</strong> labelled punches
            </span>
            <span>
              split: <strong className="text-neutral-100">{result.split_strategy}</strong>
            </span>
          </div>
          <p className="mt-1 break-all font-mono text-[10px] text-neutral-500">{result.path}</p>
          {result.excluded.length > 0 && (
            <p className="mt-2 text-neutral-400">
              {result.excluded.length} take(s) left out:{" "}
              {result.excluded.map((x) => x.reason).join("; ")}
            </p>
          )}
          {result.warnings.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-4 text-amber-200">
              {result.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
