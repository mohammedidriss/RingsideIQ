"use client";

/**
 * Train the dataset's camera punch counter and see whether it beats the current
 * one (services.training). "Train" exports the dataset first, so every take is
 * in, then trains in the background (a few minutes) and scores the new counter
 * next to today's on the test stretches it never trained on — per category and
 * per camera. Labels come from the wrist sensors, so neither counter is graded
 * against itself.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type CounterScore, type ProtocolBlockSpec, type TrainingStatus } from "@/lib/api";

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

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

export function TrainingCard({
  datasetId,
  plan,
  canTrain,
}: {
  datasetId: string;
  plan: ProtocolBlockSpec[];
  canTrain: boolean; // there are completed takes
}) {
  const [st, setSt] = useState<TrainingStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(
    () =>
      api
        .trainingStatus(datasetId)
        .then(setSt)
        .catch(() => {}),
    [datasetId],
  );
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!st?.running) return;
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [st?.running, load]);

  const train = async () => {
    setStarting(true);
    setErr(null);
    try {
      // every take in, recovered pose included; no face blurring — training needs no video
      await api.datasetExport(datasetId, { blur: false });
      setSt(await api.trainCounter(datasetId));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setStarting(false);
    }
  };

  const r = st?.latest;
  const title = (key: string) => plan.find((b) => b.key === key)?.title ?? key.replace(/_/g, " ");
  const better = r ? r.test.model.f1 - r.test.baseline.f1 : 0;

  return (
    <section className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Punch counter — training</h2>
          <p className="text-xs text-neutral-500">
            Teaches a counter from your takes (the wrist sensors give each punch&apos;s time and
            hand), then tests it on recordings it never saw.
          </p>
        </div>
        <button
          onClick={train}
          disabled={!canTrain || starting || !!st?.running}
          title={canTrain ? undefined : "Record and complete a take first"}
          className="rounded-xl bg-violet-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
        >
          {starting ? "Exporting…" : st?.running ? "Training…" : r ? "🧠 Train again" : "🧠 Train"}
        </button>
      </div>

      {err && <p className="text-xs text-red-300">{err}</p>}
      {st?.running && (
        <p className="animate-pulse text-sm text-violet-300">
          {st.step ?? "starting"} — this takes a few minutes; you can leave the page.
        </p>
      )}
      {st?.error && !st.running && <p className="text-xs text-red-300">Training failed: {st.error}</p>}

      {r && (
        <>
          <div
            className={`rounded-xl border p-3 text-sm ${
              better > 0.02
                ? "border-emerald-500/30 bg-emerald-950/30 text-emerald-200"
                : "border-amber-500/30 bg-amber-950/30 text-amber-200"
            }`}
          >
            {better > 0.02 ? "✓ The trained counter beats the current one" : "The trained counter isn't better yet"}
            : <strong>{pct(r.test.model.f1)}</strong> vs {pct(r.test.baseline.f1)} of punches right
            (F1) on {r.data.test_stretches} test stretches — {r.data.test_punches} punches it never
            trained on.
            <div className="mt-1 text-xs text-neutral-400">
              Trained on {r.data.train_punches} punches ({r.data.train_stretches} stretches, {r.data.takes}{" "}
              takes, {r.data.fighters} fighter{r.data.fighters === 1 ? "" : "s"}) · model v{r.version},{" "}
              {new Date(r.created_at).toLocaleString()}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-xs">
              <thead className="text-left text-[10px] uppercase tracking-wide text-neutral-500">
                <tr>
                  <th className="py-1.5 pr-3 font-normal">Test category</th>
                  <th className="py-1.5 pr-3 font-normal">Punches</th>
                  <th className="py-1.5 pr-3 font-normal">Current counter</th>
                  <th className="py-1.5 font-normal">Trained counter</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {r.by_block.map((b) => (
                  <tr key={b.block} className="text-neutral-200">
                    <td className="py-1.5 pr-3">{title(b.block)}</td>
                    <td className="py-1.5 pr-3 tabular-nums">{b.model.truth}</td>
                    <td className="py-1.5 pr-3 tabular-nums">
                      <Score s={b.baseline} />
                    </td>
                    <td className="py-1.5 tabular-nums">
                      <Score s={b.model} best={b.model.f1 > b.baseline.f1} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-[11px] text-neutral-500">
            Counted = what one camera counted on average; F1 = how many punches it got right, with
            false ones and missed ones both counting against it.
            {!r.data.reviewed_labels && " The labels are auto-labels from the wrist sensors, not reviewed yet."}
            {r.data.fighters < 3 &&
              " With one fighter the results show how well it counts you — not other people."}
          </p>

          {st && st.runs.length > 1 && (
            <div className="text-[11px] text-neutral-500">
              Earlier runs:{" "}
              {st.runs
                .slice(0, -1)
                .reverse()
                .map((x) => `v${x.version} ${pct(x.f1_model)} vs ${pct(x.f1_baseline)}`)
                .join(" · ")}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function Score({ s, best = false }: { s: CounterScore; best?: boolean }) {
  return (
    <span>
      <span className={best ? "font-semibold text-emerald-300" : ""}>{pct(s.f1)}</span>
      <span className="ml-1.5 text-neutral-500">counted {s.counted}</span>
    </span>
  );
}
