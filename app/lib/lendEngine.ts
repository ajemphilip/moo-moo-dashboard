/**
 * "Lend this device to Moo": this browser does Moo's AI work.
 *
 * Three models run here, on the device's own graphics chip:
 *   story and checks  — a small Llama 3.2 through WebLLM
 *   the child's speech — Whisper through transformers.js
 *   Moo's voice        — Kokoro through kokoro-js
 *
 * The page asks the Moo for work (/engine/next), does it, and posts the
 * result (/engine/result). The Moo never has to reach this device.
 */

export type JobKind = "chat" | "transcribe" | "speak" | "moderate";

export interface Job {
  id: string;
  kind: JobKind;
  payload: Record<string, unknown>;
}

export interface LoadProgress {
  part: "story" | "speech" | "voice";
  text: string;
  done: boolean;
}

const HEADERS = { "Content-Type": "application/json", "X-Moo-Setup": "1" };

// The AI libraries are loaded from a CDN in the browser, not bundled into the
// Moo's own files. Exact versions, so every device runs the same code.
const LIB = {
  webllm: "https://esm.run/@mlc-ai/web-llm@0.2.79",
  transformers: "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.5",
  kokoro: "https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/dist/kokoro.web.js",
  // Kitten TTS written for phone browsers: no wasm, about 130 MB at its peak.
  kitten: "https://cdn.jsdelivr.net/npm/kitten-tts-webgpu@0.1.1/dist-lib/index.js",
};
const load = (url: string): Promise<any> => import(/* webpackIgnore: true */ url);

/* ------------------------------------------------------------------ models */

let llm: any = null;
let asr: any = null;
let tts: any = null;

export function isIOS(): boolean {
  return /iPhone|iPad|iPod/.test(navigator.userAgent);
}

export type Quality = "instant" | "auto" | "fast" | "better";

async function pickLlm(quality: Quality = "auto", wanted = ""): Promise<string> {
  // Phones get the 1B model, computers the 3B one. Use 16-bit shaders only
  // where the graphics chip supports them.
  let f16 = false;
  try {
    const adapter = await (navigator as any).gpu?.requestAdapter();
    f16 = !!adapter?.features?.has("shader-f16");
  } catch {
    /* keep f32 */
  }
  if (wanted) return `${wanted}-${f16 ? "q4f16_1" : "q4f32_1"}-MLC`;
  // An iPhone or iPad gets Qwen 2.5 0.5B unless another model is chosen on
  // the device. The smaller SmolLM2 360M fits more easily, but it read the
  // instructions back instead of writing a story (21 November 2026).
  if (isIOS()) return `Qwen2.5-0.5B-Instruct-${f16 ? "q4f16_1" : "q4f32_1"}-MLC`;
  if (quality === "instant") return `Qwen2.5-0.5B-Instruct-${f16 ? "q4f16_1" : "q4f32_1"}-MLC`;
  const memory = (navigator as any).deviceMemory as number | undefined;
  const small =
    quality === "fast" || (quality === "auto" && (isIOS() || (memory !== undefined && memory < 8)));
  const size = small ? "1B" : "3B";
  return `Llama-3.2-${size}-Instruct-${f16 ? "q4f16_1" : "q4f32_1"}-MLC`;
}

let llmLoaded = "";
let asrLoaded = "";
let ttsLoaded = "";

/* ------------------------------------------------- choices kept on this device */

// What this device runs is its own business: a phone needs smaller models than
// a laptop, so the choice is stored in this browser, not on the Moo.
export interface DeviceChoice {
  story: string; // "" = follow the size preset
  listen: string; // "" = follow the size preset
  voice: string; // "" = automatic: Kitten on iPhone and iPad, Kokoro elsewhere
  kittenVoice: string; // the Kitten voice of this device
  single: string; // "on" = one model at a time, anything else = all at once
}
const CHOICE_KEY = "moo_device_models";
const LOADING_KEY = "moo_device_loading";

