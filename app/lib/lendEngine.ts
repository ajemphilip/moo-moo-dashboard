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
};
const load = (url: string): Promise<any> => import(/* webpackIgnore: true */ url);

/* ------------------------------------------------------------------ models */

let llm: any = null;
let asr: any = null;
let tts: any = null;

export function isIOS(): boolean {
  return /iPhone|iPad|iPod/.test(navigator.userAgent);
}

async function pickLlm(): Promise<string> {
  // Phones get the 1B model, computers the 3B one. Use 16-bit shaders only
  // where the graphics chip supports them.
  let f16 = false;
  try {
    const adapter = await (navigator as any).gpu?.requestAdapter();
    f16 = !!adapter?.features?.has("shader-f16");
  } catch {
    /* keep f32 */
  }
  const memory = (navigator as any).deviceMemory as number | undefined;
  const small = isIOS() || (memory !== undefined && memory < 8);
  const size = small ? "1B" : "3B";
  return `Llama-3.2-${size}-Instruct-${f16 ? "q4f16_1" : "q4f32_1"}-MLC`;
}

export async function loadModels(onProgress: (p: LoadProgress) => void): Promise<string> {
  const llmId = await pickLlm();

  if (!llm) {
    const webllm = await load(LIB.webllm);
    onProgress({ part: "story", text: `Loading ${llmId}…`, done: false });
    llm = await webllm.CreateMLCEngine(llmId, {
      initProgressCallback: (r: { text: string }) =>
        onProgress({ part: "story", text: r.text, done: false }),
    });
  }
  onProgress({ part: "story", text: `${llmId} ready`, done: true });

  if (!asr) {
    const { pipeline } = await load(LIB.transformers);
    onProgress({ part: "speech", text: "Loading Whisper…", done: false });
    asr = await pipeline("automatic-speech-recognition", "onnx-community/whisper-base.en", {
      device: "webgpu",
      dtype: { encoder_model: "fp32", decoder_model_merged: "q4" },
      progress_callback: (p: any) => {
        if (p.status === "progress" && p.file)
          onProgress({ part: "speech", text: `${p.file} ${Math.round(p.progress)}%`, done: false });
      },
    } as any);
  }
  onProgress({ part: "speech", text: "Whisper ready", done: true });

  if (!tts) {
    const { KokoroTTS } = await load(LIB.kokoro);
    onProgress({ part: "voice", text: "Loading Kokoro voice…", done: false });
    tts = await KokoroTTS.from_pretrained("onnx-community/Kokoro-82M-v1.0-ONNX", {
      dtype: "q8",
      device: "wasm",
      progress_callback: (p: any) => {
        if (p.status === "progress" && p.file)
          onProgress({ part: "voice", text: `${p.file} ${Math.round(p.progress)}%`, done: false });
      },
    } as any);
  }
  onProgress({ part: "voice", text: "Kokoro voice ready", done: true });
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
  if (job.kind === "speak") {
    const audio = await tts.generate(String(p.text ?? ""), { voice: "af_heart" });
    return { wav_b64: bytesToB64(audio.toWav()) };
  }
  throw new Error(`Unknown job ${job.kind}`);
}

/* -------------------------------------------------------------------- loop */

export interface LoopEvents {
  onJob: (job: Job | null, seconds?: number) => void;
  onError: (message: string) => void;
}

export async function lend(name: string, device: string, stop: () => boolean, ev: LoopEvents) {
  let engineId = "";
  const hello = async () => {
    const r = await fetch("/engine/hello", {
      method: "POST",
      headers: HEADERS,
      body: JSON.stringify({ name, device }),
    });
    engineId = (await r.json()).engine_id;
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
