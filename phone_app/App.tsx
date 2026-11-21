// Moo AI: this phone does the listening, the stories and the voice for a Moo
// storyteller on the same home network.

import { useEffect, useRef, useState } from "react";
import { Pressable, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { StatusBar } from "expo-status-bar";
import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import { DEFAULTS, LISTEN_MODELS, STORY_MODELS, VOICES, load, runJob, unload, type Choice, type Settings } from "./src/ai";
import { lend, report, type Job } from "./src/moo";

const KIND: Record<string, string> = { chat: "writing", moderate: "checking", transcribe: "listening", speak: "speaking" };

function Picker({ title, choices, value, onChange, disabled }: {
  title: string; choices: Choice[]; value: string; onChange: (id: string) => void; disabled?: boolean;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.h2}>{title}</Text>
      {choices.map((c) => (
        <Pressable
          key={c.id}
          disabled={disabled}
          onPress={() => onChange(c.id)}
          style={[styles.choice, value === c.id && styles.choiceOn, disabled && { opacity: 0.5 }]}
        >
          <Text style={[styles.choiceText, value === c.id && styles.choiceTextOn]}>{c.label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

export default function App() {
  const [address, setAddress] = useState("http://moo.local:8000");
  const [settings, setSettings] = useState<Settings>(DEFAULTS);
  const [status, setStatus] = useState<"off" | "loading" | "ready" | "working" | "error">("off");
  const [parts, setParts] = useState<Record<string, string>>({});
  const [line, setLine] = useState("");
  const [jobs, setJobs] = useState(0);
  const [last, setLast] = useState("");
  const stop = useRef(true);
  const base = address.trim().replace(/\/+$/, "");

  const prepare = async (s: Settings) => {
    setStatus("loading");
    setLine("");
    try {
      await load(s, (part, text) => setParts((p) => ({ ...p, [part]: text })));
      setStatus("ready");
      return true;
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      setStatus("error");
      setLine(`Could not load: ${msg}`);
      report(base, `app error while loading: ${msg.slice(0, 100)}`);
      return false;
    }
  };

  const start = async () => {
    if (!stop.current) return;
    stop.current = false;
    await activateKeepAwakeAsync().catch(() => {});
    report(base, "app starting");
    if (!(await prepare(settings))) {
      stop.current = true;
      return;
    }
    // Tell the Moo to take its AI from a lent device.
    fetch(`${base}/ai`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Moo-Setup": "1" },
      body: JSON.stringify({ mode: "browser" }),
    }).catch(() => {});
    report(base, "app loaded");
    lend(base, "iPhone app", runJob, () => stop.current, {
      onState: setLine,
      onJob: (job: Job | null, seconds?: number) => {
        if (job) {
          setStatus("working");
          setLast(KIND[job.kind] ?? job.kind);
        } else {
          setStatus("ready");
          setJobs((n) => n + 1);
          if (seconds !== undefined) setLast((k) => `${k}, ${seconds.toFixed(1)} s`);
        }
      },
    }).finally(() => setStatus("off"));
  };

  const halt = () => {
    stop.current = true;
    deactivateKeepAwake();
    setStatus("off");
    setLine("Stopped. The models stay loaded until the app is closed.");
  };

  // A changed choice takes effect at once, also while the phone is lending.
  const change = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    if (!stop.current) prepare(next);
  };

  useEffect(() => () => unload(), []);

  const on = status !== "off";
  const headline =
    status === "off" ? "Not lending" : status === "loading" ? "Getting the models ready…" :
    status === "working" ? `Working for Moo: ${last}` : status === "error" ? "Stopped with a problem" : "Ready and waiting for Moo";

  return (
    <SafeAreaView style={styles.page}>
      <StatusBar style="dark" />
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.h1}>Moo AI</Text>
        <Text style={styles.sub}>This phone does Moo's listening, stories and voice. Keep the app open while your child plays.</Text>

        <View style={[styles.card, styles.now]}>
          <Text style={styles.h2}>{headline}</Text>
          {(["listening", "voice", "story"] as const).map((k) =>
            parts[k] ? <Text key={k} style={styles.small}>{k[0].toUpperCase() + k.slice(1)}: {parts[k]}</Text> : null
          )}
          {!!line && <Text style={styles.small}>{line}</Text>}
          {jobs > 0 && <Text style={styles.small}>{jobs} jobs done{last ? ` · last: ${last}` : ""}</Text>}
          <Pressable onPress={on ? halt : start} style={[styles.button, on && styles.buttonStop]}>
            <Text style={styles.buttonText}>{on ? "Stop" : "Lend this phone to Moo"}</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <Text style={styles.h2}>Address of the Moo</Text>
          <TextInput
            style={styles.input}
            value={address}
            onChangeText={setAddress}
            editable={!on}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
          />
        </View>

        <Picker title="Story model" choices={STORY_MODELS} value={settings.story} onChange={(id) => change({ story: id })} disabled={status === "loading"} />
        <Picker title="Listening model" choices={LISTEN_MODELS} value={settings.listen} onChange={(id) => change({ listen: id })} disabled={status === "loading"} />
        <Picker title="Voice (Kokoro)" choices={VOICES} value={settings.voice} onChange={(id) => change({ voice: id })} disabled={status === "loading"} />
      </ScrollView>
    </SafeAreaView>
  );
}

const INK = "#2b2118";
const ACCENT = "#7c5cdb";
const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: "#fff8ec" },
  scroll: { padding: 20, gap: 14 },
  h1: { fontSize: 30, fontWeight: "700", color: INK, marginTop: 8 },
  sub: { fontSize: 15, color: INK, opacity: 0.7 },
  h2: { fontSize: 17, fontWeight: "600", color: INK },
  small: { fontSize: 13, color: INK, opacity: 0.75 },
  card: { backgroundColor: "#fff", borderRadius: 16, padding: 16, gap: 8 },
  now: { borderWidth: 2, borderColor: ACCENT },
  button: { backgroundColor: ACCENT, borderRadius: 12, padding: 14, alignItems: "center", marginTop: 6 },
  buttonStop: { backgroundColor: "#b91c1c" },
  buttonText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  input: { borderWidth: 1, borderColor: "#d8d0c4", borderRadius: 10, padding: 10, fontSize: 15, color: INK },
  choice: { borderWidth: 1, borderColor: "#d8d0c4", borderRadius: 10, padding: 10 },
  choiceOn: { backgroundColor: ACCENT, borderColor: ACCENT },
  choiceText: { fontSize: 14, color: INK },
  choiceTextOn: { color: "#fff", fontWeight: "600" },
});