export const STORY_CHOICES: { id: string; label: string }[] = [
  { id: "", label: "Automatic — by the size preset (Qwen 2.5 0.5B on iPhone and iPad)" },
  { id: "SmolLM2-360M-Instruct", label: "SmolLM2 360M — smallest, about 0.4 GB; too weak, repeats its instructions" },
  { id: "Qwen2.5-0.5B-Instruct", label: "Qwen 2.5 0.5B — about 0.6 GB, plain" },
  { id: "Llama-3.2-1B-Instruct", label: "Llama 3.2 1B — about 1 GB" },
  { id: "Qwen2.5-1.5B-Instruct", label: "Qwen 2.5 1.5B — about 1.4 GB" },
  { id: "gemma-2-2b-it", label: "Gemma 2 2B — about 2 GB, tablets and computers" },
  { id: "Llama-3.2-3B-Instruct", label: "Llama 3.2 3B — about 2.5 GB, computers" },
  { id: "Qwen2.5-3B-Instruct", label: "Qwen 2.5 3B — about 2.5 GB, computers" },
];
export const LISTEN_CHOICES: { id: string; label: string }[] = [
  { id: "", label: "Automatic — by the size preset" },
  { id: "onnx-community/whisper-tiny.en", label: "Whisper tiny — smallest and quickest" },
  { id: "onnx-community/whisper-base.en", label: "Whisper base — hears better" },
];

export const VOICE_CHOICES: { id: string; label: string }[] = [
  { id: "", label: "Automatic — Kitten mini on iPhone and iPad, Kokoro on computers" },
  { id: "kitten-nano", label: "Kitten nano — lightest, 24 MB, for phones" },
  { id: "kitten-micro", label: "Kitten micro — 41 MB, for phones" },
  { id: "kitten-mini", label: "Kitten mini — 78 MB, best Kitten" },
  { id: "kokoro", label: "Kokoro — most natural, for computers; too heavy for a phone browser" },
  { id: "kokoro-gpu", label: "Kokoro on the graphics chip — for computers; too heavy for a phone browser" },
];
export const KITTEN_VOICES = ["Bruno", "Hugo", "Jasper", "Leo", "Kiki", "Luna", "Rosie", "Bella"];
const NO_CHOICE: DeviceChoice = { story: "", listen: "", voice: "", kittenVoice: "Bruno", single: "" };
export const MEMORY_CHOICES: { id: string; label: string }[] = [
  { id: "", label: "All three at once — the usual way" },
  { id: "on", label: "One model at a time — an experiment, slower" },
];

export function deviceChoice(): DeviceChoice {
  try {
    return { ...NO_CHOICE, ...JSON.parse(localStorage.getItem(CHOICE_KEY) || "{}") };
  } catch {
    return { ...NO_CHOICE };
  }
}

/** The voice model this device runs: a Kitten size, or "kokoro". */
function voiceEngine(): string {
  return deviceChoice().voice || (isIOS() ? "kitten-mini" : "kokoro");
}

export function setDeviceChoice(c: Partial<DeviceChoice>) {
  try {
    localStorage.setItem(CHOICE_KEY, JSON.stringify({ ...deviceChoice(), ...c }));
  } catch {
    /* not stored */
  }
}

/** What was being loaded when the browser last died, or null. A phone browser
 *  that runs out of memory just closes the page, so a note is left before
 *  each model loads and removed when it has loaded. */
export function crashedWhileLoading(): string | null {
  try {
    return localStorage.getItem(LOADING_KEY);
  } catch {
    return null;
  }
}

export function clearCrash() {
  try {
    localStorage.removeItem(LOADING_KEY);
  } catch {
    /* nothing to clear */
  }
}

function report(stage: string) {
  fetch("/engine/report", {
    method: "POST",
    headers: HEADERS,
    body: JSON.stringify({ device: navigator.userAgent.slice(0, 120), stage }),
    keepalive: true,
  }).catch(() => {});
}

function mark(stage: string | null) {
  try {
    if (stage) localStorage.setItem(LOADING_KEY, stage);
    else localStorage.removeItem(LOADING_KEY);
  } catch {
    /* not stored */
  }
  // Tell the Moo, so the stage shows in its log even if this page dies.
  fetch("/engine/report", {
    method: "POST",
    headers: HEADERS,
    body: JSON.stringify({ device: navigator.userAgent.slice(0, 120), stage: stage ?? "loaded" }),
    keepalive: true,
  }).catch(() => {});
}

type Progress = (p: LoadProgress) => void;
let lastQuality: Quality = "auto";
let lastProgress: Progress = () => {};

