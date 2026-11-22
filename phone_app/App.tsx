// Moo: the app for a Moo storyteller, on iPhone and Android.
//
// It is the Moo's remote, with the same stories, games and letters as the Moo
// website, and it is also the Moo's AI: the phone writes, listens and speaks
// for the Moo, out of sight, as soon as it has found the Moo on the wifi.
// The stories and games themselves run on the Moo.

import { useCallback, useEffect, useRef, useState } from "react";
import { Linking, Modal, Platform, Pressable, SafeAreaView, ScrollView, Share, StyleSheet, Text, TextInput, View } from "react-native";
import { StatusBar } from "expo-status-bar";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import { DEFAULTS, LISTEN_MODELS, PHONE_GB, PHONE_NAME, STORY_MODELS, VOICE_MODELS, downloadGB, fits, load, nameOf, runJob, unload, voicesOf, type Choice, type Settings } from "./src/ai";
import { Kitten2Host } from "./src/kitten2";
import { bye, lend, report, type Job } from "./src/moo";
import { connectMoo, findMoos } from "./src/ble";
import { BLUETOOTH, findMoo, moo, type AiSettings, type Computer, type Game, type GameState, type KnownDevice, type LentDevice, type LetterState, type StoryState } from "./src/pi";

const KIND: Record<string, string> = { chat: "writing", moderate: "checking", transcribe: "listening", speak: "speaking" };
const PHASE: Record<string, string> = {
  idle: "Not started",
  greeting: "Moo is saying hello",
  listening: "Moo is listening to your child",
  thinking: "Moo is thinking",
  telling: "Moo is telling the story",
  quiz: "Moo asked a question",
  spelling: "Spelling on the dots",
  ready: "Waiting for you: continue or stop",
  done: "Finished",
  error: "Stopped with a problem",
  showing: "Raising the dots",
  holding: "Holding the letter",
  lowering: "Lowering the dots",
};
const ADDRESS_KEY = "moo_address";
const SETTINGS_KEY = "moo_models";
// Set once the parent has chosen and installed the models; before that nothing is downloaded.
const CHOSEN_KEY = "moo_models_chosen";
type Tab = "story" | "games" | "letters" | "phone";
type Engine = "off" | "loading" | "ready" | "working" | "error";

function Button({ title, onPress, kind = "main", disabled }: { title: string; onPress: () => void; kind?: "main" | "stop" | "plain"; disabled?: boolean }) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={[styles.button, kind === "stop" && styles.buttonStop, kind === "plain" && styles.buttonPlain, disabled && { opacity: 0.45 }]}
    >
      <Text style={[styles.buttonText, kind === "plain" && { color: ACCENT }]}>{title}</Text>
    </Pressable>
  );
}

function Picker({ title, choices, value, onChange, disabled }: {
  title: string; choices: Choice[]; value: string; onChange: (id: string) => void; disabled?: boolean;
}) {
  // Every model is shown. One that does not fit this phone is greyed out and says so when tapped,
  // so the parent knows it exists and why it cannot be used here.
  const [tooBig, setTooBig] = useState("");
  return (
    <View style={styles.card}>
      <Text style={styles.h2}>{title}</Text>
      {choices.map((c) => {
        const ok = fits(c);
        return (
          <Pressable
            key={c.id}
            disabled={disabled}
            onPress={() => {
              if (ok) {
                setTooBig("");
                onChange(c.id);
              } else {
                setTooBig(`${c.label.split(" — ")[0]} is too big for this phone's ${PHONE_GB.toFixed(0)} GB of memory. Choose a smaller one.`);
              }
            }}
            style={[styles.choice, value === c.id && styles.choiceOn, (disabled || !ok) && { opacity: 0.45 }]}
          >
            <Text style={[styles.choiceText, value === c.id && styles.choiceTextOn]}>{c.label}</Text>
            {!ok && <Text style={styles.small}>Needs a phone with {c.minGB} GB or more</Text>}
          </Pressable>
        );
      })}
      {!!tooBig && <Text style={styles.warn}>{tooBig}</Text>}
    </View>
  );
}

