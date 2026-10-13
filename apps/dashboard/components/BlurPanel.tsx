"use client";

/**
 * Face blurring for the export (ADR-014): the background job's progress, and each
 * clip's state with its blurred close-ups to check. Exported video is only ever
 * the blurred copy (faces pixelated, no sound, no metadata); a clip that isn't
 * ready is left out. `onFinished` fires when a running job ends, so the page can
 * export again and take the new videos in.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type BlurStatus } from "@/lib/api";

export function BlurPanel({
  datasetId,
  watch,
  onFinished,
}: {
  datasetId: string;
  watch: number; // bump to re-read the status (e.g. after an export started a job)
  onFinished: () => void;
}) {
  const [st, setSt] = useState<BlurStatus | null>(null);
  const [open, setOpen] = useState<string | null>(null); // "take/camera" being viewed
  const [img, setImg] = useState<string | null>(null);
  const wasRunning = useRef(false);

  const load = useCallback(
    () =>
      api
        .blurStatus(datasetId)
        .then((s) => {
          setSt(s);
          if (wasRunning.current && !s.running) onFinished();
          wasRunning.current = s.running;
        })
        .catch(() => {}),
    [datasetId, onFinished],
  );

  useEffect(() => {
    load();
  }, [load, watch]);

  useEffect(() => {
    if (!st?.running) return;
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, [st?.running, load]);

  useEffect(() => () => (img ? URL.revokeObjectURL(img) : undefined), [img]);

  const view = async (takeId: string, camera: string) => {
    const key = `${takeId}/${camera}`;
    if (open === key) {
      setOpen(null);
      return;
    }
    setOpen(key);
    setImg(await api.blurCheckUrl(datasetId, takeId, camera));
  };

  if (!st || st.clips.length === 0) return null;
  const { ready, pending, failed } = st.counts;

  return (
    <div className="mt-3 rounded-xl border border-white/10 bg-black/30 p-3 text-xs sm:w-[30rem]">
      <div className="font-medium text-neutral-200">Face-blurred video</div>
      {st.running ? (
        <p className="mt-1 animate-pulse text-violet-300">
          Blurring {Math.min(st.done + 1, st.total)} of {st.total}
          {st.current ? ` (take ${st.current})` : ""} — about twice as fast as the video plays;
          you can leave the page. The export updates itself when it&apos;s done.
        </p>
      ) : (
        <p className="mt-1 text-neutral-400">
          {ready} of {st.clips.length} clips blurred
          {pending > 0 && ` · ${pending} waiting`}
          {failed > 0 && ` · ${failed} couldn't be blurred safely (left out)`}. Only blurred copies
          go into the export — faces pixelated, no sound, no metadata.
        </p>
      )}
      {st.error && <p className="mt-1 text-red-300">Blurring stopped: {st.error}</p>}
      {!st.running && pending > 0 && (
        <button
          onClick={() => api.startBlur(datasetId).then(setSt).catch(() => {})}
          className="mt-2 rounded-lg border border-violet-400/40 px-2 py-1 text-violet-200 hover:bg-violet-500/10"
        >
          Blur the {pending} waiting
        </button>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-neutral-400 hover:text-neutral-200">
          Check the close-ups
        </summary>
        <ul className="mt-2 space-y-1">
          {st.clips.map((c) => {
            const key = `${c.take_id}/${c.camera}`;
            return (
              <li key={key}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-neutral-400">
                    {c.take_id.slice(0, 8)} · {c.camera}
                  </span>
                  <span
                    className={
                      c.status === "ready"
                        ? "text-emerald-300"
                        : c.status === "failed"
                          ? "text-red-300"
                          : "text-neutral-500"
                    }
                  >
                    {c.status === "ready"
                      ? c.stats && c.stats.whole_frame > 0
                        ? `blurred · ${c.stats.whole_frame} frame(s) with no face found, blurred whole`
                        : "blurred"
                      : c.status === "failed"
                        ? `left out — ${c.error}`
                        : "waiting"}
                  </span>
                  {c.has_check && (
                    <button onClick={() => view(c.take_id, c.camera)} className="text-violet-300 hover:underline">
                      {open === key ? "hide" : "view"}
                    </button>
                  )}
                </div>
                {open === key && img && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={img} alt={`Blurred close-ups, ${c.camera}`} className="mt-1 w-full rounded" />
                )}
              </li>
            );
          })}
        </ul>
      </details>
    </div>
  );
}