async function loadStory(onProgress: Progress, quality: Quality): Promise<string> {
  const choice = deviceChoice();
  const llmId = await pickLlm(quality, choice.story);

  if (llm && llmLoaded !== llmId) {
    await llm.unload?.();
    llm = null;
  }
  if (!llm) {
    const webllm = await load(LIB.webllm);
    onProgress({ part: "story", text: `Loading ${llmId}…`, done: false });
    mark(`the story model ${llmId}`);
    llm = await webllm.CreateMLCEngine(
      llmId,
      {
        initProgressCallback: (r: { text: string }) =>
          onProgress({ part: "story", text: r.text, done: false }),
      },
      // The memory a model keeps for its context grows with the window. Moo's
      // prompts are short, so a phone gets half the usual window.
      isIOS() ? { context_window_size: 2048 } : undefined
    );
  }
  llmLoaded = llmId;
  onProgress({ part: "story", text: `${llmId} ready`, done: true });
  return llmId;
}

async function loadListen(onProgress: Progress, quality: Quality) {
  const choice = deviceChoice();
  const asrId =
    choice.listen || (quality === "instant" || isIOS() ? "onnx-community/whisper-tiny.en" : "onnx-community/whisper-base.en");
  if (asr && asrLoaded !== asrId) {
    await asr.dispose?.();
    asr = null;
  }
  if (!asr) {
    const { pipeline, env } = await load(LIB.transformers);
    // One thread on a phone: several threads share one large block of memory
    // that has to be set aside in advance, and a phone often refuses it.
    if (isIOS() && env?.backends?.onnx?.wasm) env.backends.onnx.wasm.numThreads = 1;
    onProgress({ part: "speech", text: "Loading Whisper…", done: false });
    mark(`the listening model ${asrId.split("/")[1]}${isIOS() ? " (on the processor)" : ""}`);
    asrLoaded = asrId;
    // On an iPhone or iPad the graphics chip is already full with the story
    // model: Whisper loaded there closed the page (21 November 2026). There
    // it runs on the processor, in its small 8 bit form.
    const light = isIOS();
    asr = await pipeline("automatic-speech-recognition", asrId, {
      device: light ? "wasm" : "webgpu",
      dtype: light ? "q8" : { encoder_model: "fp32", decoder_model_merged: "q4" },
      progress_callback: (p: any) => {
        if (p.status === "progress" && p.file)
          onProgress({ part: "speech", text: `${p.file} ${Math.round(p.progress)}%`, done: false });
      },
    } as any);
  }
  onProgress({ part: "speech", text: "Whisper ready", done: true });
}

async function loadVoice(onProgress: Progress) {
  const wantVoice = voiceEngine();
  if (tts && ttsLoaded !== wantVoice) tts = null;
  if (!tts && wantVoice.startsWith("kitten-")) {
    // Kokoro closed the page on the iPhone 16 Pro even with the smallest
    // story model (21 November 2026). Kitten runs on the graphics chip
    // without wasm and needs far less memory.
    const lib = await load(LIB.kitten);
    const model = wantVoice.slice("kitten-".length);
    onProgress({ part: "voice", text: `Loading the Kitten ${model} voice…`, done: false });
    mark(`the Kitten ${model} voice`);
    // The first sentence downloads the model and starts the engine.
    await lib.textToSpeech("Hello.", {
      model,
      voice: deviceChoice().kittenVoice,
      onProgress: (stage: string) => onProgress({ part: "voice", text: stage, done: false }),
    });
    tts = { kitten: lib, model };
  } else if (!tts) {
    const { KokoroTTS } = await load(LIB.kokoro);
    const gpu = wantVoice === "kokoro-gpu";
    onProgress({ part: "voice", text: "Loading Kokoro voice…", done: false });
    mark(gpu ? "the Kokoro voice on the graphics chip" : "the Kokoro voice");
    tts = await KokoroTTS.from_pretrained("onnx-community/Kokoro-82M-v1.0-ONNX", {
      dtype: gpu ? "fp16" : "q8",
      device: gpu ? "webgpu" : "wasm",
      progress_callback: (p: any) => {
        if (p.status === "progress" && p.file)
          onProgress({ part: "voice", text: `${p.file} ${Math.round(p.progress)}%`, done: false });
      },
    } as any);
  }
  ttsLoaded = wantVoice;
  onProgress({ part: "voice", text: "Voice ready", done: true });
}

async function unloadStory() {
  if (!llm) return;
  await llm.unload?.();
  llm = null;
}

async function unloadListen() {
  if (!asr) return;
  await asr.dispose?.();
  asr = null;
}

async function unloadVoice() {
  // Kitten has no way to be unloaded and is small, so it stays.
  if (!tts || tts.kitten) return;
  await tts.model?.dispose?.();
  tts = null;
}

