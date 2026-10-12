"use client";

/**
 * Bouts (ADR-011) — two-fighter fights. A *training* session has one fighter (the
 * existing per-fighter Sessions page); a *bout* links two, one per corner. This
 * page lists bouts and creates a new one: pick a red + blue fighter, and it creates
 * a session for each fighter and assigns the corners.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Bout, type Fighter } from "@/lib/api";

export default function BoutsPage() {
  const router = useRouter();
  const [bouts, setBouts] = useState<Bout[]>([]);
  const [fighters, setFighters] = useState<Fighter[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [creating, setCreating] = useState(false);
  const [redId, setRedId] = useState("");
  const [blueId, setBlueId] = useState("");
  const [label, setLabel] = useState("");

  useEffect(() => {
    api.listBouts().then(setBouts).catch((e) => setErr(String(e)));
    api.listFighters().then(setFighters).catch(() => {});
  }, []);

  const canCreate = Boolean(redId && blueId && redId !== blueId && !creating);

  const create = async () => {
    if (!canCreate) return;
    setCreating(true);
    setErr(null);
    try {
      const bout = await api.createBout({ label: label.trim() || null });
      // One single-fighter session per corner, linked to the bout.
      const redSession = await api.createSession(redId, "live_webcam");
      await api.assignBoutCorner(bout.id, redSession.id, "red");
      const blueSession = await api.createSession(blueId, "live_webcam");
      await api.assignBoutCorner(bout.id, blueSession.id, "blue");
      router.push(`/bouts/${bout.id}`);
    } catch (e) {
      setErr(String(e));
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6 px-4 py-5 sm:px-8 sm:py-6">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Bouts</h1>
          <p className="text-sm text-neutral-400">
            Two-fighter fights. For a single fighter, start a training session from the
            fighter&apos;s Training sessions page.
          </p>
        </div>
        <button
          onClick={() => setShowNew((s) => !s)}
          className="shrink-0 rounded-xl bg-emerald-500 px-4 py-2 text-sm font-medium text-black hover:bg-emerald-400"
        >
          {showNew ? "Cancel" : "+ Start fight"}
        </button>
      </header>

      {err && (
        <p className="rounded-2xl border border-red-500/30 bg-red-950/30 p-3 text-sm text-red-200">
          {err}
        </p>
      )}

      {showNew && (
        <div className="card space-y-4">
          <h2 className="text-lg font-semibold">Start a fight</h2>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Label (optional) — e.g. Smith v Jones"
            className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm"
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <CornerPicker
              corner="red"
              fighters={fighters}
              value={redId}
              exclude={blueId}
              onChange={setRedId}
            />
            <CornerPicker
              corner="blue"
              fighters={fighters}
              value={blueId}
              exclude={redId}
              onChange={setBlueId}
            />
          </div>
          <button
            onClick={create}
            disabled={!canCreate}
            className="rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400 disabled:opacity-40"
          >
            {creating ? "Starting…" : "Start fight"}
          </button>
        </div>
      )}

      {bouts.length === 0 ? (
        <p className="text-sm text-neutral-500">No fights yet — start one to record it.</p>
      ) : (
        <ul className="space-y-3">
          {bouts.map((b) => (
            <li key={b.id}>
              <Link
                href={`/bouts/${b.id}`}
                className="card flex items-center justify-between transition-colors hover:border-white/15"
              >
                <div>
                  <div className="font-semibold">{b.label || "Untitled bout"}</div>
                  <div className="text-xs text-neutral-400">
                    {new Date(b.scheduled_at).toLocaleString()} · {b.round_count}×
                    {Math.round(b.round_duration_s / 60)}min
                  </div>
                </div>
                {b.winner_corner ? (
                  <span
                    className={
                      b.winner_corner === "red"
                        ? "text-sm text-red-400"
                        : b.winner_corner === "blue"
                          ? "text-sm text-sky-400"
                          : "text-sm text-neutral-300"
                    }
                  >
                    {b.winner_corner === "draw" ? "Draw" : `${b.winner_corner} won`}
                    {b.result_method ? ` · ${b.result_method}` : ""}
                  </span>
                ) : (
                  <span className="text-sm text-neutral-500">open →</span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CornerPicker({
  corner,
  fighters,
  value,
  exclude,
  onChange,
}: {
  corner: "red" | "blue";
  fighters: Fighter[];
  value: string;
  exclude: string;
  onChange: (id: string) => void;
}) {
  const tint =
    corner === "red"
      ? "border-red-500/40 bg-red-500/[0.05] text-red-300"
      : "border-sky-500/40 bg-sky-500/[0.05] text-sky-300";
  return (
    <div className={`rounded-xl border p-3 ${tint}`}>
      <div className="mb-2 text-xs font-semibold uppercase">{corner} corner</div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-neutral-100"
      >
        <option value="">Select fighter…</option>
        {fighters
          .filter((f) => f.id !== exclude)
          .map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
      </select>
    </div>
  );
}
