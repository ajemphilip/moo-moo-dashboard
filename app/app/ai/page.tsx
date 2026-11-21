"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { deviceName } from "@/components/ThisDevice";
import {
  api,
  PiApiError,
  type AiEngine,
  type AiOptions,
  type AiSettings,
  type AiTest,
} from "@/lib/api";
import {
  canRunHere,
  startThisDevice,
  stopThisDevice,
  subscribe,
  thisDeviceWanted,
  type EngineState,
  type Quality,
} from "@/lib/lendEngine";

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

type Choice = "cloud" | "this" | "computer" | "other";

function Option({
  active, title, text, onClick, disabled, children,
}: {
  active: boolean; title: string; text: string; onClick?: () => void; disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className={`moo-card flex flex-col gap-2 border-2 ${active ? "border-moo-accent" : "border-transparent"}`}>
      <button type="button" onClick={onClick} disabled={disabled} className="text-left flex flex-col gap-1 disabled:opacity-50">
        <span className="font-semibold">{active ? "● " : "○ "}{title}</span>
        <span className="text-sm text-moo-ink/70">{text}</span>
      </button>
      {children}
    </div>
  );
}

function Select({
  label, value, choices, onChange,
}: {
  label: string; value: string; choices: { id: string; label: string }[]; onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      <select className="moo-input" value={value} onChange={(e) => onChange(e.target.value)}>
        {choices.map((c) => (
          <option key={c.id} value={c.id}>{c.label}</option>
        ))}
      </select>
    </label>
  );
}

function AiPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const setup = params.get("setup") === "1";
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [options, setOptions] = useState<AiOptions | null>(null);
  const [engines, setEngines] = useState<AiEngine[] | null>(null);
  const [lent, setLent] = useState<{ name: string; jobs_done: number }[]>([]);
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<AiTest | null>(null);
  const [error, setError] = useState<string | null>(null);

  const here = typeof window !== "undefined" ? canRunHere() : null;
  const secureUrl =
    typeof window !== "undefined"
      ? `https://${window.location.hostname}:8443/ai/?thisdevice=1${setup ? "&setup=1" : ""}`
      : "";

  const refreshOptions = () => api.aiOptions().then(setOptions).catch(() => setOptions(null));

  useEffect(() => subscribe(setEngine), []);
  useEffect(() => {
    api.aiGet().then(setSettings).catch((e) => setError(errMsg(e)));
    api.aiDiscover().then((r) => setEngines(r.engines)).catch(() => setEngines([]));
    refreshOptions();
    const tick = () => api.engineStatus().then((s) => setLent(s.devices)).catch(() => {});
    tick();
    const id = setInterval(tick, 3000);
    return () => clearInterval(id);
  }, []);

  // Arrived from the plain address with "this device" chosen: carry on here.
  useEffect(() => {
    if (params.get("thisdevice") === "1" && !here) chooseThisDevice();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async (changes: Partial<AiSettings>) => {
    setBusy(true);
    setError(null);
    setTest(null);
    try {
      const s = await api.aiUpdate(changes);
      setSettings(s);
      await refreshOptions();
      return s;
    } catch (e) {
      setError(errMsg(e));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const chooseCloud = async () => {
    stopThisDevice();
    await save({ mode: "cloud" });
  };
  const chooseComputer = async (e: AiEngine) => {
    stopThisDevice();
    await save({ mode: "local", local_url: e.url, local_name: e.name });
  };
  const chooseOther = async () => {
    stopThisDevice();
    await save({ mode: "browser" });
  };
  async function chooseThisDevice() {
    if (here) {
      window.location.href = secureUrl;
      return;
    }
    const s = await save({ mode: "browser" });
    startThisDevice(deviceName(), (s?.browser_quality as Quality) ?? "auto");
  }

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

  const mode = settings?.mode;
  const thisDevice = mode === "browser" && thisDeviceWanted() && engine?.status !== "off";
  const choice: Choice | null =
    mode === "cloud" ? "cloud" : mode === "local" ? "computer" : mode === "browser" ? (thisDevice ? "this" : "other") : null;

  return (
    <StepLayout
      title="AI engine"
      subtitle="Where Moo's listening, stories and voice are worked out — and how they sound."
      back={setup ? "/apikey" : "/dashboard"}
    >
      <Option
        active={choice === "this"}
        title="This device"
        text="The phone or computer you are holding does the work in this browser, on its own chip. Free and private. Keep the Moo website open on it while your child plays."
        onClick={chooseThisDevice}
        disabled={busy}
      >
        {here && choice !== "this" && (
          <p className="text-xs text-moo-ink/60">
            Opens the Moo&apos;s secure address first. Your browser warns once — choose Advanced, then continue.
          </p>
        )}
        {choice === "this" && engine && (
          <p className="text-sm">
            {engine.status === "loading"
              ? `Loading… ${Object.values(engine.progress).map((p) => p.text).join(" · ")}`
              : engine.status === "error"
                ? engine.note
                : `Ready · ${engine.model} · ${engine.jobsDone} jobs${engine.lastSeconds ? `, last ${engine.lastSeconds}s` : ""}`}
          </p>
        )}
      </Option>

      <Option
        active={choice === "computer"}
        title="A computer with Moo Local"
        text="A computer at home runs Moo Local. The best local quality: bigger models and the expressive voice. The computer has to be on."
      >
        {engines === null && <Spinner label="Looking for computers…" />}
        {engines?.length === 0 && <p className="text-sm text-amber-700">No computer with Moo Local found.</p>}
        {engines?.map((e) => (
          <button
            key={e.url}
            type="button"
            disabled={busy || !e.reachable}
            onClick={() => chooseComputer(e)}
            className={`rounded-moo border px-3 py-2 text-left disabled:opacity-50 ${
              choice === "computer" && settings?.local_url === e.url
                ? "bg-moo-accent text-white border-moo-accent"
                : "bg-white border-moo-ink/20"
            }`}
          >
            <div className="font-medium">{e.name}</div>
            <div className="text-xs opacity-70">{e.ready ? "Ready" : e.reachable ? "Loading its models" : "Not answering"}</div>
          </button>
        ))}
      </Option>

      <Option
        active={choice === "cloud"}
        title="Cloud — OpenAI"
        text={`Needs internet and an OpenAI key; each story costs a little.${settings && !settings.has_openai_key ? " No key is saved yet." : ""}`}
        onClick={chooseCloud}
        disabled={busy || !settings?.has_openai_key}
      />

      <Option
        active={choice === "other"}
        title="Another phone or laptop"
        text={lent.length ? `Lent right now: ${lent.map((d) => d.name).join(", ")}` : "Open the Moo website on another device and choose This device there."}
        onClick={chooseOther}
        disabled={busy}
      />

      {/* Quality and voice for the engine in use */}
      {options && !options.unreachable && (
        <div className="moo-card flex flex-col gap-3">
          <h2 className="font-semibold">Quality and voice</h2>
          {options.mode === "browser" && options.qualities && (
            <Select
              label="Story model on the device"
              value={settings?.browser_quality ?? "auto"}
              choices={options.qualities}
              onChange={async (v) => {
                const s = await save({ browser_quality: v as Quality });
                if (choice === "this") {
                  stopThisDevice();
                  startThisDevice(deviceName(), (s?.browser_quality as Quality) ?? "auto");
                }
              }}
            />
          )}
          {options.mode === "local" && options.story_models && options.story_models.length > 0 && (
            <Select
              label="Story model"
              value={settings?.story_model ?? options.default_story_model ?? options.story_models[0].id}
              choices={options.story_models}
              onChange={(v) => save({ story_model: v })}
            />
          )}
          {options.mode === "cloud" && options.story_models && (
            <Select
              label="Story model"
              value={settings?.cloud_story_model ?? "gpt-4o-mini"}
              choices={options.story_models}
              onChange={(v) => save({ cloud_story_model: v })}
            />
          )}
          {options.voice_engines && options.voice_engines.length > 1 && (
            <Select
              label="Voice model"
              value={
                (options.mode === "cloud" ? settings?.cloud_voice_model : settings?.voice_engine) ??
                options.voice_engines[0].id
              }
              choices={options.voice_engines}
              onChange={(v) => save(options.mode === "cloud" ? { cloud_voice_model: v } : { voice_engine: v })}
            />
          )}
          {options.mode !== "cloud" && settings?.voice_engine === "orpheus" && options.orpheus_voices && (
            <Select
              label="Voice"
              value={options.orpheus_voices.some((v) => v.id === settings?.voice) ? settings!.voice! : "tara"}
              choices={options.orpheus_voices}
              onChange={(v) => save({ voice: v })}
            />
          )}
          {options.mode !== "cloud" && options.voices && options.voices.length > 0 &&
            settings?.voice_engine !== "chatterbox" && settings?.voice_engine !== "orpheus" && (
            <Select
              label="Voice"
              value={options.voices.some((v) => v.id === settings?.voice) ? settings!.voice! : "af_heart"}
              choices={options.voices}
              onChange={(v) => save({ voice: v })}
            />
          )}
          {options.mode === "cloud" && (
            <p className="text-sm text-moo-ink/70">
              The cloud voice itself is chosen under <a href="/preferences" className="underline">Preferences</a>.
            </p>
          )}
          {options.mode === "local" && settings?.voice_engine === "chatterbox" && (
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">
                Expressiveness: {Math.round((settings?.expressiveness ?? 0.6) * 100)}%
              </span>
              <input
                type="range" min={25} max={100} step={5}
                value={Math.round((settings?.expressiveness ?? 0.6) * 100)}
                onChange={(e) => setSettings((s) => (s ? { ...s, expressiveness: Number(e.target.value) / 100 } : s))}
                onPointerUp={(e) => save({ expressiveness: Number((e.target as HTMLInputElement).value) / 100 })}
                onKeyUp={(e) => save({ expressiveness: Number((e.target as HTMLInputElement).value) / 100 })}
              />
              <span className="text-xs text-moo-ink/60">Calm ← → dramatic. Chatterbox takes about twice as long as the sentence to speak.</span>
            </label>
          )}
          <Button variant="secondary" onClick={runTest} disabled={busy || !settings?.ready}>
            {busy ? "Working…" : "Hear it on Moo"}
          </Button>
          {test && (
            <p className="text-sm text-emerald-700">
              “{test.line}” — story {test.story_seconds}s, voice {test.voice_seconds}s.
            </p>
          )}
        </div>
      )}

      {error && <p className="text-red-600 text-sm">{error}</p>}

      <Button
        onClick={() => router.replace(setup ? "/preferences" : "/dashboard")}
        variant="secondary"
        className="mt-auto"
      >
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