/** One model at a time: a phone browser gives a page too little memory for
 *  all three, so only the model the next job needs is kept, and the others
 *  are loaded again from the device's storage when their turn comes. */
export function oneAtATime(): boolean {
  // Not the default: on the iPhone 16 Pro it gained nothing. Kokoro closed the
  // page even when loaded alone, and the set that does fit, fits all at once.
  return deviceChoice().single === "on";
}

/** Make sure the model for this kind of job is loaded, and in the
 *  one-at-a-time mode that the others are not. */
async function need(part: "story" | "speech" | "voice") {
  if (oneAtATime()) {
    if (part !== "story") await unloadStory();
    if (part !== "voice") await unloadVoice();
  }
  const missing = (part === "story" && !llm) || (part === "speech" && !asr) || (part === "voice" && !tts);
  if (!missing) return;
  if (part === "story") await loadStory(lastProgress, lastQuality);
  if (part === "speech") await loadListen(lastProgress, lastQuality);
  if (part === "voice") await loadVoice(lastProgress);
  mark(null);
}

export async function loadModels(onProgress: Progress, quality: Quality = "auto"): Promise<string> {
  lastQuality = quality;
  lastProgress = onProgress;
  if (oneAtATime()) {
    // Fetch each model once, so that later swaps come from the device's
    // storage, and end with the story model, which the first job needs.
    await unloadStory();
    await loadVoice(onProgress);
    await unloadVoice();
    await loadListen(onProgress, quality); // small, and stays loaded
    const id = await loadStory(onProgress, quality);
    mark(null);
    return `${id}, one model at a time`;
  }
  if (isIOS()) {
    // On a phone Whisper goes first. It needs one block of the processor's
    // memory, and after the story model was loaded that block was sometimes
    // refused ("RangeError: Out of memory", 21 November 2026).
    await loadListen(onProgress, quality);
    await loadVoice(onProgress);
    const id = await loadStory(onProgress, quality);
    mark(null);
    return id;
  }
  const llmId = await loadStory(onProgress, quality);
  await loadListen(onProgress, quality);
  await loadVoice(onProgress);
  mark(null);
  return llmId;
}

/* -------------------------------------------------------------------- jobs */

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

/** 16-bit PCM WAV -> mono Float32 at 16 kHz, as Whisper wants it. */
function wavTo16k(bytes: Uint8Array): Float32Array {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = 12;
  let rate = 16000;
  let channels = 1;
  let dataStart = 44;
  let dataLen = bytes.byteLength - 44;
  while (pos + 8 <= bytes.byteLength) {
    const id = String.fromCharCode(...bytes.subarray(pos, pos + 4));
    const size = view.getUint32(pos + 4, true);
    if (id === "fmt ") {
      channels = view.getUint16(pos + 10, true);
      rate = view.getUint32(pos + 12, true);
    } else if (id === "data") {
      dataStart = pos + 8;
      dataLen = Math.min(size, bytes.byteLength - dataStart);
      break;
    }
    pos += 8 + size + (size % 2);
  }
  const frames = Math.floor(dataLen / 2 / channels);
  const mono = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    let sum = 0;
    for (let c = 0; c < channels; c++) sum += view.getInt16(dataStart + (i * channels + c) * 2, true);
    mono[i] = sum / channels / 32768;
  }
  if (rate === 16000) return mono;
  const n = Math.round((frames * 16000) / rate);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i * rate) / 16000;
    const i0 = Math.floor(x);
    const i1 = Math.min(i0 + 1, frames - 1);
    out[i] = mono[i0] + (mono[i1] - mono[i0]) * (x - i0);
  }
  return out;
}

