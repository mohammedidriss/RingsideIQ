"use client";

/**
 * Dataset protocol card (RQ2) — on a dataset take's recording screen (ADR-013).
 * The fighter works through fixed blocks (30 jabs, 30 crosses, …) and the coach
 * taps Start / End for each. The wrist IMUs time every punch inside a block, the
 * block gives its type, and the take comes out labeled ({take}/labels.json) with
 * no hand-tagging. The live count is the sanity check: 30 thrown should read ~30.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { imuErrorText } from "@/components/ImuSensors";
import { api, type ProtocolBlock, type ProtocolBlockSpec, type TakeProtocol } from "@/lib/api";

function target(spec: ProtocolBlockSpec): string {
  if (spec.reps) return `${spec.reps}`;
  if (spec.duration_s) return `${Math.round(spec.duration_s / 60)} min`;
  return "";
}

function fmtClock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function ProtocolCard({ takeId, readOnly = false }: { takeId: string; readOnly?: boolean }) {
  const [p, setP] = useState<TakeProtocol | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const startedLocal = useRef<number | null>(null); // when this tab started the open block
  const [, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      setP(await api.protocol(takeId));
    } catch {
      /* keep the last state; transient */
    }
  }, [takeId]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const r = await api.protocol(takeId).catch(() => null);
      if (!alive) return;
      if (r) setP(r);
      timer = setTimeout(poll, r?.active != null ? 1000 : 5000);
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [takeId]);

  // Tick the block timer once a second while a block runs.
  useEffect(() => {
    if (p?.active == null) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [p?.active]);

  const act = async (fn: () => Promise<TakeProtocol>, after?: () => void) => {
    setBusy(true);
    setErr(null);
    try {
      setP(await fn());
      after?.();
    } catch (e) {
      setErr(imuErrorText(e));
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  if (!p) return null;
  if (readOnly && p.blocks.length === 0) return null;

  const active = p.active != null ? p.blocks[p.active] : null;
  // Latest kept attempt per block key (a Redo discards the earlier one).
  const latest = new Map<string, ProtocolBlock>();
  for (const b of p.blocks) if (!b.discarded) latest.set(b.key, b);
  // A one-block take (recorded from a category) shows just its own block.
  const plan = p.take_block ? p.plan.filter((s) => s.key === p.take_block) : p.plan;
  const nextKey = plan.find((s) => !latest.has(s.key))?.key ?? null;
  const done = plan.filter((s) => latest.get(s.key)?.t_end_ms != null).length;

  return (
    <div className="rounded-2xl border border-white/10 p-4">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-left"
        aria-expanded={open}
      >
        <span>
          <span className="text-sm font-semibold">Dataset protocol</span>
          <span className="ml-2 text-[11px] text-neutral-500">
            {p.take_block ? (done ? "done" : "1 block") : `${done}/${plan.length} blocks`}
          </span>
        </span>
        <span className="text-xs text-neutral-500">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="mt-3 space-y-3">
          <p className="text-[11px] text-neutral-500">
            {(p.stance ?? "orthodox").replace(/^\w/, (c) => c.toUpperCase())} · lead hand ={" "}
            <strong className="text-neutral-300">{p.lead_hand}</strong>. Punches are timed by the
            wrist sensors and labeled with the block&apos;s type.
          </p>

          {!readOnly && !p.recording && p.active == null && (
            <p className="text-[11px] text-neutral-500">
              Start the cameras, then work through the blocks.
            </p>
          )}
          {!readOnly && p.recording && !p.imu_running && (
            <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-200">
              Wrist sensors aren&apos;t recording — blocks will be marked but can&apos;t be
              labeled. Check both sensors are on.
            </p>
          )}

          {p.swap_evidence.length > 0 && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-200">
              <p className="font-medium">
                {p.imu_hands_swapped
                  ? "The wrist swap may be wrong for this take."
                  : "The wrist sensors look swapped (left unit on the right wrist)."}
              </p>
              <ul className="mt-1 list-disc pl-4 text-amber-100/80">
                {p.swap_evidence.map((why) => (
                  <li key={why}>{why}</li>
                ))}
              </ul>
              <button
                disabled={busy}
                onClick={() => {
                  if (
                    p.labels_edited &&
                    !window.confirm("Relabel with the wrists swapped? This replaces the reviewed labels.")
                  )
                    return;
                  act(() => api.protocolSwapWrists(takeId, !p.imu_hands_swapped, p.labels_edited));
                }}
                className="mt-1.5 rounded-md bg-amber-500 px-2 py-0.5 text-[11px] font-semibold text-black hover:bg-amber-400 disabled:opacity-50"
              >
                {p.imu_hands_swapped ? "Undo swap" : "Swap wrists"}
              </button>
            </div>
          )}
          {p.imu_hands_swapped && p.swap_evidence.length === 0 && (
            <p className="text-[11px] text-sky-300">
              Wrists swapped for this take — the sensors were worn on the opposite wrists.{" "}
              <button
                disabled={busy}
                onClick={() => act(() => api.protocolSwapWrists(takeId, false, p.labels_edited))}
                className="underline hover:text-sky-200"
              >
                Undo
              </button>
            </p>
          )}

          <ol className="space-y-1">
            {plan.map((spec) => {
              const b = latest.get(spec.key);
              const isActive = active?.key === spec.key && b === active;
              const finished = b?.t_end_ms != null;
              const ok =
                finished && spec.reps && b?.detected != null
                  ? Math.abs(b.detected - spec.reps) <= 2
                  : true;
              return (
                <li
                  key={spec.key}
                  className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs ${
                    isActive ? "bg-red-500/10 ring-1 ring-red-500/40" : "bg-white/[0.02]"
                  }`}
                  title={spec.hint || undefined}
                >
                  <span className="w-3 text-center">
                    {isActive ? (
                      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500" />
                    ) : finished ? (
                      <span className="text-emerald-400">✓</span>
                    ) : (
                      <span className="text-neutral-600">○</span>
                    )}
                  </span>
                  <span className={`min-w-0 flex-1 truncate ${finished ? "text-neutral-400" : "text-neutral-200"}`}>
                    {spec.title}
                    <span className="ml-1 text-[10px] text-neutral-500">{target(spec)}</span>
                  </span>

                  {isActive ? (
                    <>
                      <span className="tabular-nums text-neutral-300">
                        {spec.kind === "negative"
                          ? startedLocal.current != null
                            ? fmtClock(Date.now() - startedLocal.current)
                            : ""
                          : `${b?.detected ?? 0}${spec.reps ? ` / ${spec.reps}` : ""}`}
                      </span>
                      {!readOnly && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            act(() => api.protocolEnd(takeId), () => (startedLocal.current = null))
                          }
                          className="rounded-md bg-red-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                        >
                          End
                        </button>
                      )}
                    </>
                  ) : finished ? (
                    <>
                      <span
                        className={`tabular-nums ${ok ? "text-emerald-300" : "text-amber-300"}`}
                        title={
                          spec.kind === "negative"
                            ? "Bursts in the no-punch block — should be 0"
                            : spec.kind === "combo"
                              ? `Complete combos (every punch found, in order); ${b?.off_hand ?? 0} incomplete`
                              : ok
                                ? "Detected punches"
                                : `Expected ~${spec.reps}. Check for missed or extra punches when reviewing.`
                        }
                      >
                        {spec.kind === "negative"
                          ? `${b?.off_hand ?? 0} bursts`
                          : spec.kind === "combo"
                            ? `${b?.detected ?? 0} combos`
                            : (b?.detected ?? "–")}
                      </span>
                      {!readOnly && active == null && (
                        <button
                          disabled={busy}
                          onClick={() => act(() => api.protocolDiscard(takeId, b!.index))}
                          className="text-[10px] text-neutral-500 hover:text-red-300"
                          title="Discard this attempt, then record the block again"
                        >
                          Redo
                        </button>
                      )}
                    </>
                  ) : (
                    !readOnly &&
                    active == null && (
                      <button
                        disabled={busy || !p.recording}
                        title={p.recording ? undefined : "Start the cameras first"}
                        onClick={() =>
                          act(
                            () => api.protocolStart(takeId, spec.key),
                            () => (startedLocal.current = Date.now()),
                          )
                        }
                        className={`rounded-md px-2 py-0.5 text-[11px] font-semibold disabled:opacity-50 ${
                          spec.key === nextKey
                            ? "bg-emerald-500 text-black hover:bg-emerald-400"
                            : "border border-white/15 text-neutral-300 hover:bg-white/5"
                        }`}
                      >
                        Start
                      </button>
                    )
                  )}
                </li>
              );
            })}
          </ol>

          {p.labels && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/10 pt-2 text-[11px]">
              <span className="text-neutral-400">
                <strong className="text-neutral-200">{p.labels.count}</strong> punches labeled
                {p.labels_edited && <span className="text-sky-300"> · reviewed copy kept</span>}
              </span>
              <button
                disabled={busy}
                onClick={() => {
                  if (
                    p.labels_edited &&
                    !window.confirm("Replace the reviewed labels with freshly generated ones?")
                  )
                    return;
                  act(() => api.protocolLabels(takeId, p.labels_edited));
                }}
                className="text-neutral-500 hover:text-neutral-200"
              >
                Regenerate
              </button>
            </div>
          )}

          {err && <p className="text-[11px] text-red-300">{err}</p>}
        </div>
      )}
    </div>
  );
}
