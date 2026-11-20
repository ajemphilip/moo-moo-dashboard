"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/Button";
import { StepLayout } from "@/components/StepLayout";
import { isIOS, lend, loadModels, type Job, type LoadProgress } from "@/lib/lendEngine";

const JOB_LABEL: Record<string, string> = {
  chat: "Writing for Moo…",
  moderate: "Checking the story is kind…",
  transcribe: "Listening to the child…",
  speak: "Making Moo's voice…",
};

function guessName(): string {
  if (typeof navigator === "undefined") return "A browser";
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android phone";
  if (/Mac/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows computer";
  return "A browser";
}

export default function LendPage() {
  const [problem, setProblem] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [phase, setPhase] = useState<"idle" | "loading" | "lending">("idle");
  const [progress, setProgress] = useState<Record<string, LoadProgress>>({});
  const [model, setModel] = useState("");
  const [current, setCurrent] = useState<Job | null>(null);
  const [done, setDone] = useState(0);
  const [lastSeconds, setLastSeconds] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const stopRef = useRef(false);
  const wakeRef = useRef<any>(null);

  useEffect(() => {
    setName(guessName());
    if (process.env.NEXT_PUBLIC_GITHUB_PAGES === "true") {
      setProblem("Open this page on the Moo itself: https://moo.local:8443/lend/");
    } else if (!window.isSecureContext) {
      setProblem(
        `This page needs the Moo's secure address. Open https://${window.location.hostname}:8443/lend/ ` +
          "and continue past the browser's warning once."
      );
    } else if (!(navigator as any).gpu) {
      setProblem("This browser has no WebGPU, so it cannot run Moo's AI. Try Chrome or Safari on a newer device.");
    }
    return () => {
      stopRef.current = true;
      wakeRef.current?.release?.().catch(() => {});
    };
  }, []);

  const start = async () => {
    stopRef.current = false;
    setNote(null);
    try {
      wakeRef.current = await (navigator as any).wakeLock?.request("screen");
    } catch {
      setNote("Could not keep the screen awake. Keep this page open and the screen on.");
    }
    setPhase("loading");
    try {
      const id = await loadModels((p) => setProgress((old) => ({ ...old, [p.part]: p })));
      setModel(id);
    } catch (e: any) {
      setNote(`Could not load the models: ${e?.message ?? e}`);
      setPhase("idle");
      return;
    }
    setPhase("lending");
    lend(name || guessName(), navigator.userAgent.slice(0, 120), () => stopRef.current, {
      onJob: (job, seconds) => {
        setCurrent(job);
        if (!job) {
          setDone((d) => d + 1);
          if (seconds !== undefined) setLastSeconds(Math.round(seconds * 10) / 10);
        }
      },
      onError: (m) => setNote(m),
    }).catch((e) => setNote(String(e?.message ?? e)));
  };

  const stop = () => {
    stopRef.current = true;
    wakeRef.current?.release?.().catch(() => {});
    setPhase("idle");
    setCurrent(null);
  };

  return (
    <StepLayout
      title="Lend this device to Moo"
      subtitle="This phone or computer does Moo's listening, stories and voice — no key, and nothing leaves your home after the first download."
      back="/dashboard"
    >
      {problem ? (
        <div className="moo-card text-sm text-amber-800">{problem}</div>
      ) : phase === "idle" ? (
        <>
          <div className="moo-card flex flex-col gap-2">
            <label className="text-sm font-medium" htmlFor="name">Name for this device</label>
            <input id="name" className="moo-input" value={name} onChange={(e) => setName(e.target.value)} />
            <p className="text-sm text-moo-ink/70">
              The first time, this downloads about {isIOS() ? "1" : "2.5"} GB of AI models into the
              browser. After that they are stored and start in seconds. Keep this page open and the
              screen on while your child plays.
            </p>
          </div>
          <Button onClick={start}>Start lending</Button>
        </>
      ) : (
        <>
          <div className="moo-card flex flex-col gap-2 text-sm">
            {(["story", "speech", "voice"] as const).map((part) => (
              <div key={part} className="flex justify-between gap-3">
                <span className="text-moo-ink/60 capitalize">{part}</span>
                <span className={`text-right ${progress[part]?.done ? "text-emerald-700" : ""}`}>
                  {progress[part]?.text ?? "waiting"}
                </span>
              </div>
            ))}
          </div>
          {phase === "lending" && (
            <div className="moo-card text-center flex flex-col gap-1 py-5">
              <p className="font-semibold">
                {current ? JOB_LABEL[current.kind] ?? current.kind : "Ready — waiting for Moo"}
              </p>
              <p className="text-sm text-moo-ink/60">
                {done} jobs done{lastSeconds !== null ? `, last one took ${lastSeconds}s` : ""}
              </p>
              <p className="text-xs text-moo-ink/50">{model}</p>
            </div>
          )}
          <Button variant="secondary" onClick={stop}>Stop lending</Button>
        </>
      )}
      {note && <p className="text-sm text-amber-700">{note}</p>}
    </StepLayout>
  );
}
