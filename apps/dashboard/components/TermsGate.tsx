"use client";

/**
 * Accept the terms before using the platform (ADR-016). Shown in place of the app
 * to a signed-in person who hasn't accepted the current version — an account an
 * admin made, one from before the terms existed, or after the terms changed. The
 * API refuses their requests until they accept, so this can't be skipped.
 */

import { useState } from "react";
import { TermsText } from "@/components/TermsText";
import { api, type TermsInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function TermsGate({ terms }: { terms: TermsInfo }) {
  const { user, logout, refreshUser } = useAuth();
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const accept = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.acceptTerms(terms.version);
      await refreshUser();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0a0a0f] px-4 py-8">
      <div className="w-full max-w-2xl rounded-2xl border border-white/10 bg-[#111118] p-5 sm:p-6">
        <h1 className="text-lg font-semibold text-neutral-100">
          {user?.terms_version ? "The terms and conditions have changed" : "Terms and conditions"}
        </h1>
        <p className="mt-1 text-sm text-neutral-400">
          Please read and accept them to continue{user ? `, ${user.name}` : ""}.
        </p>
        <div className="mt-4 max-h-[55vh] overflow-y-auto rounded-xl border border-white/10 bg-black/30 p-4">
          <TermsText text={terms.text} />
        </div>
        <label className="mt-4 flex cursor-pointer items-start gap-2 text-sm text-neutral-300">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-emerald-500"
          />
          I have read and agree to the terms and conditions (version {terms.version}).
        </label>
        {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            onClick={accept}
            disabled={!agreed || busy}
            className="rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400 disabled:opacity-40"
          >
            {busy ? "Saving…" : "Accept and continue"}
          </button>
          <button
            onClick={logout}
            className="rounded-xl border border-white/10 px-4 py-2 text-sm text-neutral-300 hover:bg-white/5"
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