async function runJob(job: Job): Promise<Record<string, unknown>> {
  const p = job.payload as any;
  await loadingNow.catch(() => {});
  await need(job.kind === "transcribe" ? "speech" : job.kind === "speak" ? "voice" : "story");
  if (job.kind === "chat") {
    const req: any = { messages: p.messages, max_tokens: p.max_tokens ?? 256, temperature: 0.8 };
    if (p.response_format?.type === "json_object") req.response_format = { type: "json_object" };
    const r = await llm.chat.completions.create(req);
    return { content: r.choices?.[0]?.message?.content ?? "" };
  }
  if (job.kind === "moderate") {
    const r = await llm.chat.completions.create({
      messages: [
        { role: "system", content: 'Answer as JSON with one key, "safe", true or false.' },
        {
          role: "user",
          content:
            "Is this text safe to read aloud to a young child? It must not contain violence, " +
            `fear, cruelty, adult themes or rude language.\n\n${p.text}`,
        },
      ],
      max_tokens: 20,
      response_format: { type: "json_object" },
    });
    let safe = true;
    try {
      safe = JSON.parse(r.choices?.[0]?.message?.content || "{}").safe !== false;
    } catch {
      /* treat as safe */
    }
    return { flagged: !safe };
  }
  if (job.kind === "transcribe") {
    const audio = wavTo16k(b64ToBytes(p.wav_b64));
    if (audio.length < 1600) return { text: "" };
    const r = await asr(audio);
    return { text: (r?.text ?? "").trim() };
  }
  if (job.kind === "speak" && tts?.kitten) {
    return { wav_b64: bytesToB64(await kittenSpeak(String(p.text ?? ""))) };
  }
  if (job.kind === "speak") {
    const voice = typeof p.voice === "string" && /^[ab][fm]_[a-z]+$/.test(p.voice) ? p.voice : "af_heart";
    const audio = await tts.generate(String(p.text ?? ""), { voice });
    return { wav_b64: bytesToB64(audio.toWav()) };
  }
  throw new Error(`Unknown job ${job.kind}`);
}

/** Speak with Kitten, sentence by sentence, joined into one wav. */
async function kittenSpeak(text: string): Promise<ArrayBuffer> {
  // One sentence at a time: given several at once, Kitten runs them together
  // without a pause ("scared of the dark one night a firefly came").
  const pieces: string[] = text.match(/[^.!?]+[.!?]*\s*/g) ?? [text];
  const chunks: Uint8Array[] = [];
  let rate = 24000;
  for (const piece of pieces) {
    if (!piece.trim()) continue;
    const blob: Blob = await tts.kitten.textToSpeech(piece.trim(), {
      model: tts.model,
      voice: deviceChoice().kittenVoice,
    });
    const buf = new Uint8Array(await blob.arrayBuffer());
    rate = new DataView(buf.buffer).getUint32(24, true);
    chunks.push(buf.subarray(44)); // 16 bit mono samples after the wav header
    chunks.push(new Uint8Array(Math.round(rate * 0.3) * 2)); // a breath between sentences
  }
  const size = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(44 + size);
  const v = new DataView(out.buffer);
  const tag = (o: number, t: string) => [...t].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  tag(0, "RIFF"); v.setUint32(4, 36 + size, true); tag(8, "WAVE"); tag(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  tag(36, "data"); v.setUint32(40, size, true);
  let o = 44;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out.buffer;
}

/* -------------------------------------------------------------------- loop */

export interface LoopEvents {
  onJob: (job: Job | null, seconds?: number) => void;
  onError: (message: string) => void;
}

// The id the Moo gave this browser, so that stopping can say goodbye at once.
let lentId = "";

export async function lend(name: string, device: string, stop: () => boolean, ev: LoopEvents) {
  let engineId = "";
  const hello = async () => {
    const r = await fetch("/engine/hello", {
      method: "POST",
      headers: HEADERS,
      body: JSON.stringify({ name, device }),
    });
    engineId = (await r.json()).engine_id;
    lentId = engineId;
  };
  await hello();
  while (!stop()) {
    let job: Job | null = null;
    try {
      const r = await fetch(`/engine/next?engine_id=${engineId}`);
      if (r.status === 404) {
        await hello();
        continue;
      }
      job = (await r.json()).job;
    } catch (e) {
      ev.onError("Lost the Moo for a moment, trying again…");
      await new Promise((res) => setTimeout(res, 3000));
      continue;
    }
    if (!job) continue;
    ev.onJob(job);
    const started = performance.now();
    let body: any;
    try {
      body = { engine_id: engineId, job_id: job.id, ok: true, result: await runJob(job) };
    } catch (e: any) {
      body = { engine_id: engineId, job_id: job.id, ok: false, error: String(e?.message ?? e) };
      ev.onError(`${job.kind}: ${body.error}`);
    }
    await fetch("/engine/result", { method: "POST", headers: HEADERS, body: JSON.stringify(body) });
    ev.onJob(null, (performance.now() - started) / 1000);
  }
}


/* --------------------------------------------------------------- controller */
// One engine per browser tab, shared by every page of the Moo website, so the
// device keeps working for Moo while the parent moves between screens.

export interface EngineState {
  status: "off" | "loading" | "ready" | "working" | "error";
  progress: Record<string, LoadProgress>;
  model: string;
  jobsDone: number;
  lastSeconds: number | null;
  current: Job | null;
  note: string | null;
}

const THIS_DEVICE_KEY = "moo_this_device";
let state: EngineState = {
  status: "off", progress: {}, model: "", jobsDone: 0, lastSeconds: null, current: null, note: null,
};
const listeners = new Set<(s: EngineState) => void>();
let stopFlag = false;
let running = false;
let wake: any = null;

function update(patch: Partial<EngineState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l(state));
}

