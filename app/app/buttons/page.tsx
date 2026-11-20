"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError, type ButtonCheckState } from "@/lib/api";

// Same layout as calibration: dot positions 0-5 shown as 1-6,
//   1 4
//   2 5
//   3 6
const DOT_GRID: number[][] = [
  [0, 3],
  [1, 4],
  [2, 5],
];

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

export default function ButtonsPage() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<ButtonCheckState | null>(null);
  const [flash, setFlash] = useState<number | null>(null);

  // Open the check on mount, close it on unmount.
  useEffect(() => {
    let cancelled = false;
    api
      .buttonsStart()
      .then((s) => {
        if (cancelled) return;
        setState(s);
        setReady(true);
      })
      .catch((e) => {
        if (!cancelled) setError(errMsg(e));
      });
    return () => {
      cancelled = true;
      api.buttonsStop().catch(() => {});
    };
  }, []);

  // Poll so every press on the Moo shows up within a fraction of a second.
  useEffect(() => {
    if (!ready) return;
    const id = setInterval(() => {
      api
        .buttonsState()
        .then((s) => {
          setState((prev) => {
            const before = prev?.counts.reduce((a, b) => a + b, 0) ?? 0;
            const after = s.counts.reduce((a, b) => a + b, 0);
            if (after > before && s.last !== null) setFlash(s.last);
            return s;
          });
        })
        .catch(() => {});
    }, 300);
    return () => clearInterval(id);
  }, [ready]);

  // Let a lit dot fade after a moment.
  useEffect(() => {
    if (flash === null) return;
    const id = setTimeout(() => setFlash(null), 600);
    return () => clearTimeout(id);
  }, [flash, state]);

  const restart = async () => {
    setError(null);
    try {
      await api.buttonsStop();
      setState(await api.buttonsStart());
    } catch (e) {
      setError(errMsg(e));
    }
  };

  if (!ready && !error) {
    return (
      <StepLayout title="Button check" subtitle="Waking up the Braille cell…">
        <Spinner label="Connecting to Moo…" />
      </StepLayout>
    );
  }

  const total = state?.counts.reduce((a, b) => a + b, 0) ?? 0;

  return (
    <StepLayout
      title="Button check"
      subtitle="Press each dot on the Moo, for example five times. Every click that reaches Moo lights up and is counted here."
      back="/dashboard"
    >
      <div className="moo-card flex flex-col items-center gap-4 py-6">
        <div className="grid grid-rows-3 grid-flow-col gap-x-8 gap-y-3">
          {DOT_GRID.flatMap((row) =>
            row.map((dot) => {
              const lit = flash === dot;
              const count = state?.counts[dot] ?? 0;
              return (
                <div key={dot} className="flex flex-col items-center gap-1">
                  <div
                    className={`w-16 h-16 rounded-full border-2 flex items-center justify-center text-xl font-semibold transition ${
                      lit
                        ? "bg-moo-accent text-white border-moo-accent scale-110"
                        : count > 0
                          ? "bg-white border-moo-accent/60"
                          : "bg-white border-moo-ink/20"
                    }`}
                    aria-label={`Dot ${dot + 1}, pressed ${count} times`}
                  >
                    {dot + 1}
                  </div>
                  <span className="text-sm text-moo-ink/70">{count}×</span>
                </div>
              );
            })
          )}
        </div>
        <p className="text-sm text-moo-ink/60">{total} clicks counted</p>
        {state?.dry_run && (
          <p className="text-xs text-amber-700">Dry-run — no Braille hardware detected.</p>
        )}
      </div>

      <p className="text-sm text-moo-ink/60 -mt-1">
        If you clicked a dot five times and it shows fewer, some clicks are not
        reaching Moo. If it shows more, the button bounces. The dots do not move
        during this check.
      </p>

      {state && state.recent.length > 0 && (
        <div className="moo-card text-sm text-moo-ink/70">
          <div className="font-semibold mb-1">Latest clicks</div>
          {state.recent.slice(0, 6).map((p, i) => (
            <div key={i}>
              Dot {p.button + 1} — {p.seconds_ago}s ago
            </div>
          ))}
        </div>
      )}

      {error && <p className="text-red-600 text-sm">{error}</p>}

      <div className="mt-auto flex flex-col gap-2">
        <Button variant="secondary" onClick={restart}>
          Start counting again
        </Button>
        <Button href="/dashboard" variant="secondary">
          Done
        </Button>
      </div>
    </StepLayout>
  );
}
