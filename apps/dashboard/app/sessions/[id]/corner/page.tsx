"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Session, type CoachAdviceResponse } from "@/lib/api";


export default function CornerPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [session, setSession] = useState<Session | null>(null);
  const [advice, setAdvice] = useState<CoachAdviceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.getSession(id).then(setSession).catch((e) => setErr(String(e)));
  }, [id]);

  const handleGenerate = async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await api.generateAdvice(id);
      setAdvice(res);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  };

  if (!session) {
    return (
      <main className="flex h-screen items-center justify-center bg-black p-8 text-2xl text-neutral-400">
        {err ? <span className="text-red-500">{err}</span> : "Loading…"}
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-black p-6 text-white sm:p-12">
      <header className="mb-12 flex items-center justify-between">
        <Link
          href={`/sessions/${id}`}
          className="rounded-xl bg-neutral-900 px-6 py-4 text-xl font-medium tracking-wide active:bg-neutral-800"
        >
          ← Exit Corner
        </Link>
        <div className="text-right">
          <div className="text-sm font-bold uppercase tracking-widest text-neutral-500">
            Gym Mode
          </div>
          <div className="text-2xl font-semibold">
            {session.bout_id ? "Fight" : "Training session"} {session.id.split("-")[0]}
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-4xl space-y-12">
        {err && (
          <div className="rounded-2xl border-4 border-red-900 bg-red-950 p-6 text-2xl text-red-200">
            {err}
          </div>
        )}

        {!advice && !loading && (
          <button
            onClick={handleGenerate}
            className="w-full rounded-3xl bg-amber-500 py-16 text-5xl font-black text-black active:bg-amber-400"
          >
            GET CORNER ADVICE
          </button>
        )}

        {loading && (
          <div className="flex w-full items-center justify-center rounded-3xl border-4 border-dashed border-amber-500/50 py-16">
            <span className="animate-pulse text-4xl font-bold tracking-widest text-amber-500">
              ANALYZING...
            </span>
          </div>
        )}

        {advice && (
          <div className="animate-in fade-in slide-in-from-bottom-8 duration-700">
            <section className="rounded-3xl border-2 border-neutral-800 bg-neutral-950 p-8 shadow-2xl sm:p-12">
              <h2 className="mb-6 text-sm font-bold uppercase tracking-widest text-amber-500">
                Summary
              </h2>
              <p className="text-3xl font-medium leading-relaxed sm:text-4xl sm:leading-relaxed">
                {advice.summary}
              </p>
            </section>

            <section className="mt-10">
              <h2 className="mb-6 text-sm font-bold uppercase tracking-widest text-sky-400">
                Action Plan
              </h2>
              <ul className="space-y-6">
                {advice.action_items.map((item, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-6 rounded-3xl bg-neutral-900 p-8"
                  >
                    <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-sky-500/20 text-3xl font-black text-sky-400">
                      {i + 1}
                    </span>
                    <span className="text-3xl font-medium sm:text-4xl">
                      {item}
                    </span>
                  </li>
                ))}
              </ul>
            </section>

            <button
              onClick={handleGenerate}
              className="mt-16 w-full rounded-2xl bg-neutral-900 py-8 text-2xl font-bold tracking-widest text-neutral-400 active:bg-neutral-800"
            >
              REGENERATE
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
