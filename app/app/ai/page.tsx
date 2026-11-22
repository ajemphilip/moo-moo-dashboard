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
  type KnownDevice,
} from "@/lib/api";
import {
  canRunHere,
  clearCrash,
  crashedWhileLoading,
  deviceChoice,
  KITTEN_VOICES,
  KITTEN2_VOICES,
  LISTEN_CHOICES,
  MEMORY_CHOICES,
  reloadModels,
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
  const [lent, setLent] = useState<{ name: string; jobs_done: number; current?: boolean }[]>([]);
  // Every device that has worked for this Moo, on now or not (the Moo remembers them).
  const [known, setKnown] = useState<KnownDevice[]>([]);
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<AiTest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [crashed, setCrashed] = useState<string | null>(null);
  const [mine, setMine] = useState({ story: "", listen: "", voice: "", kittenVoice: "Bruno", kitten2Voice: "Eleanor", single: "" });

  const here = typeof window !== "undefined" ? canRunHere() : null;
  // This device runs on the Moo's own secure address. On the Moo's website that is this host; on GitHub
  // Pages it is the Moo's address on the wifi, which the Moo gives over Bluetooth, else moo.local. The
  // page's own host was used here before, which sent GitHub Pages to github.io:8443 (22 November 2026).
  const githubPages = process.env.NEXT_PUBLIC_GITHUB_PAGES === "true";
  const [mooHost, setMooHost] = useState<string>(
    typeof window !== "undefined" && !githubPages ? window.location.hostname : "moo.local",
  );
  useEffect(() => {
    if (githubPages) api.status().then((st) => st.address && setMooHost(st.address)).catch(() => {});
  }, [githubPages]);
  const secureUrl = `https://${mooHost}:8443/ai/?thisdevice=1${setup ? "&setup=1" : ""}`;

  const refreshOptions = () => api.aiOptions().then(setOptions).catch(() => setOptions(null));

  useEffect(() => subscribe(setEngine), []);
  useEffect(() => {
    api.aiGet().then(setSettings).catch((e) => setError(errMsg(e)));
    api.aiDiscover().then((r) => setEngines(r.engines)).catch(() => setEngines([]));
    refreshOptions();
    const tick = () =>
      api.engineStatus().then((s) => {
        setLent(s.devices);
        setKnown(s.known ?? []);
      }).catch(() => {});
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
  // Another lent phone or computer becomes the current one; this browser stops lending.
  const chooseOther = async (name: string) => {
    if (name !== deviceName()) stopThisDevice();
    await api.engineUse(name).catch(() => {});
    await save({ mode: "browser" });
  };
  async function chooseThisDevice() {
    if (here) {
      window.location.href = secureUrl;
      return;
    }
    clearCrash();
    setCrashed(null);
    await api.engineUse(deviceName()).catch(() => {});
    const s = await save({ mode: "browser" });
    startThisDevice(deviceName(), (s?.browser_quality as Quality) ?? "auto");
  }

  // The models this device runs are kept on this device.
  const chooseMine = (c: Partial<{ story: string; listen: string; voice: string; kittenVoice: string; kitten2Voice: string; single: string }>) => {
    setDeviceChoice(c);
    setMine(deviceChoice());
    // Takes effect at once: only the model that changed is loaded again.
    if (thisDevice) {
      clearCrash();
      setCrashed(null);
      reloadModels((settings?.browser_quality as Quality) ?? "auto");
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
  // Kitten 2 carries its own voices, fewer in the Pico than in the Nano.
  const usesKitten2 = mine.voice.startsWith("kitten2-");
  const kitten2Voices = KITTEN2_VOICES[mine.voice.slice("kitten2-".length)] ?? [];
  const kitten2Voice = kitten2Voices.includes(mine.kitten2Voice) ? mine.kitten2Voice : kitten2Voices[0];
  const mode = settings?.mode;
  const thisDevice = mode === "browser" && thisDeviceWanted() && engine?.status !== "off";
  // The rows of the device list. Computers come from the search on the network and the Moo's memory;
  // phones and browsers from the devices lending now and the Moo's memory.
  type Row = { key: string; name: string; text: string; current: boolean; on: boolean; remembered: boolean; use?: () => void };
  const ago = (t?: number | null) => {
    if (!t) return "";
    const m = Math.round((Date.now() / 1000 - t) / 60);
    return m < 2 ? "just now" : m < 90 ? `${m} minutes ago` : m < 36 * 60 ? `${Math.round(m / 60)} hours ago` : `${Math.round(m / 1440)} days ago`;
  };
  const knownNames = new Set(known.map((k) => k.name));
  const devices: Row[] = [];
  for (const e of engines ?? []) {
    devices.push({
      key: `pc-${e.url}`,
      name: e.name,
      on: e.reachable,
      current: settings?.mode === "local" && settings?.local_url === e.url,
      remembered: knownNames.has(e.name),
      text: `Moo Local · ${e.ready ? "ready" : e.reachable ? "on, getting ready" : `off${ago(known.find((k) => k.name === e.name)?.last_seen) ? `, last seen ${ago(known.find((k) => k.name === e.name)?.last_seen)}` : ""} · start Moo Local on it`}`,
      use: e.reachable ? () => chooseComputer(e) : undefined,
    });
  }
  const lentNames = new Set(lent.map((d) => d.name));
  const lentRows = [
    ...lent.map((d) => ({ name: d.name, on: true, current: !!d.current, jobs: d.jobs_done, last: null as number | null })),
    ...known.filter((k) => k.kind === "lent" && !lentNames.has(k.name)).map((k) => ({ name: k.name, on: false, current: false, jobs: 0, last: k.last_seen ?? null })),
  ];
  for (const d of lentRows) {
    const mine = d.name === deviceName();
    const app = /app\b/.test(d.name);
    devices.push({
      key: `lent-${d.name}`,
      name: mine ? `${d.name} (this device)` : d.name,
      on: d.on || (mine && thisDevice),
      current: settings?.mode === "browser" && (mine ? thisDevice : !thisDevice && d.current),
      remembered: knownNames.has(d.name),
      text: d.on
        ? `${app ? "The Moo app" : "A browser"} · ${d.jobs} jobs done`
        : `${app ? "The Moo app" : "A browser"} · off${d.last ? `, last seen ${ago(d.last)}` : ""} · ${app ? "open the Moo app on it" : "open the Moo website on it and set it up"}`,
      use: mine ? chooseThisDevice : d.on ? () => chooseOther(d.name) : undefined,
    });
  }
  devices.sort((a, b) => Number(b.current) - Number(a.current) || Number(b.on) - Number(a.on));
  const choice: Choice | null =
    mode === "cloud" ? "cloud" : mode === "local" ? "computer" : mode === "browser" ? (thisDevice ? "this" : lent.length ? "computer" : null) : null;

  // What is in use right now, in plain words, for the card at the top.
  const labelOf = (list: { id: string; label: string }[] | undefined, id: string | null | undefined) =>
    (list?.find((x) => x.id === id)?.label ?? id ?? "").split(" — ")[0];
  const localMachine = engines?.find((e) => e.url === settings?.local_url);
  const lentOther = mode === "browser" && !thisDevice;
  const where =
    mode === "cloud"
      ? "the cloud (OpenAI)"
      : mode === "local"
        ? settings?.local_name ?? "a local machine"
        : thisDevice
          ? `this device (${deviceName()})`
          : lent.find((d) => d.current)
            ? `${lent.find((d) => d.current)!.name}, a lent phone or computer`
            : "a lent device, but none is lending right now";
  const voiceEngineId = settings?.voice_engine ?? "kokoro";
  const inUse: { what: string; value: string }[] =
    mode === "local" && options?.mode === "local"
      ? [
          { what: "Story", value: labelOf(options.story_models, settings?.story_model ?? options.default_story_model) },
          { what: "Listening", value: labelOf(options.listen_models, settings?.listen_model ?? options.default_listen_model) },
          {
            what: "Voice",
            value: `${labelOf(options.voice_engines, voiceEngineId)}, ${labelOf(options.voices_by_engine?.[voiceEngineId], settings?.voice) || "its first voice"}`,
          },
        ]
      : mode === "cloud"
        ? [
            { what: "Story", value: settings?.cloud_story_model ?? "gpt-4o-mini" },
            { what: "Listening", value: "Whisper, at OpenAI" },
            { what: "Voice", value: settings?.cloud_voice_model ?? "tts-1" },
          ]
        : thisDevice
          ? [
              { what: "Story", value: engine?.model ? engine.model.split("-q4")[0] : labelOf(STORY_CHOICES, mine.story) },
              { what: "Listening", value: labelOf(LISTEN_CHOICES, mine.listen) },
              {
                what: "Voice",
                value: usesKitten2
                  ? `${labelOf(VOICE_CHOICES, mine.voice)}, ${kitten2Voice}`
                  : usesKitten
                    ? `${labelOf(VOICE_CHOICES, mine.voice || "kitten-mini")}, ${mine.kittenVoice}`
                    : `Kokoro, ${labelOf(options?.voices, settings?.voice) || "Heart"}`,
              },
              { what: "Memory", value: labelOf(MEMORY_CHOICES, mine.single) },
            ]
          : [];
  const loadState =
    thisDevice && engine
      ? engine.status === "loading"
        ? "Loading its models…"
        : engine.status === "working"
          ? "Working for Moo right now"
          : engine.status === "error"
            ? "Stopped with a problem"
            : "Loaded and ready"
      : mode === "local"
        ? localMachine
          ? localMachine.ready
            ? "On and ready. A model is loaded the first time it is used."
            : localMachine.reachable
              ? "On, still getting ready"
              : "Not answering. Is the computer on, with Moo Local running?"
          : ""
        : "";
  const settingsTitle =
    mode === "local"
      ? `Settings for ${settings?.local_name ?? "the local machine"}`
      : mode === "cloud"
        ? "Settings for the cloud"
        : thisDevice
          ? "Settings for this device"
          : "Settings";

  return (
    <StepLayout
      title="AI engine"
      subtitle="Where Moo's listening, stories and voice are worked out — and how they sound."
      back={setup ? "/apikey" : "/dashboard"}
    >
      <div className="moo-card flex flex-col gap-1 border-2 border-moo-accent/40">
        <h2 className="font-semibold">In use right now</h2>
        <p className="text-sm">
          Moo&apos;s AI runs on <b>{settings ? where : "…"}</b>.
        </p>
        {inUse.map((r) => (
          <p key={r.what} className="text-sm text-moo-ink/80">
            <span className="inline-block w-20 text-moo-ink/60">{r.what}</span>
            {r.value}
          </p>
        ))}
        {lentOther && lent.length > 0 && (
          <p className="text-sm text-moo-ink/80">Its models are chosen on that device, on this same screen.</p>
        )}
        {loadState && <p className="text-xs text-moo-ink/60">{loadState}</p>}
      </div>

      {/* One list of every device that can do the Moo's AI: the one in use first, the others one tap
          away, and the ones that are off greyed out with when they were last seen. A device is added by
          setting it up: this one with the button below, a phone with the Moo app, a computer with Moo Local. */}
      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">Devices</h2>
        <p className="text-sm text-moo-ink/70">
          The phones, browsers and computers that do Moo&apos;s AI. The one in use is first; tap another to use it.
        </p>
        {devices.length === 0 && <p className="text-sm text-amber-700">No device yet. Set this one up below.</p>}
        {devices.map((d) => (
          <div key={d.key} className="flex items-stretch gap-2">
            <button
              type="button"
              disabled={busy || d.current || !d.use}
              onClick={() => d.use?.()}
              className={`flex-1 rounded-moo border px-3 py-2 text-left disabled:opacity-100 ${
                d.current ? "bg-moo-accent text-white border-moo-accent" : d.on ? "bg-white border-moo-ink/20" : "bg-white border-moo-ink/10 opacity-60"
              }`}
            >
              <div className="font-medium">{d.name}{d.current ? " · in use now" : ""}</div>
              <div className="text-xs opacity-80">{d.text}</div>
            </button>
            {!d.current && d.remembered && (
              <button
                type="button"
                title="Take it off the list"
                onClick={() => api.engineForget(d.name).then(() => setKnown((k) => k.filter((x) => x.name !== d.name))).catch(() => {})}
                className="rounded-moo border border-moo-ink/10 px-3 text-xs text-moo-ink/60"
              >
                Forget
              </button>
            )}
          </div>
        ))}
        {engines === null && <Spinner label="Looking for computers…" />}
        <div className="flex flex-col gap-2 mt-1">
          <Button onClick={chooseThisDevice} disabled={busy || thisDevice}>
            {thisDevice ? "This device is set up" : "Set up this device"}
          </Button>
          {here && !thisDevice && (
            <p className="text-xs text-moo-ink/60">
              {githubPages
                ? <>Opens the Moo&apos;s own page at <span className="font-mono">{mooHost}</span>. This device has to be on the same wifi as the Moo. </>
                : <>Opens the Moo&apos;s secure address first. </>}
              Your browser warns once — choose Advanced, then continue.
            </p>
          )}
          {crashed && (
            <p className="text-sm text-red-600">
              Last time this browser closed while loading {crashed}. That usually means the device ran out of memory.
              Choose smaller models under Settings for this device, then set it up again.
            </p>
          )}
          {thisDevice && engine && (
            <p className="text-sm">
              {engine.status === "loading"
                ? `Loading… ${Object.values(engine.progress).map((p) => p.text).join(" · ")}`
                : engine.status === "error"
                  ? engine.note
                  : `Ready · ${engine.model} · ${engine.jobsDone} jobs${engine.lastSeconds ? `, last ${engine.lastSeconds}s` : ""}`}
            </p>
          )}
          <Button variant="secondary" onClick={() => router.push("/ai/setup")}>
            Set up a local machine
          </Button>
          <p className="text-xs text-moo-ink/60">A phone is set up by opening the Moo app on it, on the same wifi as the Moo.</p>
        </div>
      </div>

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
          <h2 className="font-semibold">{settingsTitle}</h2>
          {lentOther && (
            <p className="text-sm text-moo-ink/70">
              The story, listening and voice models of a lent device are chosen on that device. Only the settings
              below are shared.
            </p>
          )}
          {options.mode === "browser" && options.qualities && (
            <Select
              label="Size preset, for devices left on Automatic"
              value={settings?.browser_quality ?? "auto"}
              choices={options.qualities}
              onChange={async (v) => {
                const s = await save({ browser_quality: v as Quality });
                if (choice === "this") reloadModels((s?.browser_quality as Quality) ?? "auto");
              }}
            />
          )}
          {options.mode === "browser" && !here && (
            <>
              {!thisDevice && (
                <p className="text-sm font-medium">What this device would load, if you tap This device:</p>
              )}
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
              {usesKitten2 && (
                <Select
                  label="Voice on this device"
                  value={kitten2Voice}
                  choices={kitten2Voices.map((v) => ({ id: v, label: v }))}
                  onChange={(v) => chooseMine({ kitten2Voice: v })}
                />
              )}
              <Select
                label="Memory on this device"
                value={mine.single}
                choices={MEMORY_CHOICES}
                onChange={(v) => chooseMine({ single: v })}
              />
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
          {!options.voices_by_engine && !(options.mode === "browser" && !here && (usesKitten || usesKitten2)) && options.mode !== "cloud" && options.voices && options.voices.length > 0 &&
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
