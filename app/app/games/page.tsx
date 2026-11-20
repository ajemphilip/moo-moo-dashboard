"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { WordGame } from "@/components/WordGame";
import { api, PiApiError, type GameKind, type GameState } from "@/lib/api";

const GAMES: { kind: GameKind; title: string; text: string }[] = [
  {
    kind: "spell",
    title: "Spell a word",
    text: "Moo raises each letter of a word. Your child feels it and presses every raised dot down.",
  },
  {
    kind: "name",
    title: "Which letter is this?",
    text: "Moo raises one letter. Your child feels it and says the letter out loud.",
  },
  {
    kind: "build",
    title: "Riddle and make a letter",
    text: "Moo tells a riddle. Your child guesses the word, then presses dots to raise its first letter.",
  },
  {
    kind: "mix",
    title: "Mix of all three",
    text: "A different game for every word.",
  },
];

const PHASE_LABEL: Record<string, string> = {
  idle: "Getting ready…",
  telling: "Moo is talking — listen!",
  thinking: "Moo is choosing a word…",
  quiz: "Moo is asking — listen!",
  listening: "Listening — answer Moo out loud",
  spelling: "Word game on the Braille cell…",
  done: "All done",
  error: "Something went wrong",
};

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

export default function GamesPage() {
  const [state, setState] = useState<GameState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const startedRef = useRef(false);

  useEffect(() => {
    if (!started) return;
    const id = setInterval(() => {
      api.gamesState().then(setState).catch(() => {});
    }, 700);
    return () => clearInterval(id);
  }, [started]);

  // End the session if the parent leaves the page.
  useEffect(() => {
    return () => {
      if (startedRef.current) api.gamesStop().catch(() => {});
    };
  }, []);

  const start = async (kind: GameKind) => {
    setError(null);
    try {
      setState(await api.gamesStart(kind));
      setStarted(true);
      startedRef.current = true;
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const stop = async () => {
    try {
      await api.gamesStop();
    } catch {
      /* already stopped */
    }
    startedRef.current = false;
    setStarted(false);
    setState(null);
  };

  if (!started) {
    return (
      <StepLayout
        title="Word games"
        subtitle="Short games on the Braille cell, without a story. Moo picks a simple word for each round."
        back="/dashboard"
      >
        {GAMES.map((g) => (
          <button
            key={g.kind}
            type="button"
            onClick={() => start(g.kind)}
            className="moo-card text-left flex flex-col gap-1 active:scale-[0.99] transition"
          >
            <span className="font-semibold">{g.title}</span>
            <span className="text-sm text-moo-ink/70">{g.text}</span>
          </button>
        ))}
        <p className="text-sm text-moo-ink/60">
          After each word Moo asks whether to play another. Your child says yes,
          or &ldquo;the end&rdquo; to finish.
        </p>
        {error && <p className="text-red-600 text-sm">{error}</p>}
      </StepLayout>
    );
  }

  const phase = state?.phase ?? "idle";
  const working = !["done", "error", "spelling"].includes(phase);
  const title = GAMES.find((g) => g.kind === state?.game)?.title ?? "Word games";

  return (
    <StepLayout
      title={title}
      subtitle={state && state.rounds > 0 ? `${state.rounds} played` : undefined}
      back="/dashboard"
    >
      <div className="moo-card flex flex-col gap-2 items-center text-center py-5">
        {working && <Spinner />}
        <p className="font-semibold">{PHASE_LABEL[phase] ?? phase}</p>
        {state?.dry_run && (
          <p className="text-xs text-amber-700">Dry-run — no Braille hardware detected.</p>
        )}
      </div>

      <WordGame state={state} />

      {state?.message && phase !== "error" && (
        <div className="moo-card">
          <p className="text-xs uppercase tracking-wide text-moo-ink/40 mb-1">Moo says</p>
          <p className="text-moo-ink/90">{state.message}</p>
        </div>
      )}

      {phase === "error" && (
        <p className="text-red-600 text-sm">{state?.error ?? "The game ran into a problem."}</p>
      )}

      <div className="mt-auto flex flex-col gap-2">
        <Button variant="secondary" onClick={stop}>
          {phase === "done" || phase === "error" ? "Back to the games" : "Stop"}
        </Button>
      </div>
    </StepLayout>
  );
}
