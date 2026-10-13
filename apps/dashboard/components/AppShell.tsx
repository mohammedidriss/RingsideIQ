"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Sidebar } from "@/components/Sidebar";
import { MobileTopBar, MobileBottomBar } from "@/components/MobileNav";
import { TermsGate } from "@/components/TermsGate";
import { api, type TermsInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // The current terms (ADR-016): a signed-in person who hasn't accepted them sees
  // only the TermsGate — the API refuses their requests until they do.
  const [terms, setTerms] = useState<TermsInfo | null>(null);
  const [termsFailed, setTermsFailed] = useState(false);
  useEffect(() => {
    if (user && !terms) api.terms().then(setTerms).catch(() => setTermsFailed(true));
  }, [user, terms]);

  // The camera slave page (ADR-010) is joined by phones with only a session token —
  // it must render with no login and no app chrome.
  // Sessions and dataset takes (ADR-013) both have a public phone-camera page.
  // …and the linked-phone camera station (/camera) — phones never log in.
  const isCameraSlave =
    /^\/(sessions|takes)\/[^/]+\/camera$/.test(pathname) || pathname === "/camera";

  useEffect(() => { setSidebarOpen(false); }, [pathname]);

  const isPublic = pathname === "/" || pathname === "/terms";

  useEffect(() => {
    if (!loading && !user && !isPublic && !isCameraSlave) router.push("/");
  }, [loading, user, isPublic, router, isCameraSlave]);

  // Login page, the terms and the public camera slave page — no chrome, no guard.
  if (isPublic) return <>{children}</>;
  if (isCameraSlave) return <>{children}</>;

  if (loading) {
    return (
      <div className="flex h-screen overflow-hidden">
        <div className="hidden md:block w-56 shrink-0 border-r border-white/5 bg-[#0d0d12]" />
        <main className="mobile-shell-main flex-1 overflow-y-auto">{children}</main>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center text-neutral-400">
        Redirecting…
      </div>
    );
  }

  if (!terms && !termsFailed) {
    return <div className="flex h-screen items-center justify-center text-neutral-400">Loading…</div>;
  }
  if (terms && user.terms_version !== terms.version) return <TermsGate terms={terms} />;

  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0a0f]">
      {/* Mobile top bar */}
      <MobileTopBar onMenuToggle={() => setSidebarOpen((o) => !o)} menuOpen={sidebarOpen} />

      {/* Sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar — full height slide-in on mobile, permanent on desktop */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-[60] w-[280px] transform transition-transform duration-200 ease-in-out
          md:relative md:z-auto md:w-56 md:translate-x-0 md:shrink-0
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
        `}
      >
        <Sidebar onNavigate={() => setSidebarOpen(false)} />
      </aside>

      {/* Page content — padded top/bottom for mobile bars via CSS class */}
      <main className="mobile-shell-main flex-1 overflow-y-auto">
        {children}
      </main>

      {/* Mobile bottom tab bar */}
      <MobileBottomBar />
    </div>
  );
}
