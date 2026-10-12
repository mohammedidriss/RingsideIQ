"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { FighterBackLink } from "@/components/FighterBackLink";
import { api, type Fighter } from "@/lib/api";

// Fallback page: if a user lands here (e.g. bookmark, direct URL), we
// let them pick a fighter, create a bare session, and redirect to the
// detail page where the full setup UI lives.

export default function NewSessionPage() {
  return (
    <Suspense
      fallback={<main className="p-8 text-sm text-neutral-400">Loading…</main>}
    >
      <NewSessionInner />
    </Suspense>
  );
}

function NewSessionInner() {
  const router = useRouter();
  const params = useSearchParams();
  const presetFighter = params.get("fighter");
  const [fighters, setFighters] = useState<Fighter[]>([]);
  const [fighterId, setFighterId] = useState<string>("");
  const [newFighterName, setNewFighterName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    // If a fighter is preset, create immediately and redirect.
    if (presetFighter) {
      setBusy(true);
      api
        .createSession(presetFighter, "live_webcam", "mediapipe")
        .then((s) => router.replace(`/sessions/${s.id}`))
        .catch((e) => {
          setErr(String(e));
          setBusy(false);
        });
      return;
    }
    // Otherwise load the fighter list for manual selection.
    api
      .listFighters()
      .then((fs) => {
        setFighters(fs);
        if (fs[0]) setFighterId(fs[0].id);
      })
      .catch((e) => setErr(String(e)));
  }, [presetFighter, router]);

  const createFighter = async () => {
    if (!newFighterName.trim()) return;
    const f = await api.createFighter(newFighterName.trim());
    setFighters([...fighters, f]);
    setFighterId(f.id);
    setNewFighterName("");
  };

  const submit = async () => {
    if (!fighterId) return;
    setBusy(true);
    setErr(null);
    try {
      const s = await api.createSession(fighterId, "live_webcam", "mediapipe");
      router.push(`/sessions/${s.id}`);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  // When preset fighter was given, show a loading state while creating.
  if (presetFighter) {
    return (
      <main className="mx-auto max-w-xl space-y-4 p-8">
        <FighterBackLink fighterId={presetFighter} />
        {err && <p className="text-sm text-red-400">{err}</p>}
        {busy && <p className="text-sm text-neutral-400">Creating session…</p>}
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-xl space-y-6 p-8">
      <h1 className="text-2xl font-semibold">Training session</h1>
      <p className="text-sm text-neutral-400">
        Pick a fighter and we&apos;ll create a training session. You can configure the
        round structure, source, and pose backend on the session page.
      </p>

      {err && <p className="text-sm text-red-400">{err}</p>}

      <section className="space-y-2 rounded-lg border border-neutral-800 p-4">
        <h2 className="text-sm font-medium text-neutral-300">Fighter</h2>
        {fighters.length > 0 ? (
          <select
            className="w-full rounded bg-neutral-900 p-2 text-sm"
            value={fighterId}
            onChange={(e) => setFighterId(e.target.value)}
          >
            {fighters.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name} ({f.stance})
              </option>
            ))}
          </select>
        ) : (
          <p className="text-sm text-neutral-500">No fighters yet.</p>
        )}
        <div className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded bg-neutral-900 p-2 text-sm"
            placeholder="New fighter name…"
            value={newFighterName}
            onChange={(e) => setNewFighterName(e.target.value)}
          />
          <button
            onClick={createFighter}
            className="rounded bg-neutral-700 px-3 text-sm hover:bg-neutral-600"
          >
            Add fighter
          </button>
        </div>
      </section>

      <button
        onClick={submit}
        disabled={!fighterId || busy}
        className="rounded-xl bg-emerald-500 px-5 py-3 text-sm font-semibold text-black shadow-lg shadow-emerald-900/30 hover:bg-emerald-400 disabled:opacity-50"
      >
        {busy ? "Creating…" : "Start training session →"}
      </button>
    </main>
  );
}
