"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError, type StoryState } from "@/lib/api";
import { WordGame } from "@/components/WordGame";


const PHASE_LABEL: Record<StoryState["phase"], string> = {
  idle: "Getting ready…",
  greeting: "Moo is talking — listen!",
  listening: "Listening — answer Moo out loud",
  thinking: "Moo is writing the story…",
  telling: "Moo is reading the story…",
  quiz: "Moo wants your child's idea — listen!",
  spelling: "Word game on the Braille cell…",
  ready: "Ready for the next part",
  done: "The end",
  error: "Something went wrong",
};

const WORKING: StoryState["phase"][] = [
  "greeting",
  "listening",
  "thinking",
  "telling",
  "quiz",
  "spelling",
];

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

export default function StoryPage() {
  const [state, setState] = useState<StoryState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const startedRef = useRef(false);

  // Poll the session while it runs so the phone follows each phase.
  useEffect(() => {
    if (!started) return;
    const id = setInterval(() => {
      api.storyState().then(setState).catch(() => {});
    }, 1000);
    return () => clearInterval(id);
  }, [started]);

  // End the session if the parent leaves the page.
  useEffect(() => {
    return () => {
      if (startedRef.current) api.storyStop().catch(() => {});
    };
  }, []);

  const start = async () => {
    setError(null);
    try {
      setState(await api.storyStart());
      setStarted(true);
      startedRef.current = true;
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const cont = async () => {
    setError(null);
    try {
      setState(await api.storyContinue());
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const finish = async () => {
    try {
      await api.storyStop();
    } catch {
      /* ignore */
    }
    startedRef.current = false;
    setStarted(false);
    setState(null);
  };

  // --- Not started yet ----------------------------------------------------
  if (!started) {
    return (
      <StepLayout
        title="Story time"
        subtitle="Moo tells a story together with your child."
        back="/dashboard"
      >
        <div className="moo-card flex flex-col gap-3">
          <p className="text-sm text-moo-ink/70">
            Moo says hello and asks what kind of story to tell. After a{" "}
            <strong>chime</strong>, your child answers <strong>out loud</strong>.
          </p>
          <p className="text-sm text-moo-ink/70">
            Moo tells a <strong>short part</strong> of the story, then asks
            <strong> what happens next</strong>. After the chime your child
            answers out loud and can take their time — Moo waits until they
            stop talking, repeats back what it heard, and asks again if it
            didn&apos;t catch a usable answer.
          </p>
          <p className="text-sm text-moo-ink/70">
            A word from the story is then spelled on the Braille cell, and
            the next part grows from your child&apos;s idea.
          </p>
        </div>
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <Button onClick={start} className="mt-auto">
          Start a story
        </Button>
      </StepLayout>
    );
  }

  const phase = state?.phase ?? "idle";
  const working = WORKING.includes(phase);

  return (
    <StepLayout
      title="Story time"
      subtitle={state ? `Part ${Math.max(state.parts, 1)}` : undefined}
      back="/dashboard"
    >
      {/* Live phase */}
      <div className="moo-card flex flex-col gap-2 items-center text-center py-5">
        {working && phase !== "spelling" && <Spinner />}
        <p className="font-semibold">{PHASE_LABEL[phase]}</p>
        {phase === "greeting" && (
          <p className="text-sm text-moo-ink/60">
            A chime will tell your child when to answer.
          </p>
        )}
        {phase === "listening" && (
          <p className="text-sm text-moo-accent">
            🔔 After the chime, answer out loud — Moo waits until you stop
            talking.
          </p>
        )}
        {state?.dry_run && (
          <p className="text-xs text-amber-700">
            Dry-run — no Braille hardware detected.
          </p>
        )}
      </div>

      <WordGame state={state} />

      {/* What Moo just said */}
      {state?.message && phase !== "error" && (
        <div className="moo-card">
          <p className="text-xs uppercase tracking-wide text-moo-ink/40 mb-1">
            Moo says
          </p>
          <p className="text-moo-ink/90">{state.message}</p>
        </div>
      )}

      {/* The story so far */}
      {state?.story && (
        <div className="moo-card">
          <p className="text-xs uppercase tracking-wide text-moo-ink/40 mb-1">
            The story so far
          </p>
          <p className="text-moo-ink/80 whitespace-pre-line text-sm leading-relaxed">
            {state.story}
          </p>
        </div>
      )}

      {phase === "error" && (
        <p className="text-red-600 text-sm">
          {state?.error ?? "The story ran into a problem."}
        </p>
      )}
      {error && <p className="text-red-600 text-sm">{error}</p>}

      {/* Controls */}
      <div className="mt-auto flex flex-col gap-2">
        {phase === "ready" && (
          <Button onClick={cont}>Continue the story</Button>
        )}
        {phase === "error" && (
          <Button onClick={cont}>Try again</Button>
        )}
        <Button variant="secondary" onClick={finish}>
          {phase === "ready" || phase === "error" ? "Finish" : "Stop story"}
        </Button>
      </div>
    </StepLayout>
  );
}
