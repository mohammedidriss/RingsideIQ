"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";

function extractFighterId(p: string) {
  return p.match(/^\/fighters\/([^/]+)/)?.[1] ?? null;
}

function pageTitle(p: string): string {
  if (p === "/") return "Alion";
  if (/\/admin\/users/.test(p)) return "Users";
  if (/\/admin\/fighters/.test(p)) return "Fighters";
  if (/\/admin\/coaches/.test(p)) return "Coaches";
  if (/\/admin\/gyms/.test(p)) return "Gyms";
  if (/\/admin/.test(p)) return "Admin";
  if (/\/gym-dashboard\/members/.test(p)) return "Members";
  if (/\/gym-dashboard\/coaches/.test(p)) return "Coaches";
  if (/\/gym-dashboard\/gyms/.test(p)) return "Gyms";
  if (/\/gym-dashboard/.test(p)) return "Gym Dashboard";
  if (/\/sessions\/new/.test(p)) return "Training Session";
  if (/\/sessions\//.test(p)) return "Training Session";
  if (/\/sessions/.test(p)) return "Training Sessions";
  if (/\/hrv/.test(p)) return "HRV";
  if (/\/observations/.test(p)) return "Notes";
  if (/\/medical/.test(p)) return "Medical";
  if (/\/team/.test(p)) return "Team";
  if (/\/imu/.test(p)) return "IMU";
  if (/\/fighters\//.test(p)) return "Fighter";
  if (/\/coaches\//.test(p)) return "Coach";
  if (/\/profile/.test(p)) return "Profile";
  if (/\/compare/.test(p)) return "Compare";
  return "Alion";
}

export function MobileTopBar({
  onMenuToggle,
  menuOpen,
}: {
  onMenuToggle: () => void;
  menuOpen: boolean;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();

  const homeHref = (() => {
    if (!user) return "/";
    if (user.role === "fighter" && user.profile_id) return `/fighters/${user.profile_id}`;
    if (user.role === "coach" && user.profile_id) return `/coaches/${user.profile_id}`;
    if (user.role === "gym_manager") return "/gym-dashboard";
    if (user.role === "admin") return "/admin";
    return "/";
  })();

  const depth = pathname.split("/").filter(Boolean).length;
  const isDeep = depth > 1 && pathname !== homeHref;

  return (
    <div
      className="fixed inset-x-0 top-0 z-50 md:hidden"
      style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}
    >
      <div className="flex h-[52px] items-center gap-2 border-b border-white/[0.06] bg-[#0a0a0f]/90 px-3 backdrop-blur-xl">
        {/* Left action */}
        <div className="w-9 shrink-0">
          {isDeep && (
            <button
              onClick={() => router.back()}
              className="flex h-9 w-9 items-center justify-center rounded-full text-neutral-300 active:bg-white/10"
            >
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M12.5 5L7.5 10L12.5 15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          )}
        </div>

        {/* Title */}
        <p className="flex-1 text-center text-[15px] font-semibold tracking-tight text-white">
          {pageTitle(pathname)}
        </p>

        {/* Right action — menu */}
        <button
          onClick={onMenuToggle}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-neutral-300 active:bg-white/10"
        >
          {menuOpen ? (
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M5 5L15 15M15 5L5 15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M3 6H17M3 10H17M3 14H17" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}

type Tab = { id: string; label: string; href: string; icon: React.ReactNode };

export function MobileBottomBar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();
  const fighterId = extractFighterId(pathname);

  const homeHref = (() => {
    if (!user) return "/";
    if (user.role === "fighter" && user.profile_id) return `/fighters/${user.profile_id}`;
    if (user.role === "coach" && user.profile_id) return `/coaches/${user.profile_id}`;
    if (user.role === "gym_manager") return "/gym-dashboard";
    if (user.role === "admin") return "/admin";
    return "/";
  })();

  const tabs: Tab[] = [];

  // Home
  tabs.push({
    id: "home",
    label: "Home",
    href: homeHref,
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
        <path d="M3 10.5L12 3L21 10.5V20H16V15H8V20H3V10.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"/>
      </svg>
    ),
  });

  // Context-aware middle tabs
  if (fighterId) {
    tabs.push({
      id: "sessions",
      label: "Training",
      href: `/fighters/${fighterId}/sessions`,
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.7"/>
          <path d="M12 7.5V12L15 14" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
        </svg>
      ),
    });
    tabs.push({
      id: "hrv",
      label: "HRV",
      href: `/fighters/${fighterId}/hrv`,
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <path d="M2 12H5L8 5L12 19L16 8L19 14H22" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      ),
    });
  } else if (user?.role === "admin") {
    tabs.push({
      id: "users",
      label: "Users",
      href: "/admin/users",
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.7"/>
          <path d="M4 20c0-4.418 3.582-8 8-8s8 3.582 8 8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
        </svg>
      ),
    });
    tabs.push({
      id: "fighters",
      label: "Fighters",
      href: "/admin/fighters",
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="9" cy="8" r="3" stroke="currentColor" strokeWidth="1.7"/>
          <path d="M3 20c0-3.314 2.686-6 6-6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
          <circle cx="17" cy="10" r="3" stroke="currentColor" strokeWidth="1.7"/>
          <path d="M21 20c0-3.314-2.686-6-6-6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
        </svg>
      ),
    });
  } else if (user?.role === "gym_manager") {
    tabs.push({
      id: "members",
      label: "Members",
      href: "/gym-dashboard/members",
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="9" cy="8" r="3" stroke="currentColor" strokeWidth="1.7"/>
          <path d="M3 20c0-3.314 2.686-6 6-6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
          <circle cx="17" cy="10" r="3" stroke="currentColor" strokeWidth="1.7"/>
          <path d="M21 20c0-3.314-2.686-6-6-6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
        </svg>
      ),
    });
  }

  // Record button (coaches/fighters in fighter context)
  const canRecord = fighterId && user?.role !== "admin" && user?.role !== "gym_manager";

  // Profile always last
  tabs.push({
    id: "profile",
    label: "Profile",
    href: "/profile",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.7"/>
        <path d="M4 20c0-4.418 3.582-8 8-8s8 3.582 8 8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
      </svg>
    ),
  });

  const isActive = (href: string) => {
    if (href === homeHref) return pathname === href || pathname === homeHref;
    return pathname === href || pathname.startsWith(href + "/");
  };

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-50 md:hidden"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
    >
      <div className="flex h-[56px] items-stretch border-t border-white/[0.08] bg-[#0a0a0f]/92 backdrop-blur-xl">
        {tabs.map((tab) => {
          const active = isActive(tab.href);
          return (
            <Link
              key={tab.id}
              href={tab.href}
              className={`flex flex-1 flex-col items-center justify-center gap-[3px] transition-colors active:opacity-70 ${
                active ? "text-emerald-400" : "text-neutral-500"
              }`}
            >
              <span className={`transition-transform ${active ? "scale-110" : ""}`}>
                {tab.icon}
              </span>
              <span className={`text-[10px] font-medium ${active ? "text-emerald-400" : "text-neutral-500"}`}>
                {tab.label}
              </span>
            </Link>
          );
        })}

        {canRecord && (
          <button
            onClick={async () => {
              try {
                const s = await api.createSession(fighterId!, "live_webcam", "mediapipe");
                router.push(`/sessions/${s.id}`);
              } catch { /* ignore */ }
            }}
            className="flex flex-1 flex-col items-center justify-center gap-[3px] text-emerald-400 active:opacity-70"
          >
            <span>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.7"/>
                <path d="M12 8V16M8 12H16" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>
              </svg>
            </span>
            <span className="text-[10px] font-medium">Record</span>
          </button>
        )}
      </div>
    </nav>
  );
}
