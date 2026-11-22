// The three models that run on the phone: one that writes, one that listens
// and one that speaks. All of them come from react-native-executorch and use
// the phone's own AI hardware, on iPhone and on Android alike: each model is
// asked for its DEFAULT file, which the library picks per platform (Core ML
// or MLX on an iPhone, XNNPACK on Android).

import * as Device from "expo-device";
import {
  createKokoroTextToSpeech,
  createLLMChatSession,
  createReverbTextToSpeech,
  createSupertonicTextToSpeech,
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
  minGB?: number; // the least memory a phone needs for this model
}

/** The phone's memory in GB, or 0 when the system does not say. */
export const PHONE_GB = Device.totalMemory ? Device.totalMemory / 1024 ** 3 : 0;

/** A model is offered only on a phone with enough memory for it. */
export const fits = (c: Choice) => !c.minGB || !PHONE_GB || PHONE_GB >= c.minGB - 0.25;

// Story models, smallest first. The percentages are from the Moo's own test on
// a computer (step 32 of the workbook); the LFM models were not in that test.
// The memory floors leave room for the voice, the listening model and the
// system, since a phone gives one app only part of its memory.
export const STORY_MODELS: (Choice & { config: () => any })[] = [
  { id: "lfm350m", label: "LFM2.5 350M — for small phones, about 0.3 GB", minGB: 2, config: () => M.llm.LFM2_5_350M.DEFAULT },
  { id: "lfm1b", label: "LFM2.5 1.2B — about 0.8 GB, new and not yet tested", minGB: 3, config: () => M.llm.LFM2_5_1_2B.DEFAULT },
  { id: "llama1b", label: "Llama 3.2 1B — about 1 GB, 57% in the test", minGB: 3, config: () => M.llm.LLAMA3_2_1B.DEFAULT },
  { id: "qwen3b", label: "Qwen 2.5 3B — about 2 GB, 82%", minGB: 6, config: () => M.llm.QWEN2_5_3B.DEFAULT },
  { id: "llama3b", label: "Llama 3.2 3B — about 2 GB, 89%, keeps the story rules well", minGB: 6, config: () => M.llm.LLAMA3_2_3B.DEFAULT },
  { id: "gemma4", label: "Gemma 4 E2B — about 3 GB, newest", minGB: 8, config: () => M.llm.GEMMA4_E2B.DEFAULT },
  { id: "qwen3_4b", label: "Qwen 3 4B — about 2.5 GB, largest", minGB: 8, config: () => M.llm.QWEN3_4B.DEFAULT },
];

export const LISTEN_MODELS: (Choice & { config: () => any })[] = [
  { id: "tiny", label: "Whisper tiny — quickest", config: () => M.speechToText.WHISPER.EN.TINY.DEFAULT },
  { id: "base", label: "Whisper base — hears better", minGB: 4, config: () => M.speechToText.WHISPER.EN.BASE.DEFAULT },
  { id: "small", label: "Whisper small — hears best", minGB: 8, config: () => M.speechToText.WHISPER.EN.SMALL.DEFAULT },
];

// Voice models. Kokoro was the maker's favourite on the Mac. Supertonic 3 is
// quick and bright; Reverb is the largest and reads like a narrator.
export const VOICE_MODELS: (Choice & { config: () => any; voices: Choice[] })[] = [
  {
    id: "kokoro",
    label: "Kokoro — natural, the favourite on the Mac",
    config: () => M.textToSpeech.KOKORO.EN_US.DEFAULT,
    voices: [
      { id: "af_heart", label: "Heart — warm" },
      { id: "af_sarah", label: "Sarah — calm" },
      { id: "af_river", label: "River" },
      { id: "am_michael", label: "Michael — gentle" },
      { id: "am_adam", label: "Adam" },
      { id: "am_santa", label: "Santa" },
    ],
  },
  {
    id: "supertonic",
    label: "Supertonic 3 — quick and bright",
    config: () => M.textToSpeech.SUPERTONIC.DEFAULT,
    voices: ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"].map((v) => ({
      id: v,
      label: `${v[0] === "F" ? "Woman" : "Man"} ${v[1]}`,
    })),
  },
  {
    id: "reverb",
    label: "Reverb — narrator voices, largest",
    minGB: 8,
    config: () => M.textToSpeech.REVERB.DEFAULT,
    voices: [
      { id: "en_female_narrator", label: "Woman, narrator" },
      { id: "en_male_narrator_1", label: "Man, narrator" },
      { id: "en_male_narrator_2", label: "Man, second narrator" },
      { id: "en_female_whisper", label: "Woman, whispering" },
    ],
  },
];

export const voicesOf = (engine: string): Choice[] =>
  (VOICE_MODELS.find((m) => m.id === engine) ?? VOICE_MODELS[0]).voices;

export interface Settings {
  story: string;
  listen: string;
  engine: string; // the voice model
  voice: string;
}

/** The starting choice for this phone, by its memory. A phone that does not
 *  say how much it has is treated as a small one. */
