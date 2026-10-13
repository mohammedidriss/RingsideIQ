"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlionWordmark } from "@/components/AlionLogo";
import { useAuth } from "@/lib/auth";
import { api, type AuthUser, type TermsInfo, type UserRole } from "@/lib/api";

/** Compute the profile URL a user should land on after login.
 *  Returns null if no suitable destination exists (e.g. profile_id missing). */
function profileUrlFor(u: AuthUser): string | null {
  if (u.role === "fighter" && u.profile_id) return `/fighters/${u.profile_id}`;
  if (u.role === "coach" && u.profile_id) return `/coaches/${u.profile_id}`;
  if (u.role === "referee" && u.profile_id) return `/referees/${u.profile_id}`;
  if (u.role === "gym_manager") return "/gym-dashboard";
  if (u.role === "admin") return "/admin";
  // No linked profile — cannot redirect (avoids infinite loop on "/")
  return null;
}

export default function Home() {
  const router = useRouter();
  const { user, loading, login, register } = useAuth();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<UserRole>("fighter");
  const [stayLoggedIn, setStayLoggedIn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Creating an account means accepting the current terms (ADR-016).
  const [terms, setTerms] = useState<TermsInfo | null>(null);
  const [agreed, setAgreed] = useState(false);

  useEffect(() => {
    if (mode === "register" && !terms) api.terms().then(setTerms).catch(() => {});
  }, [mode, terms]);

  // Auto-redirect authenticated users to their profile page
  useEffect(() => {
    if (!loading && user) {
      const dest = profileUrlFor(user);
      if (dest) router.replace(dest);
    }
  }, [user, loading, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      let loggedInUser: AuthUser;
      if (mode === "login") {
        loggedInUser = await login(email, password, stayLoggedIn);
      } else {
        if (!terms || !agreed) throw new Error("Please accept the terms and conditions");
        loggedInUser = await register(email, password, name, role, stayLoggedIn, terms.version);
      }
      // Redirect immediately to profile
      const dest = profileUrlFor(loggedInUser);
      if (dest) {
        router.replace(dest);
      } else {
        setError("Your account has no linked profile. Please contact an admin.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  };

  // Show loading spinner while auth is resolving or user is being redirected
  const destination = user ? profileUrlFor(user) : null;
  if (loading || (user && destination)) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-neutral-500">Loading...</p>
      </div>
    );
  }

  // ─── Not logged in → Login / Register form ─────────────────────
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md space-y-8">
        {/* Logo */}
        <div className="text-center">
          <AlionWordmark size={48} />
          <p className="mt-3 text-sm text-neutral-400">
            Multi-modal AI coaching for combat sports
          </p>
        </div>

        {/* Form card */}
        <div className="card">
          {/* Tabs */}
          <div className="mb-6 flex border-b border-white/10">
            <button
              onClick={() => { setMode("login"); setError(null); }}
              className={`flex-1 pb-3 text-sm font-medium transition-colors ${
                mode === "login"
                  ? "border-b-2 border-emerald-500 text-white"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              Sign In
            </button>
            <button
              onClick={() => { setMode("register"); setError(null); }}
              className={`flex-1 pb-3 text-sm font-medium transition-colors ${
                mode === "register"
                  ? "border-b-2 border-emerald-500 text-white"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              Create Account
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "register" && (
              <>
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-400">
                    Full Name
                  </label>
                  <input
                    type="text"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Your name"
                    className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm text-neutral-200 placeholder:text-neutral-600 focus:border-emerald-500/40 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-400">
                    Role
                  </label>
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value as UserRole)}
                    className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm text-neutral-200 focus:border-emerald-500/40 focus:outline-none"
                  >
                    <option value="fighter">Fighter</option>
                    <option value="coach">Coach</option>
                    <option value="referee">Referee</option>
                    <option value="gym_manager">Gym Manager</option>
                  </select>
                </div>
              </>
            )}

            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-400">
                Email
              </label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm text-neutral-200 placeholder:text-neutral-600 focus:border-emerald-500/40 focus:outline-none"
                autoComplete="email"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-400">
                Password
              </label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === "register" ? "Min 6 characters" : "Your password"}
                minLength={mode === "register" ? 6 : undefined}
                className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm text-neutral-200 placeholder:text-neutral-600 focus:border-emerald-500/40 focus:outline-none"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>

            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={stayLoggedIn}
                onChange={(e) => setStayLoggedIn(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-white/20 bg-white/[0.03] accent-emerald-500"
              />
              <span className="text-xs text-neutral-400">Stay logged in</span>
            </label>

            {mode === "register" && (
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="mt-0.5 h-3.5 w-3.5 rounded border-white/20 bg-white/[0.03] accent-emerald-500"
                />
                <span className="text-xs text-neutral-400">
                  I agree to the{" "}
                  <a href="/terms" target="_blank" className="text-emerald-300 underline hover:text-emerald-200">
                    terms and conditions
                  </a>
                  {terms ? ` (version ${terms.version})` : ""}
                </span>
              </label>
            )}

            {error && (
              <p className="rounded-lg border border-red-500/30 bg-red-950/30 px-3 py-2 text-xs text-red-300">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting || (mode === "register" && (!agreed || !terms))}
              className="w-full rounded-xl bg-emerald-500 py-2.5 text-sm font-semibold text-black hover:bg-emerald-400 disabled:opacity-50"
            >
              {submitting
                ? "..."
                : mode === "login"
                  ? "Sign In"
                  : "Create Account"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
