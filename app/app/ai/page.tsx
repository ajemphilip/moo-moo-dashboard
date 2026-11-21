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
  clearCrash,
  crashedWhileLoading,
  deviceChoice,
  KITTEN_VOICES,
  LISTEN_CHOICES,
  VOICE_CHOICES,
  setDeviceChoice,
  STORY_CHOICES,
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

type Choice = "cloud" | "this" | "computer";

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
  const [crashed, setCrashed] = useState<string | null>(null);
  const [mine, setMine] = useState({ story: "", listen: "", voice: "", kittenVoice: "Bruno" });
  const [copied, setCopied] = useState(false);
  const [address, setAddress] = useState("");

  const here = typeof window !== "undefined" ? canRunHere() : null;
  const secureUrl =
    typeof window !== "undefined"
      ? `https://${window.location.hostname}:8443/ai/?thisdevice=1${setup ? "&setup=1" : ""}`
      : "";

  const installLine =
    typeof window !== "undefined"
      ? `curl -fsSL http://${window.location.hostname}:8000/local-ai/install.sh | bash`
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

  useEffect(() => {
    setCrashed(crashedWhileLoading());
    setMine(deviceChoice());
  }, []);

  // Arrived from the plain address with "this device" chosen: carry on here,
  // unless the browser died while loading last time (it would die again).
  useEffect(() => {
    if (params.get("thisdevice") === "1" && !here && !crashedWhileLoading()) chooseThisDevice();
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
  const addByAddress = async () => {
    // "192.168.1.20", "mac.local:8765" or a full address all work.
    let a = address.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (!a) return;
    if (!a.includes(":")) a += ":8765";
    stopThisDevice();
    const s = await save({ mode: "local", local_url: `http://${a}/v1`, local_name: a.split(":")[0] });
    if (s) {
      setAddress("");
      api.aiDiscover().then((r) => setEngines(r.engines)).catch(() => {});
    }
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
    clearCrash();
    setCrashed(null);
    const s = await save({ mode: "browser" });
    startThisDevice(deviceName(), (s?.browser_quality as Quality) ?? "auto");
  }

  // The models this device runs are kept on this device.
  const chooseMine = (c: Partial<{ story: string; listen: string; voice: string; kittenVoice: string }>) => {
    setDeviceChoice(c);
    setMine(deviceChoice());
    if (thisDevice) {
      stopThisDevice();
      clearCrash();
      setCrashed(null);
      startThisDevice(deviceName(), (settings?.browser_quality as Quality) ?? "auto");
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

  const usesKitten =
    mine.voice.startsWith("kitten-") ||
    (mine.voice === "" && typeof navigator !== "undefined" && /iPhone|iPad|iPod/.test(navigator.userAgent));
  const mode = settings?.mode;
  const thisDevice = mode === "browser" && thisDeviceWanted() && engine?.status !== "off";
  const choice: Choice | null =
    mode === "cloud" ? "cloud" : mode === "local" ? "computer" : mode === "browser" ? (thisDevice ? "this" : "computer") : null;

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
        {crashed && (
          <p className="text-sm text-red-600">
            Last time this browser closed while loading {crashed}. That usually means the device ran out of memory.
            Choose smaller models under Quality and voice, then tap This device again.
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
        title="A computer at home"
        text="A computer at home does the work. Pick it from the list, or set one up. The computer has to be on."
      >
        {engines === null && <Spinner label="Looking for computers…" />}
        {engines?.length === 0 && lent.length === 0 && (
          <p className="text-sm text-amber-700">No computer found yet. Set one up below.</p>
        )}
        {engines?.map((e) => (
          <button
            key={e.url}
            type="button"
            disabled={busy || !e.reachable}
            onClick={() => chooseComputer(e)}
            className={`rounded-moo border px-3 py-2 text-left disabled:opacity-50 ${
              mode === "local" && settings?.local_url === e.url
                ? "bg-moo-accent text-white border-moo-accent"
                : "bg-white border-moo-ink/20"
            }`}
          >
            <div className="font-medium">{e.name}</div>
            <div className="text-xs opacity-70">{e.ready ? "Ready" : e.reachable ? "Loading its models" : "Not answering"}</div>
          </button>
        ))}
        {lent.map((d) => (
          <button
            key={`lent-${d.name}`}
            type="button"
            disabled={busy}
            onClick={chooseOther}
            className={`rounded-moo border px-3 py-2 text-left disabled:opacity-50 ${
              mode === "browser" && !thisDevice ? "bg-moo-accent text-white border-moo-accent" : "bg-white border-moo-ink/20"
            }`}
          >
            <div className="font-medium">{d.name}</div>
            <div className="text-xs opacity-70">Lent from its browser · {d.jobs_done} jobs done</div>
          </button>
        ))}
        <details className="text-sm">
          <summary className="cursor-pointer font-medium text-moo-accent">Set up a computer</summary>
          <div className="mt-2 flex flex-col gap-2 text-moo-ink/80">
            <p>
              <b>A Mac with Apple silicon:</b> open Terminal on that Mac, paste this line and press Return. It installs
              Moo Local (about 5 GB) and starts it. The Mac then appears in the list above.
            </p>
            <code className="block break-all rounded-moo bg-moo-ink/5 p-2 text-xs">{installLine}</code>
            <Button
              variant="secondary"
              onClick={() => navigator.clipboard?.writeText(installLine).then(() => setCopied(true)).catch(() => {})}
            >
              {copied ? "Copied" : "Copy the line"}
            </Button>
            <p>
              <b>Any other computer or phone:</b> open the Moo website in its browser and choose This device there. It
              then appears in the list above.
            </p>
            <p>
              <b>A computer that is not found by itself:</b> type its address, for example 192.168.1.20.
            </p>
            <div className="flex gap-2">
              <input
                className="moo-input flex-1"
                placeholder="Address of the computer"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                inputMode="url"
                autoCapitalize="none"
              />
              <Button variant="secondary" onClick={addByAddress} disabled={busy || !address.trim()}>
                Add
              </Button>
            </div>
          </div>
        </details>
      </Option>

      <Option
        active={choice === "cloud"}
        title="Cloud — OpenAI"
        text={`Needs internet and an OpenAI key; each story costs a little.${settings && !settings.has_openai_key ? " No key is saved yet." : ""}`}
        onClick={chooseCloud}
        disabled={busy || !settings?.has_openai_key}
      />

      {/* Quality and voice for the engine in use */}
      {options && !options.unreachable && (
        <div className="moo-card flex flex-col gap-3">
          <h2 className="font-semibold">Quality and voice</h2>
          {options.mode === "browser" && options.qualities && (
            <Select
              label="Size preset for lent devices"
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
          {options.mode === "browser" && !here && (
            <>
              <Select
                label="Story model on this device"
                value={mine.story}
                choices={STORY_CHOICES}
                onChange={(v) => chooseMine({ story: v })}
              />
              <Select
                label="Listening model on this device"
                value={mine.listen}
                choices={LISTEN_CHOICES}
                onChange={(v) => chooseMine({ listen: v })}
              />
              <Select
                label="Voice model on this device"
                value={mine.voice}
                choices={VOICE_CHOICES}
                onChange={(v) => chooseMine({ voice: v })}
              />
              {usesKitten && (
                <Select
                  label="Voice on this device"
                  value={mine.kittenVoice}
                  choices={KITTEN_VOICES.map((v) => ({ id: v, label: v }))}
                  onChange={(v) => chooseMine({ kittenVoice: v })}
                />
              )}
              <p className="text-xs text-moo-ink/60">
                These are remembered on this device only, so a phone and a computer can each have their own.
              </p>
            </>
          )}
          {options.mode === "local" && options.tiers && options.tiers.length > 0 && (
            <Select
              label="Speed and quality"
              value={settings?.tier && options.tiers.some((t) => t.id === settings.tier) ? settings.tier : "custom"}
              choices={[...options.tiers, { id: "custom", label: "Custom — my own choices below" }]}
              onChange={(v) => {
                const t = options.tiers!.find((x) => x.id === v);
                save(t ? { tier: t.id, ...t.settings } : { tier: "custom" });
              }}
            />
          )}
          {options.mode === "local" && options.story_models && options.story_models.length > 0 && (
            <Select
              label="Story model"
              value={settings?.story_model ?? options.default_story_model ?? options.story_models[0].id}
              choices={options.story_models}
              onChange={(v) => save({ tier: "custom", story_model: v })}
            />
          )}
          {options.mode === "local" && options.listen_models && options.listen_models.length > 0 && (
            <Select
              label="Listening model"
              value={settings?.listen_model ?? options.default_listen_model ?? options.listen_models[0].id}
              choices={options.listen_models}
              onChange={(v) => save({ tier: "custom", listen_model: v })}
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
              choices={options.voice_engines.map((e) => {
                // The quality in use is shown next to the voice model's name.
                const qs = options.voice_qualities?.[e.id];
                const q = qs?.find((x) => x.id === (settings?.voice_qualities?.[e.id] ?? options.default_voice_quality?.[e.id]));
                return q ? { id: e.id, label: `${e.label} · ${q.label.split(" — ")[0]} quality` } : e;
              })}
              onChange={(v) => {
                if (options.mode === "cloud") return save({ cloud_voice_model: v });
                const first = options.voices_by_engine?.[v]?.[0]?.id;
                return save({ tier: "custom", voice_engine: v, ...(first ? { voice: first } : {}) });
              }}
            />
          )}
          {options.mode === "local" && options.voice_qualities && (() => {
            const eng = settings?.voice_engine ?? "kokoro";
            const qs = options.voice_qualities[eng];
            if (!qs || qs.length < 2) return null;
            return (
              <Select
                label="Voice quality"
                value={settings?.voice_qualities?.[eng] ?? options.default_voice_quality?.[eng] ?? qs[qs.length - 1].id}
                choices={qs}
                onChange={(v) => save({ tier: "custom", voice_qualities: { ...(settings?.voice_qualities ?? {}), [eng]: v } })}
              />
            );
          })()}
          {options.voices_by_engine && (() => {
            const eng = settings?.voice_engine ?? options.voice_engines?.find((e) => e.id === "kokoro")?.id ?? "kokoro";
            const list = options.voices_by_engine[eng];
            if (!list || list.length === 0) return null;
            return (
              <Select
                label="Voice"
                value={list.some((v) => v.id === settings?.voice) ? settings!.voice! : list[0].id}
                choices={list}
                onChange={(v) => save({ tier: "custom", voice: v })}
              />
            );
          })()}
          {!options.voices_by_engine && options.mode !== "cloud" && settings?.voice_engine === "orpheus" && options.orpheus_voices && (
            <Select
              label="Voice"
              value={options.orpheus_voices.some((v) => v.id === settings?.voice) ? settings!.voice! : "tara"}
              choices={options.orpheus_voices}
              onChange={(v) => save({ voice: v })}
            />
          )}
          {!options.voices_by_engine && !(options.mode === "browser" && !here && usesKitten) && options.mode !== "cloud" && options.voices && options.voices.length > 0 &&
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
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Clarity of Moo&apos;s speaker: {Math.round(settings?.clarity_db ?? 12)}</span>
            <input
              type="range" min={0} max={18} step={1}
              value={Math.round(settings?.clarity_db ?? 12)}
              onChange={(e) => setSettings((s) => (s ? { ...s, clarity_db: Number(e.target.value) } : s))}
              onPointerUp={(e) => save({ clarity_db: Number((e.target as HTMLInputElement).value) })}
              onKeyUp={(e) => save({ clarity_db: Number((e.target as HTMLInputElement).value) })}
            />
            <span className="text-xs text-moo-ink/60">Muffled ← → crisp. Move it, then press Hear it on Moo.</span>
          </label>
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
