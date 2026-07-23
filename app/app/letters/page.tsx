"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError, type LetterState } from "@/lib/api";

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

const PHASE_LABEL: Record<LetterState["phase"], string> = {
  idle: "Feel the letter, then press the buttons to clear it",
  showing: "Raising the dots…",
  holding: "Feel the letter!",
  lowering: "Lowering the dots…",
};

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

export default function LettersPage() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<LetterState | null>(null);

  // Open the exercise session on mount, close it on unmount.
  useEffect(() => {
    let cancelled = false;
    api
      .lettersStart()
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
      api.lettersStop().catch(() => {});
    };
  }, []);

  // Poll the cell state so the phone follows the raise/hold/lower cycle
  // and the repeat counter (the child pressing a button on the Moo).
  useEffect(() => {
    if (!ready) return;
    const id = setInterval(() => {
      api.lettersState().then(setState).catch(() => {});
    }, 700);
    return () => clearInterval(id);
  }, [ready]);

  const busy = !!state && state.phase !== "idle";

  const pick = async (letter: string) => {
    if (busy) return;
    setError(null);
    try {
      setState(await api.lettersShow(letter));
    } catch (e) {
      setError(errMsg(e));
    }
  };

  if (!ready && !error) {
    return (
      <StepLayout title="Letter practice" subtitle="Waking up the Braille cell…">
        <Spinner label="Connecting to Moo…" />
      </StepLayout>
    );
  }

  return (
    <StepLayout
      title="Letter practice"
      subtitle="Tap a letter — Moo raises its dots and holds them up for your child to feel."
      back="/dashboard"
    >
      {/* Current letter / phase */}
      <div className="moo-card text-center py-6">
        {state?.letter ? (
          <>
            <div className="text-7xl font-bold text-moo-accent leading-none">
              {state.letter}
            </div>
            <p className="mt-3 text-moo-ink/70">
              {PHASE_LABEL[state.phase]}
            </p>
            {state.repeats > 0 && (
              <p className="mt-1 text-sm text-emerald-700">
                Repeated {state.repeats}×
              </p>
            )}
          </>
        ) : (
          <p className="text-moo-ink/60 py-6">Pick a letter below to begin.</p>
        )}
        {state?.dry_run && (
          <p className="mt-2 text-xs text-amber-700">
            Dry-run — no Braille hardware detected.
          </p>
        )}
      </div>

      <p className="text-sm text-moo-ink/60 -mt-1">
        The dots stay raised — no timer. Your child feels the letter, then
        presses the <strong>buttons on the Moo</strong> to lower the dots one
        by one and clear it.
      </p>

      {/* A–Z grid */}
      <div className="grid grid-cols-5 gap-2">
        {ALPHABET.map((ch) => {
          const active = state?.letter === ch;
          return (
            <button
              key={ch}
              type="button"
              disabled={busy}
              onClick={() => pick(ch)}
              className={`aspect-square rounded-moo border text-xl font-semibold transition disabled:opacity-40 ${
                active
                  ? "bg-moo-accent text-white border-moo-accent"
                  : "bg-white border-moo-ink/20 active:scale-95"
              }`}
            >
              {ch}
            </button>
          );
        })}
      </div>

      {error && <p className="text-red-600 text-sm">{error}</p>}

      <Button href="/dashboard" variant="secondary" className="mt-auto">
        Done
      </Button>
    </StepLayout>
  );
}