/** Ask the Moo for a state every second and a half while `on`. */
function usePoll<T>(get: (() => Promise<T>) | null, on: boolean, set: (s: T) => void) {
  useEffect(() => {
    if (!get || !on) return;
    let live = true;
    const tick = () => get().then((s) => live && set(s)).catch(() => {});
    tick();
    const id = setInterval(tick, 1500);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [get, on, set]);
}

export default function App() {
  const [tab, setTab] = useState<Tab>("story");
  const [address, setAddress] = useState("http://moo.local:8000");
  const [base, setBase] = useState<string | null>(null); // the Moo that answered
  const [searching, setSearching] = useState(true);
  const [settings, setSettings] = useState<Settings>(DEFAULTS);
  const [needsChoice, setNeedsChoice] = useState(false);
  const [engine, setEngine] = useState<Engine>("off");
  const [parts, setParts] = useState<Record<string, string>>({});
  const [engineNote, setEngineNote] = useState("");
  const [jobs, setJobs] = useState(0);
  const [last, setLast] = useState("");
  const [note, setNote] = useState("");
  const [story, setStory] = useState<StoryState | null>(null);
  const [game, setGame] = useState<GameState | null>(null);
  const [letters, setLetters] = useState<LetterState | null>(null);
  const stop = useRef(true);

  const api = base ? moo(base) : null;
  const ready = engine === "ready" || engine === "working";

  /* ------------------------------------------------- the AI on this phone */

  const prepare = async (s: Settings, at: string) => {
    setEngine("loading");
    try {
      await load(s, (part, text) => setParts((p) => ({ ...p, [part]: text })));
      setEngine("ready");
      return true;
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      setEngine("error");
      const memory = /memory|oom|allocat|too large|jetsam/i.test(msg);
      setEngineNote(
        memory
          ? `A model is too big for this phone (${msg}). Choose a smaller story model or voice below, then try again.`
          : `Could not load the models: ${msg}`
      );
      report(at, `app error while loading: ${msg.slice(0, 100)}`);
      return false;
    }
  };

  const startEngine = async (at: string, s: Settings) => {
    if (!stop.current) return;
    stop.current = false;
    await activateKeepAwakeAsync().catch(() => {});
    report(at, `app starting on ${PHONE_NAME}`);
    if (!(await prepare(s, at))) {
      stop.current = true;
      return;
    }
    moo(at).useThisPhone().catch(() => {});
    report(at, "app loaded");
    lend(at, PHONE_NAME, runJob, () => stop.current, {
      onState: setEngineNote,
      onJob: (job: Job | null, seconds?: number) => {
        if (job) {
          setEngine("working");
          setLast(KIND[job.kind] ?? job.kind);
        } else {
          setEngine("ready");
          setJobs((n) => n + 1);
          if (seconds !== undefined) setLast((k) => `${k}, ${seconds.toFixed(1)} s`);
        }
      },
    }).finally(() => {
      stop.current = true;
      setEngine("off");
    });
  };

  const stopEngine = () => {
    stop.current = true;
    bye();
    deactivateKeepAwake();
    setEngine("off");
    setEngineNote("This phone no longer does the Moo's AI. The models stay loaded until the app is closed.");
  };

  /* ------------------------------------------------------ finding the Moo */

  const connect = useCallback(async (wanted: string, s: Settings) => {
    setSearching(true);
    setNote("");
    const found = await findMoo(wanted.trim().replace(/\/+$/, ""));
    setSearching(false);
    setBase(found);
    if (!found) {
      setNote("The Moo was not found. Check that the phone is on the Moo's wifi, or type its address under This phone.");
      return;
    }
    setAddress(found);
    AsyncStorage.setItem(ADDRESS_KEY, found).catch(() => {});
    // The models are downloaded only after the parent has chosen them once.
    const chosen = (await AsyncStorage.getItem(CHOSEN_KEY).catch(() => null)) === "1";
    setNeedsChoice(!chosen);
    if (chosen) startEngine(found, s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* --------------------------------------------- finding the Moo by Bluetooth */
  // Bluetooth finds the Moo nearby with no address to type. The controls work over it; the AI work, with
  // its sound files, needs the phone on the Moo's wifi, so the app moves to the wifi as soon as it can.
  const [bleFound, setBleFound] = useState<{ id: string; name: string }[]>([]);
  const [bleBusy, setBleBusy] = useState(false);
  const [bleNote, setBleNote] = useState("");
  const [needsWifi, setNeedsWifi] = useState(false);
  const [networks, setNetworks] = useState<{ ssid: string; signal: number; secured: boolean }[]>([]);
  const [wifiPick, setWifiPick] = useState("");
  const [wifiPassword, setWifiPassword] = useState("");

  const useBluetoothMoo = async (id: string) => {
    setBleBusy(true);
    setBleNote("Connecting over Bluetooth…");
    try {
      const name = await connectMoo(id);
      const st = await moo(BLUETOOTH).status();
      if (st.wifi_connected && st.address) {
        const overWifi = await findMoo(`http://${st.address}:8000`);
        if (overWifi) {
          setBleNote(`Found ${name} by Bluetooth. It is on the wifi ${st.wifi_ssid ?? ""}, and the app uses that now.`);
          await connect(overWifi, settings);
          return;
        }
        setBase(BLUETOOTH);
        setBleNote(
          `Connected to ${name} over Bluetooth. The Moo is on the wifi ${st.wifi_ssid ?? ""}, but this phone cannot reach it there: ` +
            "put the phone on that wifi so it can do the stories' AI."
        );
      } else {
        setBase(BLUETOOTH);
        setBleNote(`Connected to ${name} over Bluetooth. The Moo is not on a wifi yet: choose one below.`);
        openWifi(BLUETOOTH);
      }
    } catch (e: any) {
      setBleNote(String(e?.message ?? e));
    } finally {
      setBleBusy(false);
    }
  };

  const findByBluetooth = async () => {
    setBleBusy(true);
    setBleFound([]);
    setBleNote("Looking for Moos nearby…");
    const list: { id: string; name: string }[] = [];
    try {
      await findMoos(6, (d) => {
        list.push(d);
        setBleFound([...list]);
      });
      setBleBusy(false);
      if (list.length === 0) setBleNote("No Moo found nearby. Is it switched on and close to the phone?");
      else if (list.length === 1) await useBluetoothMoo(list[0].id);
      else setBleNote("Tap the Moo to connect to.");
    } catch (e: any) {
      setBleBusy(false);
      setBleNote(String(e?.message ?? e));
    }
  };

  /* ----------------------------------------------------- the Moo's wifi, two ways */
  // 1. Over Bluetooth: the app found the Moo nearby and gives it the home wifi's name and password.
  // 2. Without Bluetooth: the phone joins the Moo's own wifi (moo- and four digits) and gives it the home
  //    wifi from there, or the Moo is already on the home wifi and is reached by its address.
  const [wifiNow, setWifiNow] = useState<string | null>(null);
  const [wifiNote, setWifiNote] = useState("");
  const onMooHotspot = !!base && base.includes("10.41.0.1");

  const openWifi = (at: string) => {
    setNeedsWifi(true);
    setWifiPick("");
    setWifiNote("Looking for wifi networks near the Moo…");
    moo(at)
      .wifiScan()
      .then((r) => {
        setNetworks(r.networks);
        setWifiNote(r.networks.length ? "Choose the home wifi." : "The Moo sees no wifi networks.");
      })
      .catch((e) => setWifiNote(String(e?.message ?? e)));
  };

  // The Moo's own wifi: give it the home wifi straight away.
  useEffect(() => {
    if (onMooHotspot && base) openWifi(base);
  }, [onMooHotspot]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!base) return;
    moo(base).status().then((st) => setWifiNow(st.wifi_connected ? st.wifi_ssid ?? "a wifi" : null)).catch(() => {});
  }, [base]);

  const giveWifi = async () => {
    if (!base) return;
    const at = base;
    setBleBusy(true);
    setWifiNote(`The Moo is joining ${wifiPick}…`);
    try {
      await moo(at).wifiConnect(wifiPick, wifiPassword);
      setWifiPassword("");
      if (at === BLUETOOTH) {
        // Over Bluetooth the link stays while the Moo changes wifi, so the app can wait and see.
        for (let i = 0; i < 20; i++) {
          await new Promise((r) => setTimeout(r, 3000));
          const st = await moo(BLUETOOTH).status().catch(() => null);
          if (st?.wifi_connected && st.address && st.wifi_ssid === wifiPick) {
            setNeedsWifi(false);
            setWifiNow(st.wifi_ssid);
            const overWifi = await findMoo(`http://${st.address}:8000`);
            setWifiNote(`The Moo is on ${st.wifi_ssid}.${overWifi ? "" : " Put this phone on the same wifi for the stories' AI."}`);
            if (overWifi) await connect(overWifi, settings);
            return;
          }
        }
        setWifiNote("The Moo did not join that wifi. Check the password and try again.");
      } else {
        // Over wifi the Moo leaves the network the phone reached it on, so the phone has to follow it.
        setNeedsWifi(false);
        setWifiNote(`The Moo is moving to ${wifiPick}. Put this phone on ${wifiPick} too; the app looks for the Moo there.`);
        stop.current = true;
        setBase(null);
        for (let i = 0; i < 24; i++) {
          await new Promise((r) => setTimeout(r, 5000));
          const found = await findMoo("http://moo.local:8000");
          if (found) {
            setWifiNote(`The Moo is on ${wifiPick}.`);
            await connect(found, settings);
            return;
          }
        }
        setWifiNote(`The Moo was not found on ${wifiPick} yet. When the phone is on that wifi, tap Look again.`);
      }
    } catch (e: any) {
      setWifiNote(String(e?.message ?? e));
    } finally {
      setBleBusy(false);
    }
  };

  const wifiCard = (
    <View style={styles.card}>
      <Text style={styles.h2}>The Moo&apos;s wifi</Text>
      <Text style={styles.small}>
        {onMooHotspot
          ? "The phone is on the Moo's own wifi. Give the Moo your home wifi below."
          : base === BLUETOOTH
            ? `Connected over Bluetooth. ${wifiNow ? `The Moo is on ${wifiNow}.` : "The Moo is not on a wifi."}`
            : wifiNow
              ? `The Moo is on ${wifiNow}.`
              : "Asking the Moo…"}
      </Text>
      {!needsWifi && <Button title="Put the Moo on another wifi" kind="plain" disabled={!base || bleBusy} onPress={() => base && openWifi(base)} />}
      {needsWifi && (
        <View style={{ gap: 8 }}>
          {networks.map((n) => (
            <Pressable key={n.ssid} onPress={() => setWifiPick(n.ssid)} style={[styles.choice, wifiPick === n.ssid && styles.choiceOn]}>
              <Text style={[styles.choiceText, wifiPick === n.ssid && styles.choiceTextOn]}>{n.ssid}{n.secured ? "" : " · open"}</Text>
            </Pressable>
          ))}
          {!!wifiPick && (
            <>
              <TextInput style={styles.input} value={wifiPassword} onChangeText={setWifiPassword} placeholder="Wifi password" secureTextEntry autoCapitalize="none" autoCorrect={false} />
              <Button title={`Put the Moo on ${wifiPick}`} disabled={bleBusy} onPress={giveWifi} />
            </>
          )}
          <Button title="Cancel" kind="plain" onPress={() => setNeedsWifi(false)} />
        </View>
      )}
      {!!wifiNote && <Text style={styles.small}>{wifiNote}</Text>}
    </View>
  );

  const bluetoothCard = (
    <View style={styles.card}>
      <Text style={styles.h2}>Connect with Bluetooth</Text>
      <Text style={styles.small}>Finds the Moo nearby, with no address to type. It can also put the Moo on a wifi.</Text>
      <Button title={bleBusy ? "Working…" : "Find the Moo with Bluetooth"} kind="plain" disabled={bleBusy} onPress={findByBluetooth} />
      {bleFound.length > 1 &&
        bleFound.map((d) => (
          <Pressable key={d.id} disabled={bleBusy} onPress={() => useBluetoothMoo(d.id)} style={styles.choice}>
            <Text style={styles.choiceText}>{d.name}</Text>
          </Pressable>
        ))}
      {!!bleNote && <Text style={styles.small}>{bleNote}</Text>}
    </View>
  );

  useEffect(() => {
    (async () => {
      const saved = (await AsyncStorage.getItem(ADDRESS_KEY).catch(() => null)) ?? "http://moo.local:8000";
      let s = DEFAULTS;
      try {
        s = { ...DEFAULTS, ...JSON.parse((await AsyncStorage.getItem(SETTINGS_KEY)) ?? "{}") };
      } catch {
        /* keep the defaults */
      }
      setSettings(s);
      setAddress(saved);
      connect(saved, s);
    })();
    return () => unload();
  }, [connect]);

  // A changed model takes effect at once, also while the phone is working.
  const change = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(next)).catch(() => {});
    if (!stop.current && base) prepare(next, base);
  };

  /* ------------------------------------------------- the Moo's activities */

  const getStory = useCallback(() => api!.storyState(), [base]); // eslint-disable-line react-hooks/exhaustive-deps
  const getGame = useCallback(() => api!.gamesState(), [base]); // eslint-disable-line react-hooks/exhaustive-deps
  const getLetters = useCallback(() => api!.lettersState(), [base]); // eslint-disable-line react-hooks/exhaustive-deps
  usePoll(base ? getStory : null, tab === "story", setStory);
  usePoll(base ? getGame : null, tab === "games", setGame);
  usePoll(base ? getLetters : null, tab === "letters", setLetters);


  // One request to the Moo at a time: a tap that arrives while the last one is
  // still on its way is dropped. Two story starts in the same second once ran
  // two stories at once (21 November 2026).
  const inFlight = useRef(false);
  const [sending, setSending] = useState(false);
  const act = async (what: () => Promise<unknown>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setNote("");
    try {
      await what();
    } catch (e: any) {
      setNote(String(e?.message ?? e));
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  };

  // Every device that can be the Moo's AI: this phone, other lent phones and
  // computers, and Moo Local machines. The one in use is shown first.
  const [lent, setLent] = useState<LentDevice[]>([]);
  const [knownDevices, setKnownDevices] = useState<KnownDevice[]>([]);
  const [aiNow, setAiNow] = useState<AiSettings | null>(null);
  const [computers, setComputers] = useState<Computer[]>([]);
  const getDevices = useCallback(
    () => Promise.all([api!.lent(), api!.aiGet()]).then(([l, a]) => ({ lent: l.devices, known: l.known ?? [], ai: a })),
    [base] // eslint-disable-line react-hooks/exhaustive-deps
  );
  const setDevices = useCallback((d: { lent: LentDevice[]; known: KnownDevice[]; ai: AiSettings }) => {
    setLent(d.lent);
    setKnownDevices(d.known);
    setAiNow(d.ai);
  }, []);
  usePoll(base ? getDevices : null, tab === "phone", setDevices);
  useEffect(() => {
    if (!base || tab !== "phone") return;
    const look = () => moo(base).computers().then((r) => setComputers(r.engines)).catch(() => {});
    look();
    const id = setInterval(look, 15000);
    return () => clearInterval(id);
  }, [base, tab]);

  type Row = { key: string; title: string; text: string; current: boolean; off?: boolean; use?: () => Promise<unknown>; forget?: () => Promise<unknown> };
  const ago = (t?: number | null) => {
    if (!t) return "";
    const m = Math.round((Date.now() / 1000 - t) / 60);
    return m < 2 ? "just now" : m < 90 ? `${m} minutes ago` : m < 36 * 60 ? `${Math.round(m / 60)} hours ago` : `${Math.round(m / 1440)} days ago`;
  };
  const rows: Row[] = [];
  if (aiNow) {
    const lentNow = aiNow.mode === "browser";
    const me = lent.find((d) => d.name === PHONE_NAME);
    rows.push({
      key: "me",
      title: `This phone (${PHONE_NAME})`,
      text: ready ? `Models loaded · ${jobs} jobs done here` : engine === "loading" ? "Loading its models…" : "Not doing AI right now",
      current: lentNow && !!me?.current,
      use: async () => {
        if (stop.current && base && base !== BLUETOOTH) startEngine(base, settings);
        await api!.useLent(PHONE_NAME);
      },
    });
    for (const d of lent.filter((d) => d.name !== PHONE_NAME)) {
      rows.push({
        key: `lent-${d.name}`,
        title: d.name,
        text: `${/app\b/.test(d.name) ? "The Moo app" : "A browser"} · ${d.jobs_done} jobs done`,
        current: lentNow && !!d.current,
        use: () => api!.useLent(d.name),
      });
    }
    // Phones and browsers that are off now: the Moo remembers them.
    const lendingNames = new Set(lent.map((d) => d.name));
    for (const k of knownDevices.filter((k) => k.kind === "lent" && !lendingNames.has(k.name) && k.name !== PHONE_NAME)) {
      rows.push({
        key: `off-${k.name}`,
        title: k.name,
        text: `Off${k.last_seen ? `, last seen ${ago(k.last_seen)}` : ""} · ${/app\b/.test(k.name) ? "open the Moo app on it" : "open the Moo website on it and set it up"}`,
        current: false,
        off: true,
        forget: () => api!.forget(k.name),
      });
    }
    // One row per machine: Moo Locals on the same computer (another port, a second copy) are one device.
    // The one in use, or else one that answers, stands for the machine.
    const hostOf = (url: string) => (url.split("//").pop() ?? "").split(/[:/]/)[0];
    const byHost = new Map<string, Computer>();
    for (const c of computers) {
      const h = hostOf(c.url);
      const had = byHost.get(h);
      const inUse = (x: Computer) => aiNow.mode === "local" && aiNow.local_url === x.url;
      if (!had || inUse(c) || (!inUse(had) && c.reachable && !had.reachable)) byHost.set(h, c);
    }
    for (const c of byHost.values()) {
      rows.push({
        key: `pc-${c.url}`,
        title: c.name,
        text: `Moo Local · ${c.ready ? "ready" : c.reachable ? "on, getting ready" : "off · start Moo Local on it"}`,
        current: aiNow.mode === "local" && aiNow.local_url === c.url,
        off: !c.reachable,
        use: c.reachable ? () => api!.useComputer(c) : undefined,
        forget: knownDevices.some((k) => k.name === c.name) ? () => api!.forget(c.name) : undefined,
      });
    }
    if (aiNow.has_openai_key || aiNow.mode === "cloud") {
      rows.push({ key: "cloud", title: "Cloud, OpenAI", text: "Needs internet; each story costs a little", current: aiNow.mode === "cloud", use: () => api!.useCloud() });
    }
    rows.sort((a, b) => Number(b.current) - Number(a.current) || Number(!!a.off) - Number(!!b.off));
  }
  // Adding a device: it joins the list by itself once it works for the Moo.
  const [adding, setAdding] = useState(false);
  const host = (base ?? address).replace(/^https?:\/\//, "").replace(/:\d+.*$/, "");
  const ADD_WAYS = [
    {
      title: "Another phone, with the Moo app",
      text: "Install the Moo app on it and open it on the same wifi. It finds the Moo and joins this list.",
      share: `Moo app: open it on the same wifi as the Moo. If it asks for the Moo's address, use ${base ?? address}`,
    },
    {
      title: "Any phone or computer, in its browser",
      text: "Open this address, accept the browser's warning once, and tap This device.",
      share: `https://${host}:8443/ai/?thisdevice=1`,
    },
    {
      title: "A computer with Moo Local, for the best quality",
      text: "Open this page on a Windows, Mac or Linux computer and download Moo Local. Once it runs, the computer shows in this list.",
      share: `http://${host}:8000/ai/setup/`,
    },
  ];
  const addCard = (
    <View style={{ gap: 10 }}>
      {ADD_WAYS.map((w) => (
        <View key={w.title} style={styles.choice}>
          <Text style={styles.choiceText}>{w.title}</Text>
          <Text style={styles.small}>{w.text}</Text>
          <Text selectable style={styles.code}>{w.share}</Text>
          <Button title="Send to the other device" kind="plain" onPress={() => Share.share({ message: w.share }).catch(() => {})} />
        </View>
      ))}
    </View>
  );
  const devicesCard = (
    <View style={styles.card}>
      <Text style={styles.h2}>Who does the Moo's AI</Text>
      {rows.length === 0 && <Text style={styles.small}>Asking the Moo…</Text>}
      {rows.map((r, i) => (
        <View key={r.key}>
          {i === 1 && rows[0].current && <Text style={[styles.small, { marginBottom: 6 }]}>Other devices, tap one to use it</Text>}
          <Pressable
            disabled={r.current || !r.use || sending}
            onPress={() => r.use && act(async () => {
              await r.use!();
              setDevices(await getDevices());
            })}
            style={[styles.choice, r.current && styles.choiceOn, !r.current && !r.use && { opacity: 0.5 }]}
          >
            <Text style={[styles.choiceText, r.current && styles.choiceTextOn]}>{r.title}{r.current ? " · in use now" : ""}</Text>
            <Text style={[styles.small, r.current && { color: "#fff", opacity: 0.9 }]}>{r.text}</Text>
          </Pressable>
          {!r.current && r.forget && (
            <Pressable onPress={() => act(async () => { await r.forget!(); setDevices(await getDevices()); })} style={{ alignSelf: "flex-end", padding: 4 }}>
              <Text style={[styles.small, { textDecorationLine: "underline" }]}>Forget {r.title}</Text>
            </Pressable>
          )}
        </View>
      ))}
      <Button title={adding ? "Close" : "Add a device"} kind="plain" onPress={() => setAdding((a) => !a)} />
      {adding && addCard}
    </View>
  );

  const busy = !!(story?.active || game?.active || letters?.active);
  const needAi = !ready ? (engine === "loading" ? "The phone is still getting its models ready…" : "The phone's AI is not running. Start it under This phone.") : "";

  /* --------------------------------------------------------------- screens */

  const storyScreen = (
    <View style={styles.card}>
      <Text style={styles.h2}>Story time</Text>
      <Text style={styles.body}>Moo greets your child, asks what the story should be about, tells it part by part and asks questions along the way.</Text>
      <Text style={styles.phase}>{PHASE[story?.phase ?? "idle"] ?? story?.phase}</Text>
      {!!story?.message && <Text style={styles.quote}>“{story.message}”</Text>}
      {!!story?.spelling_word && <Text style={styles.small}>Word on the dots: {story.spelling_word}{story.spell_letter ? `, letter ${story.spell_letter}` : ""}</Text>}
      {!!story?.error && <Text style={styles.warn}>{story.error}</Text>}
      {!story?.active ? (
        <Button title={sending ? "Starting…" : "Start a story"} disabled={!ready || busy || sending} onPress={() => act(() => api!.storyStart().then(setStory))} />
      ) : (
        <>
          <Button title="Continue the story" disabled={story.phase !== "ready" || sending} onPress={() => act(() => api!.storyContinue().then(setStory))} />
          <Button title="Stop" kind="stop" onPress={() => act(() => api!.storyStop())} />
        </>
      )}
      {!!story?.story && (
        <View style={{ marginTop: 6 }}>
          <Text style={styles.small}>The story so far ({story.parts} {story.parts === 1 ? "part" : "parts"})</Text>
          <Text style={styles.body}>{story.story}</Text>
        </View>
      )}
    </View>
  );

  const GAMES: { id: Game; title: string; text: string }[] = [
    { id: "mix", title: "A mix", text: "Moo picks a game each round" },
    { id: "spell", title: "Spell a word", text: "Feel each letter, press a button for every raised dot" },
    { id: "name", title: "Name the letter", text: "Feel a letter and say its name" },
    { id: "build", title: "Build the letter", text: "Raise the dots that make a letter" },
  ];
  const gamesScreen = (
    <View style={styles.card}>
      <Text style={styles.h2}>Braille games</Text>
      {game?.active ? (
        <>
          <Text style={styles.phase}>{GAMES.find((g) => g.id === game.game)?.title ?? "Game"}: {PHASE[game.phase] ?? game.phase}</Text>
          {!!game.message && <Text style={styles.quote}>“{game.message}”</Text>}
          {!!game.spelling_word && <Text style={styles.small}>Word: {game.spelling_word}{game.spell_letter ? `, letter ${game.spell_letter}` : ""}</Text>}
          <Text style={styles.small}>{game.rounds} {game.rounds === 1 ? "round" : "rounds"} played</Text>
          <Button title="Stop the game" kind="stop" onPress={() => act(() => api!.gamesStop())} />
        </>
      ) : (
        GAMES.map((g) => (
          <Pressable key={g.id} disabled={!ready || busy || sending} onPress={() => act(() => api!.gamesStart(g.id).then(setGame))} style={[styles.choice, (!ready || busy) && { opacity: 0.45 }]}>
            <Text style={styles.choiceText}>{g.title}</Text>
            <Text style={styles.small}>{g.text}</Text>
          </Pressable>
        ))
      )}
      {!!game?.error && <Text style={styles.warn}>{game.error}</Text>}
    </View>
  );

  const ABC = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
  const showLetter = (l: string) =>
    act(async () => {
      if (!letters?.active) await api!.lettersStart();
      setLetters(await api!.lettersShow(l));
    });
  const lettersScreen = (
    <View style={styles.card}>
      <Text style={styles.h2}>Letters</Text>
      <Text style={styles.body}>Tap a letter and Moo raises its dots. No AI is needed for this.</Text>
      {letters?.active && !!letters.letter && <Text style={styles.phase}>{letters.letter}: {PHASE[letters.phase] ?? letters.phase}</Text>}
      <View style={styles.grid}>
        {ABC.map((l) => (
          <Pressable key={l} onPress={() => showLetter(l)} disabled={!!(story?.active || game?.active)} style={[styles.key, letters?.letter === l && styles.choiceOn]}>
            <Text style={[styles.keyText, letters?.letter === l && styles.choiceTextOn]}>{l}</Text>
          </Pressable>
        ))}
      </View>
      {letters?.active && <Button title="Lower the dots and finish" kind="plain" onPress={() => act(() => api!.lettersStop().then(() => setLetters(null)))} />}
    </View>
  );

  const engineLine =
    engine === "off" ? "Not doing the Moo's AI" : engine === "loading" ? "Getting the models ready…" :
    engine === "working" ? `Working for Moo: ${last}` : engine === "error" ? "The AI stopped with a problem" : "Ready: this phone is the Moo's AI";
  /* ------------------------------------------------------- the connection wizard */
  // One step at a time, from a Moo that is connected to nothing to a story. The step follows from what is
  // true now, so the wizard can be opened at any point and shows the next thing to do.
  const [wizard, setWizard] = useState<null | "how" | "ble" | "hotspot" | "address">(null);
  const step: "how" | "ble" | "hotspot" | "address" | "wifi" | "phone" | "done" = !wizard
    ? "how"
    : !base
      ? wizard
      : needsWifi || onMooHotspot
        ? "wifi"
        : base === BLUETOOTH
          ? "phone"
          : "done";
  const STEP_NO: Record<typeof step, number> = { how: 1, ble: 2, hotspot: 2, address: 2, wifi: 3, phone: 4, done: 5 };
  const openWizard = () => {
    setWizard("how");
    setBleNote("");
    setWifiNote("");
  };
  const wizardBody =
    step === "how" ? (
      <>
        <Text style={styles.wizTitle}>How shall the phone find the Moo?</Text>
        <Text style={styles.body}>Switch the Moo on and keep the phone close to it.</Text>
        <Pressable style={styles.choice} onPress={() => { setWizard("ble"); findByBluetooth(); }}>
          <Text style={styles.choiceText}>With Bluetooth (easiest)</Text>
          <Text style={styles.small}>The phone finds the Moo nearby. Nothing to type but your wifi password.</Text>
        </Pressable>
        <Pressable style={styles.choice} onPress={() => setWizard("hotspot")}>
          <Text style={styles.choiceText}>Through the Moo&apos;s own wifi</Text>
          <Text style={styles.small}>A Moo that is on no wifi makes its own, named moo- and four digits. The phone joins it for a moment.</Text>
        </Pressable>
        <Pressable style={styles.choice} onPress={() => setWizard("address")}>
          <Text style={styles.choiceText}>It is already on my wifi</Text>
          <Text style={styles.small}>Type the Moo&apos;s address, if the app did not find it by itself.</Text>
        </Pressable>
      </>
    ) : step === "ble" ? (
      <>
        <Text style={styles.wizTitle}>Finding the Moo with Bluetooth</Text>
        <Text style={styles.body}>If the phone asks to allow Bluetooth or to pair with the Moo, say yes.</Text>
        {bleFound.length > 1 &&
          bleFound.map((d) => (
            <Pressable key={d.id} disabled={bleBusy} onPress={() => useBluetoothMoo(d.id)} style={styles.choice}>
              <Text style={styles.choiceText}>{d.name}</Text>
            </Pressable>
          ))}
        {!!bleNote && <Text style={styles.small}>{bleNote}</Text>}
        <Button title={bleBusy ? "Looking…" : "Look again"} kind="plain" disabled={bleBusy} onPress={findByBluetooth} />
      </>
    ) : step === "hotspot" ? (
      <>
        <Text style={styles.wizTitle}>Join the Moo&apos;s own wifi</Text>
        <Text style={styles.body}>
          1. Open the phone&apos;s Settings, then Wi-Fi.{"\n"}2. Join the network named moo- and four digits. It has no password.{"\n"}3. Come back
          to this app.
        </Text>
        <Button title="Open the phone's Settings" kind="plain" onPress={() => Linking.openSettings().catch(() => {})} />
        <Button title={searching ? "Looking for the Moo…" : "I have joined it"} disabled={searching} onPress={() => connect("http://10.41.0.1:8000", settings)} />
        {!!note && <Text style={styles.small}>{note}</Text>}
      </>
    ) : step === "address" ? (
      <>
        <Text style={styles.wizTitle}>The Moo&apos;s address</Text>
        <Text style={styles.body}>The Moo&apos;s screen on the website shows it, or your router&apos;s list of devices. It looks like http://192.168.2.52:8000</Text>
        <TextInput style={styles.input} value={address} onChangeText={setAddress} autoCapitalize="none" autoCorrect={false} keyboardType="url" />
        <Button title={searching ? "Looking for the Moo…" : "Connect"} disabled={searching} onPress={() => connect(address, settings)} />
        {!!note && <Text style={styles.small}>{note}</Text>}
      </>
    ) : step === "wifi" ? (
      <>
        <Text style={styles.wizTitle}>Give the Moo your wifi</Text>
        <Text style={styles.body}>Choose the wifi of your home and type its password. The Moo remembers it.</Text>
        {!needsWifi && <Button title="Show the networks" kind="plain" onPress={() => base && openWifi(base)} />}
        {networks.map((n) => (
          <Pressable key={n.ssid} onPress={() => setWifiPick(n.ssid)} style={[styles.choice, wifiPick === n.ssid && styles.choiceOn]}>
            <Text style={[styles.choiceText, wifiPick === n.ssid && styles.choiceTextOn]}>{n.ssid}{n.secured ? "" : " · open"}</Text>
          </Pressable>
        ))}
        {!!wifiPick && (
          <>
            <TextInput style={styles.input} value={wifiPassword} onChangeText={setWifiPassword} placeholder="Wifi password" secureTextEntry autoCapitalize="none" autoCorrect={false} />
            <Button title={bleBusy ? "The Moo is joining…" : `Put the Moo on ${wifiPick}`} disabled={bleBusy} onPress={giveWifi} />
          </>
        )}
        {!!wifiNote && <Text style={styles.small}>{wifiNote}</Text>}
      </>
    ) : step === "phone" ? (
      <>
        <Text style={styles.wizTitle}>Put the phone on the same wifi</Text>
        <Text style={styles.body}>
          The Moo is on {wifiNow ?? "a wifi"}. The phone does the stories&apos; thinking, so it has to be on {wifiNow ?? "that wifi"} too.
        </Text>
        <Button title="Open the phone's Settings" kind="plain" onPress={() => Linking.openSettings().catch(() => {})} />
        <Button title={bleBusy ? "Checking…" : "Check again"} disabled={bleBusy} onPress={findByBluetooth} />
        {!!bleNote && <Text style={styles.small}>{bleNote}</Text>}
      </>
    ) : (
      <>
        <Text style={styles.wizTitle}>The Moo is connected</Text>
        <Text style={styles.body}>
          {wifiNow ? `The Moo is on ${wifiNow}, and this phone reaches it there. ` : "This phone reaches the Moo. "}
          {ready ? "The phone's AI is ready." : engine === "loading" ? "The phone is getting its models ready; the first time takes a few minutes." : ""}
        </Text>
        <Button title="Start a story" disabled={!ready} onPress={() => { setWizard(null); setTab("story"); }} />
      </>
    );
  const wizardModal = (
    <Modal visible={!!wizard} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setWizard(null)}>
      <SafeAreaView style={styles.page}>
        <ScrollView contentContainerStyle={styles.scroll}>
          <Text style={styles.small}>Set up a Moo · step {STEP_NO[step]} of 5</Text>
          <View style={styles.card}>{wizardBody}</View>
          {step !== "how" && step !== "done" && <Button title="Back to the start" kind="plain" onPress={() => setWizard("how")} />}
          <Button title="Close" kind="plain" onPress={() => setWizard(null)} />
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );

  // Nothing is downloaded until the parent has looked at the models and tapped Install.
  const installChosen = () => {
    AsyncStorage.setItem(CHOSEN_KEY, "1").catch(() => {});
    setNeedsChoice(false);
    if (base && base !== BLUETOOTH) startEngine(base, settings);
  };
  const chooserCard = (
    <View style={[styles.card, styles.now]}>
      <Text style={styles.h2}>Choose the models for this phone</Text>
      <Text style={styles.body}>
        Nothing is downloaded until you tap Install. The ones that suit this phone{PHONE_GB ? ` (${PHONE_GB.toFixed(0)} GB)` : ""} are already chosen:
      </Text>
      <Text style={styles.small}>Story: {nameOf(STORY_MODELS, settings.story)}</Text>
      <Text style={styles.small}>Listening: {nameOf(LISTEN_MODELS, settings.listen)}</Text>
      <Text style={styles.small}>Voice: {nameOf(VOICE_MODELS, settings.engine)}, {nameOf(voicesOf(settings.engine), settings.voice)}</Text>
      <Button title={`Install and start, about ${downloadGB(settings).toFixed(1)} GB`} disabled={!base || base === BLUETOOTH} onPress={installChosen} />
      {tab !== "phone" ? (
        <Button title="Change the models" kind="plain" onPress={() => setTab("phone")} />
      ) : (
        <Text style={styles.small}>To change them, choose below before you tap Install.</Text>
      )}
    </View>
  );

  const phoneScreen = (
    <>
      {needsChoice && chooserCard}
      <Button title="Set up a Moo, step by step" onPress={openWizard} />
      {devicesCard}
      <View style={[styles.card, styles.now]}>
        <Text style={styles.h2}>{engineLine}</Text>
        {(["listening", "voice", "story"] as const).map((k) =>
          parts[k] ? <Text key={k} style={styles.small}>{k[0].toUpperCase() + k.slice(1)}: {parts[k]}</Text> : null
        )}
        {!!engineNote && <Text style={styles.small}>{engineNote}</Text>}
        {jobs > 0 && <Text style={styles.small}>{jobs} jobs done{last ? ` · last: ${last}` : ""}</Text>}
        {PHONE_GB > 0 && <Text style={styles.small}>This phone has {PHONE_GB.toFixed(1)} GB of memory. Only the models that fit are shown.</Text>}
        {engine === "off" || engine === "error" ? (
          <Button title="Be the Moo's AI" disabled={!base} onPress={() => base && base !== BLUETOOTH && startEngine(base, settings)} />
        ) : (
          <>
            <Button title="Hear a test line on the Moo" kind="plain" disabled={!ready} onPress={() => act(() => api!.testVoice().then((r) => setNote(`Moo said: “${r.line}”`)))} />
            <Button title="Stop being the Moo's AI" kind="stop" onPress={stopEngine} />
          </>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.h2}>Address of the Moo</Text>
        <TextInput style={styles.input} value={address} onChangeText={setAddress} autoCapitalize="none" autoCorrect={false} keyboardType="url" />
        <Text style={styles.small}>Most Android phones need the number form, such as http://192.168.2.52:8000</Text>
        <Button title={searching ? "Looking for the Moo…" : "Connect"} kind="plain" disabled={searching} onPress={() => connect(address, settings)} />
      </View>
      {bluetoothCard}
      {!!base && wifiCard}

      <Picker title="Story model" choices={STORY_MODELS} value={settings.story} onChange={(id) => change({ story: id })} disabled={engine === "loading"} />
      <Picker title="Listening model" choices={LISTEN_MODELS} value={settings.listen} onChange={(id) => change({ listen: id })} disabled={engine === "loading"} />
      <Picker
        title="Voice model"
        choices={VOICE_MODELS}
        value={settings.engine}
        onChange={(id) => change({ engine: id, voice: voicesOf(id)[0].id })}
        disabled={engine === "loading"}
      />
      <Picker title="Voice" choices={voicesOf(settings.engine)} value={settings.voice} onChange={(id) => change({ voice: id })} disabled={engine === "loading"} />
    </>
  );

  const TABS: { id: Tab; title: string }[] = [
    { id: "story", title: "Story" },
    { id: "games", title: "Games" },
    { id: "letters", title: "Letters" },
    { id: "phone", title: "This phone" },
  ];

  return (
    <SafeAreaView style={styles.page}>
      <StatusBar style="dark" />
      {wizardModal}
      <Kitten2Host />
      <View style={styles.header}>
        <Text style={styles.h1}>Moo</Text>
        <Text style={styles.small}>
          {searching ? "Looking for the Moo…" : base ? `Connected to the Moo · ${engineLine}` : "Not connected to a Moo"}
        </Text>
      </View>
      <ScrollView contentContainerStyle={styles.scroll}>
        {!!note && <Text style={styles.warn}>{note}</Text>}
        {!!base && (needsWifi || onMooHotspot) && tab !== "phone" && wifiCard}
        {!!base && needsChoice && tab !== "phone" && chooserCard}
        {tab !== "phone" && tab !== "letters" && !!base && !!needAi && <Text style={styles.small}>{needAi}</Text>}
        {!base && !searching && tab !== "phone" ? (
          <View style={styles.card}>
            <Text style={styles.h2}>No Moo found</Text>
            <Text style={styles.body}>Put the phone on the same wifi as the Moo, or find it with Bluetooth below.</Text>
            <Text style={styles.small}>
              Without Bluetooth: open the phone&apos;s wifi settings, join the Moo&apos;s own wifi (named moo- and four digits), come back and tap
              Look again. Or type the Moo&apos;s address under This phone.
            </Text>
            <Button title="Set up a Moo, step by step" onPress={openWizard} />
            <Button title="Look again" kind="plain" onPress={() => connect(address, settings)} />
          </View>
        ) : null}
        {!base && !searching && tab !== "phone" && bluetoothCard}
        {!base && !searching && tab !== "phone" ? null : tab === "story" ? storyScreen : tab === "games" ? gamesScreen : tab === "letters" ? lettersScreen : phoneScreen}
      </ScrollView>
      <View style={styles.tabs}>
        {TABS.map((t) => (
          <Pressable key={t.id} onPress={() => setTab(t.id)} style={styles.tab}>
            <Text style={[styles.tabText, tab === t.id && styles.tabOn]}>{t.title}</Text>
          </Pressable>
        ))}
      </View>
    </SafeAreaView>
  );
}

const INK = "#2b2118";
const ACCENT = "#7c5cdb";
const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: "#fff8ec" },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 6, gap: 2 },
  scroll: { padding: 20, paddingTop: 8, gap: 14 },
  h1: { fontSize: 30, fontWeight: "700", color: INK },
  h2: { fontSize: 18, fontWeight: "600", color: INK },
  body: { fontSize: 15, color: INK, lineHeight: 21 },
  small: { fontSize: 13, color: INK, opacity: 0.75 },
  phase: { fontSize: 16, fontWeight: "600", color: ACCENT },
  wizTitle: { fontSize: 22, fontWeight: "700", color: INK },
  quote: { fontSize: 15, color: INK, fontStyle: "italic", lineHeight: 21 },
  warn: { fontSize: 14, color: "#b91c1c" },
  code: { fontSize: 13, color: INK, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace", backgroundColor: "#f4efe6", padding: 8, borderRadius: 8 },
  card: { backgroundColor: "#fff", borderRadius: 16, padding: 16, gap: 10 },
  now: { borderWidth: 2, borderColor: ACCENT },
  button: { backgroundColor: ACCENT, borderRadius: 12, padding: 14, alignItems: "center" },
  buttonStop: { backgroundColor: "#b91c1c" },
  buttonPlain: { backgroundColor: "transparent", borderWidth: 1, borderColor: ACCENT },
  buttonText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  input: { borderWidth: 1, borderColor: "#d8d0c4", borderRadius: 10, padding: 10, fontSize: 15, color: INK },
  choice: { borderWidth: 1, borderColor: "#d8d0c4", borderRadius: 10, padding: 12, gap: 2 },
  choiceOn: { backgroundColor: ACCENT, borderColor: ACCENT },
  choiceText: { fontSize: 15, color: INK, fontWeight: "500" },
  choiceTextOn: { color: "#fff", fontWeight: "600" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  key: { width: 46, height: 46, borderRadius: 10, borderWidth: 1, borderColor: "#d8d0c4", alignItems: "center", justifyContent: "center" },
  keyText: { fontSize: 18, fontWeight: "600", color: INK },
  tabs: { flexDirection: "row", borderTopWidth: 1, borderTopColor: "#e8dfd2", backgroundColor: "#fff" },
  tab: { flex: 1, paddingVertical: 14, alignItems: "center" },
  tabText: { fontSize: 14, color: INK, opacity: 0.6 },
  tabOn: { opacity: 1, fontWeight: "700", color: ACCENT },
});
