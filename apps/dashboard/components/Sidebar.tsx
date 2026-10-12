"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AlionWordmark } from "@/components/AlionLogo";
import { ProfileAvatar } from "@/components/ProfileAvatar";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";

type NavItem = { label: string; href: string; icon: string };

/** Extract fighter ID from pathname like /fighters/abc-123/sessions */
function extractFighterId(pathname: string): string | null {
  const m = pathname.match(/^\/fighters\/([^/]+)/);
  return m ? m[1] : null;
}

/** Extract coach ID from pathname like /coaches/abc-123 */
function extractCoachId(pathname: string): string | null {
  const m = pathname.match(/^\/coaches\/([^/]+)/);
  return m ? m[1] : null;
}

const FIGHTER_TABS: { slug: string; label: string; icon: string }[] = [
  { slug: "", label: "Dashboard", icon: "▦" },
  { slug: "sessions", label: "Training sessions", icon: "▷" },
  { slug: "hrv", label: "HRV", icon: "♥" },
  { slug: "observations", label: "Observations", icon: "✎" },
  { slug: "medical", label: "Medical", icon: "✚" },
  { slug: "team", label: "Team", icon: "◈" },
  { slug: "imu", label: "IMU", icon: "▤" },
];

export function Sidebar({ onNavigate }: { onNavigate?: () => void } = {}) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const activeRole = user?.role ?? null;

  const fighterId = extractFighterId(pathname);
  const coachId = extractCoachId(pathname);

  // Compute the user's "home" URL based on role
  const homeHref = (() => {
    if (!user) return "/";
    if (user.role === "fighter" && user.profile_id) return `/fighters/${user.profile_id}`;
    if (user.role === "coach" && user.profile_id) return `/coaches/${user.profile_id}`;
    if (user.role === "referee" && user.profile_id) return `/referees/${user.profile_id}`;
    if (user.role === "gym_manager") return "/gym-dashboard";
    if (user.role === "admin") return "/admin";
    return "/";
  })();

  // Build nav items based on context
  const navItems: NavItem[] = [];

  // Always show Home (points to user's profile page)
  navItems.push({ label: "Home", href: homeHref, icon: "H" });

  // Role-specific global items
  if (activeRole === "admin") {
    navItems.push({ label: "Admin Dashboard", href: "/admin", icon: "⚙" });
    navItems.push({ label: "Users", href: "/admin/users", icon: "◉" });
    navItems.push({ label: "All Fighters", href: "/admin/fighters", icon: "⊕" });
    navItems.push({ label: "All Coaches", href: "/admin/coaches", icon: "◈" });
    navItems.push({ label: "Gym Managers", href: "/admin/gym-managers", icon: "M" });
    navItems.push({ label: "All Gyms", href: "/admin/gyms", icon: "⌂" });
    navItems.push({ label: "Compare Backends", href: "/compare", icon: "C" });
  }
  if (activeRole === "gym_manager") {
    navItems.push({ label: "Dashboard", href: "/gym-dashboard", icon: "▦" });
    navItems.push({ label: "Members", href: "/gym-dashboard/members", icon: "◉" });
    navItems.push({ label: "Coaches", href: "/gym-dashboard/coaches", icon: "◈" });
    navItems.push({ label: "Gyms", href: "/gym-dashboard/gyms", icon: "⌂" });
  }
  // Bouts (ADR-011) — two-fighter fights; for everyone who runs sessions (not admin,
  // who is restricted from training data).
  if (activeRole && activeRole !== "admin") {
    navItems.push({ label: "Bouts", href: "/bouts", icon: "⚔" });
  }
  // Datasets (ADR-013) — research recordings, kept apart from training sessions.
  // IRB data: coaches (the researcher) and fighters only.
  if (activeRole === "coach" || activeRole === "fighter") {
    navItems.push({ label: "Datasets", href: "/datasets", icon: "▤" });
  }

  // Fighter context tabs (when viewing any fighter page)
  // Privacy: admin only sees general info (Dashboard + Team); gym_manager can't see Medical
  const CONFIDENTIAL_SLUGS = ["sessions", "hrv", "observations", "medical", "imu"];
  const visibleTabs = activeRole === "admin"
    ? FIGHTER_TABS.filter((t) => !CONFIDENTIAL_SLUGS.includes(t.slug))
    : activeRole === "gym_manager"
      ? FIGHTER_TABS.filter((t) => t.slug !== "medical")
      : FIGHTER_TABS;
  const fighterTabs: NavItem[] = fighterId
    ? visibleTabs.map((t) => ({
        label: t.label,
        href: t.slug ? `/fighters/${fighterId}/${t.slug}` : `/fighters/${fighterId}`,
        icon: t.icon,
      }))
    : [];

  // Coach context
  const coachTabs: NavItem[] = coachId
    ? [{ label: "Coach Profile", href: `/coaches/${coachId}`, icon: "P" }]
    : [];

  const isTabActive = (href: string, isExact?: boolean) => {
    if (href === "/") return pathname === "/";
    if (isExact) return pathname === href;
    return pathname === href || pathname.startsWith(href + "/");
  };

  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col border-r border-white/5 bg-[#0d0d12]">
      {/* Logo */}
      <div className="px-4 pt-5 pb-4">
        <Link href="/">
          <AlionWordmark size={28} />
        </Link>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2">
        {/* Global nav */}
        {navItems.map((item) => (
          <Link
            key={item.href + item.label}
            href={item.href}
            onClick={onNavigate}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
              isTabActive(item.href)
                ? "bg-white/[0.08] text-white font-medium"
                : "text-neutral-400 hover:bg-white/[0.04] hover:text-neutral-200"
            }`}
          >
            <span className="flex h-5 w-5 items-center justify-center rounded text-[11px] bg-white/[0.06]">
              {item.icon}
            </span>
            {item.label}
          </Link>
        ))}

        {/* Fighter context section */}
        {fighterTabs.length > 0 && (
          <>
            <div className="mt-4 mb-1 px-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-600">
              Fighter
            </div>
            {fighterTabs.map((t) => {
              // Dashboard tab: exact match only
              const active =
                t.href === `/fighters/${fighterId}`
                  ? pathname === t.href || pathname === `${t.href}/matrix`
                  : isTabActive(t.href);
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                    active
                      ? "bg-white/10 text-white font-medium"
                      : "text-neutral-400 hover:bg-white/5 hover:text-neutral-100"
                  }`}
                >
                  <span className="w-4 text-center text-xs" aria-hidden>
                    {t.icon}
                  </span>
                  {t.label}
                </Link>
              );
            })}

            {/* Training session button (coaches only — admin manages accounts, not sessions) */}
            {activeRole !== "fighter" && activeRole !== "gym_manager" && activeRole !== "admin" && (
              <button
                onClick={async (e) => {
                  const btn = e.currentTarget;
                  btn.disabled = true;
                  try {
                    const s = await api.createSession(fighterId!, "live_webcam", "mediapipe");
                    router.push(`/sessions/${s.id}`);
                  } catch {
                    btn.disabled = false;
                  }
                }}
                className="mt-2 flex w-full items-center justify-center rounded-lg bg-emerald-500 px-3 py-2 text-sm font-medium text-black hover:bg-emerald-400 disabled:opacity-50"
              >
                + Training session
              </button>
            )}
          </>
        )}

        {/* Coach context section */}
        {coachTabs.length > 0 && (
          <>
            <div className="mt-4 mb-1 px-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-600">
              Coach
            </div>
            {coachTabs.map((t) => (
              <Link
                key={t.href}
                href={t.href}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                  isTabActive(t.href)
                    ? "bg-white/10 text-white font-medium"
                    : "text-neutral-400 hover:bg-white/5 hover:text-neutral-100"
                }`}
              >
                <span className="flex h-5 w-5 items-center justify-center rounded text-[11px] bg-white/[0.06]">
                  {t.icon}
                </span>
                {t.label}
              </Link>
            ))}
          </>
        )}
      </nav>

      {/* Profile footer */}
      <div className="border-t border-white/5 p-3">
        {user ? (
          <div className="flex items-center gap-2">
            <Link href="/profile" className="flex items-center gap-2 min-w-0 flex-1 rounded-lg px-1 py-0.5 -ml-1 hover:bg-white/[0.05] transition-colors" title="Edit profile">
              <ProfileAvatar name={user.name} photo_path={user.photo_path} size={32} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">{user.name}</p>
                <p className="truncate text-[10px] capitalize text-neutral-500">
                  {user.role === "gym_manager" ? "gym manager" : user.role}
                </p>
              </div>
            </Link>
            <button
              onClick={() => { logout(); router.push("/"); }}
              className="text-[10px] text-neutral-600 hover:text-neutral-300"
              title="Sign out"
            >
              Log out
            </button>
          </div>
        ) : (
          <p className="text-[11px] text-neutral-600">Not signed in</p>
        )}
      </div>
    </aside>
  );
}
