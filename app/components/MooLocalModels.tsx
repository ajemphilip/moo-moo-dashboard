"use client";

// The models of the Moo Local in use, one dropdown for each kind. Nothing is
// downloaded until the parent picks a model. Picking another one installs
// it, checks that it runs, and then removes the one it replaces, so each
// kind keeps one model on the computer. A model too big for the computer is
// shown but cannot be picked.

import { useCallback, useEffect, useRef, useState } from "react";
import { AiSettings, localModelsApi, LocalModel, LocalModelJob, LocalModels, PiApiError } from "@/lib/api";

const KINDS: { kind: LocalModel["kind"]; title: string; none: string }[] = [
  { kind: "story", title: "Story model", none: "None yet — Moo cannot tell stories" },
  { kind: "listen", title: "Listening model", none: "None yet — Moo cannot hear the child" },
  { kind: "voice", title: "Voice model", none: "None yet — Moo cannot speak" },
];

function size(mb: number): string {
  return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
}

function short(label: string): string {
  return label.split(" — ")[0];
}

// The Moo's settings that make it use a model once it is installed.
function settingsFor(id: string): Partial<AiSettings> {
  const kind = id.split(":")[0];
  const rest = id.slice(id.indexOf(":") + 1);
  if (kind === "story") return { tier: "custom", story_model: rest };
  if (kind === "listen") return { tier: "custom", listen_model: rest };
  const engine = rest.split(":")[0];
  const sub = rest.split(":").slice(1).join(":");
  if (engine === "piper") return { tier: "custom", voice_engine: "piper", voice: sub };
  if (sub) return { tier: "custom", voice_engine: engine, voice: null, voice_qualities: { [engine]: sub } };
  return { tier: "custom", voice_engine: engine, voice: null };
}

// The model of each kind that the Moo uses now, as a catalogue id.
function inUse(kind: string, s: AiSettings | null | undefined): string | null {
  if (!s) return null;
  if (kind === "story") return s.story_model ? `story:${s.story_model.replace(/:latest$/, "")}` : null;
  if (kind === "listen") return s.listen_model ? `listen:${s.listen_model}` : null;
  const e = s.voice_engine;
  if (!e) return null;
  if (e === "piper") return `voice:piper:${s.voice ?? ""}`;
  const q = s.voice_qualities?.[e];
  return q ? `voice:${e}:${q}` : `voice:${e}`;
}

export function MooLocalModels({
  settings,
  onChanged,
  onChosen,
}: {
  settings?: AiSettings | null;
  onChanged?: () => void;
  onChosen?: (changes: Partial<AiSettings>) => unknown;
}) {
  const [data, setData] = useState<LocalModels | null>(null);
  const [job, setJob] = useState<LocalModelJob | null>(null);
  const [error, setError] = useState<{ kind: string; text: string } | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const chosen = useRef(onChosen);
  chosen.current = onChosen;

  const load = useCallback(() => {
    localModelsApi
      .list()
      .then((d) => {
        setData(d);
        setJob(d.job);
      })
      .catch((e) => setError({ kind: "", text: e instanceof PiApiError ? e.message : "The models could not be read." }));
  }, []);

  useEffect(load, [load]);

  // While a download runs, follow it; when it ends, read the list again and
  // switch the Moo to the new model.
  useEffect(() => {
    if (job?.status !== "running") return;
    const t = setInterval(() => {
      localModelsApi
        .job()
        .then(({ job: j }) => {
          setJob(j);
          if (j && j.status !== "running") {
            load();
            if (j.status === "done") Promise.resolve(chosen.current?.(settingsFor(j.id))).finally(() => changed.current?.());
            else changed.current?.();
          }
        })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [job?.status, load]);

  const pick = async (kind: string, id: string, installed: LocalModel[]) => {
    setError(null);
    if (installed.some((m) => m.id === id)) {
      // Already on this computer: only switch the Moo to it.
      await chosen.current?.(settingsFor(id));
      return;
    }
    try {
      setJob((await localModelsApi.install(id, installed.map((m) => m.id))).job);
    } catch (e) {
      setError({ kind, text: e instanceof PiApiError ? e.message : "It could not be installed." });
    }
  };

  if (!data) {
    return error ? <p className="text-sm text-red-600">{error.text}</p> : null;
  }

  const running = job?.status === "running";

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-moo-ink/70">
        Models on {data.computer}: {size(data.memory_mb)} of memory, {size(data.disk_free_mb)} free. Picking a model
        downloads it and removes the one it replaces. Nothing downloads until you pick.
      </p>
      {KINDS.map(({ kind, title, none }) => {
        const list = data.models.filter((m) => m.kind === kind);
        if (list.length === 0) return null;
        const installed = list.filter((m) => m.installed);
        const mine = job && (job.kind === kind || list.some((m) => m.id === job.id)) ? job : null;
        const used = inUse(kind, settings);
        const value =
          mine?.status === "running" ? mine.id : installed.find((m) => m.id === used)?.id ?? installed[0]?.id ?? "";
        return (
          <label key={kind} className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{title}</span>
            <select
              className="moo-input"
              value={value}
              disabled={running}
              onChange={(e) => e.target.value && pick(kind, e.target.value, installed)}
            >
              {installed.length === 0 && <option value="">{none}</option>}
              {list.map((m) => (
                <option key={m.id} value={m.id} disabled={!m.fits && !m.installed}>
                  {m.label} · {size(m.size_mb)}, needs {size(m.needs_mb)}
                  {!m.fits ? " · too big" : ""}
                </option>
              ))}
            </select>
            {installed.length > 1 && (
              <span className="text-xs text-moo-ink/60">
                Also on this computer: {installed.slice(1).map((m) => short(m.label)).join(", ")}
              </span>
            )}
            {mine && mine.status === "running" && (
              <span className="flex flex-col gap-1 text-xs text-moo-ink/70" aria-live="polite">
                {mine.phase === "checking"
                  ? mine.message
                  : `Downloading ${mine.label}: ${size(mine.done_mb)} of ${size(mine.total_mb)}`}
                <span className="h-1.5 rounded-full bg-moo-ink/10">
                  <span
                    className="block h-1.5 rounded-full bg-moo-accent"
                    style={{ width: `${Math.min(100, Math.round((100 * mine.done_mb) / Math.max(1, mine.total_mb)))}%` }}
                  />
                </span>
              </span>
            )}
            {mine && mine.status !== "running" && mine.message && (
              <span className={`text-xs ${mine.status === "error" ? "text-red-600" : "text-emerald-700"}`}>{mine.message}</span>
            )}
            {error?.kind === kind && <span className="text-xs text-red-600">{error.text}</span>}
          </label>
        );
      })}
      {error && !error.kind && <p className="text-xs text-red-600">{error.text}</p>}
    </div>
  );
}