export function engineState(): EngineState {
  return state;
}

export function subscribe(fn: (s: EngineState) => void): () => void {
  listeners.add(fn);
  fn(state);
  return () => listeners.delete(fn);
}

export function thisDeviceWanted(): boolean {
  try {
    return localStorage.getItem(THIS_DEVICE_KEY) === "1";
  } catch {
    return false;
  }
}

export function canRunHere(): string | null {
  if (typeof window === "undefined") return "Not in a browser";
  if (process.env.NEXT_PUBLIC_GITHUB_PAGES === "true") return "Open the Moo's own address to use this device.";
  if (!window.isSecureContext) return "This needs the Moo's secure address (https).";
  if (!(navigator as any).gpu) return "This browser has no WebGPU, so it cannot run Moo's AI.";
  return null;
}

// Model changes wait in line, and a job waits for them, so a job never runs
// while its model is being swapped.
let loadingNow: Promise<unknown> = Promise.resolve();

/** Apply the models chosen on this device without reloading the page. A model
 *  that did not change stays as it is. */
export async function reloadModels(quality: Quality = lastQuality) {
  if (!running) return;
  update({ status: "loading", note: null });
  const run = loadingNow
    .catch(() => {})
    .then(() => loadModels((p) => update({ progress: { ...state.progress, [p.part]: p } }), quality));
  loadingNow = run;
  try {
    const id = await run;
    update({ model: id as string, status: "ready" });
  } catch (e: any) {
    clearCrash();
    report(`error while changing models: ${String(e?.message ?? e).slice(0, 100)}`);
    update({ status: "error", note: `Could not load the models: ${e?.message ?? e}` });
  }
}

export async function startThisDevice(name: string, quality: Quality = "auto") {
  try {
    localStorage.setItem(THIS_DEVICE_KEY, "1");
  } catch {
    /* not stored */
  }
  // Already lending (or stopped a moment ago, with its loop still waiting on
  // the Moo): keep that loop and bring the models in line with the choices.
  if (running) {
    stopFlag = false;
    await reloadModels(quality);
    return;
  }
  const problem = canRunHere();
  if (problem) {
    update({ status: "error", note: problem });
    return;
  }
  running = true;
  stopFlag = false;
  try {
    wake = await (navigator as any).wakeLock?.request("screen");
  } catch {
    /* keep going without */
  }
  update({ status: "loading", note: null });
  try {
    const id = await loadModels((p) => update({ progress: { ...state.progress, [p.part]: p } }), quality);
    update({ model: id, status: "ready" });
  } catch (e: any) {
    running = false;
    clearCrash(); // an error that was caught is not a crash
    report(`error while loading: ${String(e?.message ?? e).slice(0, 100)}`);
    update({ status: "error", note: `Could not load the models: ${e?.message ?? e}` });
    return;
  }
  lend(name, navigator.userAgent.slice(0, 120), () => stopFlag, {
    onJob: (job, seconds) => {
      if (job) update({ current: job, status: "working" });
      else
        update({
          current: null,
          status: "ready",
          jobsDone: state.jobsDone + 1,
          lastSeconds: seconds !== undefined ? Math.round(seconds * 10) / 10 : state.lastSeconds,
        });
    },
    onError: (m) => update({ note: m }),
  })
    .catch((e) => update({ status: "error", note: String(e?.message ?? e) }))
    .finally(() => {
      running = false;
    });
}

export function stopThisDevice() {
  try {
    localStorage.removeItem(THIS_DEVICE_KEY);
  } catch {
    /* ignore */
  }
  stopFlag = true;
  // Leave the Moo's list of lent devices now, not 40 seconds from now.
  if (lentId) {
    fetch("/engine/bye", { method: "POST", headers: HEADERS, body: JSON.stringify({ engine_id: lentId }), keepalive: true }).catch(() => {});
    lentId = "";
  }
  wake?.release?.().catch(() => {});
  update({ status: "off", current: null });
}