function defaults(): Settings {
  const gb = PHONE_GB;
  const v = { engine: "kokoro", voice: "af_heart" };
  if (gb >= 5.75) return { story: "llama3b", listen: "base", ...v };
  if (gb >= 3.75) return { story: "lfm1b", listen: "base", ...v };
  if (gb >= 2.75) return { story: "lfm1b", listen: "tiny", ...v };
  return { story: "lfm350m", listen: "tiny", ...v };
}

export const DEFAULTS: Settings = defaults();

/** A name for this phone in the Moo's list of lent devices. */
export const PHONE_NAME = `${Device.modelName ?? "Phone"} app${PHONE_GB ? `, ${Math.round(PHONE_GB)} GB` : ""}`;

type Progress = (part: "story" | "listening" | "voice", text: string) => void;

let storyFiles: any = null; // the story model's files on the phone
let storyId = "";
let stt: any = null;
let sttId = "";
let tts: any = null;
let ttsId = "";
let voice = DEFAULTS.voice;

const pct = (p: number) => `${Math.round(p * 100)}%`;

/** Fetch what is missing and load it. Called at the start and whenever a
 *  choice changes; a model that did not change stays loaded. */
export async function load(s: Settings, onProgress: Progress) {
  voice = s.voice;

  if (sttId !== s.listen) {
    stt?.dispose?.();
    stt = null;
    const pick = LISTEN_MODELS.find((m) => m.id === s.listen) ?? LISTEN_MODELS[0];
    onProgress("listening", "Fetching…");
    const files = await download(pick.config(), { onProgress: (p: number) => onProgress("listening", `Fetching ${pct(p)}`) });
    onProgress("listening", "Loading…");
    stt = await createWhisperSpeechToText(files);
    sttId = s.listen;
  }
  onProgress("listening", "Ready");

  if (ttsId !== s.engine) {
    tts?.dispose?.();
    tts = null;
    const pick = VOICE_MODELS.find((m) => m.id === s.engine) ?? VOICE_MODELS[0];
    onProgress("voice", "Fetching…");
    const files = await download(pick.config(), {
      onProgress: (p: number) => onProgress("voice", `Fetching ${pct(p)}`),
    });
    onProgress("voice", "Loading… the first time takes a while");
    const create: any =
      pick.id === "supertonic" ? createSupertonicTextToSpeech : pick.id === "reverb" ? createReverbTextToSpeech : createKokoroTextToSpeech;
    tts = await create(files);
    ttsId = pick.id;
  }
  onProgress("voice", "Ready");

  if (storyId !== s.story) {
    storyFiles = null;
    const pick = STORY_MODELS.find((m) => m.id === s.story) ?? STORY_MODELS[0];
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
  ttsId = "";
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
    // No thinking out loud: Qwen 3 spent all its words on it and gave no answer (21 November 2026).
    generationConfig: { maxNewTokens: maxTokens, temperature: json ? 0.2 : 0.8, enableThinking: false },
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

/** What Whisper heard, without the sounds it names and the loops it falls into.
 *  The Moo's chime before listening is in the recording, and Whisper on the
 *  phone wrote it as "(beeping)", then "(buzzing)" over and over, and that
 *  became the theme of a story (21 November 2026). */
export function cleanHeard(raw: string): string {
  let t = raw
    .replace(/\([^)]*\)|\[[^\]]*\]|\*[^*]*\*|♪+/g, " ") // (beeping) [music] *laughs* ♪
    .replace(/\s+/g, " ")
    .trim();
  // A phrase said three or more times in a row is a loop, not the child: keep it once.
  t = t.replace(/\b((?:\w+[\s,.!?'-]*){1,6}?)(?:\1){2,}/gi, "$1").trim();
  // Only punctuation or a dash left means nothing was said.
  return /[a-z0-9]/i.test(t) ? t.replace(/^[-–—\s]+/, "") : "";
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
    return { text: cleanHeard(String(await stt.transcribe(audio, { language: "en" }))) };
  }
  if (job.kind === "speak") {
    const parts: Float32Array[] = [];
    let rate = KOKORO_SAMPLE_RATE;
    const text = String(p.text ?? "");
    // A voice that belongs to another model (after a switch) falls back to its first voice.
    const own = voicesOf(ttsId);
    const v = own.some((c) => c.id === voice) ? voice : own[0].id;
    const options =
      ttsId === "supertonic" ? { voiceStyle: v, lang: "en", speed: 1.0 } :
      ttsId === "reverb" ? { voice: v, language: "en", speed: 1.0 } :
      { voice: v, speed: 1.0 };
    for await (const chunk of tts.synthesize(text, options)) {
      parts.push(chunk.audio);
      if (chunk.sampleRate) rate = chunk.sampleRate;
    }
    return { wav_b64: bytesToB64(floatsToWav(parts, rate)) };
  }
  throw new Error(`Unknown job ${job.kind}`);
}
