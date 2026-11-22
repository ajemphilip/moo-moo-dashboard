// Kitten TTS 2 (Pico, Nano, Mini) in the app, through a hidden web view.
//
// The small Kitten TTS 2 models run in a browser with kitten-tts-2-browser, the maker's port of their text
// front end to JavaScript. The app loads that library in a web view it keeps out of sight and asks it to
// speak. A web view is a process of its own with its own memory, so the voice does not take memory from the
// story model, and it needs no native ONNX runtime: the one for React Native could not be built for the
// iPhone on 22 November 2026, because its download server presented the wrong certificate.

import { useRef } from "react";
import { View } from "react-native";
import { WebView as WebViewImpl, type WebViewMessageEvent } from "react-native-webview";

// react-native-webview 14 types its class against an older React: under React 19 its props resolve to never.
const WebView: any = WebViewImpl;
type WebViewHandle = { injectJavaScript: (js: string) => void; reload: () => void };

const LIBRARY = "https://cdn.jsdelivr.net/gh/ajemphilip/kitten-tts-2-browser@v0.1.0/src/index.js";

const PAGE = `<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module">
const send = (m) => window.ReactNativeWebView.postMessage(JSON.stringify(m));
let lib = null, tts = null, loading = null, loadedModel = '';
const toB64 = (buf) => { const b = new Uint8Array(buf); let s = ''; for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000)); return btoa(s); };
async function load(model) {
  if (tts && loadedModel === model) return;
  lib = lib || await import('${LIBRARY}');
  if (tts) { await tts.release(); tts = null; }
  const files = new Set();
  tts = await lib.KittenTTS2.load({ model, onProgress: (p) => {
    if (p.stage === 'download' && p.file) files.add(p.file);
    send({ type: 'progress', text: p.stage === 'prepare' ? 'Loading…' : p.stage === 'ready' ? 'Ready' : 'Fetching ' + files.size + ' of 12 files' });
  } });
  loadedModel = model;
}
window.mooKitten = async (cmd) => {
  try {
    if (cmd.type === 'load') { loading = load(cmd.model); await loading; send({ id: cmd.id, ok: true }); }
    if (cmd.type === 'speak') {
      await loading;
      const wav = await tts.generateWav(cmd.text, { voice: cmd.voice });
      send({ id: cmd.id, ok: true, wav_b64: toB64(wav) });
    }
  } catch (e) { send({ id: cmd.id, ok: false, error: String(e && e.message || e) }); }
};
send({ type: 'ready' });
</script></body></html>`;

type Reply = { id?: number; ok?: boolean; error?: string; wav_b64?: string; type?: string; text?: string };

let view: WebViewHandle | null = null;
let pageReady: Promise<void> = new Promise(() => {});
let markReady: () => void = () => {};
let onProgress: (text: string) => void = () => {};
let nextId = 1;
const waiting = new Map<number, { resolve: (r: Reply) => void; reject: (e: Error) => void }>();

function resetReady() {
  pageReady = new Promise((res) => (markReady = res));
}
resetReady();

function ask(cmd: Record<string, unknown>): Promise<Reply> {
  const id = nextId++;
  return pageReady.then(
    () =>
      new Promise<Reply>((resolve, reject) => {
        waiting.set(id, { resolve, reject });
        view?.injectJavaScript(`window.mooKitten(${JSON.stringify({ ...cmd, id })}); true;`);
      })
  ).then((r) => {
    if (!r.ok) throw new Error(r.error || "Kitten 2 failed");
    return r;
  });
}

/** Load a Kitten 2 size in the hidden web view (the first time downloads it). */
export function loadKitten2(model: "pico" | "nano" | "mini", progress: (text: string) => void) {
  onProgress = progress;
  return ask({ type: "load", model });
}

/** Speak a line with the loaded Kitten 2: a 16 bit mono wav at 24 kHz, as base64. */
export async function speakKitten2(text: string, voice: string): Promise<string> {
  return (await ask({ type: "speak", text, voice })).wav_b64 ?? "";
}

/** The hidden web view. Mounted once, out of sight, for as long as the app runs. */
export function Kitten2Host() {
  const ref = useRef<WebViewHandle>(null);
  const onMessage = (e: WebViewMessageEvent) => {
    let m: Reply;
    try {
      m = JSON.parse(e.nativeEvent.data);
    } catch {
      return;
    }
    if (m.type === "ready") {
      view = ref.current;
      markReady();
    } else if (m.type === "progress") {
      onProgress(m.text ?? "");
    } else if (m.id !== undefined) {
      waiting.get(m.id)?.resolve(m);
      waiting.delete(m.id);
    }
  };
  return (
    <View style={{ width: 0, height: 0, overflow: "hidden" }} pointerEvents="none">
      <WebView
        ref={ref}
        source={{ html: PAGE, baseUrl: "https://moo.local.app/" }}
        originWhitelist={["*"]}
        javaScriptEnabled
        onMessage={onMessage}
        // A web view that the system closed for memory is started again; its next load fetches from cache.
        onContentProcessDidTerminate={() => {
          resetReady();
          ref.current?.reload();
        }}
        onRenderProcessGone={() => {
          resetReady();
          ref.current?.reload();
        }}
      />
    </View>
  );
}
