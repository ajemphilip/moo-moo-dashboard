// The three models that run on the phone: one that writes, one that listens
// and one that speaks. All of them come from react-native-executorch and use
// the phone's own AI hardware.

import {
  createKokoroTextToSpeech,
  createLLMChatSession,
  createWhisperSpeechToText,
  download,
  KOKORO_SAMPLE_RATE,
  models,
  setTelemetryEnabled,
} from "react-native-executorch";
import type { Job, JobResult } from "./moo";
import { b64ToBytes, bytesToB64, floatsToWav, wavTo16k } from "./wav";

// The library reports each download to its makers unless told not to.
setTelemetryEnabled(false);

const M: any = models;

export interface Choice {
  id: string;
  label: string;
}

// Story models, smallest first. The scores are from the Moo's own test of the
// same families on a computer (step 32 of the workbook); on the phone they
// are 4 bit versions, so expect a little less.
export const STORY_MODELS: (Choice & { config: () => any })[] = [
  { id: "llama1b", label: "Llama 3.2 1B — small and quick", config: () => M.llm.LLAMA3_2_1B.MLX_INT4 },
  { id: "qwen3b", label: "Qwen 2.5 3B — quick, good", config: () => M.llm.QWEN2_5_3B.MLX_INT4 },
  { id: "llama3b", label: "Llama 3.2 3B — keeps the story rules well", config: () => M.llm.LLAMA3_2_3B.MLX_INT4 },
  { id: "gemma4", label: "Gemma 4 E2B — newest", config: () => M.llm.GEMMA4_E2B.MLX_INT4 },
  { id: "qwen3_4b", label: "Qwen 3 4B — largest", config: () => M.llm.QWEN3_4B.MLX_INT4 },
];

export const LISTEN_MODELS: (Choice & { config: () => any })[] = [
  { id: "tiny", label: "Whisper tiny — quickest", config: () => M.speechToText.WHISPER.EN.TINY.COREML_FP16 },
  { id: "base", label: "Whisper base — good", config: () => M.speechToText.WHISPER.EN.BASE.COREML_FP16 },
  { id: "small", label: "Whisper small — hears best", config: () => M.speechToText.WHISPER.EN.SMALL.COREML_FP16 },
];

export const VOICES: Choice[] = [
  { id: "af_heart", label: "Heart — warm" },
  { id: "af_sarah", label: "Sarah — calm" },
  { id: "af_river", label: "River" },
  { id: "am_michael", label: "Michael — gentle" },
  { id: "am_adam", label: "Adam" },
  { id: "am_santa", label: "Santa" },
];

export interface Settings {
  story: string;
  listen: string;
  voice: string;
}

export const DEFAULTS: Settings = { story: "llama3b", listen: "base", voice: "af_heart" };

type Progress = (part: "story" | "listening" | "voice", text: string) => void;

let storyFiles: any = null; // the story model's files on the phone
let storyId = "";
let stt: any = null;
let sttId = "";
let tts: any = null;
let voice = DEFAULTS.voice;

const pct = (p: number) => `${Math.round(p * 100)}%`;

/** Fetch what is missing and load it. Called at the start and whenever a
 *  choice changes; a model that did not change stays loaded. */
export async function load(s: Settings, onProgress: Progress) {
  voice = s.voice;

  if (sttId !== s.listen) {
    stt?.dispose?.();
    stt = null;
    const pick = LISTEN_MODELS.find((m) => m.id === s.listen) ?? LISTEN_MODELS[1];
    onProgress("listening", "Fetching…");
    const files = await download(pick.config(), { onProgress: (p: number) => onProgress("listening", `Fetching ${pct(p)}`) });
    onProgress("listening", "Loading…");
    stt = await createWhisperSpeechToText(files);
    sttId = s.listen;
  }
  onProgress("listening", "Ready");

  if (!tts) {
    onProgress("voice", "Fetching…");
    const files = await download(M.textToSpeech.KOKORO.EN_US.COREML_FP32, {
      onProgress: (p: number) => onProgress("voice", `Fetching ${pct(p)}`),
    });
    onProgress("voice", "Loading… the first time takes a while");
    tts = await createKokoroTextToSpeech(files);
  }
  onProgress("voice", "Ready");

  if (storyId !== s.story) {
    storyFiles = null;
    const pick = STORY_MODELS.find((m) => m.id === s.story) ?? STORY_MODELS[2];
    onProgress("story", "Fetching…");
    storyFiles = await download(pick.config(), { onProgress: (p: number) => onProgress("story", `Fetching ${pct(p)}`) });
    storyId = s.story;
  }
  onProgress("story", "Ready");
}

export function unload() {
  stt?.dispose?.();
  tts?.dispose?.();
  stt = null;
  tts = null;
  sttId = "";
  storyFiles = null;
  storyId = "";
}

/** One answer from the story model for a list of chat messages. */
async function chat(messages: { role: string; content: string }[], maxTokens: number, json: boolean): Promise<string> {
  if (!storyFiles) throw new Error("The story model is not loaded");
  // The last message is sent; everything before it is the conversation so far.
  const earlier = messages.slice(0, -1);
  const last = messages[messages.length - 1];
  const session: any = await createLLMChatSession(storyFiles, {
    initialMessages: earlier as any,
    generationConfig: { maxNewTokens: maxTokens, temperature: json ? 0.2 : 0.8 },
  } as any);
  try {
    const turn = await session.sendMessage(last?.content ?? "");
    let text = String(turn.messages[turn.messages.length - 1]?.content ?? "").trim();
    if (json) {
      // There is no JSON mode on the phone: keep what stands between the braces.
      const a = text.indexOf("{");
      const b = text.lastIndexOf("}");
      if (a >= 0 && b > a) text = text.slice(a, b + 1);
    }
    return text;
  } finally {
    session.dispose();
  }
}

/** Do one job for the Moo. */
export async function runJob(job: Job): Promise<JobResult> {
  const p = job.payload;
  if (job.kind === "chat") {
    const json = p.response_format?.type === "json_object";
    return { content: await chat(p.messages ?? [], p.max_tokens ?? 256, json) };
  }
  if (job.kind === "moderate") {
    let safe = true;
    try {
      const answer = await chat(
        [
          { role: "system", content: 'Answer as JSON with one key, "safe", true or false.' },
          {
            role: "user",
            content:
              "Is this text safe to read aloud to a young child? It must not contain violence, " +
              `fear, cruelty, adult themes or rude language.\n\n${p.text}`,
          },
        ],
        20,
        true
      );
      safe = JSON.parse(answer).safe !== false;
    } catch {
      /* treated as safe, as on the other engines */
    }
    return { flagged: !safe };
  }
  if (job.kind === "transcribe") {
    const audio = wavTo16k(b64ToBytes(String(p.wav_b64 ?? "")));
    if (audio.length < 1600) return { text: "" };
    return { text: String(await stt.transcribe(audio, { language: "en" })).trim() };
  }
  if (job.kind === "speak") {
    const parts: Float32Array[] = [];
    for await (const chunk of tts.synthesize(String(p.text ?? ""), { voice, speed: 1.0 })) {
      parts.push(chunk.audio);
    }
    return { wav_b64: bytesToB64(floatsToWav(parts, KOKORO_SAMPLE_RATE)) };
  }
  throw new Error(`Unknown job ${job.kind}`);
}
