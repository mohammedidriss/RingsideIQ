"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { HrvScoreScatter } from "@/components/AggregateCharts";
import { HrvMetric, ReadinessGauge, RmssdTrend } from "@/components/HrvCharts";
import { getPairedDevice } from "@/components/PolarH10Card";
import {
  api,
  type FighterReadiness,
  type MatrixResponse,
  type Session,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";


export default function HrvTab({ params }: { params: { id: string } }) {
  const { user } = useAuth();
  if (user?.role === "admin") {
    return (
      <div className="space-y-4 px-4 py-8 sm:px-8 sm:py-12">
        <div className="text-4xl">🔒</div>
        <h1 className="text-xl font-semibold">Access Restricted</h1>
        <p className="max-w-md text-sm text-neutral-400">
          HRV and biometric data is confidential. System administrators manage
          accounts and general information only.
        </p>
      </div>
    );
  }
  const [sessions, setSessions] = useState<Session[]>([]);
  const [matrix, setMatrix] = useState<MatrixResponse | null>(null);
  const [readiness, setReadiness] = useState<FighterReadiness | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.listSessions(params.id),
      api.fighterMatrix(params.id),
      api.fighterReadiness(params.id),
    ])
      .then(([s, m, r]) => {
        setSessions(s);
        setMatrix(m);
        setReadiness(r);
      })
      .catch((e) => setErr(String(e)));
  }, [params.id]);

  const baselined = sessions
    .filter((s) => s.baseline_rmssd_ms != null)
    .sort((a, b) => a.started_at.localeCompare(b.started_at));

  if (err)
    return <p className="text-sm text-red-400">{err}</p>;

  if (baselined.length === 0) {
    return (
      <div className="space-y-6 px-4 py-5 sm:px-8 sm:py-6">
        <header>
          <h1 className="text-2xl font-semibold">HRV</h1>
          <p className="text-sm text-neutral-400">
            Heart-rate variability — resting baselines and correlation with
            performance.
          </p>
        </header>
        <PolarH10Config />
        <div className="card text-sm text-neutral-400">
          <p className="font-medium text-neutral-200">
            No HRV baseline recordings yet
          </p>
          <p className="mt-2 text-neutral-500">
            Open a session in <em>pending</em> status and upload a 5-min
            resting RR-interval CSV (single column{" "}
            <code className="rounded bg-black/30 px-1">rr_ms</code> or two
            columns{" "}
            <code className="rounded bg-black/30 px-1">t_ms,rr_ms</code>) to
            populate this tab.
          </p>
          <Link
            href={`/fighters/${params.id}/sessions`}
            className="mt-3 inline-block text-emerald-400 hover:underline"
          >
            View sessions →
          </Link>
        </div>
      </div>
    );
  }

  const latest = baselined[baselined.length - 1];
  const rmssd = latest.baseline_rmssd_ms!;
  // Use defensible per-fighter z-score readiness when available; the
  // legacy absolute remap is a clearly-flagged cold-start fallback.
  const readinessScore = readiness?.score ?? Math.round(
    Math.max(0, Math.min(1, (rmssd - 20) / 70)) * 100,
  );
  const readinessMode = readiness?.mode ?? "absolute";

  const r = matrix?.pearson_r ?? null;
  const n = matrix?.points.length ?? 0;
  // Don't slap a "very strong" label on n=3. With small samples the
  // confidence interval on r is enormous; only show a strength descriptor
  // when the sample is big enough that the label is statistically defensible.
  const STRENGTH_MIN_N = 10;
  const correlationStrength =
    r == null
      ? "—"
      : n < STRENGTH_MIN_N
        ? `n=${n} (need ${STRENGTH_MIN_N}+ for strength label)`
        : Math.abs(r) < 0.1
          ? "no correlation"
          : Math.abs(r) < 0.3
            ? "weak"
            : Math.abs(r) < 0.5
              ? "moderate"
              : Math.abs(r) < 0.7
                ? "strong"
                : "very strong";
  // Fisher z-transform 95% CI for Pearson's r — communicates uncertainty
  // honestly when the sample is small.
  const ciOf = (rho: number, k: number): [number, number] | null => {
    if (k < 4) return null;
    const z = 0.5 * Math.log((1 + rho) / (1 - rho));
    const se = 1 / Math.sqrt(k - 3);
    const lo = z - 1.96 * se;
    const hi = z + 1.96 * se;
    return [
      (Math.exp(2 * lo) - 1) / (Math.exp(2 * lo) + 1),
      (Math.exp(2 * hi) - 1) / (Math.exp(2 * hi) + 1),
    ];
  };
  const ci = r != null ? ciOf(r, n) : null;

  return (
    <div className="space-y-6 px-4 py-5 sm:px-8 sm:py-6">
      <header>
        <h1 className="text-2xl font-semibold">HRV</h1>
        <p className="text-sm text-neutral-400">
          {baselined.length} session{baselined.length === 1 ? "" : "s"} with
          recorded resting baseline.
        </p>
      </header>

      <PolarH10Config />

      {/* HEADLINE STATS — most important numbers, scannable */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <HeadlineStat
          label="Latest readiness"
          value={readinessScore.toString()}
          unit={readinessMode === "z_score" ? "/ 100 · calibrated" : "/ 100"}
          tone={
            readinessScore >= 75
              ? "lime"
              : readinessScore >= 55
                ? "yellow"
                : readinessScore >= 35
                  ? "orange"
                  : "red"
          }
        />
        <HeadlineStat
          label="Latest RMSSD"
          value={rmssd.toFixed(0)}
          unit="ms"
          tone="purple"
        />
        <HeadlineStat
          label="Resting HR"
          value={latest.baseline_mean_hr_bpm?.toFixed(0) ?? "—"}
          unit="bpm"
          tone="orange"
        />
        <HeadlineStat
          label="HRV vs Score r"
          value={r != null ? r.toFixed(2) : "—"}
          unit={correlationStrength}
          tone={
            r != null && n >= STRENGTH_MIN_N && Math.abs(r) >= 0.5
              ? "lime"
              : "neutral"
          }
        />
      </div>

      {readiness && readiness.mode === "absolute" && (
        <div className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
          Readiness is using the cold-start absolute remap. After{" "}
          {readiness.min_history_required} recorded baselines (currently{" "}
          {readiness.history_n}), the score switches to a per-fighter
          z-score against this fighter&apos;s own RMSSD history — far more
          defensible. Keep recording resting HRV before sessions.
        </div>
      )}

      {/* PRIMARY CHARTS — readiness gauge + trend, both clearly visible */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card">
          <div className="flex items-baseline justify-between">
            <h2 className="text-base font-semibold">Readiness gauge</h2>
            <span className="text-[10px] uppercase tracking-wider text-neutral-500">
              RMSSD-based
            </span>
          </div>
          <div className="mt-2 flex justify-center">
            <ReadinessGauge value={readinessScore} />
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-center text-xs">
            <HrvMetric label="RMSSD" value={rmssd.toFixed(1)} unit="ms" />
            <HrvMetric
              label="SDNN"
              value={latest.baseline_sdnn_ms?.toFixed(1)}
              unit="ms"
            />
            <HrvMetric
              label="Mean HR"
              value={latest.baseline_mean_hr_bpm?.toFixed(0)}
              unit="bpm"
            />
          </div>
          <p className="mt-3 text-[11px] text-neutral-500">
            {readinessMode === "z_score" && readiness ? (
              <>
                Per-fighter z-score: today&apos;s RMSSD vs this
                fighter&apos;s rolling baseline (n={readiness.history_n},
                mean {readiness.baseline_mean_ms?.toFixed(1)} ms ± SD{" "}
                {readiness.baseline_sd_ms?.toFixed(1)} ms · z={readiness.z}).
              </>
            ) : (
              <>
                Cold-start formula: clamp((RMSSD − 20)/70). Universal remap;
                will switch to per-fighter z-score after enough baselines.
              </>
            )}
          </p>
        </div>

        <div className="card lg:col-span-2">
          <h2 className="text-base font-semibold">RMSSD trend</h2>
          <RmssdTrend sessions={baselined} />
          <p className="mt-2 text-[11px] text-neutral-500">
            Recovery trend across recorded baselines. Drops 7+ ms over a week
            often flag overtraining.
          </p>
        </div>
      </div>

      {/* CORRELATION SCATTER — readiness vs performance, inline */}
      {matrix && matrix.points.length > 0 && (
        <div className="card">
          <h2 className="text-base font-semibold">
            Resting HRV vs performance score
          </h2>
          <p className="mt-1 text-[11px] text-neutral-500">
            Each dot is one session. Higher RMSSD (right) = better recovery;
            higher score (up) = busier + faster session. Older sessions are
            grey, recent ones amber.
          </p>
          <div className="mt-3">
            <HrvScoreScatter
              points={matrix.points}
              pearson_r={matrix.pearson_r}
              slope={matrix.slope}
              intercept={matrix.intercept}
            />
          </div>
          {r != null && (
            <p className="mt-3 text-[11px] text-neutral-400">
              Pearson r = {r.toFixed(2)} on n={n}.{" "}
              {ci ? (
                <>
                  95% CI [{ci[0].toFixed(2)}, {ci[1].toFixed(2)}] (Fisher z).
                </>
              ) : (
                <>Confidence interval requires n ≥ 4.</>
              )}{" "}
              {n < STRENGTH_MIN_N && (
                <span className="text-amber-300">
                  Sample is small ({n} sessions); the strength label is
                  withheld until n ≥ {STRENGTH_MIN_N} so it isn&apos;t
                  overclaimed.
                </span>
              )}
            </p>
          )}
        </div>
      )}

      <div className="card">
        <h2 className="text-base font-semibold">Session baselines</h2>
        <p className="mt-1 text-xs text-neutral-500">
          Every recorded resting baseline, newest first. Click a row to open
          the session.
        </p>
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-xs uppercase text-neutral-500">
            <tr>
              <th className="py-1">Date</th>
              <th>RMSSD (ms)</th>
              <th>SDNN (ms)</th>
              <th>Mean HR</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {[...baselined].reverse().map((s) => (
              <tr key={s.id} className="border-t border-white/5">
                <td className="py-2">
                  <Link
                    href={`/sessions/${s.id}`}
                    className="text-emerald-400 hover:underline"
                  >
                    {new Date(s.started_at).toLocaleString()}
                  </Link>
                </td>
                <td className="font-mono">{s.baseline_rmssd_ms!.toFixed(1)}</td>
                <td className="font-mono">
                  {s.baseline_sdnn_ms?.toFixed(1) ?? "—"}
                </td>
                <td className="font-mono">
                  {s.baseline_mean_hr_bpm?.toFixed(0) ?? "—"}
                </td>
                <td className="text-neutral-500">{s.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

    </div>
  );
}

type BleDevice = { name: string; address: string };
const LS_KEY_ADDR = "polar_h10_address";
const LS_KEY_NAME = "polar_h10_name";

function PolarH10Config() {
  const [paired, setPaired] = useState<BleDevice | null>(null);
  const [devices, setDevices] = useState<BleDevice[]>([]);
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setPaired(getPairedDevice());
  }, []);

  const scan = async () => {
    setScanning(true);
    setErr(null);
    setDevices([]);
    try {
      const res = await api.scanBleDevices();
      setDevices(res.devices);
      if (res.devices.length === 1) pair(res.devices[0]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  };

  const pair = (d: BleDevice) => {
    localStorage.setItem(LS_KEY_ADDR, d.address);
    localStorage.setItem(LS_KEY_NAME, d.name);
    setPaired(d);
    setDevices([]);
  };

  const unpair = () => {
    localStorage.removeItem(LS_KEY_ADDR);
    localStorage.removeItem(LS_KEY_NAME);
    setPaired(null);
    setDevices([]);
  };

  return (
    <div className="card space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold">Polar H10</span>
          <span className="rounded-full bg-blue-900/50 px-2 py-0.5 text-[10px] font-medium text-blue-300">BLE</span>
        </div>
        {paired && (
          <span className="flex items-center gap-1.5 text-xs text-emerald-400">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
            Connected
          </span>
        )}
      </div>

      {/* Device status */}
      {paired ? (
        <div className="flex items-center justify-between rounded-xl border border-emerald-800/40 bg-emerald-950/20 px-4 py-3">
          <div>
            <p className="text-sm font-medium text-emerald-300">{paired.name}</p>
            <p className="mt-0.5 font-mono text-[11px] text-neutral-500">{paired.address}</p>
          </div>
          <button
            onClick={unpair}
            className="rounded-lg border border-red-800/40 bg-red-950/30 px-3 py-1.5 text-xs text-red-400 hover:bg-red-900/40 hover:text-red-300"
          >
            Unpair
          </button>
        </div>
      ) : (
        <div className="rounded-xl border border-neutral-800 bg-neutral-950/40 px-4 py-3">
          <p className="text-sm text-neutral-400">No device paired. Scan to find nearby Polar H10 devices.</p>
        </div>
      )}

      {/* Scan button + results */}
      <div className="space-y-3">
        <button
          onClick={scan}
          disabled={scanning}
          className="flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
        >
          {scanning ? (
            <>
              <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
              Scanning for devices…
            </>
          ) : (
            <>
              <span>⊕</span> Scan for Polar H10
            </>
          )}
        </button>

        {err && (
          <p className="rounded-lg border border-red-700/40 bg-red-950/30 px-3 py-2 text-xs text-red-300">
            {err}
          </p>
        )}

        {devices.length > 1 && (
          <div className="space-y-2">
            <p className="text-xs text-neutral-500">Multiple devices found — select one to pair:</p>
            {devices.map((d) => (
              <button
                key={d.address}
                onClick={() => pair(d)}
                className="flex w-full items-center justify-between rounded-xl border border-neutral-700 bg-neutral-900 px-4 py-2.5 text-sm hover:border-blue-500 hover:bg-blue-950/20"
              >
                <span className="font-medium">{d.name}</span>
                <span className="font-mono text-xs text-neutral-500">{d.address}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Capabilities */}
      <div className="space-y-2 border-t border-white/5 pt-4">
        <p className="text-xs font-medium uppercase tracking-wider text-neutral-500">Device capabilities</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {[
            { icon: "♥", label: "Live HR streaming", desc: "Real-time heart rate during capture via BLE" },
            { icon: "〜", label: "RR intervals", desc: "Beat-to-beat intervals for RMSSD / SDNN calculation" },
            { icon: "📊", label: "Inter-round recovery", desc: "HR recovery curves between rounds" },
            { icon: "📈", label: "Day-over-day RMSSD", desc: "Drift tracking to flag overtraining" },
            { icon: "🔗", label: "Auto-start with capture", desc: "BLE stream starts automatically when a session begins" },
            { icon: "☁", label: "HRV replay upload", desc: "Upload a saved CSV to analyse without the device" },
          ].map((c) => (
            <div key={c.label} className="flex items-start gap-3 rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2.5">
              <span className="mt-0.5 text-base">{c.icon}</span>
              <div>
                <p className="text-xs font-medium text-neutral-200">{c.label}</p>
                <p className="mt-0.5 text-[11px] text-neutral-500">{c.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Setup instructions */}
      <div className="rounded-xl border border-white/5 bg-white/[0.02] px-4 py-3 text-xs text-neutral-500 space-y-1">
        <p className="font-medium text-neutral-300">Setup instructions</p>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>Wet the Polar H10 electrodes and strap to chest.</li>
          <li>Make sure Bluetooth is enabled on this device.</li>
          <li>Click <span className="text-white">Scan for Polar H10</span> above — the strap is discoverable for ~5 min after putting it on.</li>
          <li>Select your device from the list to pair it.</li>
          <li>Start a training session — BLE streaming begins automatically with capture.</li>
        </ol>
      </div>
    </div>
  );
}

function HeadlineStat({
  label,
  value,
  unit,
  tone,
}: {
  label: string;
  value: string;
  unit: string;
  tone: "lime" | "yellow" | "orange" | "red" | "purple" | "neutral";
}) {
  const valColor = {
    lime: "text-lime-300",
    yellow: "text-yellow-300",
    orange: "text-orange-300",
    red: "text-red-300",
    purple: "text-violet-300",
    neutral: "text-neutral-200",
  }[tone];
  return (
    <div className="rounded-2xl border border-white/5 bg-white/[0.02] p-3">
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className={`text-2xl font-semibold tabular-nums ${valColor}`}>
          {value}
        </span>
        <span className="text-xs text-neutral-500">{unit}</span>
      </div>
    </div>
  );
}
