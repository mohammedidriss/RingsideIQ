"use client";

/** The terms and conditions — public, linked from the sign-up form (ADR-016). */

import Link from "next/link";
import { useEffect, useState } from "react";
import { TermsText } from "@/components/TermsText";
import { api, type TermsInfo } from "@/lib/api";

export default function TermsPage() {
  const [terms, setTerms] = useState<TermsInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .terms()
      .then(setTerms)
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <main className="mx-auto max-w-2xl px-4 py-8 sm:py-12">
      <Link href="/" className="text-sm text-neutral-400 hover:text-neutral-200">
        ← Back to sign in
      </Link>
      <div className="mt-4 rounded-2xl border border-white/10 bg-[#111118] p-5 sm:p-6">
        {terms ? (
          <>
            <TermsText text={terms.text} />
            <p className="mt-6 text-xs text-neutral-500">Version {terms.version}</p>
          </>
        ) : (
          <p className="text-sm text-neutral-400">{err ?? "Loading…"}</p>
        )}
      </div>
    </main>
  );
}
