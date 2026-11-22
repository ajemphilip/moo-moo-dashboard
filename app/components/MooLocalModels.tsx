"use client";

// The models of the Moo Local in use: nothing is downloaded until the parent
// chooses it here. Moo Local refuses a model too big for its computer and
// names a smaller one; this list shows that before anyone taps Install.

import { useCallback, useEffect, useRef, useState } from "react";
import { localModelsApi, LocalModel, LocalModelJob, LocalModels, PiApiError } from "@/lib/api";

const KINDS: { kind: LocalModel["kind"]; title: string; empty: string }[] = [
  { kind: "story", title: "Story", empty: "No story model yet: Moo cannot tell stories with this computer." },
  { kind: "listen", title: "Listening", empty: "No listening model yet: Moo cannot hear the child with this computer." },
  { kind: "voice", title: "Voice", empty: "No voice yet: Moo cannot speak with this computer." },
];

function size(mb: number): string {
  return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
}

export function MooLocalModels({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<LocalModels | null>(null);
  const [job, setJob] = useState<LocalModelJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<LocalModel["kind"] | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;

  const load = useCallback(() => {
    localModelsApi
      .list()
      .then((d) => {
        setData(d);
        setJob(d.job);
        setError(null);
        // Open the first kind that has nothing installed, so the parent sees what is missing.
        setOpen((o) => o ?? KINDS.find((k) => !d.models.some((m) => m.kind === k.kind && m.installed))?.kind ?? null);
      })
      .catch((e) => setError(e instanceof PiApiError ? e.message : "The models could not be read."));
  }, []);

  useEffect(load, [load]);

  // While a download runs, follow it; when it ends, read the list again.
  useEffect(() => {
    if (job?.status !== "running") return;
    const t = setInterval(() => {
      localModelsApi
        .job()
        .then(({ job: j }) => {
          setJob(j);
          if (j && j.status !== "running") {
            load();
            changed.current?.();
          }
        })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [job?.status, load]);

  const install = async (m: LocalModel) => {
    setError(null);
    setBusy(m.id);
    try {
      setJob((await localModelsApi.install(m.id)).job);
    } catch (e) {
      setError(e instanceof PiApiError ? e.message : "It could not be installed.");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (m: LocalModel) => {
    if (!window.confirm(`Remove ${m.label.split(" — ")[0]} from ${data?.computer ?? "this computer"}? It can be installed again later.`)) return;
    setError(null);
    setBusy(m.id);
    try {
      await localModelsApi.remove(m.id);
      load();
      onChanged?.();
    } catch (e) {
      setError(e instanceof PiApiError ? e.message : "It could not be removed.");
    } finally {
      setBusy(null);
    }
  };

  if (!data) {
    return error ? <p className="text-sm text-red-600">{error}</p> : null;
  }

  const running = job?.status === "running";

  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-medium">Models</h3>
      <p className="text-xs text-moo-ink/70">
        {data.computer} has {size(data.memory_mb)} of memory and {size(data.disk_free_mb)} free. Nothing is downloaded
        until you choose it. A model too big for this computer cannot be installed.
      </p>

      {job && (
        <div className="rounded-moo bg-moo-ink/5 p-2 text-sm" aria-live="polite">
          {running ? (
            <>
              <div>
                Installing {job.label}: {size(job.done_mb)} of {size(job.total_mb)}
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-moo-ink/10">
                <div
                  className="h-1.5 rounded-full bg-moo-accent"
                  style={{ width: `${Math.min(100, Math.round((100 * job.done_mb) / Math.max(1, job.total_mb)))}%` }}
                />
              </div>
            </>
          ) : (
            <div className={job.status === "error" ? "text-red-600" : "text-emerald-700"}>{job.message}</div>
          )}
        </div>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {KINDS.map(({ kind, title, empty }) => {
        const list = data.models.filter((m) => m.kind === kind);
        if (list.length === 0) return null;
        const have = list.filter((m) => m.installed);
        return (
          <details
            key={kind}
            open={open === kind}
            onToggle={(e) => {
              const isOpen = (e.target as HTMLDetailsElement).open;
              setOpen((o) => (isOpen ? kind : o === kind ? null : o));
            }}
            className="rounded-moo border border-moo-ink/10 p-2"
          >
            <summary className="cursor-pointer text-sm font-medium">
              {title}: {have.length > 0 ? `${have.length} installed` : "none installed"}
            </summary>
            {have.length === 0 && <p className="mt-1 text-xs text-moo-ink/70">{empty}</p>}
            {kind === "story" && !data.ollama && (
              <p className="mt-1 text-xs text-red-600">Ollama is not running on this computer, so story models cannot be installed.</p>
            )}
            <ul className="mt-2 flex flex-col gap-2">
              {list.map((m) => (
                <li key={m.id} className="flex items-start justify-between gap-2 text-sm">
                  <div className="min-w-0">
                    <div className={m.fits || m.installed ? "" : "text-moo-ink/50"}>{m.label}</div>
                    <div className="text-xs text-moo-ink/60">
                      {size(m.size_mb)} download, needs about {size(m.needs_mb)} of memory
                      {m.installed ? " · installed" : ""}
                    </div>
                    {!m.fits && <div className="text-xs text-red-600">{m.too_big}</div>}
                  </div>
                  {m.installed ? (
                    <button
                      type="button"
                      className="moo-button-secondary shrink-0 px-3 py-1 text-xs"
                      onClick={() => remove(m)}
                      disabled={busy !== null || (running && job?.id === m.id)}
                    >
                      Remove
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="moo-button-secondary shrink-0 px-3 py-1 text-xs"
                      onClick={() => install(m)}
                      disabled={busy !== null || running || !m.fits}
                      title={m.fits ? undefined : m.too_big ?? undefined}
                    >
                      {running && job?.id === m.id ? "Installing…" : "Install"}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}
