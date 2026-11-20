"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError, type AiEngine, type AiSettings, type AiTest } from "@/lib/api";

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

function AiPageInner() {
  const router = useRouter();
  const setup = useSearchParams().get("setup") === "1";
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [engines, setEngines] = useState<AiEngine[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<AiTest | null>(null);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    setSearching(true);
    try {
      setEngines((await api.aiDiscover()).engines);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSearching(false);
    }
  };

  useEffect(() => {
    api.aiGet().then(setSettings).catch((e) => setError(errMsg(e)));
    search();
  }, []);

  const [lent, setLent] = useState<{ name: string; jobs_done: number }[]>([]);
  useEffect(() => {
    const tick = () => api.engineStatus().then((s) => setLent(s.devices)).catch(() => {});
    tick();
    const id = setInterval(tick, 3000);
    return () => clearInterval(id);
  }, []);

  const choose = async (mode: "cloud" | "local" | "browser", engine?: AiEngine) => {
    setBusy(true);
    setError(null);
    setTest(null);
    try {
      const s = await api.aiSet(mode, engine?.url, engine?.name);
      setSettings(s);
      if (setup && s.ready) router.replace("/preferences");
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setBusy(true);
    setError(null);
    setTest(null);
    try {
      setTest(await api.aiTest());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const local = settings?.mode === "local";
  const browser = settings?.mode === "browser";
  const secureLend =
    typeof window !== "undefined" ? `https://${window.location.hostname}:8443/lend/` : "/lend/";

  return (
    <StepLayout
      title="AI engine"
      subtitle="Where Moo's listening, stories and voice are worked out."
      back={setup ? "/apikey" : "/dashboard"}
    >
      {/* Cloud */}
      <button
        type="button"
        disabled={busy || !settings?.has_openai_key}
        onClick={() => choose("cloud")}
        className={`moo-card text-left flex flex-col gap-1 border-2 transition disabled:opacity-50 ${
          !local && !browser && settings ? "border-moo-accent" : "border-transparent"
        }`}
      >
        <span className="font-semibold">Cloud — OpenAI</span>
        <span className="text-sm text-moo-ink/70">
          The best voices and stories. Needs internet and an OpenAI key, and each
          story costs a little.
          {!settings?.has_openai_key && " No key is saved yet."}
        </span>
      </button>

      {/* Local */}
      <div
        className={`moo-card flex flex-col gap-2 border-2 ${
          local ? "border-moo-accent" : "border-transparent"
        }`}
      >
        <span className="font-semibold">Local — a computer on your network</span>
        <span className="text-sm text-moo-ink/70">
          Free and private. Nothing leaves your home and no key is needed. The
          computer runs Moo Local and has to be on while your child plays.
        </span>
        {searching && <Spinner label="Looking for computers…" />}
        {!searching && engines && engines.length === 0 && (
          <p className="text-sm text-amber-700">
            No computer with Moo Local found on this network yet.
          </p>
        )}
        {engines?.map((e) => {
          const chosen = local && settings?.local_url === e.url;
          return (
            <button
              key={e.url}
              type="button"
              disabled={busy || !e.reachable}
              onClick={() => choose("local", e)}
              className={`rounded-moo border px-3 py-2 text-left disabled:opacity-50 ${
                chosen ? "bg-moo-accent text-white border-moo-accent" : "bg-white border-moo-ink/20"
              }`}
            >
              <div className="font-medium">{e.name}</div>
              <div className={`text-xs ${chosen ? "text-white/80" : "text-moo-ink/60"}`}>
                {e.ready ? "Ready" : e.reachable ? "Found, still loading its models" : "Not answering"}
                {chosen && " · in use"}
              </div>
            </button>
          );
        })}
        <button type="button" onClick={search} disabled={searching} className="text-sm text-moo-accent underline self-start">
          Search again
        </button>
      </div>

      {/* A lent phone or laptop */}
      <div
        className={`moo-card flex flex-col gap-2 border-2 ${
          browser ? "border-moo-accent" : "border-transparent"
        }`}
      >
        <span className="font-semibold">A phone or laptop — lend it to Moo</span>
        <span className="text-sm text-moo-ink/70">
          Any newer phone or computer does the work in its web browser. Open the Lend page on it,
          keep it open, and Moo uses it. Nothing to install, no key, free.
        </span>
        <p className="text-sm">
          {lent.length > 0
            ? `Lent right now: ${lent.map((d) => d.name).join(", ")}`
            : "No device is lent right now."}
        </p>
        <a href={secureLend} className="text-sm text-moo-accent underline self-start">
          Open the Lend page on this device
        </a>
        <Button variant="secondary" onClick={() => choose("browser")} disabled={busy}>
          {browser ? "In use" : "Use a lent device"}
        </Button>
      </div>

      {settings?.ready && (
        <div className="moo-card flex flex-col gap-2">
          <Button variant="secondary" onClick={runTest} disabled={busy}>
            {busy ? "Working…" : "Test the engine"}
          </Button>
          {test && (
            <p className="text-sm text-emerald-700">
              “{test.line}” — story {test.story_seconds}s, voice {test.voice_seconds}s ({test.mode}).
            </p>
          )}
        </div>
      )}

      {error && <p className="text-red-600 text-sm">{error}</p>}

      <Button href={setup ? "/preferences" : "/dashboard"} variant="secondary" className="mt-auto">
        {setup ? "Continue" : "Done"}
      </Button>
    </StepLayout>
  );
}

export default function AiPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <AiPageInner />
    </Suspense>
  );
}
